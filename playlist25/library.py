"""Validated local audio, with paths confined to the configured library."""

from __future__ import annotations

import errno
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata

from .db import now, source_dict


class LibraryError(ValueError):
    """An audio operation could not safely be completed."""


AUDIO_EXTENSIONS = frozenset({"mp3", "flac", "ogg", "opus", "m4a", "aac", "wav", "aif", "aiff", "wma", "webm", "mp4"})


def _plain_text(value: str, limit: int = 240) -> str:
    value = unicodedata.normalize("NFC", str(value))
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())[:limit]


def safe_filename(source_id: int, title: str, extension: str) -> str:
    """Return a short Windows-compatible filename with a stable source id."""
    if isinstance(source_id, bool) or not isinstance(source_id, int) or not 1 <= source_id <= 2**63 - 1:
        raise LibraryError("Identifiant de morceau invalide.")
    extension = str(extension).lower().lstrip(".")
    if extension not in AUDIO_EXTENSIONS:
        raise LibraryError("Format audio non pris en charge.")
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", _plain_text(title, 140)).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned) or "Sans titre"
    # Linux limits components in UTF-8 bytes and Windows in UTF-16 units.
    # A character-only cap fails for long accented titles and emoji.
    cleaned = cleaned.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .")
    # The stable prefix also makes reserved DOS names such as CON harmless.
    return f"source{source_id:06d} - {cleaned}.{extension}"


def _confined(base: Path, value: str | Path) -> Path:
    raw = str(value)
    if not raw or "\\" in raw or "\x00" in raw or ":" in raw:
        raise LibraryError("Chemin audio invalide.")
    relative = Path(raw)
    if relative.is_absolute() or any(part in {"..", "."} for part in relative.parts) or re.match(r"^[A-Za-z]:", raw):
        raise LibraryError("Le chemin doit rester dans la bibliothèque audio.")
    try:
        resolved_base = Path(base).resolve()
        candidate = (resolved_base / relative).resolve()
    except (OSError, RuntimeError) as exc:
        raise LibraryError("Le chemin audio ne peut pas être résolu.") from exc
    if not candidate.is_relative_to(resolved_base) or candidate == resolved_base:
        raise LibraryError("Le chemin sort de la bibliothèque audio.")
    return candidate


def resolve_audio_path(cfg, relative: str | Path) -> Path:
    return _confined(Path(cfg.audio_dir), relative)


