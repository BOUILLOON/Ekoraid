"""Public playlist collection never expands an arbitrary remote list."""
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from playlist25.config import Settings
from playlist25.db import connect, init_db
from playlist25.guest import create_guest_app
from playlist25.guest_playlists import classify_source, prepare_youtube_proposal
from playlist25.providers import SourceError

VIDEO = "BaW_jenozKc"
PLAYLIST = "PL1234567890abc"


def test_playlist_proposal_is_canonical_distinct_and_does_not_access_youtube():
    with patch("playlist25.providers._metadata", side_effect=AssertionError("No public expansion")), patch("playlist25.providers.subprocess.run", side_effect=AssertionError("No external command")):
        entry = prepare_youtube_proposal(f"http://music.youtube.com/playlist?list={PLAYLIST}&feature=share")
    assert entry == {"video_id": None, "url": f"https://www.youtube.com/playlist?list={PLAYLIST}",
                     "title": "Playlist YouTube proposée", "artist": "", "status": "pending", "source_kind": "playlist"}
    assert classify_source(entry) == "playlist"


def test_playlist_accepts_user_title_without_inventing_metadata():
    entry = prepare_youtube_proposal(f"https://youtube.com/playlist?list={PLAYLIST}", "  Mes titres pour l’été  ", "")
    assert entry["title"] == "Mes titres pour l’été"
    assert entry["artist"] == "" and "channel" not in entry and "duration" not in entry


def test_video_and_playlist_link_requires_choice_in_guest_collection():
    url = f"https://youtube.com/watch?v={VIDEO}&list={PLAYLIST}"
    with pytest.raises(SourceError) as error:
        prepare_youtube_proposal(url)
    assert error.value.status == 409 and error.value.need_choice and error.value.code == "need_choice"
    track = prepare_youtube_proposal(url, mode="video")
    playlist = prepare_youtube_proposal(url, mode="playlist")
    assert track["video_id"] == VIDEO and track["source_kind"] == "video"
    assert track["url"] == f"https://www.youtube.com/watch?v={VIDEO}"
    assert playlist["video_id"] is None and playlist["source_kind"] == "playlist"
    assert playlist["url"] == f"https://www.youtube.com/playlist?list={PLAYLIST}"


def test_explicit_playlist_kind_does_not_keep_single_video_id():
    entry = prepare_youtube_proposal(f"https://youtu.be/{VIDEO}?list={PLAYLIST}", kind="playlist")
    assert entry["video_id"] is None and entry["source_kind"] == "playlist"
    with pytest.raises(SourceError):
        prepare_youtube_proposal(f"https://youtu.be/{VIDEO}", kind="playlist")
    with pytest.raises(SourceError):
        prepare_youtube_proposal(f"https://youtu.be/{VIDEO}?list={PLAYLIST}", kind="playlist", mode="video")


@pytest.mark.parametrize("url", [f"https://youtube.com/playlist?list=RD{VIDEO}", "https://youtube.com/playlist?list=UU1234567890abc", "http://127.0.0.1/playlist?list=PL1234567890abc", "https://youtube.com.evil.example/playlist?list=PL1234567890abc", "file:///playlist?list=PL1234567890abc", "https://youtube.com/channel/name", "https://youtube.com/playlist?list=PL1234567890abc&list=PL9999999999", "https://youtube.com/playlist?list=invalid"])
def test_playlist_suggestions_reject_unsafe_or_unbounded_sources(url):
    with pytest.raises(SourceError):
        prepare_youtube_proposal(url)


@pytest.mark.parametrize("title,artist", [("bad\x00title", ""), ("title", "bad\nartist"), ("a" * 241, ""), ("title", "a" * 161)])
def test_playlist_metadata_stays_bounded_without_control_characters(title, artist):
    with pytest.raises(SourceError):
        prepare_youtube_proposal(f"https://youtube.com/playlist?list={PLAYLIST}", title, artist)


@pytest.mark.parametrize("item,expected", [({"video_id": VIDEO, "url": f"https://youtube.com/watch?v={VIDEO}"}, "video"), ({"video_id": None, "url": f"https://youtube.com/playlist?list={PLAYLIST}"}, "playlist"), ({"video_id": None, "url": None, "title": "Un titre"}, "manual"), ({"video_id": None, "url": "https://internal.invalid"}, "manual"), ({}, "manual")])
def test_source_kind_is_derived_for_existing_rows_without_an_extra_column(item, expected):
    assert classify_source(item) == expected


@pytest.fixture
def cfg(tmp_path):
    result = Settings(data_dir=tmp_path / "data", session_secret="guest-playlists-test-secret", admin_password="guest-playlists-admin-secret", min_free_mb=0)
    init_db(result)
    return result


def identified_client(cfg, nickname="Alice"):
    client = TestClient(create_guest_app(cfg))
    csrf = client.get("/api/session").json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    identity = client.post("/api/identity", json={"nickname": nickname}, headers=headers)
    assert identity.status_code == 200, identity.text
    return client, headers


