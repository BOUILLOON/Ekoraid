"""Local crash/concurrency regressions; no network or external music downloads."""
import hashlib
import io
import json
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import wave

import psutil
import pytest

from playlist25.config import Settings
from playlist25.db import connect, init_db, upsert_source
from playlist25.library import LibraryError
from playlist25.providers import SourceError, analyze, search
from playlist25.queue import claim_job, enqueue_sources, queue_snapshot, recover_jobs
from playlist25.worker import _perform_job, _read_output, run


VIDEO = "BaW_jenozKc"


@pytest.fixture
def cfg(tmp_path):
    settings = Settings(root=tmp_path, data_dir=tmp_path / "data", min_free_mb=0, max_upload_mb=1,
                        admin_password="test-only", session_secret="test-only")
    init_db(settings)
    return settings


def source(conn, video_id=VIDEO):
    return upsert_source(conn, {"video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "title": "Test local", "duration": 1})


def wav(path, frame=b"\x00\x00"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(frame * 8000)
    return path


def checkpoint(cfg, job, filename, expected):
    marker = cfg.temp_dir / f"job-{job['id']}" / "publication.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"job_id": job["id"], "source_id": job["source_id"], "filename": filename,
                                 "size": len(expected), "sha256": hashlib.sha256(expected).hexdigest()}), encoding="utf-8")


def test_existing_file_releases_queued_capacity_for_next_source(cfg):
    cfg.max_queue_size = 2
    with connect(cfg) as conn:
        first, second, third = [source(conn, f"{i:011d}") for i in range(3)]
        enqueue_sources(conn, cfg, [first, second]); conn.commit()
        (cfg.audio_dir / "verified.wav").write_bytes(b"mocked verified file")
        conn.execute("UPDATE sources SET file_path='verified.wav' WHERE id=?", (first,))
        with patch("playlist25.queue.verify_audio", return_value={"duration": 1}):
            result = enqueue_sources(conn, cfg, [first, third])
        assert result["queued"] == [third]
        assert conn.execute("SELECT status FROM jobs WHERE source_id=?", (first,)).fetchone()[0] == "available"
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0] == 2


@pytest.mark.parametrize("invalid", [{}, "not-a-list", None])
def test_malformed_search_metadata_is_an_honest_provider_error(cfg, invalid):
    with patch("playlist25.providers._metadata", return_value=({"entries": invalid}, "")):
        with pytest.raises(SourceError) as caught:
            search("Artiste", cfg)
    assert caught.value.status == 502


def test_extreme_numbers_and_invalid_accessibility_do_not_crash_metadata(cfg):
    info = {"entries": [{"id": VIDEO, "duration": 10**400}, {"id": "aaaaaaaaaaa", "availability": []}], "playlist_count": 10**400}
    with patch("playlist25.providers._metadata", return_value=(info, "")):
        result = analyze("https://youtube.com/playlist?list=PL1234567890abc", cfg)
    assert result["entries"][0]["duration"] is None
    assert not result["entries"][1]["available"]
    assert not result["complete"] and result["warnings"]


def test_output_reader_uses_bounded_reads_and_discards_oversized_remainder():
    class Stream(io.StringIO):
        sizes = []
        def readline(self, size=-1):
            self.sizes.append(size)
            return super().readline(size)
    stream = Stream("x" * 1000000 + "\nactual next line\n")
    lines, stopped, finished = queue.Queue(), threading.Event(), threading.Event()
    _read_output(stream, lines, stopped, finished)
    assert finished.is_set()
    assert all(0 < size <= 16001 for size in stream.sizes)
    assert len(lines.get_nowait()) == 16000
    assert lines.get_nowait() == "actual next line\n"
    assert lines.empty()


def test_output_reader_cancellation_unblocks_full_queue():
    lines, stopped, finished = queue.Queue(maxsize=1), threading.Event(), threading.Event()
    reader = threading.Thread(target=_read_output, args=(io.StringIO("one\ntwo\nthree\n"), lines, stopped, finished))
    reader.start()
    deadline = time.monotonic() + 2
    while lines.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    stopped.set(); reader.join(timeout=2)
    assert not reader.is_alive() and finished.is_set()
    assert lines.qsize() == 1


