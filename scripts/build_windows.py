"""Build the self-contained Windows desktop folder from verified runtime archives.

Run on Windows x64 after installing requirements.txt and requirements-desktop-build.txt.
This script never reads private configuration or the user's music/data folders.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent.parent
RUNTIMES = {
    "node": {
        "version": "24.19.0", "filename": "node-v24.19.0-win-x64.zip",
        "url": "https://nodejs.org/dist/v24.19.0/node-v24.19.0-win-x64.zip",
        "sha256": "57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73",
        "checksum_source": "https://nodejs.org/dist/v24.19.0/SHASUMS256.txt",
        "members": {"node.exe": "node.exe"},
    },
    "ffmpeg": {
        "version": "8.1.2", "filename": "ffmpeg-8.1.2-essentials_build.zip",
        "url": "https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/ffmpeg-8.1.2-essentials_build.zip",
        "sha256": "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
        "checksum_source": "https://api.github.com/repos/GyanD/codexffmpeg/releases/tags/8.1.2 (asset digest)",
        "members": {"bin/ffmpeg.exe": "ffmpeg.exe", "bin/ffprobe.exe": "ffprobe.exe"},
    },
}


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(runtime: dict, cache: Path) -> Path:
    """TLS stays enabled; an unverified archive is never opened or executed."""
    if urlsplit(runtime["url"]).scheme != "https" or not re.fullmatch(r"[a-f0-9]{64}", runtime["sha256"]):
        raise ValueError("Runtime download requires an HTTPS URL and a pinned SHA256.")
    cache.mkdir(parents=True, exist_ok=True)
    filename = runtime["filename"]
    if Path(filename).name != filename or not filename.endswith(".zip"):
        raise ValueError("Unsafe runtime archive name.")
    destination = cache / filename
    if destination.exists():
        if digest_file(destination) != runtime["sha256"]:
            raise ValueError(f"Cached archive checksum mismatch: {filename}. Remove that build cache file before retrying.")
        return destination
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        request = Request(runtime["url"], headers={"User-Agent": "Studio-Fanny-build/1"})
        with urlopen(request, timeout=120) as response, partial.open("xb") as output:
            downloaded = 0
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > 512 * 1024 * 1024:
                    raise ValueError("Runtime archive exceeds the allowed build size.")
                output.write(chunk)
        if digest_file(partial) != runtime["sha256"]:
            raise ValueError(f"Downloaded archive checksum mismatch: {filename}. It will not be extracted or used.")
        os.replace(partial, destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)


def _safe_zip_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    path = PurePosixPath(name)
    mode = info.external_attr >> 16
    return bool(name and "\\" not in name and not path.is_absolute() and ":" not in name and ".." not in path.parts and not stat.S_ISLNK(mode))


def vendor_runtime(archive: Path, runtime: dict, tools: Path, licenses: Path) -> None:
    """Extract only explicitly selected binaries and the runtime's license."""
    if digest_file(archive) != runtime["sha256"]:
        raise ValueError("Runtime archive integrity must be verified before extraction.")
    tools.mkdir(parents=True, exist_ok=True)
    licenses.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if any(not _safe_zip_member(info) for info in members):
            raise ValueError("Runtime ZIP contains an unsafe path or symbolic link.")
        for suffix, filename in runtime["members"].items():
            matches = [info for info in members if not info.is_dir() and (info.filename == suffix or info.filename.endswith("/" + suffix))]
            if len(matches) != 1:
                raise ValueError(f"Expected one runtime binary {suffix}; found {len(matches)}.")
            if Path(filename).name != filename or not filename.endswith(".exe"):
                raise ValueError("Runtime output filename is invalid.")
            with source.open(matches[0]) as original, (tools / filename).open("wb") as destination:
                shutil.copyfileobj(original, destination)
        notices = [info for info in members if not info.is_dir() and PurePosixPath(info.filename).name.lower() in {"license", "license.txt", "copying", "copying.txt"}]
        if not notices:
            raise ValueError("The runtime archive has no license notice; do not distribute it.")
        notice = min(notices, key=lambda info: (len(PurePosixPath(info.filename).parts), info.filename))
        (licenses / f"{runtime['filename']}.LICENSE.txt").write_bytes(source.read(notice))


