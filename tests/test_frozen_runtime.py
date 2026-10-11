"""Executable dispatch and external-data isolation, without a Windows dependency."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest
from fastapi.testclient import TestClient

from playlist25 import __main__ as runtime, db, providers
from playlist25.admin import create_admin_app
from playlist25.config import Settings, initialize_local_config


def test_external_home_loads_private_config_and_relative_data_without_overwriting(tmp_path):
    home = tmp_path / "User data" / "Studio Fanny"
    private = initialize_local_config(home)
    original = private.read_bytes()
    assert initialize_local_config(home).read_bytes() == original
    with private.open("a", encoding="utf-8") as output:
        output.write("DATA_DIR=ma-musique\nMAX_PROPOSALS=0\nMAX_PLAYLIST_ITEMS=0\nMAX_QUEUE_SIZE=0\n")
    cfg_bytes = private.read_bytes()
    elsewhere = tmp_path / "Unrelated working directory"
    elsewhere.mkdir()
    environment = {key: value for key, value in os.environ.items()
                   if key not in {name.upper() for name in Settings.__dataclass_fields__}}
    environment.update(PLAYLIST25_HOME=str(home), PYTHONPATH=str(Path(__file__).resolve().parent.parent))
    code = """from playlist25.config import ROOT, load_settings
