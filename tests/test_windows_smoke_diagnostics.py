"""CI failure diagnostics survive cleanup without starting Windows services."""
from importlib.util import module_from_spec, spec_from_file_location
from collections import deque
import json
from pathlib import Path
import sys
import threading
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


class FakeConsoleEvents:
    """Deterministic native events; no Windows desktop is needed for unit tests."""
    def __init__(self, windows=()):
        self.windows, self.pending = list(windows), deque()
        self.processed = threading.Event()
        self.closed = False

    def visible_windows(self):
        return list(self.windows)

    def watch(self, callback):
        self.callback = callback

    def pump(self):
        while self.pending:
            self.callback(self.pending.popleft())
            self.processed.set()

    def close(self):
        self.closed = True


def console_window(handle=100, owner_pid=900, class_name="ConsoleWindowClass"):
    return {"handle": handle, "owner_pid": owner_pid, "class": class_name}


def test_console_guard_ignores_existing_runner_console_and_normal_gui():
    native = FakeConsoleEvents([console_window()])
    report = smoke.initial_report()
    with smoke.ConsoleWindowMonitor(report=report, native_factory=lambda: native):
        native.pending.append(console_window())
        native.pending.append(console_window(101, 501, "TkTopLevel"))
        assert native.processed.wait(2)
    assert native.closed
    assert report["console_windows"]["new_visible_windows"] == []
    assert report["console_windows"]["observer_errors"] == []
    assert "no visible console popups" in report["checks"][-1]


def test_console_guard_detects_transient_conhost_outside_application_tree():
    # No process ancestry is consulted. SHOW is enough even after the window
    # has disappeared from the next EnumWindows snapshot.
    native = FakeConsoleEvents([console_window()])
    report = smoke.initial_report()
    transient = console_window(200, 999_999)
    with pytest.raises(RuntimeError, match="unexpected visible console"):
        with smoke.ConsoleWindowMonitor(report=report, native_factory=lambda: native):
            native.pending.append(transient)
            assert native.processed.wait(2)
    assert native.closed
    assert report["console_windows"]["new_visible_windows"] == [transient]
    assert report["checks"] == []


def test_console_guard_detects_new_terminal_and_reused_window_handle():
    native = FakeConsoleEvents([console_window()])
    with smoke.ConsoleWindowMonitor(require_quiet=False, native_factory=lambda: native) as observer:
        native.pending.extend((console_window(101, 333, "CASCADIA_HOSTING_WINDOW_CLASS"),
                               console_window(100, 444)))
        assert native.processed.wait(2)
    assert {item["owner_pid"] for item in observer.observed.values()} == {333, 444}


def test_console_guard_fails_closed_when_native_hook_cannot_start():
    native = FakeConsoleEvents()
    native.watch = Mock(side_effect=RuntimeError("SetWinEventHook unavailable"))
    report = smoke.initial_report()
    with pytest.raises(RuntimeError, match="observer did not start.*SetWinEventHook"):
        with smoke.ConsoleWindowMonitor(report=report, native_factory=lambda: native):
            pytest.fail("An unavailable observer must never count as a passing check")
    assert native.closed and not report["checks"]


def test_console_guard_preserves_events_even_when_fixture_itself_fails():
    native = FakeConsoleEvents()
    report = smoke.initial_report()
    with pytest.raises(ValueError, match="original fixture error"):
        with smoke.ConsoleWindowMonitor(report=report, native_factory=lambda: native):
            native.pending.append(console_window())
            assert native.processed.wait(2)
            raise ValueError("original fixture error")
    assert len(report["console_windows"]["new_visible_windows"]) == 1
    assert not report["checks"]


def test_native_show_callback_observes_root_console_without_visibility_recheck():
    # A SHOW event can be delivered after a very brief console became hidden;
    # requiring IsWindowVisible again would lose that popup.
    native = object.__new__(smoke.WindowsConsoleEvents)
    native.user32 = SimpleNamespace(SetWinEventHook=Mock(return_value=123), UnhookWinEvent=Mock(return_value=1))
    native.event_type = lambda callback: callback
    native.window = Mock(return_value=console_window())
    observed = []
    native.watch(observed.append)
    arguments = native.user32.SetWinEventHook.call_args.args
    assert arguments[:3] == (0x8002, 0x8002, None)
    assert arguments[-3:] == (0, 0, 0)  # All PIDs/threads; out-of-context delivery.
    native.callback(123, 0x8002, 100, -4, 0, 1, 1)  # Child/client object, not a window.
    assert not observed
    native.callback(123, 0x8002, 100, 0, 0, 1, 2)
    assert observed == [console_window()]
    native.close()
    native.user32.UnhookWinEvent.assert_called_once_with(123)


@pytest.mark.parametrize("visible_handle,detected,hidden_handle,error", [
    (123, True, 0, None),
    (123, False, 0, "calibration failed"),
    (0, True, 0, "calibration failed"),
    (123, True, 456, "still owns a console"),
])
def test_console_calibration_requires_real_detection_and_hidden_null_console(
        tmp_path, monkeypatch, visible_handle, detected, hidden_handle, error):
    real_identity = smoke.ConsoleWindowMonitor.identity
    modes = []
    class CalibrationMonitor:
        identity = staticmethod(real_identity)
        def __init__(self, *, require_quiet=True):
            modes.append(require_quiet)
            self.observed = {real_identity(console_window()): console_window()} if detected else {}
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
    monkeypatch.setattr(smoke, "ConsoleWindowMonitor", CalibrationMonitor)
    monkeypatch.setattr(smoke, "WindowsConsoleEvents", lambda: SimpleNamespace(visible_windows=lambda: []))
    monkeypatch.setattr(smoke.subprocess, "CREATE_NEW_CONSOLE", 0x10, raising=False)
    monkeypatch.setattr(smoke, "hidden_process_options", lambda: {"creationflags": 0x08000000})
    run = Mock(side_effect=[SimpleNamespace(stdout=json.dumps({"console": visible_handle})),
                            SimpleNamespace(stdout=json.dumps({"console": hidden_handle}))])
    monkeypatch.setattr(smoke.subprocess, "run", run)
    report = smoke.initial_report()
    if error:
        with pytest.raises(RuntimeError, match=error):
            smoke.calibrate_console_observer({}, tmp_path, report)
        assert not report["checks"]
    else:
        smoke.calibrate_console_observer({}, tmp_path, report)
        assert report["console_calibration"] == {"visible_cui_detected": True, "hidden_cui_get_console_window": 0}
        assert modes == [False, True]
        assert [call.kwargs["creationflags"] for call in run.call_args_list] == [0x10, 0x08000000]


def test_harness_cui_cleanup_uses_create_no_window(monkeypatch):
    monkeypatch.setattr(smoke, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(smoke.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    run = Mock()
    monkeypatch.setattr(smoke.subprocess, "run", run)
    process = SimpleNamespace(pid=500, poll=lambda: None, wait=Mock())
    smoke.stop_process(process)
    assert run.call_args.args[0] == ["taskkill", "/PID", "500", "/T", "/F"]
    assert run.call_args.kwargs["creationflags"] == 0x08000000
    process.wait.assert_called_once_with(timeout=15)
