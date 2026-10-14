"""Private configuration uses only temporary files and simulated read-only RPCs."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import stat
import threading

from dotenv import dotenv_values
from fastapi.testclient import TestClient
import pytest

from playlist25 import cloud_sync, db, private_config
from playlist25.app import create_app
from playlist25.config import Settings


def empty_snapshot():
    return {"schema_version": 1, "settings": {}, "guests": [], "sources": [], "proposals": [], "votes": []}


@pytest.fixture
def studio(tmp_path):
    home = tmp_path / "Private studio with spaces"
    home.mkdir()
    cfg = Settings(root=home, data_dir=home / "existing music", admin_password="temporary-test-password",
                   session_secret="temporary-test-session-secret", min_free_mb=0,
                   supabase_url="https://previous.supabase.co", supabase_service_role_key="sb_secret_previous_fixture")
    private = home / ".env.local"
    private.write_text("# Preserve the existing installation\n"
                       "ADMIN_PASSWORD='temporary-test-password'\nSESSION_SECRET='temporary-test-session-secret'\n"
                       "DATA_DIR='existing music'\nOUTPUT_FORMAT='source'\n"
                       "SUPABASE_URL='https://previous.supabase.co'\n"
                       "SUPABASE_SERVICE_ROLE_KEY='sb_secret_previous_fixture'\n", encoding="utf-8")
    db.init_db(cfg)
    with db.connect(cfg) as conn:
        source = db.upsert_source(conn, {"title": "Existing request must remain"})
        conn.execute("INSERT INTO proposals(source_id,created_at) VALUES(?,?)", (source, db.now()))
    audio = cfg.audio_dir / "existing.mp3"
    audio.write_bytes(b"existing local audio fixture")
    client = TestClient(create_app("admin", cfg))
    csrf = client.get("/api/session").json()["csrf_token"]
    login = client.post("/api/login", json={"password": cfg.admin_password}, headers={"X-CSRF-Token": csrf})
    assert login.status_code == 200
    return cfg, private, client, {"X-CSRF-Token": login.json()["csrf_token"]}


def test_cloud_configuration_is_private_csrf_protected_read_only_and_live(studio, monkeypatch):
    cfg, path, client, headers = studio
    calls = []
    def rpc(candidate, name, payload):
        calls.append((candidate.supabase_url, candidate.supabase_service_role_key, name, payload))
        return empty_snapshot()
    monkeypatch.setattr(cloud_sync, "_rpc", rpc)
    monkeypatch.setattr(cloud_sync, "push", lambda *args: pytest.fail("Configuration must never publish"))
    before = dotenv_values(path)
    body = {"project_url": " https://new-project.supabase.co/ ", "key": "sb_secret_new_fixture"}
    assert TestClient(create_app("admin", cfg)).post("/api/cloud/configure", json=body).status_code == 401
    assert client.post("/api/cloud/configure", json=body).status_code == 403
    public = TestClient(create_app("guest", cfg))
    public.cookies.update(client.cookies)
    assert public.post("/api/cloud/configure", json=body, headers=headers).status_code == 404
    result = client.post("/api/cloud/configure", json=body, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["configured"] is True and result.json()["secret_present"] is True
    assert "sb_secret" not in result.text
    assert calls == [("https://new-project.supabase.co", "sb_secret_new_fixture", "playlist25_snapshot", {})]
    after = dotenv_values(path)
    assert after["SUPABASE_URL"] == cfg.supabase_url == "https://new-project.supabase.co"
    assert after["SUPABASE_SERVICE_ROLE_KEY"] == cfg.supabase_service_role_key == "sb_secret_new_fixture"
    assert {key: value for key, value in before.items() if not key.startswith("SUPABASE_")} == {
        key: value for key, value in after.items() if not key.startswith("SUPABASE_")}
    if private_config.os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    status = client.get("/api/cloud/status").json()
    assert status["configured"] and status["url"] == cfg.supabase_url and status["secret_present"]
    assert "sb_secret" not in json.dumps(status)
    pulled = []
    monkeypatch.setattr(cloud_sync, "pull", lambda config, conn: pulled.append((config.supabase_url, config.supabase_service_role_key)) or {"proposals": 0})
    assert client.post("/api/cloud/sync", headers=headers).status_code == 200
    assert pulled == [("https://new-project.supabase.co", "sb_secret_new_fixture")]
    with db.connect(cfg) as conn:
        assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert (cfg.audio_dir / "existing.mp3").read_bytes() == b"existing local audio fixture"
    assert "# Preserve the existing installation" in path.read_text(encoding="utf-8")
    assert not list(path.parent.glob(".env-cloud-*"))


@pytest.mark.parametrize("key", [None, "", "   "])
def test_omitted_or_empty_key_preserves_and_reverifies_current_key(studio, monkeypatch, key):
    cfg, path, client, headers = studio
    calls = []
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", lambda candidate: calls.append(candidate.supabase_service_role_key) or empty_snapshot())
    body = {"project_url": cfg.supabase_url}
    if key is not None:
        body["key"] = key
    result = client.post("/api/cloud/configure", json=body, headers=headers)
    assert result.status_code == 200, result.text
    assert calls == ["sb_secret_previous_fixture"]
    assert dotenv_values(path)["SUPABASE_SERVICE_ROLE_KEY"] == cfg.supabase_service_role_key == calls[0]


@pytest.mark.parametrize("body", [
    {"project_url": "http://wrong.supabase.co", "key": "sb_secret_cannot_leak"},
    {"project_url": "https://correct.supabase.co/path", "key": "sb_secret_cannot_leak"},
    {"project_url": "https://correct.supabase.co", "key": "sb_publishable_cannot_leak"},
    {"project_url": "https://correct.supabase.co", "key": "sbp_cannot_leak"},
    {"project_url": "https://correct.supabase.co", "key": "sb_secret_cannot_leak\nheader"},
    {"project_url": "https://correct.supabase.co", "key": {"private": "cannot_leak"}},
    {"project_url": "https://correct.supabase.co", "key": "cannot_leak" * 1000},
    {"project_url": "https://correct.supabase.co", "key": "sb_secret_cannot_leak", "unexpected": "cannot_leak"},
])
def test_invalid_input_never_echoes_secret_or_changes_private_configuration(studio, monkeypatch, body):
    cfg, path, client, headers = studio
    before, connection = path.read_bytes(), (cfg.supabase_url, cfg.supabase_service_role_key)
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", lambda *args: pytest.fail("Invalid input must not contact Supabase"))
    result = client.post("/api/cloud/configure", json=body, headers=headers)
    assert result.status_code == 422
    assert "cannot_leak" not in result.text
    assert path.read_bytes() == before and (cfg.supabase_url, cfg.supabase_service_role_key) == connection


@pytest.mark.parametrize("key", ["", "sb_publishable_bad_previous", "sbp_bad_previous", "malformed_previous"])
def test_invalid_existing_key_is_rejected_when_key_field_is_empty(studio, monkeypatch, key):
    cfg, path, client, headers = studio
    cfg.supabase_service_role_key = key
    before = path.read_bytes()
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", lambda *args: pytest.fail("Invalid stored key must not contact Supabase"))
    result = client.post("/api/cloud/configure", json={"project_url": cfg.supabase_url, "key": ""}, headers=headers)
    assert result.status_code == 422
    assert path.read_bytes() == before and cfg.supabase_service_role_key == key


def test_remote_denial_and_malformed_snapshot_keep_existing_file_and_hide_error_body(studio, monkeypatch):
    cfg, path, client, headers = studio
    before, connection = path.read_bytes(), (cfg.supabase_url, cfg.supabase_service_role_key)
    def denied(candidate):
        raise cloud_sync.CloudSyncError("Supabase refuse la synchronisation. secret-response: " + candidate.supabase_service_role_key)
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", denied)
    body = {"project_url": "https://new.supabase.co", "key": "sb_secret_cannot_leak"}
    result = client.post("/api/cloud/configure", json=body, headers=headers)
    assert result.status_code == 503 and "refuse" in result.json()["detail"]
    assert "cannot_leak" not in result.text and "secret-response" not in result.text
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", lambda candidate: {"proposals": []})
    assert client.post("/api/cloud/configure", json=body, headers=headers).status_code == 503
    assert path.read_bytes() == before and (cfg.supabase_url, cfg.supabase_service_role_key) == connection


def test_atomic_replace_failure_keeps_live_and_saved_configuration(studio, monkeypatch):
    cfg, path, client, headers = studio
    before, connection = path.read_bytes(), (cfg.supabase_url, cfg.supabase_service_role_key)
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", lambda candidate: empty_snapshot())
    def denied_replace(*args):
        assert path.read_bytes() == before
        assert (cfg.supabase_url, cfg.supabase_service_role_key) == connection
        raise OSError("cannot_leak filesystem failure")
    monkeypatch.setattr(private_config.os, "replace", denied_replace)
    result = client.post("/api/cloud/configure", json={"project_url": "https://new.supabase.co", "key": "sb_secret_new_fixture"}, headers=headers)
    assert result.status_code == 503 and "cannot_leak" not in result.text
    assert path.read_bytes() == before and (cfg.supabase_url, cfg.supabase_service_role_key) == connection
    assert not list(path.parent.glob(".env-cloud-*"))


def test_config_flush_uses_writable_handle_for_windows(studio, monkeypatch):
    cfg, path, client, headers = studio
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", lambda candidate: empty_snapshot())
    flush = private_config.os.fsync
    called = []
    def writable_flush(descriptor):
        # Linux accepts fsync on a read-only descriptor; Windows needs writing
        # permission. An empty write verifies that contract without data edits.
        assert private_config.os.write(descriptor, b"") == 0
        called.append(descriptor)
        flush(descriptor)
    monkeypatch.setattr(private_config.os, "fsync", writable_flush)
    result = client.post("/api/cloud/configure", json={"project_url": cfg.supabase_url}, headers=headers)
    assert result.status_code == 200 and called


def test_first_private_file_retains_existing_process_identity_and_data_location(studio, monkeypatch):
    cfg, path, client, headers = studio
    path.unlink()
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", lambda candidate: empty_snapshot())
    result = client.post("/api/cloud/configure", json={"project_url": cfg.supabase_url}, headers=headers)
    assert result.status_code == 200
    saved = dotenv_values(path)
    assert saved["ADMIN_PASSWORD"] == cfg.admin_password
    assert saved["SESSION_SECRET"] == cfg.session_secret
    assert Path(saved["DATA_DIR"]) == cfg.data_dir
    assert (cfg.audio_dir / "existing.mp3").read_bytes() == b"existing local audio fixture"


def test_concurrent_configuration_and_readers_never_mix_connection_pairs(studio, monkeypatch):
    cfg, path, client, headers = studio
    entered, release = threading.Event(), threading.Event()
    calls = []
    def snapshot(candidate):
        pair = (candidate.supabase_url, candidate.supabase_service_role_key)
        calls.append(pair)
        if pair[0] == "https://one.supabase.co":
            entered.set()
            assert release.wait(5)
        return empty_snapshot()
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", snapshot)
    before = path.read_bytes()
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(private_config.configure_cloud, cfg, "https://one.supabase.co", "sb_secret_one_fixture")
        assert entered.wait(5)
        second = executor.submit(private_config.configure_cloud, cfg, "https://two.supabase.co", "sb_secret_two_fixture")
        reading = executor.submit(private_config.cloud_settings_snapshot, cfg)
        assert path.read_bytes() == before
        assert (cfg.supabase_url, cfg.supabase_service_role_key) == ("https://previous.supabase.co", "sb_secret_previous_fixture")
        release.set()
        assert first.result(timeout=5)["configured"] and second.result(timeout=5)["configured"]
        read = reading.result(timeout=5)
    assert calls == [("https://one.supabase.co", "sb_secret_one_fixture"), ("https://two.supabase.co", "sb_secret_two_fixture")]
    assert (read.supabase_url, read.supabase_service_role_key) in calls
    saved = dotenv_values(path)
    assert (saved["SUPABASE_URL"], saved["SUPABASE_SERVICE_ROLE_KEY"]) == (cfg.supabase_url, cfg.supabase_service_role_key) == calls[-1]


def test_running_pull_keeps_its_old_connection_after_configuration_changes(studio, monkeypatch):
    cfg, path, client, headers = studio
    started, release = threading.Event(), threading.Event()
    pairs = []
    def pull(connection, conn):
        pairs.append((connection.supabase_url, connection.supabase_service_role_key))
        started.set()
        assert release.wait(5)
        pairs.append((connection.supabase_url, connection.supabase_service_role_key))
        return {"proposals": 0}
    monkeypatch.setattr(cloud_sync, "pull", pull)
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", lambda candidate: empty_snapshot())
    with ThreadPoolExecutor(max_workers=1) as executor:
        pulling = executor.submit(client.post, "/api/cloud/sync", headers=headers)
        assert started.wait(5)
        try:
            changed = client.post("/api/cloud/configure", json={"project_url": "https://new.supabase.co", "key": "sb_secret_new_fixture"}, headers=headers)
            assert changed.status_code == 200
        finally:
            release.set()
        assert pulling.result(timeout=5).status_code == 200
    assert pairs == [("https://previous.supabase.co", "sb_secret_previous_fixture")] * 2


@pytest.mark.parametrize("role,expected", [("service_role", 200), ("anon", 422)])
def test_legacy_jwt_requires_service_role_and_read_only_verification(studio, monkeypatch, role, expected):
    cfg, path, client, headers = studio
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    key = "eyJfixture." + payload + ".fixture_signature"
    called = []
    monkeypatch.setattr(cloud_sync, "fetch_snapshot", lambda candidate: called.append(candidate.supabase_service_role_key) or empty_snapshot())
    result = client.post("/api/cloud/configure", json={"project_url": cfg.supabase_url, "key": key}, headers=headers)
    assert result.status_code == expected and key not in result.text
    assert called == ([key] if expected == 200 else [])
