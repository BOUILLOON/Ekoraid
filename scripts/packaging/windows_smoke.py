"""Exercise the actual frozen Windows executable with locally generated audio.

No Supabase writes, external searches or YouTube/audio downloads occur here.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import ctypes
import http.cookiejar
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
import uuid
import wave
import zipfile
import psutil


CONSOLE_WINDOW_CLASSES = frozenset(("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"))


def hidden_process_options():
    """Hide harness-owned CUI tools, while observing the application's children."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class WindowsConsoleEvents:
    """Observe actual visible console windows, including external conhost owners.

    WinEvent catches brief SHOW events between enumeration passes. No window
    titles, command lines or pre-existing runner consoles enter the report.
    The hook and its message pump must live on the same thread.
    """
    def __init__(self):
        from ctypes import wintypes
        self.user32 = ctypes.windll.user32
        self.message = wintypes.MSG()
        self.enum_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        self.event_type = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                            ctypes.c_long, ctypes.c_long, ctypes.c_ulong, ctypes.c_ulong)
        self.user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        self.user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        self.user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        self.user32.EnumWindows.argtypes = [self.enum_type, ctypes.c_void_p]
        self.user32.SetWinEventHook.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
                                               self.event_type, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        self.user32.SetWinEventHook.restype = ctypes.c_void_p
        self.user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
        self.user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p,
                                            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self.hook = None

    def window(self, handle):
        if not handle:
            return None
        name = ctypes.create_unicode_buffer(256)
        if not self.user32.GetClassNameW(handle, name, len(name)) or name.value not in CONSOLE_WINDOW_CLASSES:
            return None
        owner = ctypes.c_ulong()
        self.user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        return {"handle": int(handle), "owner_pid": owner.value, "class": name.value}

    def visible_windows(self):
        found = []
        @self.enum_type
        def callback(handle, _):
            if self.user32.IsWindowVisible(handle) and (window := self.window(handle)):
                found.append(window)
            return True
        if not self.user32.EnumWindows(callback, None):
            raise RuntimeError("Native console window enumeration failed.")
        return found

    def watch(self, on_show):
        @self.event_type
        def callback(_hook, _event, handle, object_id, child_id, _thread, _time):
            if object_id == 0 and child_id == 0 and (window := self.window(handle)):
                on_show(window)
        self.callback = callback  # Keep the C callback alive until UnhookWinEvent.
        self.hook = self.user32.SetWinEventHook(0x8002, 0x8002, None, callback, 0, 0, 0)
        if not self.hook:
            raise RuntimeError("Native console SHOW-event hook could not be installed.")

    def pump(self):
        while self.user32.PeekMessageW(ctypes.byref(self.message), None, 0, 0, 1):
            self.user32.TranslateMessage(ctypes.byref(self.message))
            self.user32.DispatchMessageW(ctypes.byref(self.message))

    def close(self):
        if self.hook and not self.user32.UnhookWinEvent(self.hook):
            raise RuntimeError("Native console SHOW-event hook could not be removed.")
        self.hook = None


