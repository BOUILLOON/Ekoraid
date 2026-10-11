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


def _windows_standard_descriptor(handle_id, mode):
    """Own a duplicate of a usable inherited Win32 handle, never the original."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    handle = kernel.GetStdHandle(handle_id)
    if handle in (None, 0, ctypes.c_void_p(-1).value):
        return None
    # Explorer normally supplies no console handles. A numeric CRT descriptor
    # can still exist, so validate the actual handle before making a stream.
    if (kernel.GetFileType(handle) & 0x7FFF) not in (1, 2, 3):  # disk, character, pipe
        return None
    process = kernel.GetCurrentProcess()
    duplicate = wintypes.HANDLE()
    if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):  # DUPLICATE_SAME_ACCESS
        return None
    try:
        flags = (os.O_RDONLY if mode == "r" else os.O_WRONLY) | getattr(os, "O_BINARY", 0)
        # open_osfhandle takes ownership, hence the explicit duplicate above.
        return msvcrt.open_osfhandle(duplicate.value, flags)
    except (OSError, ValueError):
        kernel.CloseHandle(duplicate.value)
        return None


def _standard_descriptor(number, handle_id, mode):
    if os.name == "nt":
        return _windows_standard_descriptor(handle_id, mode)
    try:
        return os.dup(number)
    except OSError:
        return None


def _restored_stream(number, handle_id, mode):
    descriptor = _standard_descriptor(number, handle_id, mode)
    if descriptor is not None:
        try:
            os.fstat(descriptor)
            return os.fdopen(descriptor, mode, encoding="utf-8", errors="replace", buffering=1)
        except (OSError, ValueError):
            # Only descriptors/handles owned by this helper are closed. A
            # rejected CRT stream must not prevent the desktop from opening.
            try:
                os.close(descriptor)
            except OSError:
                pass
    return open(os.devnull, mode, encoding="utf-8", errors="replace")


def restore_streams():
    # Windowed PyInstaller clears sys.std* even when pipes were supplied. Keep
    # valid redirected logs; Explorer launches get independent NUL streams.
    for name, number, handle_id, mode in (("stdout", 1, -11, "w"), ("stderr", 2, -12, "w"), ("stdin", 0, -10, "r")):
        if getattr(sys, name) is None:
            setattr(sys, name, _restored_stream(number, handle_id, mode))


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
    except Exception:
        # Windowed bootloaders can hide an uncaught service traceback in a
        # dialog. Keep failures visible in the inherited private service log.
        import traceback
        traceback.print_exc(file=sys.stderr)
        if len(sys.argv) == 1:
            try:
                from tkinter import messagebox
                messagebox.showerror("Studio de Fanny", "Le studio a rencontré un problème au démarrage. Consultez son diagnostic avant de le relancer. Vos propositions et fichiers restent conservés.")
            except Exception:
                pass
        raise SystemExit(1)
