"""Background work must preserve output and avoid Windows console allocation."""
import json
import os
import subprocess
import sys
from unittest.mock import Mock

import pytest

from playlist25 import admin, library, processes
from playlist25.config import Settings


@pytest.mark.parametrize("new_group", [False, True])
def test_background_flags_prevent_console_and_retain_requested_group(new_group):
    assert processes.background_process_options(windows=False, new_process_group=new_group) == {}
    expected = 0x08000000 | (0x200 if new_group else 0)
    assert processes.background_process_options(windows=True, new_process_group=new_group) == {"creationflags": expected}


def test_tool_diagnostics_keep_results_without_allocating_a_console(monkeypatch):
    monkeypatch.setattr(admin, "background_process_options", lambda: processes.background_process_options(windows=True))
    launch = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="fixture tool 1.0\n", stderr=""))
    monkeypatch.setattr(admin.subprocess, "run", launch)
    result = admin._version(["tool.exe", "--version"])
    assert result == {"available": True, "version": "fixture tool 1.0", "error": ""}
    options = launch.call_args.kwargs
    assert options["creationflags"] & 0x08000000
    assert options["stdin"] == subprocess.DEVNULL and options["capture_output"] and options["timeout"] == 8


def test_audio_probe_keeps_stream_validation_without_allocating_a_console(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "background_process_options", lambda: processes.background_process_options(windows=True))
    path = tmp_path / "generated.wav"
    path.write_bytes(b"Fixture is read by a simulated ffprobe only")
    cfg = Settings(data_dir=tmp_path / "data")
    launch = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps({
        "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le", "duration": "1"}],
        "format": {"duration": "1", "format_name": "wav"}}), stderr=""))
    monkeypatch.setattr(library.subprocess, "run", launch)
    result = library.verify_audio(path, cfg)
    assert result["duration"] == 1
    options = launch.call_args.kwargs
    assert options["creationflags"] & 0x08000000
    assert options["stdin"] == subprocess.DEVNULL and options["capture_output"]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows console allocation check")
@pytest.mark.parametrize("new_group", [False, True])
def test_real_background_child_has_no_console_and_keeps_captured_output(new_group):
    command = [sys.executable, "-c", "import ctypes,json,sys; print(json.dumps({'console': bool(ctypes.windll.kernel32.GetConsoleWindow()), 'stdin': sys.stdin.read()}))"]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=15, check=True, **processes.background_process_options(new_process_group=new_group))
    assert json.loads(result.stdout) == {"console": False, "stdin": ""}