def verify_audio(path: str | Path, cfg, expected_duration: float | None = None) -> dict:
    """Check an actual audio stream and its duration with the configured ffprobe."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise LibraryError("Fichier audio absent ou vide.")
    command = [str(cfg.ffprobe_path), "-v", "error", "-show_entries",
               "format=duration,format_name:stream=codec_type,codec_name,duration",
               "-of", "json", "-protocol_whitelist", "file",
               "-format_whitelist", "mp3,wav,flac,ogg,aac,mov,matroska,webm,asf,aiff",
               "--", str(path.resolve())]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=min(float(cfg.task_timeout), 60), check=False)
    except FileNotFoundError as exc:
        raise LibraryError("ffprobe est introuvable : installez FFmpeg et vérifiez les diagnostics.") from exc
    except subprocess.TimeoutExpired as exc:
        raise LibraryError("Le contrôle audio a dépassé le délai autorisé.") from exc
    if result.returncode:
        raise LibraryError("ffprobe ne peut pas lire ce fichier audio.")
    try:
        info = json.loads(result.stdout)
        streams = [stream for stream in info.get("streams", []) if stream.get("codec_type") == "audio"]
        if not streams or not streams[0].get("codec_name"):
            raise LibraryError("Ce fichier ne contient aucun flux audio lisible.")
        raw_duration = info.get("format", {}).get("duration") or next((s.get("duration") for s in streams if s.get("duration")), None)
        duration = float(raw_duration)
        if not math.isfinite(duration) or duration <= 0:
            raise LibraryError("La durée du fichier audio ne peut pas être vérifiée.")
        if duration > float(cfg.max_duration) + 1:
            raise LibraryError("Ce fichier dépasse la durée maximale autorisée.")
        if expected_duration is not None:
            expected = float(expected_duration)
            if not math.isfinite(expected) or expected <= 0:
                raise LibraryError("Durée attendue invalide.")
            if abs(duration - expected) > max(3, expected * 0.08):
                raise LibraryError("La durée du fichier ne correspond pas à celle du morceau attendu.")
        return {"duration": duration, "codec": streams[0]["codec_name"], "format": info.get("format", {}).get("format_name", "")}
    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        if isinstance(exc, LibraryError):
            raise
        raise LibraryError("Les informations audio renvoyées par ffprobe sont incomplètes.") from exc


def _write_tags(path: Path, title: str, artist: str) -> None:
    """Write only known editable fields; unsupported containers keep DB metadata."""
    try:
        from mutagen import File
        from mutagen.id3 import ID3, TIT2, TPE1
        audio = File(path, easy=True)
        if audio is None or audio.__class__.__name__ in {"AAC", "ASF", "Matroska"}:
            return
        if audio.tags is None:
            audio.add_tags()
        if isinstance(audio.tags, ID3):
            audio.tags.delall("TIT2")
            audio.tags.delall("TPE1")
            if title:
                audio.tags.add(TIT2(encoding=3, text=[title]))
            if artist:
                audio.tags.add(TPE1(encoding=3, text=[artist]))
        else:
            for key, value in (("title", title), ("artist", artist)):
                if value:
                    audio[key] = [value]
                elif key in audio:
                    del audio[key]
        audio.save()
    except ImportError as exc:
        raise LibraryError("La dépendance mutagen manque : relancez l'installation.") from exc
    except Exception as exc:
        raise LibraryError("Impossible d'écrire les métadonnées de ce fichier audio.") from exc


def _space_required(directory: Path, required: int, cfg) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(directory).free < required + int(cfg.min_free_mb) * 1024 * 1024:
        raise LibraryError("Espace disque insuffisant ; libérez de l'espace avant de réessayer.")


def _install_file(staged: Path, destination: Path) -> None:
    """Install atomically, including when temp and audio use different volumes."""
    try:
        os.replace(staged, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        fd, temporary = tempfile.mkstemp(prefix=".audio-", suffix=".part", dir=destination.parent)
        os.close(fd)
        temporary = Path(temporary)
        try:
            shutil.copy2(staged, temporary)
            os.replace(temporary, destination)
            staged.unlink()
        finally:
            temporary.unlink(missing_ok=True)


def import_audio(conn: sqlite3.Connection, cfg, temp_path: str | Path, title: str = "", artist: str = "") -> dict:
    """Copy and validate a server-uploaded file; never alter the upload original."""
    original = Path(temp_path).resolve()
    if not original.is_relative_to(Path(cfg.temp_dir).resolve()) or original == Path(cfg.temp_dir).resolve():
        raise LibraryError("Le fichier à importer doit se trouver dans le dossier temporaire prévu.")
    if not original.is_file() or original.stat().st_size == 0:
        raise LibraryError("Fichier audio absent ou vide.")
    if original.stat().st_size > int(cfg.max_upload_mb) * 1024 * 1024:
        raise LibraryError("Le fichier dépasse la taille maximale d'import.")
    metadata = verify_audio(original, cfg)
    extension = original.suffix.lower().lstrip(".")
    if extension not in AUDIO_EXTENSIONS:
        raise LibraryError("Format audio non pris en charge.")
    title = _plain_text(title or original.stem)
    artist = _plain_text(artist)
    _space_required(Path(cfg.audio_dir), original.stat().st_size * 2 + 16384, cfg)
    _space_required(Path(cfg.temp_dir), original.stat().st_size + 16384, cfg)
    fd, name = tempfile.mkstemp(prefix="import-", suffix="." + extension, dir=cfg.temp_dir)
    os.close(fd)
    staged = Path(name)
    destination = None
    installed = False
    conn.execute("SAVEPOINT library_import")
    try:
        shutil.copy2(original, staged)
        _write_tags(staged, title, artist)
        verified = verify_audio(staged, cfg, metadata["duration"])
        cursor = conn.execute("INSERT INTO sources (title,artist,duration,status,created_at) VALUES (?,?,?,?,?)",
                              (title, artist, verified["duration"], "available", now()))
        source_id = cursor.lastrowid
        filename = safe_filename(source_id, title, extension)
        destination = resolve_audio_path(cfg, filename)
        if destination.exists():
            raise LibraryError("Un fichier existe déjà à cet emplacement.")
        _install_file(staged, destination)
        installed = True
        conn.execute("UPDATE sources SET file_path=? WHERE id=?", (filename, source_id))
        item = source_dict(conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
        conn.execute("RELEASE SAVEPOINT library_import")
        return item
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT library_import")
        conn.execute("RELEASE SAVEPOINT library_import")
        if installed and destination:
            destination.unlink(missing_ok=True)
        raise
    finally:
        staged.unlink(missing_ok=True)


def rename_audio(conn: sqlite3.Connection, cfg, source_id: int, title: str, artist: str) -> dict:
    row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if row is None or not row["file_path"]:
        raise LibraryError("Ce morceau ne possède pas de fichier dans la bibliothèque.")
    original = resolve_audio_path(cfg, row["file_path"])
    verify_audio(original, cfg, row["duration"])
    title, artist = _plain_text(title), _plain_text(artist)
    if not title:
        raise LibraryError("Le titre est obligatoire.")
    filename = safe_filename(source_id, title, original.suffix)
    destination = resolve_audio_path(cfg, filename)
    if destination != original and destination.exists():
        raise LibraryError("Un fichier existe déjà avec ce nom.")
    _space_required(Path(cfg.temp_dir), original.stat().st_size * 2 + 16384, cfg)
    _space_required(Path(cfg.audio_dir), original.stat().st_size + 16384, cfg)
    fd, name = tempfile.mkstemp(prefix="metadata-", suffix=original.suffix, dir=cfg.temp_dir)
    os.close(fd)
    staged = Path(name)
    installed = False
    conn.execute("SAVEPOINT library_rename")
    try:
        shutil.copy2(original, staged)
        _write_tags(staged, title, artist)
        verified = verify_audio(staged, cfg, row["duration"])
        conn.execute("UPDATE sources SET title=?,artist=?,duration=?,file_path=? WHERE id=?",
                     (title, artist, verified["duration"], filename, source_id))
        _install_file(staged, destination)
        installed = True
        if destination != original:
            original.unlink()
        item = source_dict(conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
        conn.execute("RELEASE SAVEPOINT library_rename")
        return item
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT library_rename")
        conn.execute("RELEASE SAVEPOINT library_rename")
        if installed and destination != original and original.exists():
            destination.unlink(missing_ok=True)
        raise
    finally:
        staged.unlink(missing_ok=True)


def available_sources(conn: sqlite3.Connection, cfg) -> list[dict]:
    """Only list real files; stale database status is never proof of availability."""
    items = []
    for row in conn.execute("SELECT * FROM sources WHERE status='available' AND file_path IS NOT NULL ORDER BY id"):
        try:
            path = resolve_audio_path(cfg, row["file_path"])
            if path.is_file() and path.stat().st_size:
                item = source_dict(row)
                item["size"] = path.stat().st_size
                items.append(item)
        except (LibraryError, OSError):
            continue
    return items
