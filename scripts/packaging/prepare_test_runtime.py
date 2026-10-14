"""Use the same verified Windows runtime for tests and desktop packaging."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("studio_build", root / "scripts" / "build_windows.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    if os.name != "nt" or sys.maxsize <= 2 ** 32:
        raise RuntimeError("Windows x64 is required for this test runtime.")
    work = root / "build" / "windows"
    tools, licenses = work / "vendor" / "tools", work / "vendor" / "licenses"
    for runtime in build.RUNTIMES.values():
        archive = build.download_verified(runtime, work / "cache")
        build.vendor_runtime(archive, runtime, tools, licenses)
    if os.environ.get("GITHUB_PATH"):
        with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as output:
            output.write(str(tools.resolve()) + "\n")
    print("Verified Windows Node/FFmpeg/ffprobe ready for the test suite and build.")


if __name__ == "__main__":
    main()
