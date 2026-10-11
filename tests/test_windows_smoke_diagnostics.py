"""CI failure diagnostics survive cleanup without starting Windows services."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "packaging" / "windows_smoke.py"
SPEC = spec_from_file_location("windows_smoke_diagnostics", SCRIPT)
smoke = module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_readiness_failure_includes_bounded_fixture_log(tmp_path):
    log = tmp_path / "supervisor.log"
    log.write_bytes(b"old output" * 2000 + b"\nModuleNotFoundError: required_module\n")
    process = SimpleNamespace(poll=lambda: 1, returncode=1)
    with pytest.raises(RuntimeError, match="required_module") as caught:
        smoke.wait_ready(process, "http://127.0.0.1:1", "guest", None, log_path=log)
    assert "old output" in str(caught.value)
    assert len(str(caught.value).encode("utf-8")) < 8300


def test_timeout_also_preserves_fixture_log(tmp_path, monkeypatch):
    log = tmp_path / "guest.log"
    log.write_text("server startup stalled", encoding="utf-8")
    ticks = iter((0, 41))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(ticks))
    process = SimpleNamespace(poll=lambda: None)
    with pytest.raises(RuntimeError, match="server startup stalled"):
        smoke.wait_ready(process, "http://127.0.0.1:1", "guest", None, log_path=log)


def test_failed_report_retains_completed_checks_and_logs(tmp_path, monkeypatch):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--executable", "fixture.exe", "--report", str(report_path)])
    def failing_smoke(executable, *, report):
        report["checks"].extend(("frozen doctor", "real frozen Tk GUI opened and closed"))
        report["runtime_versions"] = {"node.exe": "v24.19.0"}
        log = tmp_path / "supervisor.log"
        log.write_text("ModuleNotFoundError: missing_import", encoding="utf-8")
        smoke.preserve_fixture_logs(tmp_path, report)
        log.unlink()
        raise RuntimeError("fixture service failed")
    monkeypatch.setattr(smoke, "smoke", failing_smoke)
    with pytest.raises(RuntimeError, match="fixture service failed"):
        smoke.main()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["checks"][-1] == "real frozen Tk GUI opened and closed"
    assert report["runtime_versions"] == {"node.exe": "v24.19.0"}
    assert "missing_import" in report["local_fixture_logs"]["supervisor.log"]
    assert report["external_audio_downloads"] is False
    assert report["supabase_mutations"] is False


def test_explorer_launch_bypasses_popen_and_passes_no_standard_handles(tmp_path, monkeypatch):
    """Popen substitutes runner handles; the native call must not do that."""
    api = SimpleNamespace(CreateProcess=Mock(return_value=(100, 101, 500, 501)), CloseHandle=Mock())
    monkeypatch.setitem(sys.modules, "_winapi", api)
    monkeypatch.setattr(smoke.subprocess, "STARTUPINFO", lambda: SimpleNamespace(dwFlags=0), raising=False)
    monkeypatch.setattr(smoke.subprocess, "STARTF_USESTDHANDLES", 0x100, raising=False)
    monkeypatch.setattr(smoke.subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(smoke.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(smoke.subprocess, "Popen", Mock(side_effect=AssertionError("Popen would replace the NULL handles")))
    executable = tmp_path / "directory with spaces" / "Studio-Fanny.exe"
    env = {"PLAYLIST25_HOME": str(tmp_path), "GUEST_PORT": "8123"}
    process = smoke.launch_without_standard_handles(executable, env, tmp_path)
    arguments = api.CreateProcess.call_args.args
    assert arguments[0] == str(executable)
    assert arguments[1] == smoke.subprocess.list2cmdline([str(executable)])
    assert arguments[4] is False  # bInheritHandles, irrespective of runner CRT descriptors.
    assert arguments[5] == 0x208
    assert arguments[6] == env and arguments[7] == str(tmp_path)
    startup = arguments[8]
    assert startup.dwFlags & 0x100
    assert (startup.hStdInput, startup.hStdOutput, startup.hStdError) == (0, 0, 0)
    assert process.pid == 500
    api.CloseHandle.assert_called_once_with(101)  # Parent closes only the thread handle while child runs.


def test_detached_process_wait_is_bounded_and_closes_handle_once():
    api = SimpleNamespace(WAIT_OBJECT_0=0, WAIT_TIMEOUT=258, INFINITE=0xFFFFFFFF,
                          WaitForSingleObject=Mock(side_effect=(258, 0)),
                          GetExitCodeProcess=Mock(return_value=0), CloseHandle=Mock())
    process = smoke.DetachedProcess(100, 500, ["fixture.exe"], api)
    with pytest.raises(smoke.subprocess.TimeoutExpired):
        process.wait(timeout=0.25)
    assert api.WaitForSingleObject.call_args.args == (100, 250)
    api.CloseHandle.assert_not_called()
    assert process.poll() == process.wait(timeout=1) == 0
    api.CloseHandle.assert_called_once_with(100)


def test_detached_process_does_not_hide_native_wait_failure():
    api = SimpleNamespace(WAIT_OBJECT_0=0, WAIT_TIMEOUT=258, WaitForSingleObject=Mock(return_value=0xFFFFFFFF))
    process = smoke.DetachedProcess(100, 500, ["fixture.exe"], api)
    with pytest.raises(RuntimeError, match="poll failed"):
        process.poll()


def test_explorer_first_run_requires_actual_setup_form_and_clean_close(tmp_path, monkeypatch):
    process = SimpleNamespace(pid=500, returncode=None)
    launch = Mock(return_value=process)
    windows, cleanup = [], []
    monkeypatch.setattr(smoke, "launch_without_standard_handles", launch)
    monkeypatch.setattr(smoke, "gui_window", lambda process, **options: windows.append(options.get("title", "Studio de Fanny")) or 10)
    monkeypatch.setattr(smoke, "close_gui", lambda process, window: setattr(process, "returncode", 0))
    monkeypatch.setattr(smoke, "stop_process", lambda process: cleanup.append(process))
    home, report = tmp_path / "fresh", smoke.initial_report()
    smoke.explorer_gui_smoke(tmp_path / "fixture.exe", {"GUEST_PORT": "8123", "ADMIN_PORT": "8124"}, home, report)
    assert windows == ["Studio de Fanny", "Configurer le studio de Fanny"]
    assert process.returncode == 0 and cleanup == [process]
    assert not (home / ".env.local").exists()
    environment = launch.call_args.args[1]
    assert environment["PLAYLIST25_HOME"] == str(home)
    assert environment["DATA_DIR"] == str(home / "data")
    assert "visible first-run form and clean close" in report["checks"][0]


def test_explorer_error_popup_cannot_pass_as_first_run_gui(tmp_path, monkeypatch):
    process, cleanup = SimpleNamespace(pid=500, returncode=None), []
    monkeypatch.setattr(smoke, "launch_without_standard_handles", lambda *args: process)
    def window(process, **options):
        if options.get("title") == "Configurer le studio de Fanny":
            raise RuntimeError("The real frozen desktop GUI did not open its setup window.")
        return 10  # An error message box could share the main-window caption.
    monkeypatch.setattr(smoke, "gui_window", window)
    monkeypatch.setattr(smoke, "close_gui", Mock(side_effect=AssertionError("An error popup must not count as a GUI")))
    monkeypatch.setattr(smoke, "stop_process", lambda process: cleanup.append(process))
    report = smoke.initial_report()
    with pytest.raises(RuntimeError, match="setup window"):
        smoke.explorer_gui_smoke(tmp_path / "fixture.exe", {}, tmp_path / "fresh", report)
    assert report["checks"] == [] and cleanup == [process]


def test_configured_detached_gui_checks_services_and_preserves_database(tmp_path, monkeypatch):
    process = SimpleNamespace(pid=500, returncode=None)
    home, report, health = tmp_path / "configured", smoke.initial_report(), []
    def launch(executable, environment, fixture_home):
        assert fixture_home == home
        assert environment["DATA_DIR"] == str(home / "data")
        data = home / "data"
        data.mkdir()
        (data / "playlist.sqlite3").write_bytes(b"preserved fixture")
        (data / "processes.json").write_text(json.dumps([
            {"service": "serve guest", "pid": 501}, {"service": "serve admin", "pid": 502}, {"service": "worker", "pid": 503}]), encoding="utf-8")
        return process
    def close(process, window):
        (home / "data" / "processes.json").unlink()
        process.returncode = 0
    monkeypatch.setattr(smoke, "launch_without_standard_handles", launch)
    monkeypatch.setattr(smoke, "gui_window", lambda *args, **options: 10)
    monkeypatch.setattr(smoke, "wait_ready", lambda process, url, surface, opener, **options: health.append((url, surface, options["log_path"])))
    monkeypatch.setattr(smoke, "close_gui", close)
    monkeypatch.setattr(smoke, "stop_process", lambda process: None)
    monkeypatch.setattr(smoke.psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: None)
    smoke.explorer_gui_smoke(tmp_path / "fixture.exe", {"GUEST_PORT": "8123", "ADMIN_PORT": "8124"}, home, report, configured=True)
    assert [item[:2] for item in health] == [("http://127.0.0.1:8123", "guest"), ("http://127.0.0.1:8124", "admin")]
    assert all(item[2] == home / "data" / "diagnostics" / "studio.log" for item in health)
    assert "SUPABASE" not in (home / ".env.local").read_text(encoding="utf-8")
    assert (home / "data" / "playlist.sqlite3").read_bytes() == b"preserved fixture"
    assert "HTTP readiness and clean service stop" in report["checks"][0]


def test_normal_gui_close_accepts_owned_stop_confirmation(monkeypatch):
    calls, polls = [], iter((None, None, 0, 0))
    process = SimpleNamespace(pid=500, returncode=0, poll=lambda: next(polls))
    post = Mock(side_effect=lambda *args: calls.append(args) or 1)
    monkeypatch.setattr(smoke.ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(PostMessageW=post)), raising=False)
    monkeypatch.setattr(smoke, "find_gui_window", lambda pid, title: 11 if (pid, title) == (500, "Arrêter le studio ?") else None)
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: None)
    smoke.close_gui(process, 10)
    assert calls[0] == (10, 0x0010, 0, 0)
    assert (11, 0x0111, 1, 0) in calls


@pytest.mark.parametrize("returncode", [1, None])
def test_gui_close_rejects_error_exit_or_hanging_application(monkeypatch, returncode):
    process = SimpleNamespace(pid=500, returncode=returncode, poll=lambda: returncode)
    post = Mock(return_value=1)
    monkeypatch.setattr(smoke.ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(PostMessageW=post)), raising=False)
    monkeypatch.setattr(smoke, "find_gui_window", lambda *args: None)
    ticks = iter((0, 36))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="closing normally|within the deadline"):
        smoke.close_gui(process, 10)


def test_detached_gui_diagnostic_log_is_retained_after_fixture_cleanup(tmp_path):
    log = tmp_path / "gui-explorer-configured" / "data" / "diagnostics" / "studio.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"old line\n" * 2000 + b"RuntimeError: detached GUI fixture\n")
    report = smoke.initial_report()
    smoke.preserve_fixture_logs(tmp_path, report)
    log.unlink()
    tail = report["local_fixture_logs"]["gui-explorer-configured/data/diagnostics/studio.log"]
    assert "detached GUI fixture" in tail
    assert len(tail.encode("utf-8")) <= 8000