def test_parent_exit_and_descendant_holding_stdout_cannot_hang_worker(cfg, tmp_path):
    cfg.task_timeout = 2; cfg.max_attempts = 1
    child_pid = tmp_path / "child.pid"
    child = tmp_path / "pipe_child.py"
    child.write_text("import signal,time,os\nfrom pathlib import Path\n"
                     f"Path({str(child_pid)!r}).write_text(str(os.getpid()))\n"
                     "signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n", encoding="utf-8")
    parent = tmp_path / "pipe_parent.py"
    parent.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
                      f"subprocess.Popen([sys.executable,{str(child)!r}])\n"
                      f"while not Path({str(child_pid)!r}).exists(): time.sleep(.01)\n"
                      "sys.exit(1)\n", encoding="utf-8")
    with connect(cfg) as conn:
        enqueue_sources(conn, cfg, [source(conn)])
    failures = []
    def worker():
        try:
            with patch("playlist25.worker.yt_dlp_command", return_value=[sys.executable, str(parent)]):
                run(cfg, once=True)
        except BaseException as error:
            failures.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        thread.join(timeout=10)
        assert not thread.is_alive(), "A dead parent left its child holding stdout and blocked shutdown"
        assert not failures
        assert child_pid.exists()
        pid = int(child_pid.read_text())
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        with connect(cfg) as conn:
            assert queue_snapshot(conn)[0]["status"] == "failed"
    finally:
        if child_pid.exists():
            try:
                psutil.Process(int(child_pid.read_text())).kill()
            except psutil.NoSuchProcess:
                pass
        thread.join(timeout=2)


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="real local audio verification")
def test_late_filename_collision_preserves_existing_file_and_publishes_unique_copy(cfg):
    from playlist25.library import _install_file
    with connect(cfg) as conn:
        source_id = source(conn); enqueue_sources(conn, cfg, [source_id]); conn.commit()
        job = claim_job(conn)
        staged = wav(cfg.temp_dir / f"job-{job['id']}" / "track.wav")
        collision, original = [], []
        def install(staged, destination):
            if not collision:
                wav(destination, frame=b"\x01\x00")
                collision.append(destination); original.append(destination.read_bytes())
                raise FileExistsError("Late collision")
            return _install_file(staged, destination)
        with patch("playlist25.worker._download", return_value=(staged, {"duration": 1})), patch("playlist25.worker._install_file", side_effect=install):
            _perform_job(conn, cfg, job, threading.Event())
        item = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        assert item["status"] == "available"
        assert (cfg.audio_dir / item["file_path"]) != collision[0]
        assert collision[0].read_bytes() == original[0]


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="real local audio verification")
def test_install_failure_restores_previous_pointer_instead_of_adopting_collision(cfg):
    with connect(cfg) as conn:
        source_id = source(conn); enqueue_sources(conn, cfg, [source_id]); conn.commit()
        job = claim_job(conn)
        staged = wav(cfg.temp_dir / f"job-{job['id']}" / "track.wav")
        with patch("playlist25.worker._download", return_value=(staged, {"duration": 1})), patch("playlist25.worker._install_file", side_effect=PermissionError("Cannot publish")):
            _perform_job(conn, cfg, job, threading.Event())
        item = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        assert item["status"] == "failed" and item["file_path"] is None
        assert item["duration"] == 1


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="real local audio verification")
@pytest.mark.parametrize("matching", [True, False])
def test_recovery_uses_publication_hash_to_identify_exact_job_file(cfg, matching):
    with connect(cfg) as conn:
        source_id = source(conn); enqueue_sources(conn, cfg, [source_id]); conn.commit()
        job = claim_job(conn)
        intended = wav(cfg.temp_dir / f"job-{job['id']}" / "track.wav").read_bytes()
        published = wav(cfg.audio_dir / "published.wav", frame=b"\x00\x00" if matching else b"\x01\x00")
        conn.execute("UPDATE sources SET file_path=?,status='verifying' WHERE id=?", (published.name, source_id))
        conn.execute("UPDATE jobs SET status='verifying' WHERE id=?", (job["id"],)); conn.commit()
        checkpoint(cfg, job, published.name, intended)
        before = published.read_bytes()
        recover_jobs(conn, cfg)
        assert queue_snapshot(conn)[0]["status"] == ("available" if matching else "queued")
        assert published.read_bytes() == before
        if not matching:
            assert not enqueue_sources(conn, cfg, [source_id])["queued"]
            assert conn.execute("SELECT status FROM sources WHERE id=?", (source_id,)).fetchone()[0] == "queued"