class ConsoleWindowMonitor:
    """Fail closed if a new visible console appears during the isolated fixture."""
    def __init__(self, *, report=None, require_quiet=True, native_factory=WindowsConsoleEvents):
        self.report, self.require_quiet, self.native_factory = report, require_quiet, native_factory
        self.ready, self.stopping = threading.Event(), threading.Event()
        self.baseline, self.observed, self.errors = set(), {}, []
        self.thread = threading.Thread(target=self._run, name="console-window-observer", daemon=True)

    @staticmethod
    def identity(window):
        return window["handle"], window["owner_pid"], window["class"]

    def observe(self, window):
        # Console hosts need not be descendants of Studio-Fanny.exe. Filtering
        # on app PID ancestry would miss exactly the popup this guard tests.
        key = self.identity(window)
        if window["class"] in CONSOLE_WINDOW_CLASSES and key not in self.baseline:
            self.observed[key] = dict(window)

    def _run(self):
        native = None
        try:
            native = self.native_factory()
            self.baseline = {self.identity(window) for window in native.visible_windows()}
            native.watch(self.observe)
            self.ready.set()
            while not self.stopping.is_set():
                native.pump()
                for window in native.visible_windows():
                    self.observe(window)
                self.stopping.wait(0.02)
            native.pump()
            for window in native.visible_windows():
                self.observe(window)
        except Exception as error:
            self.errors.append(str(error))
        finally:
            if native is not None:
                try:
                    native.close()
                except Exception as error:
                    self.errors.append(str(error))
            self.ready.set()

    def __enter__(self):
        self.thread.start()
        if not self.ready.wait(5) or self.errors:
            self.close()
            raise RuntimeError("Native console observer did not start: " + "; ".join(self.errors))
        return self

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.errors.append("Native console observer did not stop within five seconds.")

    def __exit__(self, exc_type, _error, _traceback):
        self.close()
        windows = list(self.observed.values())
        if self.report is not None:
            self.report["console_windows"] = {"observer": "EVENT_OBJECT_SHOW and EnumWindows", "poll_interval_ms": 20,
                                               "new_visible_windows": windows, "observer_errors": list(self.errors)}
        if exc_type is None:
            if self.errors:
                raise RuntimeError("Native console observer failed: " + "; ".join(self.errors))
            if self.require_quiet and windows:
                raise RuntimeError("Frozen local fixture opened unexpected visible console windows: " + json.dumps(windows))
            if self.require_quiet and self.report is not None:
                self.report["checks"].append("native WinEvent/EnumWindows guard: no visible console popups during all local fixture operations")


def calibrate_console_observer(environment, home, report):
    """Prove detection with a real CUI child, then prove hidden CUI has no console."""
    probe = ("import ctypes,json,time; ctypes.windll.kernel32.GetConsoleWindow.restype=ctypes.c_void_p; "
             "print(json.dumps({'console':ctypes.windll.kernel32.GetConsoleWindow() or 0}),flush=True); time.sleep(1)")
    command = [sys.executable, "-c", probe]
    with ConsoleWindowMonitor(require_quiet=False) as positive:
        visible = subprocess.run(command, env=environment, cwd=home, capture_output=True, text=True,
                                 check=True, timeout=10, creationflags=subprocess.CREATE_NEW_CONSOLE)
    if not json.loads(visible.stdout)["console"] or not positive.observed:
        raise RuntimeError("Console observer calibration failed to detect the intentionally visible CUI window.")
    # The calibration's own console must have closed before the real guard
    # takes its baseline. Do not whitelist its HWND during the Studio test.
    native = WindowsConsoleEvents()
    deadline = time.monotonic() + 5
    while any(ConsoleWindowMonitor.identity(window) in positive.observed for window in native.visible_windows()):
        if time.monotonic() >= deadline:
            raise RuntimeError("The intentional calibration console did not close.")
        time.sleep(0.02)
    with ConsoleWindowMonitor():
        hidden = subprocess.run(command, env=environment, cwd=home, capture_output=True, text=True,
                                check=True, timeout=10, **hidden_process_options())
        handle = json.loads(hidden.stdout)["console"]
        if handle != 0:
            raise RuntimeError("CREATE_NO_WINDOW CUI probe still owns a console window.")
    report["console_calibration"] = {"visible_cui_detected": True, "hidden_cui_get_console_window": handle}
    report["checks"].append("native console observer calibration: real visible CUI detected, CREATE_NO_WINDOW CUI has GetConsoleWindow()=0")


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def request_json(opener, url, *, data=None, csrf=None, method=None):
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers, method=method)
    with opener.open(request, timeout=30) as response:
        return json.load(response)


def log_tail(log_path: Path | None, limit=8000) -> str:
    """Read a bounded local fixture log before its temporary directory closes."""
    if log_path is None:
        return ""
    try:
        with log_path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            source.seek(max(0, source.tell() - limit))
            return source.read(limit).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def readiness_error(message: str, log_path: Path | None) -> RuntimeError:
    tail = log_tail(log_path)
    return RuntimeError(message + ("\nLast local fixture service log:\n" + tail if tail else "\nThe local fixture service log is empty or unavailable."))


def wait_ready(process, url, surface, opener, *, log_path=None):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise readiness_error(f"Frozen {surface} service stopped with code {process.returncode}.", log_path)
        try:
            result = request_json(opener, url + "/health")
            if result == {"status": "ok", "surface": surface}:
                return
        except (OSError, ValueError, HTTPError, URLError):
            pass
        time.sleep(0.2)
    raise readiness_error(f"Frozen {surface} service did not become ready.", log_path)


