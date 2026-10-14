"""Validated local audio, with paths confined to the configured library."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import errno
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unicodedata
import uuid

from .db import now, source_dict


class LibraryError(ValueError):
    """An audio operation could not safely be completed."""


AUDIO_EXTENSIONS = frozenset({"mp3", "flac", "ogg", "opus", "m4a", "aac", "wav", "aif", "aiff", "wma", "webm", "mp4"})
_FILE_TRANSACTIONS = ContextVar("library_file_transactions", default={})


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _file_signature(path: Path):
    info = path.lstat()
    if _is_link(path) or not stat.S_ISREG(info.st_mode):
        raise LibraryError("Le fichier audio doit être un fichier direct.")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _discard_owned(path: Path, signature) -> None:
    """Best-effort cleanup must never remove a replacement or mask a commit."""
    try:
        if _file_signature(path) == signature:
            path.unlink()
    except (OSError, LibraryError):
        pass


def _discard_unreferenced(conn, files) -> None:
    """A failed commit can have an uncertain outcome: retain referenced files."""
    for path, signature, relative in files:
        try:
            if conn.execute("SELECT 1 FROM sources WHERE file_path=? LIMIT 1", (relative,)).fetchone():
                continue
        except sqlite3.Error:
            continue  # Preserve data when the database cannot confirm removal.
        _discard_owned(path, signature)


@contextmanager
def file_transaction(conn: sqlite3.Connection):
    """Own a DB commit and defer file removal until its outcome is known.

    Callers must perform their related SQL inside this scope without committing
    themselves. On rollback, old files stay intact and only our new files leave.
    """
    current = _FILE_TRANSACTIONS.get()
    if id(conn) in current:
        yield
        return
    changes = {"installed": [], "obsolete": []}
    token = _FILE_TRANSACTIONS.set({**current, id(conn): changes})
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            finally:
                _discard_unreferenced(conn, reversed(changes["installed"]))
            raise
        _discard_unreferenced(conn, changes["obsolete"])
    finally:
        _FILE_TRANSACTIONS.reset(token)


def _record_files(conn, installed, obsolete=(), *, outer_transaction=False):
    changes = _FILE_TRANSACTIONS.get().get(id(conn))
    if changes is not None:
        changes["installed"].extend(installed)
        changes["obsolete"].extend(obsolete)
    elif not outer_transaction:
        _discard_unreferenced(conn, obsolete)
    # An unwrapped external transaction may still roll back. Preserve its old
    # files in that case rather than guessing whether its caller will commit.


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
        unresolved = resolved_base / relative
        if any(_is_link(part) for part in (unresolved, *unresolved.parents) if part.is_relative_to(resolved_base)):
            raise LibraryError("Le chemin audio ne doit pas utiliser de lien symbolique ni de jonction.")
        candidate = unresolved.resolve()
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


def _copy_file(source: Path, destination: Path, expected=None) -> int:
    """Stream one stable regular file; reject replacement during its copy."""
    before = _file_signature(source)
    if expected is not None and before != expected:
        raise LibraryError("Le fichier audio a changé ; actualisez puis réessayez.")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as original:
        opened = os.fstat(original.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != before:
            raise LibraryError("Le fichier audio a changé pendant son ouverture.")
        with destination.open("wb") as output:
            shutil.copyfileobj(original, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        after = os.fstat(original.fileno())
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != before or _file_signature(source) != before:
        raise LibraryError("Le fichier audio a changé pendant sa copie ; réessayez.")
    if destination.stat().st_size != before[2]:
        raise LibraryError("La copie du fichier audio est incomplète.")
    return before[2]


def _install_file(staged: Path, destination: Path) -> None:
    """Publish atomically without overwriting any existing audio file."""
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    reserved = os.fstat(descriptor)
    os.close(descriptor)
    signature = reserved.st_dev, reserved.st_ino, reserved.st_size, reserved.st_mtime_ns
    temporary = None
    published = False
    try:
        if _file_signature(destination) != signature:
            raise LibraryError("La destination audio a changé ; réessayez.")
        try:
            os.replace(staged, destination)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            fd, name = tempfile.mkstemp(prefix=".audio-", suffix=".part", dir=destination.parent)
            os.close(fd)
            temporary = Path(name)
            _copy_file(staged, temporary)
            if _file_signature(destination) != signature:
                raise LibraryError("La destination audio a changé ; réessayez.")
            os.replace(temporary, destination)
            # Publication succeeded. A locked temporary file may be retried by
            # the owning job's cleanup without turning this into a failed import.
            try:
                staged.unlink()
            except OSError:
                pass
        published = True
    finally:
        if not published:
            _discard_owned(destination, signature)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def import_audio(conn: sqlite3.Connection, cfg, temp_path: str | Path, title: str = "", artist: str = "") -> dict:
    """Copy and validate a server-uploaded file; never alter the upload original."""
    original = Path(temp_path).resolve()
    if not original.is_relative_to(Path(cfg.temp_dir).resolve()) or original == Path(cfg.temp_dir).resolve():
        raise LibraryError("Le fichier à importer doit se trouver dans le dossier temporaire prévu.")
    if not original.is_file() or original.stat().st_size == 0:
        raise LibraryError("Fichier audio absent ou vide.")
    if original.stat().st_size > int(cfg.max_upload_mb) * 1024 * 1024:
        raise LibraryError("Le fichier dépasse la taille maximale d'import.")
    original_signature = _file_signature(original)
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
    installed = None
    savepoint = False
    outer_transaction = conn.in_transaction
    try:
        conn.execute("SAVEPOINT library_import")
        savepoint = True
        _copy_file(original, staged, original_signature)
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
        installed = _file_signature(destination)
        conn.execute("UPDATE sources SET file_path=? WHERE id=?", (filename, source_id))
        item = source_dict(conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
        conn.execute("RELEASE SAVEPOINT library_import")
        savepoint = False
        _record_files(conn, [(destination, installed, filename)], outer_transaction=outer_transaction)
        return item
    except BaseException:
        try:
            if savepoint and conn.in_transaction:
                conn.execute("ROLLBACK TO SAVEPOINT library_import")
                conn.execute("RELEASE SAVEPOINT library_import")
        finally:
            if installed and destination:
                _discard_unreferenced(conn, [(destination, installed, filename)])
        raise
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass


def rename_audio(conn: sqlite3.Connection, cfg, source_id: int, title: str, artist: str) -> dict:
    title, artist = _plain_text(title), _plain_text(artist)
    if not title:
        raise LibraryError("Le titre est obligatoire.")
    original = destination = staged = None
    installed = None
    savepoint = False
    outer_transaction = conn.in_transaction
    try:
        conn.execute("SAVEPOINT library_rename")
        savepoint = True
        # Obtain SQLite's writer lock before reading the source. Another rename
        # or a worker cannot publish a stale file_path over this operation.
        conn.execute("UPDATE sources SET file_path=file_path WHERE id=?", (source_id,))
        row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None or not row["file_path"]:
            raise LibraryError("Ce morceau ne possède pas de fichier dans la bibliothèque.")
        original = resolve_audio_path(cfg, row["file_path"])
        original_signature = _file_signature(original)
        verify_audio(original, cfg, row["duration"])
        filename = safe_filename(source_id, title, original.suffix)
        destination = resolve_audio_path(cfg, filename)
        if destination == original:
            # Artist-only edits must also keep the original bytes until commit.
            filename = safe_filename(source_id, f"révision {uuid.uuid4().hex[:12]} - {title}", original.suffix)
            destination = resolve_audio_path(cfg, filename)
        if destination.exists():
            raise LibraryError("Un fichier existe déjà avec ce nom.")
        _space_required(Path(cfg.temp_dir), original_signature[2] * 2 + 16384, cfg)
        _space_required(Path(cfg.audio_dir), original_signature[2] + 16384, cfg)
        fd, name = tempfile.mkstemp(prefix="metadata-", suffix=original.suffix, dir=cfg.temp_dir)
        os.close(fd)
        staged = Path(name)
        _copy_file(original, staged, original_signature)
        _write_tags(staged, title, artist)
        verified = verify_audio(staged, cfg, row["duration"])
        conn.execute("UPDATE sources SET title=?,artist=?,duration=?,file_path=? WHERE id=?",
                     (title, artist, verified["duration"], filename, source_id))
        _install_file(staged, destination)
        installed = _file_signature(destination)
        item = source_dict(conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
        conn.execute("RELEASE SAVEPOINT library_rename")
        savepoint = False
        _record_files(conn, [(destination, installed, filename)], [(original, original_signature, row["file_path"])], outer_transaction=outer_transaction)
        return item
    except BaseException:
        try:
            if savepoint and conn.in_transaction:
                conn.execute("ROLLBACK TO SAVEPOINT library_rename")
                conn.execute("RELEASE SAVEPOINT library_rename")
        finally:
            if installed and destination:
                _discard_unreferenced(conn, [(destination, installed, filename)])
        raise
    finally:
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass


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