def validate_public_config(path: Path) -> None:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or set(config) - {"url", "publicKey"}:
        raise ValueError("Only the public Supabase URL and publishable key may be bundled.")
    url, key = config.get("url", ""), config.get("publicKey", "")
    parsed = urlsplit(url)
    if url and (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".supabase.co") or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.port):
        raise ValueError("The bundled public Supabase URL is invalid.")
    if key and not isinstance(key, str):
        raise ValueError("The public Supabase key must be text.")
    if key and not key.startswith("sb_publishable_"):
        # Legacy anon JWTs are public too, but a service-role JWT must never ship.
        import base64
        try:
            payload = key.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (ValueError, IndexError, TypeError) as exc:
            raise ValueError("Use a publishable Supabase key, never a private key.") from exc
        if claims.get("role") != "anon":
            raise ValueError("A private Supabase key cannot be included in the desktop build.")


def check_bundle_files(directory: Path) -> None:
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Bundle contains an unexpected symbolic link: {path.name}")
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith(".env") or name.endswith((".sqlite", ".sqlite3", ".db")) or name in {"processes.json", "playlist.sqlite3"}:
            raise ValueError(f"Private configuration/data must not be distributed: {path.name}")


def bundle_zip(directory: Path, destination: Path) -> str:
    check_bundle_files(directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as output:
            # WindowsPath ordering folds case; archive order must use the same
            # exact portable names on Windows and during packaging checks.
            for path in sorted(directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix()):
                if path.is_file():
                    output.write(path, (Path("Studio-Fanny") / path.relative_to(directory)).as_posix())
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    digest = digest_file(destination)
    destination.with_suffix(".zip.sha256").write_text(f"{digest}  {destination.name}\n", encoding="ascii")
    return digest


def build(work: Path, destination: Path, *, run_smoke: bool = True) -> Path:
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise RuntimeError("Build on a Windows x64 runner; cross-compilation from Linux is unsupported.")
    entry = ROOT / "scripts" / "studio_entry.py"
    if not entry.is_file():
        raise RuntimeError("The desktop entry point scripts/studio_entry.py is missing.")
    public_config = ROOT / "cloud" / "public-config.json"
    validate_public_config(public_config)
    work.mkdir(parents=True, exist_ok=True)
    tools, licenses = work / "vendor" / "tools", work / "vendor" / "licenses"
    for runtime in RUNTIMES.values():
        archive = download_verified(runtime, work / "cache")
        vendor_runtime(archive, runtime, tools, licenses)
    metadata = {"built_at": datetime.now(timezone.utc).isoformat(), "platform": "Windows x64", "python": sys.version.split()[0],
                "runtimes": {name: {key: value for key, value in runtime.items() if key != "members"} for name, runtime in RUNTIMES.items()},
                "private_configuration_included": False}
    source_notice = """Studio Fanny — composants intégrés

Node.js 24.19.0 : https://nodejs.org/dist/v24.19.0/ et https://github.com/nodejs/node/tree/v24.19.0
FFmpeg 8.1.2 essentials build : https://github.com/GyanD/codexffmpeg/releases/tag/8.1.2
Code source FFmpeg : https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz
Recette et bibliothèques du fournisseur Windows : https://www.gyan.dev/ffmpeg/builds/
Les notices de licence accompagnent les exécutables dans _internal/licenses.
yt-dlp et yt-dlp-ejs : versions épinglées de requirements.txt, composants JavaScript intégrés, aucun téléchargement de code distant.
"""
    (licenses / "SOURCES.txt").write_text(source_notice, encoding="utf-8")
    pyinstaller = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--name", "Studio-Fanny", "--contents-directory", "_internal",
                   "--distpath", str(work / "dist"), "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
                   "--paths", str(ROOT), "--collect-submodules", "playlist25", "--collect-all", "yt_dlp", "--collect-all", "yt_dlp_ejs",
                   "--collect-submodules", "uvicorn", "--recursive-copy-metadata", "yt-dlp", "--copy-metadata", "yt-dlp-ejs", "--copy-metadata", "fastapi",
                   "--add-data", f"{ROOT / 'playlist25' / 'static'};playlist25/static", "--add-data", f"{public_config};cloud",
                   "--add-data", f"{licenses};licenses"]
    for binary in sorted(tools.glob("*.exe")):
        pyinstaller += ["--add-binary", f"{binary};tools"]
    pyinstaller.append(str(entry))
    subprocess.run(pyinstaller, cwd=ROOT, check=True)
    directory = work / "dist" / "Studio-Fanny"
    required = [directory / "Studio-Fanny.exe", *(directory / "_internal" / "tools" / filename for filename in ("node.exe", "ffmpeg.exe", "ffprobe.exe")),
                directory / "_internal" / "playlist25" / "static" / "admin.html", directory / "_internal" / "cloud" / "public-config.json"]
    if any(not path.is_file() for path in required):
        raise RuntimeError("The frozen folder is incomplete; publishing is blocked.")
    solver = directory / "_internal" / "yt_dlp_ejs" / "yt" / "solver" / "core.min.js"
    if not solver.is_file():
        raise RuntimeError("Bundled yt-dlp-ejs solver resources are missing; publishing is blocked.")
    for name in ("README.md", "docs/INSTALLATION.md", "docs/ADMINISTRATION.md"):
        source = ROOT / name
        if source.is_file():
            (directory / "Aide").mkdir(exist_ok=True)
            shutil.copy2(source, directory / "Aide" / source.name)
    (directory / "LIRE-MOI.txt").write_text("STUDIO FANNY\n\n1. Extrayez tout ce dossier ZIP.\n2. Double-cliquez sur Studio-Fanny.exe.\n3. Au premier lancement, choisissez votre mot de passe dans la fenêtre.\n\nPython, Node.js et FFmpeg sont déjà intégrés. Le dossier Aide contient les guides.\nLa configuration privée et les musiques sont conservées dans votre profil Windows, hors de ce dossier.\n", encoding="utf-8")
    (directory / "build-info.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    check_bundle_files(directory)
    if run_smoke:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "packaging" / "windows_smoke.py"), "--executable", str(directory / "Studio-Fanny.exe"), "--report", str(work / "windows-smoke.json")], cwd=ROOT, check=True, timeout=180)
    destination.mkdir(parents=True, exist_ok=True)
    zip_path = destination / "Studio-Fanny-Windows.zip"
    digest = bundle_zip(directory, zip_path)
    (destination / "build-info.json").write_text(json.dumps({**metadata, "archive_sha256": digest}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if run_smoke:
        shutil.copy2(work / "windows-smoke.json", destination / "windows-smoke.json")
    return zip_path


def main():
    parser = argparse.ArgumentParser(description="Build the portable Studio Fanny Windows application.")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "windows")
    parser.add_argument("--destination", type=Path, default=ROOT / "dist" / "windows")
    # Local diagnosis only: the publication workflow always executes the smoke test.
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args()
    try:
        print(build(args.work.resolve(), args.destination.resolve(), run_smoke=not args.skip_smoke))
    except Exception as error:
        # Keep the actionable failure readable via the GitHub annotations API
        # even when a client cannot retrieve Actions' redirected full logs.
        details = str(error)
        report = args.work / "windows-smoke.json"
        if report.is_file():
            try:
                smoke_error = json.loads(report.read_text(encoding="utf-8")).get("error")
                if smoke_error:
                    details = "Frozen Windows smoke: " + str(smoke_error)
            except (OSError, ValueError):
                pass
        escaped = details[:12000].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(f"::error title=Windows desktop build::{escaped}", flush=True)
        raise


if __name__ == "__main__":
    main()
