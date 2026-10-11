"""Read-only startup inventory. Never include configuration values or raw logs."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile


def _error_code(error):
    # Exception messages and tracebacks may contain private URLs or keys.
    result = {"type": type(error).__name__}
    for key in ("errno", "winerror"):
        value = getattr(error, key, None)
        if isinstance(value, int):
            result[key] = value
    return result


def _tool_version(name):
    options = {"stdin": subprocess.DEVNULL, "capture_output": True, "text": True,
               "encoding": "utf-8", "errors": "replace", "timeout": 8}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        bundle = Path(getattr(sys, "_MEIPASS", ""))
        path = bundle / "tools" / (name + ".exe") if getattr(sys, "frozen", False) else shutil.which(name)
        if not path or not Path(path).is_file():
            return {"available": False}
        output = subprocess.run([str(path), "--version" if name == "node" else "-version"], **options)
        # Report only a known version token, never arbitrary subprocess output.
        import re
        match = re.search(r"\bv(\d+\.\d+\.\d+)\b" if name == "node" else r"\bversion (\d+(?:\.\d+){1,2})\b", output.stdout)
        return {"available": output.returncode == 0, "version": match.group(1) if match else None}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return {"available": False, "error": _error_code(error)}


def collect_diagnostic(home=None, *, error=None):
    """Inspect the selected home without initializing/configuring any service."""
    result = {"created_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
              "python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False)),
              "secrets_included": False, "network_requests": False, "services_started": False}
    if error is not None:
        result["startup_error"] = _error_code(error)
    private = {}
    try:
        home = Path(home or os.environ.get("PLAYLIST25_HOME") or Path.home() / ".local/share/StudioFanny").expanduser().resolve()
        configuration = home / ".env.local"
        result["configuration_present"] = configuration.is_file()
        if result["configuration_present"]:
            from dotenv import dotenv_values
            private = dotenv_values(configuration, interpolate=False)
            result["configuration_readable"] = True
    except Exception as exc:
        home = None
        result["configuration_readable"] = False
        result["configuration_error"] = _error_code(exc)
    result["admin_password_configured"] = bool(private.get("ADMIN_PASSWORD"))
    result["supabase_configured"] = bool(private.get("SUPABASE_URL") and private.get("SUPABASE_SERVICE_ROLE_KEY"))
    try:
        if home is None:
            raise ValueError("Home unavailable")
        directory = Path(os.environ.get("DATA_DIR") or private.get("DATA_DIR") or "data").expanduser()
        directory = (directory if directory.is_absolute() else home / directory).resolve()
        result["data_directory_present"] = directory.is_dir()
        result["free_mb"] = shutil.disk_usage(directory if directory.is_dir() else home).free // 1024 ** 2
        database = directory / "playlist.sqlite3"
        result["database_present"] = database.is_file()
        if database.is_file():
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
            try:
                result["database_readable"] = connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                result["counts"] = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                                    for name in ("proposals", "sources", "guests", "votes", "jobs") if name in tables}
            finally:
                connection.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        result["data_error"] = _error_code(exc)
    result["dependencies"] = {}
    for package in ("yt-dlp", "yt-dlp-ejs", "fastapi", "Pillow"):
        try:
            result["dependencies"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["dependencies"][package] = None
        except Exception as exc:
            result["dependencies"][package] = None
            result.setdefault("dependency_errors", {})[package] = _error_code(exc)
    result["tools"] = {name: _tool_version(name) for name in ("ffmpeg", "ffprobe", "node")}
    return result


def write_diagnostic(home=None, *, destination=None, error=None):
    report = collect_diagnostic(home, error=error)
    header = ("DIAGNOSTIC DU STUDIO DE FANNY\n\n"
              "Ce rapport ne contient ni mot de passe, ni cle privee, ni titres, ni logs bruts.\n"
              "Il ne modifie pas la collecte et ne lance pas de service ou de connexion reseau.\n\n")
    text = header + json.dumps(report, indent=2, ensure_ascii=False) + "\n\n"
    text += ("Si un outil manque, extrayez tout le ZIP officiel dans un nouveau dossier.\n"
                     "Gardez l'ancienne installation et vos donnees personnelles.\n"
                     "Si Windows bloque le programme avant son lancement, consultez Aide/Diagnostic-Studio.txt.\n")
    def write(path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
        path.chmod(0o600)
        return path.resolve()
    if destination is not None:
        return write(destination)
    name = "diagnostic-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".txt"
    try:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "StudioFanny" / "diagnostics"
        return write(base / name)
    except (OSError, ValueError, RuntimeError):
        return write(Path(tempfile.mkdtemp(prefix="StudioFanny-diagnostic-")) / name)


def open_diagnostic(path):
    if os.name == "nt":
        try:
            os.startfile(str(path))
        except OSError:
            notepad = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "notepad.exe"
            subprocess.Popen([str(notepad), str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(arguments=None, *, startup_error=None):
    import argparse
    parser = argparse.ArgumentParser(description="Diagnostic local sans demarrer le studio")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(arguments)
    path = write_diagnostic(destination=args.output, error=startup_error)
    if args.open:
        open_diagnostic(path)
    print(json.dumps({"report": str(path), "secrets_included": False}))
    return 0