import json
cfg=load_settings()
print(json.dumps({'root':str(ROOT),'data':str(cfg.data_dir),'limits':[cfg.max_proposals,cfg.max_playlist_items,cfg.max_queue_size],'secret_present':bool(cfg.session_secret)}))
"""
    process = subprocess.run([sys.executable, "-c", code], cwd=elsewhere, env=environment,
                             text=True, capture_output=True, check=True)
    result = json.loads(process.stdout)
    assert result == {"root": str(home.resolve()), "data": str((home / "ma-musique").resolve()),
                      "limits": [0, 0, 0], "secret_present": True}
    assert private.read_bytes() == cfg_bytes
    assert not (elsewhere / ".env.local").exists() and not (elsewhere / "data").exists()


def test_admin_assets_are_bundled_even_when_configuration_home_is_elsewhere(tmp_path):
    cfg = Settings(root=tmp_path / "external-home", data_dir=tmp_path / "music", session_secret="test-runtime-signing-secret")
    db.init_db(cfg)
    with TestClient(create_admin_app(cfg)) as client:
        assert client.get("/").status_code == 200
        assert "Fanny" in client.get("/").text
        assert client.get("/static/admin.js").status_code == 200
    assert not (cfg.root / "playlist25" / "static").exists()


@pytest.mark.parametrize("frozen", [False, True])
def test_service_and_yt_dlp_dispatch_use_the_correct_entry(monkeypatch, frozen):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    executable = "/tmp/Studio-Fanny.exe" if frozen else sys.executable
    monkeypatch.setattr(sys, "executable", executable)
    prefix = [executable, "--service"] if frozen else [executable, "-m", "playlist25"]
    assert runtime.service_command("serve", "admin") == [*prefix, "serve", "admin"]
    assert runtime.service_command("worker", "--once") == [*prefix, "worker", "--once"]
    command = providers.yt_dlp_command(SimpleNamespace(yt_dlp_path=executable, js_runtime="node"))
    tool_prefix = [executable, "--yt-dlp"] if frozen else [executable, "-m", "yt_dlp"]
    assert command[:len(tool_prefix)] == tool_prefix
    assert "--ignore-config" in command and "--no-remote-components" in command


def _snapshot(executable, arguments, started=123.5):
    return SimpleNamespace(exe=lambda: executable, cmdline=lambda: arguments,
                           create_time=lambda: started)


@pytest.mark.parametrize("frozen", [False, True])
def test_process_identity_rejects_recycled_pid_wrong_program_and_other_home(tmp_path, monkeypatch, frozen):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    executable = str(tmp_path / "Studio-Fanny.exe") if frozen else sys.executable
    monkeypatch.setattr(sys, "executable", executable)
    cfg = Settings(root=tmp_path, data_dir=tmp_path / "music")
    record = {"pid": 100, "started": 123.5, "service": "serve admin", "home": str(cfg.root),
              "data_dir": str(cfg.data_dir), "executable": executable}
    arguments = runtime.service_command("serve", "admin")
    assert runtime._matches_service(_snapshot(executable, arguments), record, cfg)
    assert not runtime._matches_service(_snapshot(executable, arguments, started=124), record, cfg)
    assert not runtime._matches_service(_snapshot(str(tmp_path / "other.exe"), arguments), record, cfg)
    assert not runtime._matches_service(_snapshot(executable, [executable, "playlist25", "serve", "admin"]), record, cfg)
    assert not runtime._matches_service(_snapshot(executable, arguments + ["unrelated"]), record, cfg)
    assert not runtime._matches_service(_snapshot(executable, arguments), {**record, "home": str(tmp_path / "elsewhere")}, cfg)


def test_frozen_supervisor_uses_external_home_and_stop_conserves_state(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Studio-Fanny.exe"))
    cfg = Settings(root=tmp_path / "home", data_dir=tmp_path / "home" / "music", session_secret="runtime-state-test-secret")
    cfg.root.mkdir()
    db.init_db(cfg)
    private = initialize_local_config(cfg.root)
    config_bytes = private.read_bytes()
    audio = cfg.audio_dir / "original.wav"
    audio.write_bytes(b"retained original test audio")
    with db.connect(cfg) as conn:
        source = db.upsert_source(conn, {"title": "Conserver cette musique"})
        conn.execute("INSERT INTO proposals(source_id,created_at) VALUES(?,?)", (source, db.now()))
    children, launches, terminated, records = [], [], [], []

    def launch(command, **options):
        process = SimpleNamespace(pid=100 + len(children), returncode=None)
        process.poll = lambda: process.returncode
        children.append(process)
        launches.append((command, options))
        return process

    def request_stop(_):
        records.extend(json.loads((cfg.data_dir / "processes.json").read_text()))
        (cfg.data_dir / ".stop-requested").touch()

    def terminate(process):
        terminated.append(process.pid)
        process.returncode = 0

    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 123.5 + pid))
    monkeypatch.setattr(runtime.time, "sleep", request_stop)
    monkeypatch.setattr(runtime, "_terminate", terminate)
    runtime.start(cfg)
    assert [command for command, _ in launches] == [runtime.service_command("serve", "guest"), runtime.service_command("serve", "admin"), runtime.service_command("worker")]
    assert all(options["cwd"] == str(cfg.root) and options["env"]["PLAYLIST25_HOME"] == str(cfg.root)
               and options["env"]["DATA_DIR"] == str(cfg.data_dir) for _, options in launches)
    assert len(records) == 3 and all(record["executable"] == sys.executable for record in records)
    assert terminated == [process.pid for process in children]
    assert not (cfg.data_dir / "processes.json").exists()
    assert not (cfg.data_dir / ".stop-requested").exists()
    assert private.read_bytes() == config_bytes and audio.read_bytes() == b"retained original test audio"
    with db.connect(cfg) as conn:
        assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1


def test_stop_signals_only_matching_compiled_services_and_keeps_user_files(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    executable = str(tmp_path / "Studio-Fanny.exe")
    monkeypatch.setattr(sys, "executable", executable)
    cfg = Settings(root=tmp_path, data_dir=tmp_path / "data")
    cfg.ensure_directories()
    private = initialize_local_config(cfg.root)
    config_bytes = private.read_bytes()
    audio = cfg.audio_dir / "already-prepared.mp3"
    audio.write_bytes(b"keep this user's music")
    records = [{"pid": pid, "started": 123.5, "service": "worker"} for pid in range(100, 104)]
    (cfg.data_dir / "processes.json").write_text(json.dumps(records))
    correct = runtime.service_command("worker")
    snapshots = {100: _snapshot(executable, correct),
                 101: _snapshot(executable, [executable, "--yt-dlp"]),
                 102: _snapshot(executable, correct, 124.5),
                 103: _snapshot(str(tmp_path / "some-other-program.exe"), correct)}
    monkeypatch.setattr(psutil, "Process", snapshots.__getitem__)
    ticks = iter([0, 3])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    terminated = []
    monkeypatch.setattr(runtime, "_terminate", lambda process: terminated.append(process))
    runtime.stop(cfg)
    assert terminated == [snapshots[100]]
    assert private.read_bytes() == config_bytes and audio.read_bytes() == b"keep this user's music"
    assert not (cfg.data_dir / "processes.json").exists()


def test_windows_without_console_uses_bounded_subprocess_cleanup(monkeypatch):
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(runtime.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    child = Mock()
    parent = Mock()
    parent.children.return_value = [child]
    process = Mock(pid=100)
    process.send_signal.side_effect = OSError("no console attached")
    monkeypatch.setattr(psutil, "Process", lambda _: parent)
    runtime._terminate(process)
    child.terminate.assert_called_once()
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)


@pytest.mark.parametrize("field", ["max_proposals", "max_playlist_items", "max_queue_size"])
def test_quantity_limits_accept_unlimited_and_large_explicit_limits(field):
    assert getattr(Settings(), field) == 0
    assert getattr(Settings(**{field: 20001}), field) == 20001
    with pytest.raises(ValueError):
        Settings(**{field: -1})
