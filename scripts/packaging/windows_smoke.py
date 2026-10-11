"""Exercise the actual frozen Windows executable with locally generated audio.

No Supabase writes, external searches or YouTube/audio downloads occur here.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
import http.cookiejar
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
import uuid
import wave
import zipfile
import psutil


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def request_json(opener, url, *, data=None, csrf=None):
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers)
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


def gui_window(process, timeout=30):
    """Confirm Tcl/Tk assets work in the real windowed executable."""
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
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
        if owner.value != process.pid or not user32.IsWindowVisible(window):
            return True
        length = user32.GetWindowTextLengthW(window)
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window, text, length + 1)
        # The first-run configuration Toplevel also mentions Fanny. Closing
        # that child leaves the application alive, so require the main window.
        if text.value == "Studio de Fanny":
            found.append(window)
            return False
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Desktop GUI stopped before opening a window ({process.returncode}).")
        user32.EnumWindows(callback, None)
        if found:
            return found[0]
        time.sleep(0.2)
    raise RuntimeError("The real frozen desktop GUI did not open its Studio de Fanny main window.")


def stop_process(process):
    if process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15, check=False)
        process.wait(timeout=15)


def initial_report() -> dict:
    return {"status": "running", "checks": [], "external_audio_downloads": False, "supabase_mutations": False}


def preserve_fixture_logs(home: Path, report: dict) -> None:
    logs = {path.name: tail for path in home.glob("*.log") if (tail := log_tail(path))}
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
        def run(*arguments, timeout=30):
            result = subprocess.run([str(executable), *arguments], env=environment, cwd=home, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
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
            version = subprocess.run([str(tools / name), argument], env=environment, cwd=home, capture_output=True, check=True, timeout=20).stdout.decode("utf-8", errors="replace")
            if expected not in version:
                raise RuntimeError(f"Bundled {name} reports an unexpected version.")
            versions[name] = version.splitlines()[0]
        report["runtime_versions"] = versions
        report["checks"].append("bundled runtime executables start")
        # A fresh GUI home exercises the first-run form without auto-starting
        # services or displaying a stop-confirmation dialog during WM_CLOSE.
        gui_home = home / "gui-first-run"
        gui_home.mkdir()
        gui_environment = {**environment, "PLAYLIST25_HOME": str(gui_home), "DATA_DIR": str(gui_home / "data")}
        gui_log = stack.enter_context((home / "gui.log").open("wb"))
        gui = subprocess.Popen([str(executable)], env=gui_environment, cwd=gui_home, stdout=gui_log, stderr=subprocess.STDOUT)
        stack.callback(stop_process, gui)
        window = gui_window(gui)
        ctypes.windll.user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        ctypes.windll.user32.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE: normal GUI close.
        gui.wait(timeout=15)
        if gui.returncode:
            raise RuntimeError("Desktop GUI failed while closing normally.")
        report["checks"].append("real frozen Tk GUI opened and closed")
        opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        bases = {"guest": f"http://127.0.0.1:{guest_port}", "admin": f"http://127.0.0.1:{admin_port}"}
        supervisor_log_path = home / "supervisor.log"
        supervisor_log = stack.enter_context(supervisor_log_path.open("wb"))
        supervisor = subprocess.Popen([str(executable), "--service", "start"], env=environment, cwd=home, stdout=supervisor_log, stderr=subprocess.STDOUT)
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
        for surface in ("guest", "admin"):
            log_path = home / f"{surface}.log"
            log = stack.enter_context(log_path.open("wb"))
            process = subprocess.Popen([str(executable), "--service", "serve", surface], env=environment, cwd=home, stdout=log, stderr=subprocess.STDOUT)
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
        wav, mp3 = home / "authorized-test.wav", home / "authorized-test.mp3"
        with wave.open(str(wav), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 16000)
        subprocess.run([str(tools / "ffmpeg.exe"), "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-q:a", "2", str(mp3)], env=environment, capture_output=True, check=True, timeout=30)
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
        for path in ("/api/library", "/api/transfers/addresses", "/api/dashboard"):
            try:
                opener.open(bases["guest"] + path, timeout=15)
            except HTTPError as error:
                if error.code != 404:
                    raise
            else:
                raise RuntimeError("The guest surface exposed an administrative endpoint.")
        report["checks"].append("guest surface cannot expose private administration/audio")
    report["status"] = "passed"
    return report


def main():
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
