"""A remote admin lookup must not hold SQLite's writer lock for guests."""
from concurrent.futures import ThreadPoolExecutor
import threading

from fastapi.testclient import TestClient

from playlist25 import db, providers
from playlist25.app import create_app
from playlist25.config import Settings


def test_guest_can_commit_while_admin_provider_lookup_is_waiting(tmp_path, monkeypatch):
    cfg = Settings(root=tmp_path, data_dir=tmp_path / "data", min_free_mb=0,
                   admin_password="temporary-admin-password", session_secret="temporary-signing-secret-at-least-32-characters")
    db.init_db(cfg)
    guest = TestClient(create_app("guest", cfg))
    session = guest.get("/api/session").json()
    guest_headers = {"X-CSRF-Token": session["csrf_token"]}
    assert guest.post("/api/identity", json={"nickname": "Camille"}, headers=guest_headers).status_code == 200
    original = guest.post("/api/proposals", json={"kind": "manual", "title": "Demande à résoudre"}, headers=guest_headers)
    assert original.status_code == 200
    proposal_id = original.json()["item"]["id"]
    original_source = original.json()["item"]["source_id"]
    admin = TestClient(create_app("admin", cfg))
    anonymous = admin.get("/api/session").json()
    logged_in = admin.post("/api/login", json={"password": cfg.admin_password}, headers={"X-CSRF-Token": anonymous["csrf_token"]})
    assert logged_in.status_code == 200
    admin_headers = {"X-CSRF-Token": logged_in.json()["csrf_token"]}
    lookup_started, release_lookup = threading.Event(), threading.Event()
    def slow_provider(url, settings, mode=None):
        lookup_started.set()
        assert release_lookup.wait(15), "Test provider was not released"
        return {"kind": "video", "entries": [{"video_id": "BaW_jenozKc", "url": "https://www.youtube.com/watch?v=BaW_jenozKc",
                "title": "Version résolue", "artist": "", "channel": "Fixture locale", "duration": 123, "thumbnail": "", "available": True}]}
    monkeypatch.setattr(providers, "analyze", slow_provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        patch = pool.submit(admin.patch, f"/api/proposals/{proposal_id}", json={"url": "https://youtu.be/BaW_jenozKc"}, headers=admin_headers)
        try:
            assert lookup_started.wait(5), "Admin PATCH did not enter the provider"
            submission = pool.submit(guest.post, "/api/proposals", json={"kind": "manual", "title": "Idée ajoutée pendant la recherche"}, headers=guest_headers)
            response = submission.result(timeout=5)
            assert response.status_code == 200, response.text
            assert not release_lookup.is_set() and not patch.done()
            with db.connect(cfg) as conn:
                assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 2
                assert conn.execute("SELECT source_id FROM proposals WHERE id=?", (proposal_id,)).fetchone()[0] == original_source
        finally:
            release_lookup.set()
        completed = patch.result(timeout=10)
    assert completed.status_code == 200, completed.text
    assert completed.json()["item"]["id"] == proposal_id
    assert completed.json()["item"]["url"] == "https://www.youtube.com/watch?v=BaW_jenozKc"
    with db.connect(cfg) as conn:
        assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 2
        assert conn.execute("SELECT title FROM sources WHERE id=?", (original_source,)).fetchone()[0] == "Demande à résoudre"
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