def multipart_audio(path: Path):
    boundary = "StudioFannySmoke" + uuid.uuid4().hex
    pieces = []
    for name, value in (("title", "Tonalité de test autorisée"), ("artist", "Test local")):
        pieces.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    pieces.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="authorized-test.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode())
    pieces.append(path.read_bytes())
    pieces.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(pieces), "multipart/form-data; boundary=" + boundary


def find_gui_window(process_id, title):
    """Find only a visible, exactly named window belonging to our fixture."""
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
    @callback_type
    def callback(window, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value != process_id or not user32.IsWindowVisible(window):
            return True
        length = user32.GetWindowTextLengthW(window)
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window, text, length + 1)
        # The first-run configuration Toplevel also mentions Fanny. Closing
        # that child leaves the application alive, so require the main window.
        if text.value == title:
            found.append(window)
            return False
        return True
    user32.EnumWindows(callback, None)
    return found[0] if found else None


def gui_window(process, timeout=30, *, title="Studio de Fanny"):
    """Confirm the real GUI, rather than a similarly named error message box."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Desktop GUI stopped before opening a window ({process.returncode}).")
        found = find_gui_window(process.pid, title)
        if found:
            return found
        time.sleep(0.2)
    raise RuntimeError(f"The real frozen desktop GUI did not open its {title!r} window.")


def close_gui(process, window, *, timeout=35):
    """Close via the application and confirm stopping only our local fixture."""
    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    if not user32.PostMessageW(window, 0x0010, 0, 0):
        raise RuntimeError("The frozen GUI did not accept its normal close request.")
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        confirmation = find_gui_window(process.pid, "Arrêter le studio ?")
        if confirmation:
            user32.PostMessageW(confirmation, 0x0111, 1, 0)
        time.sleep(0.1)
    if process.poll() is None:
        raise RuntimeError("The frozen GUI did not close its fixture services within the deadline.")
    if process.returncode:
        raise RuntimeError(f"The frozen GUI failed while closing normally ({process.returncode}).")


class DetachedProcess:
    """Minimal process wrapper for direct CreateProcess, without Popen stdio."""
    def __init__(self, handle, process_id, command, api):
        self._handle, self.pid, self.args, self._api = handle, process_id, command, api
        self.returncode = None

    def _completed(self):
        self.returncode = self._api.GetExitCodeProcess(self._handle)
        self._api.CloseHandle(self._handle)
        self._handle = None
        return self.returncode

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        result = self._api.WaitForSingleObject(self._handle, 0)
        if result == self._api.WAIT_OBJECT_0:
            return self._completed()
        if result != self._api.WAIT_TIMEOUT:
            raise RuntimeError("Detached Windows fixture poll failed.")
        return None

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        deadline = self._api.INFINITE if timeout is None else max(0, int(timeout * 1000))
        result = self._api.WaitForSingleObject(self._handle, deadline)
        if result == self._api.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != self._api.WAIT_OBJECT_0:
            raise RuntimeError("Detached Windows fixture wait failed.")
        return self._completed()


def launch_without_standard_handles(executable, environment, home):
    """CreateProcess directly, so Popen cannot substitute runner std handles."""
    import _winapi
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESTDHANDLES
    startup.hStdInput = startup.hStdOutput = startup.hStdError = 0
    command = [str(executable)]
    process_handle, thread_handle, process_id, _ = _winapi.CreateProcess(
        str(executable), subprocess.list2cmdline(command), None, None, False,
        subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        environment, str(home), startup)
    _winapi.CloseHandle(thread_handle)
    return DetachedProcess(process_handle, process_id, command, _winapi)


def explorer_gui_smoke(executable, environment, home, report, *, configured=False):
    """Exercise actual windowed startup with NULL Windows standard handles."""
    home.mkdir()
    if configured:
        (home / ".env.local").write_text("ADMIN_PASSWORD=detached-local-fixture-password\nSESSION_SECRET=detached-local-fixture-signing-secret\n", encoding="utf-8")
    env = {**environment, "PLAYLIST25_HOME": str(home), "DATA_DIR": str(home / "data")}
    process = launch_without_standard_handles(executable, env, home)
    records = []
    try:
        window = gui_window(process)
        if configured:
            opener = build_opener(ProxyHandler({}))
            log_path = home / "data" / "diagnostics" / "studio.log"
            for surface, port in (("guest", env["GUEST_PORT"]), ("admin", env["ADMIN_PORT"])):
                wait_ready(process, f"http://127.0.0.1:{port}", surface, opener, log_path=log_path)
            records = json.loads((home / "data" / "processes.json").read_text(encoding="utf-8"))
            if {record["service"] for record in records} != {"serve guest", "serve admin", "worker"}:
                raise RuntimeError("Detached GUI did not launch all three local fixture services.")
            time.sleep(1)
        else:
            gui_window(process, title="Configurer le studio de Fanny")
            if (home / ".env.local").exists():
                raise RuntimeError("Detached first-run GUI wrote configuration before form confirmation.")
        close_gui(process, window)
        if records and any(psutil.pid_exists(record["pid"]) for record in records):
            raise RuntimeError("Detached GUI close left a fixture service running.")
        if configured and ((home / "data" / "processes.json").exists() or not (home / "data" / "playlist.sqlite3").is_file()):
            raise RuntimeError("Detached GUI close did not preserve the database and clear its process register.")
        report["checks"].append("Explorer-style detached GUI: configured auto-start, HTTP readiness and clean service stop" if configured
                                else "Explorer-style detached GUI: NULL standard handles, visible first-run form and clean close")
    finally:
        stop_process(process)


def stop_process(process):
    if process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15, check=False,
                       **hidden_process_options())
        process.wait(timeout=15)


def initial_report() -> dict:
    return {"status": "running", "checks": [], "external_audio_downloads": False, "supabase_mutations": False}


def preserve_fixture_logs(home: Path, report: dict) -> None:
    paths = list(home.glob("*.log"))
    paths.extend(home.glob("gui-explorer-*/data/diagnostics/studio.log"))
    logs = {path.relative_to(home).as_posix(): tail for path in paths if (tail := log_tail(path))}
    if logs:
        report["local_fixture_logs"] = logs


def smoke(executable: Path, *, report=None) -> dict:
    if os.name != "nt":
        raise RuntimeError("The frozen Windows smoke test must run on Windows.")
    executable = executable.resolve()
    if not executable.is_file():
        raise RuntimeError("The frozen executable is missing.")
    tools = executable.parent / "_internal" / "tools"
    report = initial_report() if report is None else report
    with tempfile.TemporaryDirectory(prefix="studio-fanny-smoke-") as temporary, ExitStack() as stack:
        home = Path(temporary)
        # Runs after child processes/log handles close but before TempDir is
        # deleted; diagnostics contain only this generated local test fixture.
        stack.callback(preserve_fixture_logs, home, report)
        environment = os.environ.copy()
        for name in list(environment):
            if name.startswith("SUPABASE_"):
                environment.pop(name)
        guest_port, admin_port = free_port(), free_port()
        while guest_port == admin_port:
            admin_port = free_port()
        environment.update({"PLAYLIST25_HOME": str(home), "DATA_DIR": str(home / "data"), "ADMIN_PASSWORD": "local-smoke-test-password-2026", "SESSION_SECRET": "local-smoke-test-signing-secret-at-least-32-characters",
                            "GUEST_HOST": "127.0.0.1", "ADMIN_HOST": "127.0.0.1", "GUEST_PORT": str(guest_port), "ADMIN_PORT": str(admin_port), "COOKIE_SECURE": "false", "MIN_FREE_MB": "0"})
        calibrate_console_observer(environment, home, report)
        # Registered before child cleanup callbacks, so monitoring continues
        # through their shutdown. Calibration events are in a separate guard.
        stack.enter_context(ConsoleWindowMonitor(report=report))
        def run(*arguments, timeout=30):
            result = subprocess.run([str(executable), *arguments], env=environment, cwd=home, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                    **hidden_process_options())
            if result.returncode:
                raise RuntimeError(f"Frozen command {arguments[0]} failed ({result.returncode}): {result.stderr[-1000:]}")
            return result
        run("--service", "init")
        doctor_result = run("--service", "doctor")
        doctor = json.loads(doctor_result.stdout)
        if not all(doctor.get(key) for key in ("ffmpeg", "ffprobe", "javascript", "writeable", "yt-dlp", "yt-dlp-ejs")):
            raise RuntimeError("Frozen doctor reports missing runtime components.")
        report["checks"].append("frozen doctor: Python/dependency metadata/FFmpeg/ffprobe/Node/writeability")
        if not run("--yt-dlp", "--version").stdout.strip():
            raise RuntimeError("Frozen yt-dlp entry point produced no version.")
        report["checks"].append("frozen yt-dlp command")
        versions = {}
        for name, argument, expected in (("ffmpeg.exe", "-version", "ffmpeg version 8.1.2"), ("ffprobe.exe", "-version", "ffprobe version 8.1.2"), ("node.exe", "--version", "v24.19.0")):
            version = subprocess.run([str(tools / name), argument], env=environment, cwd=home, capture_output=True, check=True, timeout=20,
                                     **hidden_process_options()).stdout.decode("utf-8", errors="replace")
            if expected not in version:
                raise RuntimeError(f"Bundled {name} reports an unexpected version.")
            versions[name] = version.splitlines()[0]
        report["runtime_versions"] = versions
        report["checks"].append("bundled runtime executables start")
        diagnostic_path = home / "diagnostic-fixture.txt"
        run("--diagnostic", "--output", str(diagnostic_path))
        diagnostic_text = diagnostic_path.read_text(encoding="utf-8")
        if not diagnostic_text or environment["ADMIN_PASSWORD"] in diagnostic_text or environment["SESSION_SECRET"] in diagnostic_text:
            raise RuntimeError("Standalone frozen diagnostic is missing or contains private fixture values.")
        if '"services_started": false' not in diagnostic_text or '"network_requests": false' not in diagnostic_text:
            raise RuntimeError("Frozen diagnostic must run without starting a service or network request.")
        if not (executable.parent / "Diagnostic-Studio.cmd").is_file() or not (executable.parent / "Aide" / "Diagnostic-Studio.txt").is_file():
            raise RuntimeError("The independent double-click diagnostic launcher or fallback guide is missing.")
        report["checks"].append("standalone frozen diagnostic and double-click fallback, no secrets or services")
        explorer_gui_smoke(executable, environment, home / "gui-explorer-first-run", report)
        explorer_gui_smoke(executable, environment, home / "gui-explorer-configured", report, configured=True)
        # A fresh GUI home exercises the first-run form without auto-starting
        # services or displaying a stop-confirmation dialog during WM_CLOSE.
        gui_home = home / "gui-first-run"
        gui_home.mkdir()
        gui_environment = {**environment, "PLAYLIST25_HOME": str(gui_home), "DATA_DIR": str(gui_home / "data")}
        gui_log = stack.enter_context((home / "gui.log").open("wb"))
        gui = subprocess.Popen([str(executable)], env=gui_environment, cwd=gui_home, stdout=gui_log, stderr=subprocess.STDOUT,
                               **hidden_process_options())
        stack.callback(stop_process, gui)
        window = gui_window(gui)
        close_gui(gui, window)
        report["checks"].append("real frozen Tk GUI opened and closed")
        opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        bases = {"guest": f"http://127.0.0.1:{guest_port}", "admin": f"http://127.0.0.1:{admin_port}"}
        supervisor_log_path = home / "supervisor.log"
        supervisor_log = stack.enter_context(supervisor_log_path.open("wb"))
        supervisor = subprocess.Popen([str(executable), "--service", "start"], env=environment, cwd=home, stdout=supervisor_log, stderr=subprocess.STDOUT,
                                      **hidden_process_options())
        stack.callback(stop_process, supervisor)
        for surface in ("guest", "admin"):
            wait_ready(supervisor, bases[surface], surface, opener, log_path=supervisor_log_path)
        records = json.loads((home / "data" / "processes.json").read_text(encoding="utf-8"))
        if {record["service"] for record in records} != {"serve guest", "serve admin", "worker"} or not all(psutil.pid_exists(record["pid"]) for record in records):
            raise RuntimeError("Frozen start did not supervise the guest, administrator and worker.")
        run("--service", "stop", timeout=40)
        supervisor.wait(timeout=25)
        if supervisor.returncode or any(psutil.pid_exists(record["pid"]) for record in records):
            raise RuntimeError("Frozen stop left a supervisor or child service running.")
        if (home / "data" / "processes.json").exists() or not (home / "data" / "playlist.sqlite3").is_file():
            raise RuntimeError("Frozen stop did not clear its process register while preserving the database.")
        report["checks"].append("frozen supervisor start/worker/readiness/stop with no orphan processes")
        independent_services = []
        # Only the disposable guest fixture listens on the runner's private
        # interface, so frozen QR creation uses real NTFS file identities.
        independent_environment = {**environment, "GUEST_HOST": "0.0.0.0"}
        for surface in ("guest", "admin"):
            log_path = home / f"{surface}.log"
            log = stack.enter_context(log_path.open("wb"))
            process = subprocess.Popen([str(executable), "--service", "serve", surface], env=independent_environment, cwd=home, stdout=log, stderr=subprocess.STDOUT,
                                       **hidden_process_options())
            independent_services.append(process)
            stack.callback(stop_process, process)
            wait_ready(process, bases[surface], surface, opener, log_path=log_path)
        report["checks"].append("independent frozen guest/admin HTTP health")
        with opener.open(bases["guest"] + "/", timeout=20) as response:
            if b"styles.css" not in response.read():
                raise RuntimeError("Frozen local interface assets are unavailable.")
        for surface in ("guest", "admin"):
            with opener.open(bases[surface] + "/static/styles.css", timeout=20) as response:
                if not response.read():
                    raise RuntimeError("Frozen CSS is empty.")
        session = request_json(opener, bases["admin"] + "/api/session")
        auth = request_json(opener, bases["admin"] + "/api/login", data={"password": environment["ADMIN_PASSWORD"]}, csrf=session["csrf_token"])
        csrf = auth["csrf_token"]
        report["checks"].append("local administration password and CSRF")
        def check_admin_diagnostics(client):
            diagnostics = request_json(client, bases["admin"] + "/api/diagnostics")
            for component, tool in (("ffmpeg", "ffmpeg.exe"), ("ffprobe", "ffprobe.exe"), ("javascript", "node.exe")):
                if not diagnostics[component]["available"] or diagnostics[component]["version"] != versions[tool]:
                    raise RuntimeError(f"Frozen administration diagnostic failed to run bundled {tool}.")
        check_admin_diagnostics(opener)
        wav, mp3 = home / "authorized-test.wav", home / "authorized-test.mp3"
        with wave.open(str(wav), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 16000)
        subprocess.run([str(tools / "ffmpeg.exe"), "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-q:a", "2", str(mp3)], env=environment, capture_output=True, check=True, timeout=30,
                       **hidden_process_options())
        payload, content_type = multipart_audio(mp3)
        request = Request(bases["admin"] + "/api/library/import", data=payload, headers={"Content-Type": content_type, "X-CSRF-Token": csrf})
        with opener.open(request, timeout=30) as response:
            source = json.load(response)["item"]
        if source["status"] != "available" or not source["file_path"]:
            raise RuntimeError("Generated authorized audio did not pass frozen verification/import.")
        report["checks"].append("generated WAV, real bundled FFmpeg MP3 conversion, ffprobe and metadata import")
        pack = request_json(opener, bases["admin"] + "/api/exports", data={"source_ids": [source["id"]], "make_zip": True}, csrf=csrf)
        with opener.open(bases["admin"] + pack["download_url"], timeout=30) as response:
            downloaded = response.read()
        with zipfile.ZipFile(io.BytesIO(downloaded)) as archive:
            names = archive.namelist()
            if not any(name.startswith("Anniversaire25/audio/") and name.endswith(".mp3") for name in names):
                raise RuntimeError("The frozen ZIP export has no authorized MP3.")
            if not any(name.endswith("01-selection.m3u8") for name in names):
                raise RuntimeError("The frozen ZIP export has no portable playlist.")
        report["checks"].append("frozen complete ZIP export and portable M3U8")
        # A second authenticated browser must discover and resume an existing
        # pack; no browser-memory link or second ZIP creation is required.
        reopened = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        reopened_session = request_json(reopened, bases["admin"] + "/api/session")
        request_json(reopened, bases["admin"] + "/api/login", data={"password": environment["ADMIN_PASSWORD"]}, csrf=reopened_session["csrf_token"])
        check_admin_diagnostics(reopened)
        report["checks"].append("real administration diagnostics twice: bundled FFmpeg, ffprobe and Node versions across browser sessions")
        history = request_json(reopened, bases["admin"] + "/api/exports")["items"]
        if len(history) != 1 or history[0]["download_url"] != pack["download_url"]:
            raise RuntimeError("Frozen ready ZIP history did not survive a new browser session.")
        pack_url = bases["admin"] + history[0]["download_url"]
        with reopened.open(Request(pack_url, method="HEAD"), timeout=30) as response:
            etag = response.headers["ETag"]
            if response.read() or int(response.headers["Content-Length"]) != len(downloaded):
                raise RuntimeError("Frozen ZIP HEAD returned invalid metadata.")
        pieces = []
        for value in ("bytes=0-31", "bytes=32-"):
            with reopened.open(Request(pack_url, headers={"Range": value, "If-Range": etag}), timeout=30) as response:
                if response.status != 206:
                    raise RuntimeError("Frozen ZIP did not support an authorized partial transfer.")
                pieces.append(response.read())
        if b"".join(pieces) != downloaded:
            raise RuntimeError("Frozen partial ZIP transfer changed its bytes.")
        report["checks"].append("frozen ready ZIP history after reauthentication, HEAD and two resumed ranges")
        addresses = request_json(opener, bases["admin"] + "/api/transfers/addresses")["addresses"]
        if not addresses:
            raise RuntimeError("The Windows CI runner needs a private IPv4 interface for the frozen transfer check.")
        transfer = request_json(opener, bases["admin"] + "/api/transfers", data={"export_id": history[0]["id"], "host": addresses[0]["host"]}, csrf=csrf)
        if not transfer["qr_data_url"].startswith("data:image/png;base64,"):
            raise RuntimeError("Frozen transfer creation did not produce a QR code.")
        shared = urlsplit(transfer["url"])
        transfer_url = bases["guest"] + shared.path + "/download"
        # HTTP stays on loopback, with the exact authorized LAN Host header.
        # This proves compiled authorization and bytes, not a physical phone.
        with opener.open(Request(transfer_url, headers={"Host": shared.netloc, "Range": "bytes=0-31"}), timeout=30) as response:
            if response.status != 206 or response.read() != downloaded[:32]:
                raise RuntimeError("Frozen temporary ZIP grant did not deliver the exact authorized range.")
        request_json(opener, bases["admin"] + "/api/transfers/revoke", data={"id": transfer["id"]}, csrf=csrf)
        try:
            opener.open(Request(transfer_url, headers={"Host": shared.netloc}), timeout=30)
        except HTTPError as error:
            if error.code != 404:
                raise
        else:
            raise RuntimeError("Frozen revoked temporary ZIP grant remained accessible.")
        report["checks"].append("frozen QR transfer with real Windows file IDs, exact range and revocation on loopback")
        # Only synthetic local proposals are used. The catalog starts empty,
        # and identical nicknames never confer ownership of another request.
        if request_json(opener, bases["guest"] + "/api/proposals")["categories"]:
            raise RuntimeError("Frozen studio created public categories automatically.")
        category = request_json(opener, bases["admin"] + "/api/categories", data={"name": "Moment de test local"}, csrf=csrf)["category"]
        guest_clients = []
        for _ in range(2):
            guest_client = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
            guest_csrf = request_json(guest_client, bases["guest"] + "/api/session")["csrf_token"]
            request_json(guest_client, bases["guest"] + "/api/identity", data={"nickname": "Invité de test"}, csrf=guest_csrf)
            guest_clients.append((guest_client, guest_csrf))
        owner, owner_csrf = guest_clients[0]
        stranger, stranger_csrf = guest_clients[1]
        proposal = request_json(owner, bases["guest"] + "/api/proposals", data={"kind": "manual", "title": "Demande locale de test"}, csrf=owner_csrf)["item"]
        category_url = bases["guest"] + f"/api/proposals/{proposal['id']}/categories"
        assigned = request_json(owner, category_url, data={"category_ids": [category["id"]]}, csrf=owner_csrf, method="PATCH")
        if assigned["item"]["category_ids"] != [category["id"]]:
            raise RuntimeError("Frozen owner category assignment failed.")
        try:
            request_json(stranger, category_url, data={"category_ids": []}, csrf=stranger_csrf, method="PATCH")
        except HTTPError as error:
            if error.code != 404:
                raise
        else:
            raise RuntimeError("Frozen category assignment allowed a different guest with the same nickname.")
        request_json(opener, bases["admin"] + f"/api/categories/{category['id']}", data={"active": False}, csrf=csrf, method="PATCH")
        if request_json(owner, bases["guest"] + "/api/proposals")["items"][0]["category_ids"] != [category["id"]]:
            raise RuntimeError("Frozen category archive erased an existing assignment.")
        report["checks"].append("frozen empty category catalog, organizer creation, owner-only assignment and preserved archive")
        for path in ("/api/library", "/api/transfers/addresses", "/api/dashboard", "/api/exports"):
            try:
                opener.open(bases["guest"] + path, timeout=15)
            except HTTPError as error:
                if error.code != 404:
                    raise
            else:
                raise RuntimeError("The guest surface exposed an administrative endpoint.")
        report["checks"].append("guest surface cannot expose private administration/audio")
        for process in independent_services:
            stop_process(process)
        private_path = home / ".env.local"
        database_path = home / "data" / "playlist.sqlite3"
        original_config = private_path.read_bytes()
        def fixture_rows(path):
            with closing(sqlite3.connect(path)) as connection:
                return {table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                        for table in ("guests", "sources", "proposals", "votes", "jobs", "settings", "cloud_map", "categories")}
        original_database = fixture_rows(database_path)
        original_audio_path = home / "data" / "audio" / source["file_path"]
        original_audio = original_audio_path.read_bytes()
        backup_directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="studio-fanny-private-backup-smoke-")))
        backup_path = backup_directory / "private-studio-backup.zip"
        backup_info = json.loads(run("--service", "backup", "--home", str(home), "--destination", str(backup_path)).stdout)
        inspected = json.loads(run("--service", "inspect-backup", "--archive", str(backup_path)).stdout)
        if not backup_info.get("confidential") or inspected["counts"]["audio_files"] < 1:
            raise RuntimeError("Frozen private backup omitted its confidential flag or prepared audio.")
        restored_home = backup_directory / "restored-studio"
        restored = json.loads(run("--service", "restore-backup", "--archive", str(backup_path), "--home", str(restored_home),
                                  "--expected-manifest-sha256", inspected["manifest_sha256"]).stdout)
        if Path(restored["home"]).resolve() != restored_home.resolve():
            raise RuntimeError("Frozen restoration did not use the new installation.")
        with closing(sqlite3.connect(restored_home / "data" / "playlist.sqlite3")) as connection:
            restored_file = connection.execute("SELECT file_path FROM sources WHERE id=?", (source["id"],)).fetchone()[0]
            if connection.execute("SELECT id,name,active FROM categories").fetchall() != [(category["id"], "Moment de test local", 0)]:
                raise RuntimeError("Frozen restoration did not preserve archived categories.")
            if json.loads(connection.execute("SELECT category_ids FROM proposals WHERE id=?", (proposal["id"],)).fetchone()[0]) != [category["id"]]:
                raise RuntimeError("Frozen restoration did not preserve proposal category assignments.")
        if not restored_file or (restored_home / "data" / "audio" / restored_file).read_bytes() != original_audio:
            raise RuntimeError("Frozen restoration did not preserve and relocate the verified MP3.")
        if private_path.read_bytes() != original_config or fixture_rows(database_path) != original_database:
            raise RuntimeError("Frozen backup/restoration modified the original installation.")
        if original_audio_path.read_bytes() != original_audio:
            raise RuntimeError("Frozen backup/restoration changed the original audio.")
        report["checks"].append("frozen full private backup/inspect/fresh restoration with MP3 and original data preserved")
    report["status"] = "passed"
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = initial_report()
    try:
        report = smoke(args.executable, report=report)
    except Exception as error:
        report.update(status="failed", error=str(error))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        raise
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
