"""Launch background tools without creating a Windows console window."""
from __future__ import annotations

import os
import subprocess


def background_process_options(*, new_process_group: bool = False, windows: bool | None = None) -> dict:
    """Preserve caller-owned stdio and cancellation while preventing consoles.

    Redirecting stdout alone does not prevent Windows from allocating a console
    to a command-line executable launched from the windowed studio.
    """
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    if new_process_group:
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    return {"creationflags": flags}
