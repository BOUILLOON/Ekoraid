"""Frozen entry point and source fallback. Contains no private configuration."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def prepare_runtime():
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(bundle))
        # Child services still use the selected writable home as their cwd.
        os.environ["PYTHONPATH"] = str(bundle) + os.pathsep + os.environ.get("PYTHONPATH", "")
    if not os.environ.get("PLAYLIST25_HOME"):
        if getattr(sys, "frozen", False):
            # Do not import config until the writable home has been selected.
            import json
            base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "StudioFanny"
            home = base
            try:
                chosen = Path(json.loads((base / "installation.json").read_text(encoding="utf-8"))["home"])
                if chosen.is_absolute() and (chosen / ".env.local").is_file():
                    home = chosen
            except (OSError, ValueError, TypeError, KeyError):
                pass
        else:
            home = bundle
        os.environ["PLAYLIST25_HOME"] = str(home.resolve())
    tools = bundle / "tools"
    if tools.is_dir():
        os.environ["PATH"] = str(tools) + os.pathsep + os.environ.get("PATH", "")


def restore_streams():
    # A PyInstaller windowed build sets these to None even with redirected pipes.
    # Reattach inherited Windows handles for service logs and the build smoke test.
    for name, number, handle_id, mode in (("stdout", 1, -11, "w"), ("stderr", 2, -12, "w"), ("stdin", 0, -10, "r")):
        if getattr(sys, name) is not None:
            continue
        try:
            descriptor = os.dup(number)
        except OSError:
            descriptor = None
            if os.name == "nt":
                import ctypes
                import msvcrt
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetStdHandle.restype = ctypes.c_void_p
                handle = kernel.GetStdHandle(handle_id)
                if handle not in (None, 0, ctypes.c_void_p(-1).value):
                    try:
                        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY if mode == "r" else os.O_WRONLY)
                    except OSError:
                        pass
        if descriptor is None:
            stream = open(os.devnull, mode, encoding="utf-8")
        else:
            stream = os.fdopen(descriptor, mode, encoding="utf-8", errors="replace", buffering=1)
        setattr(sys, name, stream)


def main():
    prepare_runtime()
    restore_streams()
    if len(sys.argv) > 1 and sys.argv[1] == "--service":
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        from playlist25.__main__ import main as service_main
        return service_main()
    if len(sys.argv) > 1 and sys.argv[1] == "--yt-dlp":
        from yt_dlp import main as ytdlp_main
        return ytdlp_main(sys.argv[2:])
    from playlist25.desktop import main as desktop_main
    return desktop_main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        if len(sys.argv) == 1:
            try:
                from tkinter import messagebox
                messagebox.showerror("Studio de Fanny", f"Le studio n'a pas pu démarrer.\n{error}\nVos propositions et fichiers restent conservés.")
            except Exception:
                pass
        print(f"Studio : {error}", file=sys.stderr)
        raise SystemExit(1)
