"""Windows launch regressions without network, downloads, or native processes."""
from __future__ import annotations

import io
import json
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from playlist25 import providers, worker
from playlist25.config import Settings
from playlist25.db import connect, init_db, upsert_source
from playlist25.processes import background_process_options
from playlist25.queue import claim_job, enqueue_sources


VIDEO = "BaW_jenozKc"
NO_WINDOW = 0x08000000
NEW_PROCESS_GROUP = 0x00000200


@pytest.fixture
def cfg(tmp_path):
    settings = Settings(root=tmp_path, data_dir=tmp_path / "data", min_free_mb=0,
                        max_upload_mb=1, admin_password="test-only", session_secret="test-only")
    init_db(settings)
    return settings


def windows_options(**options):
    # Changing the module's imported helper leaves pathlib and the actual OS
    # untouched, so these tests also run on Linux and macOS.
    return background_process_options(windows=True, **options)


def test_metadata_hidden_window_keeps_captured_json_and_noninteractive_input(cfg, monkeypatch):
    launched = {}

    def run(command, **options):
        launched.update(command=command, options=options)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "id": VIDEO, "title": "Réponse réelle du processus simulé", "duration": 12,
        }))

    monkeypatch.setattr(providers, "background_process_options", windows_options)
    monkeypatch.setattr(providers.subprocess, "run", run)
    result = providers.analyze(f"https://youtu.be/{VIDEO}", cfg)

    assert result["entries"][0]["title"] == "Réponse réelle du processus simulé"
    assert launched["command"][-2:] == ["--", f"https://www.youtube.com/watch?v={VIDEO}"]
    options = launched["options"]
    assert options["creationflags"] == NO_WINDOW
    assert options["stdin"] == subprocess.DEVNULL
    assert options["capture_output"] is True
    assert (options["text"], options["encoding"], options["errors"]) == (True, "utf-8", "replace")
    assert options["timeout"] == min(cfg.task_timeout, 120)
    assert options["check"] is False and not options.get("shell", False)


def test_metadata_hidden_window_preserves_timeout_error(cfg, monkeypatch):
    def timeout(command, **options):
        assert options["creationflags"] == NO_WINDOW
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(providers, "background_process_options", windows_options)
    monkeypatch.setattr(providers.subprocess, "run", timeout)
    with pytest.raises(providers.SourceError) as caught:
        providers.analyze(f"https://youtu.be/{VIDEO}", cfg)
    assert (caught.value.code, caught.value.status) == ("timeout", 503)


@pytest.mark.parametrize("cancelled", [False, True], ids=["completed", "cancelled"])
def test_hidden_download_keeps_output_capture_and_job_cleanup(cfg, monkeypatch, cancelled):
    launched, jobs = {}, []
    # Replace only worker's OS reference; global os.name must stay truthful for
    # filesystem operations performed by pathlib/SQLite during this test.
    monkeypatch.setattr(worker, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(worker, "background_process_options", windows_options)

    with connect(cfg) as conn:
        source_id = upsert_source(conn, {"video_id": VIDEO,
            "url": f"https://www.youtube.com/watch?v={VIDEO}", "title": "Test local", "duration": 1})
        enqueue_sources(conn, cfg, [source_id])
        conn.commit()
        job = claim_job(conn)
        staged = cfg.temp_dir / f"job-{job['id']}" / "track.wav"
        staged.parent.mkdir(parents=True)
        staged.write_bytes(b"local fixture, verification mocked")
        output = ('__PLAYLIST25_INFO__' + json.dumps({"id": VIDEO, "duration": 1}) + '\n'
                  '__PLAYLIST25_FILE__' + json.dumps(str(staged)) + '\n')
        process = SimpleNamespace(pid=12345, stdout=io.StringIO(output), running=cancelled)
        process.poll = lambda: None if process.running else 0
        process.wait = Mock(return_value=0)

        def popen(command, **options):
            launched.update(command=command, options=options)
            return process

        class Job:
            def __init__(self, owned_process):
                assert owned_process is process
                self.closed = 0
                jobs.append(self)

            def close(self):
                self.closed += 1
                process.running = False

        monkeypatch.setattr(worker.subprocess, "Popen", popen)
        monkeypatch.setattr(worker, "_WindowsProcessJob", Job)
        verify = Mock(return_value={"duration": 1})
        monkeypatch.setattr(worker, "verify_audio", verify)
        stop = threading.Event()
        if cancelled:
            stop.set()
            with pytest.raises(InterruptedError, match="arrêté"):
                worker._download(job, cfg, conn, stop)
            verify.assert_not_called()
        else:
            path, metadata = worker._download(job, cfg, conn, stop)
            assert path == staged.resolve() and metadata == {"duration": 1}
            verify.assert_called_once_with(staged.resolve(), cfg, 1)
            assert conn.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == "verifying"

    options = launched["options"]
    assert options["creationflags"] == NO_WINDOW | NEW_PROCESS_GROUP
    assert (options["stdin"], options["stdout"], options["stderr"]) == (
        subprocess.DEVNULL, subprocess.PIPE, subprocess.STDOUT)
    assert (options["text"], options["encoding"], options["errors"]) == (True, "utf-8", "replace")
    assert "start_new_session" not in options and not options.get("shell", False)
    assert len(jobs) == 1 and jobs[0].closed == 1
    assert process.stdout.closed
    process.wait.assert_called_with(timeout=5)


def test_windows_tree_termination_is_hidden_and_still_waits_for_exit(monkeypatch):
    monkeypatch.setattr(worker, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(worker, "background_process_options", windows_options)
    terminate = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(worker.subprocess, "run", terminate)
    process = SimpleNamespace(pid=321, poll=Mock(return_value=None), wait=Mock(return_value=0), kill=Mock())

    worker._stop_process(process)

    command, = terminate.call_args.args
    assert command == ["taskkill", "/PID", "321", "/T", "/F"]
    assert terminate.call_args.kwargs == {
        "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
        "timeout": 10, "check": False, "creationflags": NO_WINDOW,
    }
    process.wait.assert_called_once_with(timeout=5)
    process.kill.assert_not_called()
