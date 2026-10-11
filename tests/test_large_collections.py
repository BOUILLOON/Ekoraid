"""Large generated catalogues exercise real SQLite without remote downloads."""
from __future__ import annotations

import sqlite3
import json
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from playlist25 import db, exports, providers, queue
from playlist25.config import Settings

COUNT = 20_001
PLAYLIST = "PL1234567890abc"


def test_unlimited_queue_reserves_every_distinct_source_above_twenty_thousand(tmp_path):
    cfg = Settings(data_dir=tmp_path, session_secret="test-only")
    cfg.max_queue_size = 0
    db.init_db(cfg)
    with db.connect(cfg) as conn:
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        conn.executemany("INSERT INTO sources(id,video_id,url,title,created_at) VALUES(?,?,?,?,?)",
                         ((i, f"{i:011d}", f"https://www.youtube.com/watch?v={i:011d}", f"Test {i}", db.now())
                          for i in range(1, COUNT + 1)))
        result = queue.enqueue_sources(conn, cfg, list(range(1, COUNT + 1)))
        assert result == {"queued": list(range(1, COUNT + 1)), "skipped": []}
        assert conn.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == COUNT
        assert conn.execute("SELECT count(*) FROM job_events WHERE status='queued'").fetchone()[0] == COUNT
        conn.commit()
        again = queue.enqueue_sources(conn, cfg, [1, COUNT, COUNT])
        assert again["queued"] == [] and len(again["skipped"]) == 2
        assert queue.claim_job(conn)["source_id"] == 1


def test_unlimited_playlist_retains_original_positions_above_twenty_thousand(tmp_path):
    cfg = Settings(data_dir=tmp_path, session_secret="test-only")
    cfg.max_playlist_items = 0
    info = {"title": "Generated test playlist", "playlist_count": COUNT,
            "entries": [{"id": f"{i:011d}", "playlist_index": i} for i in range(1, COUNT + 1)]}
    with patch.object(providers, "_metadata", return_value=(info, "")) as metadata:
        result = providers.analyze(f"https://youtube.com/playlist?list={PLAYLIST}", cfg)
    assert metadata.call_args.kwargs["limit"] is None
    assert len(result["entries"]) == COUNT and result["complete"]
    assert [result["entries"][0]["position"], result["entries"][-1]["position"]] == [1, COUNT]
    cfg.max_playlist_items = 30_000
    with patch.object(providers, "_metadata", return_value=(info, "")) as metadata:
        result = providers.analyze(f"https://youtube.com/playlist?list={PLAYLIST}", cfg)
    assert metadata.call_args.kwargs["limit"] == 30_001
    assert len(result["entries"]) == COUNT and result["complete"]


def test_unlimited_playlist_command_omits_end_limit_and_uses_configured_timeout(tmp_path):
    cfg = Settings(data_dir=tmp_path, session_secret="test-only")
    cfg.max_playlist_items = 0
    response = Mock(returncode=0, stdout='{"entries":[]}', stderr="")
    with patch.object(providers.subprocess, "run", return_value=response) as process:
        providers.analyze(f"https://youtube.com/playlist?list={PLAYLIST}", cfg)
    assert "--playlist-end" not in process.call_args.args[0]
    assert process.call_args.kwargs["timeout"] == cfg.task_timeout


def test_unlimited_link_file_accepts_all_lines_beyond_old_byte_and_item_limits(tmp_path):
    cfg = Settings(data_dir=tmp_path, session_secret="test-only")
    cfg.max_playlist_items = 0
    text = "\n".join(f"https://youtu.be/{i:011d}" for i in range(1, COUNT + 1))
    def single(url, settings, mode):
        video_id = providers.parse_youtube_url(url, mode)["video_id"]
        return {"entries": [providers._entry({"id": video_id}, 1, settings)]}
    with patch.object(providers, "analyze", side_effect=single):
        result = providers.analyze_links(text, cfg)
    assert result["complete"] and len(result["entries"]) == COUNT
    assert result["entries"][-1]["position"] == COUNT
    assert result["entries"][-1]["video_id"] == f"{COUNT:011d}"


def test_unlimited_pack_writes_twenty_thousand_files_to_disk_and_zip(tmp_path):
    cfg = Settings(data_dir=tmp_path, session_secret="test-only", min_free_mb=0)
    cfg.max_queue_size = 0
    db.init_db(cfg)
    # Real copying and ZIP writing, while audio probing is mocked to avoid
    # invoking ffprobe 40,002 times. Separate audio tests probe real samples.
    original = cfg.audio_dir / "generated.wav"
    original.write_bytes(b"generated test bytes")
    def verify(path, settings, duration=None):
        assert Path(path).read_bytes() == b"generated test bytes"
        return {"duration": 1}
    with db.connect(cfg) as conn:
        conn.executemany("INSERT INTO sources(id,title,file_path,created_at) VALUES(?,?,?,?)",
                         ((i, f"Generated {i}", original.name, db.now()) for i in range(1, COUNT + 1)))
        with patch.object(exports, "verify_audio", side_effect=verify):
            pack = exports.prepare_pack(conn, cfg, make_zip=True)
        assert pack["count"] == COUNT
        directory = Path(pack["directory"])
        manifest = json.loads((directory / "manifeste.json").read_text())
        assert manifest["count"] == COUNT
        assert len(list((directory / "audio").iterdir())) == COUNT
        with zipfile.ZipFile(pack["archive"]) as archive:
            audio_names = [name for name in archive.namelist() if "/audio/" in name]
            assert len(audio_names) == COUNT
            assert archive.read(audio_names[-1]) == b"generated test bytes"
