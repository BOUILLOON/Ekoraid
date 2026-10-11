"""The public local search returns real pages without creating proposals/jobs."""
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from playlist25.app import create_app
from playlist25.config import Settings
from playlist25.db import connect, init_db, set_setting


@pytest.fixture
def cfg(tmp_path):
    settings = Settings(root=tmp_path, data_dir=tmp_path / "data", admin_password="test-only", session_secret="test-only")
    init_db(settings)
    return settings


def test_local_search_first_more_and_final_pages_preserve_real_source_identity(cfg):
    entries = [{"id": f"{i:011d}", "title": f"Version {i}", "channel": "Chaîne", "duration": 200} for i in range(12)]
    def metadata(target, settings, *, playlist, limit):
        return {"entries": entries[:limit]}, ""
    client = TestClient(create_app("guest", cfg))
    with patch("playlist25.providers._metadata", side_effect=metadata):
        first = client.get("/api/search?q=Artiste").json()
        second = client.get(f"/api/search?q=Artiste&offset={first['next_offset']}&limit=5").json()
        last = client.get(f"/api/search?q=Artiste&offset={second['next_offset']}&limit=5").json()
    assert len(first["items"]) == len(second["items"]) == 5
    assert len(last["items"]) == 2 and not last["has_more"]
    assert last["next_offset"] is None
    assert len({item["video_id"] for item in first["items"] + second["items"] + last["items"]}) == 12
    with connect(cfg) as conn:
        assert conn.execute("select count(*) from proposals").fetchone()[0] == 0
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 0


@pytest.mark.parametrize("parameters", ["offset=-1", "offset=100", "offset=1.5", "limit=0", "limit=11"])
def test_public_search_rejects_invalid_windows_before_network(cfg, parameters):
    client = TestClient(create_app("guest", cfg))
    with patch("playlist25.providers._metadata") as fetch:
        assert client.get("/api/search?q=Artiste&" + parameters).status_code == 422
    fetch.assert_not_called()


def test_search_pagination_still_respects_offline_mode(cfg):
    with connect(cfg) as conn:
        set_setting(conn, "offline_mode", True)
    client = TestClient(create_app("guest", cfg))
    with patch("playlist25.providers._metadata") as fetch:
        assert client.get("/api/search?q=Artiste&offset=5").status_code == 503
    fetch.assert_not_called()
