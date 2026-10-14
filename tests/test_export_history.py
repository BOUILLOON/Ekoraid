"""Ready ZIP discovery and resumable private downloads across browser sessions."""
import json
from pathlib import Path
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient

from playlist25.app import create_app
from playlist25.config import Settings
from playlist25 import db


def configuration(tmp_path):
    cfg = Settings(root=tmp_path, data_dir=tmp_path / "data", admin_password="history-local-only-password",
                   session_secret="history-local-only-signing-secret", min_free_mb=0)
    db.init_db(cfg)
    return cfg


def ready_zip(cfg):
    export_id = uuid4().hex
    directory = cfg.export_dir / export_id
    directory.mkdir()
    archive = cfg.export_dir / (export_id + ".zip")
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("Anniversaire25/manifeste.json", json.dumps({"count": 1, "warnings": []}))
        output.writestr("Anniversaire25/audio/local-fixture.txt", "Generated fixture only")
    with db.connect(cfg) as connection:
        connection.execute("INSERT INTO exports(id,directory,archive,created_at) VALUES(?,?,?,?)",
                           (export_id, str(directory), str(archive), db.now()))
    return export_id, archive


def logged_in(cfg):
    client = TestClient(create_app("admin", cfg))
    csrf = client.get("/api/session").json()["csrf_token"]
    reply = client.post("/api/login", json={"password": cfg.admin_password}, headers={"X-CSRF-Token": csrf})
    assert reply.status_code == 200
    return client


def test_ready_zip_can_be_found_after_new_browser_session_and_resumed(tmp_path):
    cfg = configuration(tmp_path)
    export_id, archive = ready_zip(cfg)
    client = TestClient(create_app("admin", cfg))
    assert client.get("/api/exports").status_code == 401
    assert client.head(f"/api/exports/{export_id}/zip").status_code == 401
    for _ in range(2):
        client = logged_in(cfg)
        history = client.get("/api/exports")
        assert history.status_code == 200
        assert len(history.json()["items"]) == 1
        item = history.json()["items"][0]
        assert item["id"] == export_id and item["size"] == archive.stat().st_size
        for value in (str(cfg.root), str(cfg.data_dir), cfg.admin_password, cfg.session_secret):
            assert value not in history.text
        url = item["download_url"]
        head = client.head(url)
        assert head.status_code == 200 and head.content == b""
        first = client.get(url, headers={"Range": "bytes=0-24"})
        rest = client.get(url, headers={"Range": "bytes=25-", "If-Range": head.headers["etag"]})
        assert first.status_code == rest.status_code == 206
        assert first.content + rest.content == archive.read_bytes()
    public = TestClient(create_app("guest", cfg))
    public.cookies.update(client.cookies)
    assert public.get("/api/exports").status_code == 404
    assert public.get(url, headers={"Range": "bytes=0-24"}).status_code == 404
    archive.unlink()
    assert client.get("/api/exports").json() == {"items": []}
    assert client.get(url).status_code == 404


def test_corrupted_or_escaped_zip_cannot_be_discovered_or_downloaded(tmp_path):
    cfg = configuration(tmp_path)
    export_id, archive = ready_zip(cfg)
    client = logged_in(cfg)
    outside = tmp_path / "outside.zip"
    outside.write_bytes(archive.read_bytes())
    with db.connect(cfg) as conn:
        conn.execute("UPDATE exports SET archive=? WHERE id=?", (str(outside), export_id))
    assert client.get("/api/exports").json()["items"] == []
    assert client.get(f"/api/exports/{export_id}/zip").status_code == 404
    with db.connect(cfg) as conn:
        conn.execute("UPDATE exports SET archive=? WHERE id=?", (str(archive), export_id))
    archive.write_bytes(b"incomplete ZIP")
    assert client.get("/api/exports").json()["items"] == []