def test_guest_api_collects_playlist_without_expansion_or_jobs(cfg, monkeypatch):
    monkeypatch.setattr("playlist25.providers.analyze", lambda *args, **kwargs: pytest.fail("A public playlist must not be expanded"))
    client, headers = identified_client(cfg)
    response = client.post("/api/proposals", json={"kind": "youtube", "url": f"https://music.youtube.com/playlist?list={PLAYLIST}&feature=share"}, headers=headers)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["source_kind"] == "playlist" and item["video_id"] is None
    assert item["url"] == f"https://www.youtube.com/playlist?list={PLAYLIST}"
    assert item["title"] == "Playlist YouTube proposée" and item["duration"] is None
    assert "file_path" not in item and "guest_id" not in item
    with connect(cfg) as conn:
        assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM playlist_entries").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_guest_api_playlist_duplicate_uses_canonical_url_and_suggests_vote(cfg, monkeypatch):
    monkeypatch.setattr("playlist25.providers.analyze", lambda *args, **kwargs: pytest.fail("No public playlist expansion"))
    alice, alice_headers = identified_client(cfg)
    first = alice.post("/api/proposals", json={"kind": "youtube", "url": f"https://youtube.com/playlist?list={PLAYLIST}", "title": "Mes années disco"}, headers=alice_headers).json()["item"]
    bob, bob_headers = identified_client(cfg, "Bob")
    duplicate = bob.post("/api/proposals", json={"kind": "youtube", "url": f"http://m.youtube.com/playlist?list={PLAYLIST}&index=4", "title": "Autre nom"}, headers=bob_headers)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["duplicate"]["id"] == first["id"]
    assert duplicate.json()["duplicate"]["source_kind"] == "playlist"
    vote = bob.post(f"/api/proposals/{first['id']}/vote", json={"active": True}, headers=bob_headers)
    assert vote.status_code == 200 and vote.json()["votes"] == 1
    with connect(cfg) as conn:
        assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM proposals").fetchone()[0] == 1


def test_guest_api_ambiguous_url_requires_choice_and_playlist_choice_never_keeps_video_id(cfg, monkeypatch):
    monkeypatch.setattr("playlist25.providers.analyze", lambda *args, **kwargs: pytest.fail("Only the playlist branch is selected"))
    client, headers = identified_client(cfg)
    payload = {"kind": "youtube", "url": f"https://youtu.be/{VIDEO}?list={PLAYLIST}"}
    ambiguous = client.post("/api/proposals", json=payload, headers=headers)
    assert ambiguous.status_code == 409, ambiguous.text
    assert ambiguous.json()["detail"]["need_choice"] is True
    selected = client.post("/api/proposals", json={**payload, "mode": "playlist"}, headers=headers)
    assert selected.status_code == 200, selected.text
    assert selected.json()["item"]["video_id"] is None and selected.json()["item"]["source_kind"] == "playlist"


def test_guest_api_manual_default_title_is_distinct_from_playlist_source(cfg, monkeypatch):
    monkeypatch.setattr("playlist25.providers.analyze", lambda *args, **kwargs: pytest.fail("No public playlist expansion"))
    client, headers = identified_client(cfg)
    payload = {"kind": "youtube", "url": f"https://youtube.com/playlist?list={PLAYLIST}"}
    first = client.post("/api/proposals", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    manual = client.post("/api/proposals", json={"kind": "manual", "title": "Playlist YouTube proposée"}, headers=headers)
    assert manual.status_code == 200 and manual.json()["item"]["source_kind"] == "manual"
    assert manual.json()["item"]["source_id"] != first.json()["item"]["source_id"]
    other = client.post("/api/proposals", json={"kind": "youtube", "url": "https://youtube.com/playlist?list=PL9999999990abc"}, headers=headers)
    assert other.status_code == 200, other.text
    assert other.json()["item"]["source_id"] != first.json()["item"]["source_id"]


def test_guest_api_concurrent_same_playlist_stays_one_source_and_proposal(cfg, monkeypatch):
    monkeypatch.setattr("playlist25.providers.analyze", lambda *args, **kwargs: pytest.fail("No public playlist expansion"))
    client, headers = identified_client(cfg)
    def propose(_):
        return client.post("/api/proposals", json={"kind": "youtube", "url": f"https://youtube.com/playlist?list={PLAYLIST}"}, headers=headers)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(propose, range(4)))
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert sum("item" in response.json() for response in responses) == 1
    with connect(cfg) as conn:
        assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM proposals").fetchone()[0] == 1


@pytest.mark.parametrize("url", ["http://127.0.0.1/playlist?list=PL1234567890abc", "https://youtube.com/playlist?list=RD1234567890abc", "https://youtube.com/playlist?list=UU1234567890abc"])
def test_guest_api_rejects_unsafe_playlist_without_a_fetch(cfg, monkeypatch, url):
    monkeypatch.setattr("playlist25.providers.analyze", lambda *args, **kwargs: pytest.fail("Unsafe input cannot be fetched"))
    client, headers = identified_client(cfg)
    response = client.post("/api/proposals", json={"kind": "youtube", "url": url}, headers=headers)
    assert response.status_code == 400, response.text
    with connect(cfg) as conn:
        assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
