"""Private, streaming local backups. Restoration never replaces a live studio."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
import unicodedata
import zipfile

from dotenv.parser import parse_stream

from . import db
from .config import Settings


class BackupError(ValueError):
    pass


_FORMAT = "playlist25-private-backup"
_VERSION = 1
_CHUNK = 1024 * 1024
_MANIFEST_LIMIT = 32 * 1024 * 1024
_CONFIG_LIMIT = 1024 * 1024
_RUNTIME_KEYS = {"FFMPEG_PATH", "FFPROBE_PATH", "JS_RUNTIME", "YT_DLP_PATH"}
_COUNTS = {"sources", "proposals", "guests", "votes", "photos", "audio_files"}
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def _link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _path(value) -> Path:
    candidate = Path(value).expanduser().absolute()
    if any(_link(part) for part in (candidate, *candidate.parents)):
        raise BackupError("Choisissez un chemin direct, sans lien symbolique ni jonction.")
    return candidate.resolve()


def _inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _private(path: Path):
    path.chmod(0o700 if path.is_dir() else 0o600)


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise BackupError("L’archive contient un chemin interdit.")
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name:
        raise BackupError("L’archive contient un chemin interdit.")
    for part in path.parts:
        if part in {".", ".."} or part.endswith((" ", ".")) or _RESERVED.match(part):
            raise BackupError("L’archive contient un nom de fichier incompatible.")
        if any(ord(char) < 32 or ord(char) == 127 or char in '<>"|?*' for char in part):
            raise BackupError("L’archive contient un nom de fichier incompatible.")
    try:
        name.encode("utf-8")
    except UnicodeError:
        raise BackupError("L’archive contient un nom de fichier incompatible.") from None
    return name


def _payload_name(name: str) -> str:
    name = _safe_name(name)
    if name not in {".env.local", "data/playlist.sqlite3"} and not name.startswith("data/audio/"):
        raise BackupError("L’archive contient un fichier étranger à une sauvegarde du studio.")
    if name == "data/audio/":
        raise BackupError("L’archive contient un chemin interdit.")
    return name


def _configuration(content: bytes) -> dict[str, str]:
    if len(content) > _CONFIG_LIMIT:
        raise BackupError("La configuration privée est trop volumineuse.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError:
        raise BackupError("La configuration privée n’est pas lisible.") from None
    result = {}
    allowed = {name.upper() for name in Settings.__dataclass_fields__ if name != "root"}
    for binding in parse_stream(io.StringIO(text)):
        if binding.error:
            raise BackupError("La configuration privée contient une ligne invalide.")
        if binding.key is None:
            continue
        if binding.key not in allowed or binding.key in result or binding.value is None:
            raise BackupError("La configuration privée contient un réglage non pris en charge.")
        if any(char in binding.value for char in ("\x00", "\n", "\r")):
            raise BackupError("La configuration privée contient une valeur invalide.")
        result[binding.key] = binding.value
    if not result.get("ADMIN_PASSWORD") or not result.get("SESSION_SECRET"):
        raise BackupError("La sauvegarde doit contenir la configuration privée complète.")
    # Validate types and supported settings without loading process variables or
    # creating any directories. Paths are interpreted only for the chosen home.
    _settings(Path.cwd(), result)
    return result


def _settings(home: Path, values: dict[str, str]) -> Settings:
    kwargs = {"root": home, "data_dir": home / "data"}
    try:
        for name, field in Settings.__dataclass_fields__.items():
            if name == "root" or name.upper() not in values:
                continue
            value = values[name.upper()]
            if name == "data_dir":
                path = Path(value).expanduser()
                kwargs[name] = path if path.is_absolute() else home / path
            elif field.type == "int":
                kwargs[name] = int(value)
            elif field.type == "bool":
                if value.lower() not in {"0", "1", "false", "true", "no", "yes", "non", "oui"}:
                    raise ValueError()
                kwargs[name] = value.lower() in {"1", "true", "yes", "oui"}
            else:
                kwargs[name] = value
        return Settings(**kwargs)
    except (ValueError, TypeError, OSError):
        raise BackupError("Les réglages de la configuration privée ne sont pas valides.") from None


def _stopped(cfg: Settings):
    records_path = cfg.data_dir / "processes.json"
    if not records_path.exists():
        return
    if _link(records_path):
        raise BackupError("Le registre du studio n’est pas un fichier direct.")
    from .__main__ import _matches_service, _process_records
    import psutil
    try:
        records = _process_records(records_path)
        for record in records:
            try:
                process = psutil.Process(record["pid"])
                if _matches_service(process, record, cfg) and process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    raise BackupError("Arrêtez le studio avant de créer sa sauvegarde.")
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                raise BackupError("L’arrêt du studio ne peut pas être confirmé.") from None
    except (RuntimeError, OSError):
        raise BackupError("Le registre du studio doit être vérifié avant la sauvegarde.") from None


def _readonly(path: Path):
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024 * 1024)
    return conn


def _columns(conn, table):
    return {row[1]: tuple(row[2:]) for row in conn.execute(f'PRAGMA table_xinfo("{table}")')}


def _canonical_database(source: Path, destination: Path) -> dict[str, int]:
    """Copy bound row values into our schema; never execute archived schema SQL."""
    try:
        with closing(_readonly(source)) as original, closing(sqlite3.connect(destination)) as target:
            target.executescript(db.SCHEMA)
            target.execute("CREATE UNIQUE INDEX proposals_owner_source_live_idx ON proposals(source_id,guest_id) WHERE withdrawn_at IS NULL")
            expected = {row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            objects = list(original.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
            if {name for kind, name, _ in objects if kind == "table"} != expected:
                raise BackupError("La sauvegarde ne contient pas une base complète du studio.")
            if any(kind not in {"table", "index"} or (sql and re.search(r"\bVIRTUAL\s+TABLE\b", sql, re.I)) for kind, _, sql in objects):
                raise BackupError("Le schéma SQLite de cette archive n’est pas pris en charge.")
            for table in sorted(expected):
                if _columns(original, table) != _columns(target, table):
                    raise BackupError("Le schéma SQLite de cette archive n’est pas pris en charge.")
                if sorted(row[2:] for row in original.execute(f'PRAGMA foreign_key_list("{table}")')) != sorted(row[2:] for row in target.execute(f'PRAGMA foreign_key_list("{table}")')):
                    raise BackupError("Les références SQLite de cette archive ne sont pas valides.")
            if original.execute("PRAGMA integrity_check").fetchone() != ("ok",) or original.execute("PRAGMA foreign_key_check").fetchone():
                raise BackupError("La base SQLite de la sauvegarde est endommagée.")
            # Foreign keys are checked after all tables were copied, so original
            # IDs and cyclic order-independent references remain unchanged.
            with target:
                for table in sorted(expected):
                    names = list(_columns(target, table))
                    columns = ",".join(f'"{name}"' for name in names)
                    marks = ",".join("?" for _ in names)
                    target.executemany(f'INSERT INTO "{table}"({columns}) VALUES({marks})', original.execute(f'SELECT {columns} FROM "{table}"'))
            if target.execute("PRAGMA foreign_key_check").fetchone():
                raise BackupError("Les références SQLite de cette archive ne sont pas valides.")
            for (path,) in target.execute("SELECT file_path FROM sources WHERE file_path IS NOT NULL AND file_path<>''"):
                _safe_name(path)
            counts = {name: target.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in ("sources", "proposals", "guests", "votes")}
            counts["photos"] = target.execute("SELECT count(*) FROM guests WHERE avatar_data<>''").fetchone()[0]
        _private(destination)
        return counts
    except (sqlite3.Error, OSError, TypeError):
        raise BackupError("La base SQLite de la sauvegarde n’est pas valide.") from None


def _space(directory: Path, needed: int):
    if shutil.disk_usage(directory).free < needed:
        raise BackupError("L’espace disque est insuffisant pour cette opération.")


def _audio_files(audio: Path):
    if not audio.exists():
        return []
    if _link(audio) or not audio.is_dir():
        raise BackupError("Le dossier audio doit être un dossier direct.")
    result = []
    folded = set()
    for directory, dirs, files in os.walk(audio, followlinks=False):
        directory = Path(directory)
        if any(_link(directory / name) for name in dirs):
            raise BackupError("Le dossier audio contient un lien symbolique ou une jonction.")
        for name in files:
            path = directory / name
            if _link(path) or not path.is_file():
                raise BackupError("Le dossier audio contient un fichier non pris en charge.")
            member = _payload_name("data/audio/" + path.relative_to(audio).as_posix())
            key = unicodedata.normalize("NFC", member).casefold()
            if key in folded:
                raise BackupError("Deux fichiers audio portent le même nom sur Windows.")
            folded.add(key)
            result.append((member, path, path.stat().st_size))
    return sorted(result)


def _write_file(archive, member, path):
    digest = hashlib.sha256()
    size = 0
    if _link(path):
        raise BackupError("Un fichier de la sauvegarde a changé pendant sa création.")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source, archive.open(member, "w", force_zip64=True) as output:
        before = os.fstat(source.fileno())
        named = path.lstat()
        if _link(path) or not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino):
            raise BackupError("La sauvegarde contient un fichier non pris en charge.")
        while chunk := source.read(_CHUNK):
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(source.fileno())
    if size != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or _link(path):
        raise BackupError("Un fichier a changé pendant la sauvegarde ; réessayez studio arrêté.")
    return {"path": member, "size": size, "sha256": digest.hexdigest()}


def _commit_file(temporary: Path, destination: Path):
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    own = os.fstat(descriptor)
    os.close(descriptor)
    try:
        temporary.replace(destination)
    except BaseException:
        if destination.exists():
            current = destination.lstat()
            if (current.st_dev, current.st_ino) == (own.st_dev, own.st_ino):
                destination.unlink()
        raise


def _summary(manifest, archive, manifest_digest, missing_audio=0):
    return {"archive": str(archive), "created_at": manifest["created_at"], "include_audio": manifest["include_audio"],
            "confidential": True, "counts": manifest["counts"], "file_count": len(manifest["files"]),
            "uncompressed_bytes": sum(item["size"] for item in manifest["files"]), "archive_bytes": archive.stat().st_size,
            "manifest_sha256": manifest_digest, "missing_audio": missing_audio}


def create_backup(home, destination, include_audio=True):
    """SQLite snapshot + private configuration + optional audio; never overwrite."""
    if not isinstance(include_audio, bool):
        raise BackupError("Le choix de sauvegarde audio n’est pas valide.")
    home, destination = _path(home), _path(destination)
    private = home / ".env.local"
    if _link(private) or not private.is_file():
        raise BackupError("Cette installation ne contient pas sa configuration privée.")
    if private.stat().st_size > _CONFIG_LIMIT:
        raise BackupError("La configuration privée est trop volumineuse.")
    content = private.read_bytes()
    cfg = _settings(home, _configuration(content))
    data = _path(cfg.data_dir)
    if _inside(destination, home) or _inside(destination, data):
        raise BackupError("Enregistrez la sauvegarde en dehors du dossier du studio et de ses données.")
    if destination.exists():
        raise BackupError("Ce fichier existe déjà ; choisissez un autre nom pour conserver la sauvegarde précédente.")
    if not destination.parent.is_dir():
        raise BackupError("Choisissez un dossier de destination existant.")
    _stopped(cfg)
    database = cfg.db_path
    if _link(database) or not database.is_file():
        raise BackupError("La base du studio est absente ou n’est pas un fichier direct.")
    audio = _audio_files(cfg.audio_dir) if include_audio else []
    _space(destination.parent, sum(size for _, _, size in audio) + 3 * database.stat().st_size + len(content) + cfg.min_free_mb * _CHUNK)
    try:
        with tempfile.TemporaryDirectory(prefix=".studio-backup-", dir=destination.parent) as working:
            work = Path(working)
            _private(work)
            snapshot = work / "snapshot.sqlite3"
            with closing(_readonly(database)) as original, closing(sqlite3.connect(snapshot)) as target:
                original.backup(target)
            counts = _canonical_database(snapshot, work / "validated.sqlite3")
            counts["audio_files"] = len(audio)
            missing = len(_missing_ids(work / "validated.sqlite3", {name[len("data/audio/"):] for name, _, _ in audio}))
            config = work / "configuration"
            config.write_bytes(content)
            _private(config)
            temporary = work / "backup.zip"
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                files = [_write_file(archive, ".env.local", config), _write_file(archive, "data/playlist.sqlite3", snapshot)]
                files.extend(_write_file(archive, name, path) for name, path, _ in audio)
                manifest = {"format": _FORMAT, "version": _VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                            "confidential": True, "include_audio": include_audio, "counts": counts, "files": files}
                raw = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(raw) > _MANIFEST_LIMIT:
                    raise BackupError("Le catalogue de fichiers de la sauvegarde est trop volumineux.")
                archive.writestr("manifest.json", raw)
            _private(temporary)
            _commit_file(temporary, destination)
        return _summary(manifest, destination, hashlib.sha256(raw).hexdigest(), missing)
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile):
        raise BackupError("La sauvegarde n’a pas pu être créée ; les données du studio sont conservées.") from None


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupError("Le manifeste de la sauvegarde contient des champs en double.")
        result[key] = value
    return result


def _manifest(archive):
    members = {}
    folded = set()
    for member in archive.infolist():
        # ZipInfo may truncate NULs or normalize separators on Windows. Check
        # the original stored name, before it can alias a permitted payload.
        if member.orig_filename != member.filename:
            raise BackupError("L’archive contient un chemin modifié ou interdit.")
        name = _safe_name(member.orig_filename)
        key = unicodedata.normalize("NFC", name).casefold()
        mode = stat.S_IFMT(member.external_attr >> 16)
        if key in folded or member.is_dir() or mode not in {0, stat.S_IFREG} or member.flag_bits & 1:
            raise BackupError("L’archive contient un doublon, un lien ou un fichier non pris en charge.")
        if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise BackupError("Le format de compression de l’archive n’est pas pris en charge.")
        folded.add(key)
        members[name] = member
    if "manifest.json" not in members or members["manifest.json"].file_size > _MANIFEST_LIMIT:
        raise BackupError("Le manifeste de la sauvegarde est absent ou invalide.")
    raw = archive.read(members["manifest.json"])
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_pairs)
        if not isinstance(manifest, dict) or manifest.get("format") != _FORMAT or type(manifest.get("version")) is not int or manifest["version"] != _VERSION:
            raise ValueError()
        if manifest.get("confidential") is not True or type(manifest.get("include_audio")) is not bool:
            raise ValueError()
        timestamp = datetime.fromisoformat(manifest["created_at"])
        if timestamp.tzinfo is None:
            raise ValueError()
        counts = manifest["counts"]
        if not isinstance(counts, dict) or set(counts) != _COUNTS or any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError()
        if not isinstance(manifest["files"], list):
            raise ValueError()
        wanted = {}
        for item in manifest["files"]:
            if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
                raise ValueError()
            name = _payload_name(item["path"])
            if name in wanted or type(item["size"]) is not int or item["size"] < 0 or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise ValueError()
            if name not in members or members[name].file_size != item["size"]:
                raise ValueError()
            wanted[name] = item
        if set(members) != {"manifest.json", *wanted} or not {".env.local", "data/playlist.sqlite3"} <= set(wanted):
            raise ValueError()
        audio = sum(name.startswith("data/audio/") for name in wanted)
        if counts["audio_files"] != audio or (not manifest["include_audio"] and audio):
            raise ValueError()
        if wanted[".env.local"]["size"] > _CONFIG_LIMIT:
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise BackupError("Le manifeste de la sauvegarde n’est pas valide.") from None
    return manifest, members, hashlib.sha256(raw).hexdigest()


@contextmanager
def _verified(archive_path, *, parent=None, expected=None):
    archive_path = _path(archive_path)
    if not archive_path.is_file():
        raise BackupError("Choisissez un fichier de sauvegarde existant.")
    verified = False
    try:
        with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory(prefix=".studio-verify-", dir=parent) as working:
            work = Path(working)
            _private(work)
            manifest, members, digest = _manifest(archive)
            if expected is not None and (not isinstance(expected, str) or digest != expected):
                raise BackupError("Cette sauvegarde a changé depuis l’aperçu ; ouvrez-la à nouveau avant de confirmer.")
            database = work / "archived.sqlite3"
            _space(work, members["data/playlist.sqlite3"].file_size * 3 + 8 * _CHUNK)
            for item in manifest["files"]:
                name = item["path"]
                checksum, size = hashlib.sha256(), 0
                output = database.open("wb") if name == "data/playlist.sqlite3" else None
                config = io.BytesIO() if name == ".env.local" else None
                try:
                    with archive.open(members[name]) as source:
                        while chunk := source.read(_CHUNK):
                            size += len(chunk)
                            if size > item["size"]:
                                raise BackupError("La taille d’un fichier de la sauvegarde est invalide.")
                            checksum.update(chunk)
                            if output:
                                output.write(chunk)
                            if config is not None:
                                config.write(chunk)
                finally:
                    if output:
                        output.close()
                if size != item["size"] or checksum.hexdigest() != item["sha256"]:
                    raise BackupError("Un fichier de la sauvegarde est endommagé ou a été modifié.")
                if config is not None:
                    values = _configuration(config.getvalue())
            canonical = work / "canonical.sqlite3"
            counts = _canonical_database(database, canonical)
            if any(counts[key] != manifest["counts"][key] for key in counts):
                raise BackupError("Les compteurs de la sauvegarde ne correspondent pas à sa base.")
            present = {item["path"][len("data/audio/"):] for item in manifest["files"] if item["path"].startswith("data/audio/")}
            missing = len(_missing_ids(canonical, present))
            verified = True
            yield archive, manifest, members, digest, canonical, values, missing
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError, EOFError):
        if verified:
            # Exceptions from the caller's restore/commit belong to that
            # operation, rather than to validation of the original archive.
            raise
        raise BackupError("La sauvegarde ne peut pas être vérifiée ou est endommagée.") from None


def inspect_backup(archive):
    """Validate all bytes and SQLite before preview; no current studio is opened."""
    with _verified(archive) as (_, manifest, _, digest, _, _, missing):
        return _summary(manifest, _path(archive), digest, missing)


def _restored_config(values):
    # Installation-specific executables and an absolute old DATA_DIR must never
    # redirect the restored copy to old data or execute programs from an archive.
    restored = {key: value for key, value in values.items() if key not in _RUNTIME_KEYS and key != "DATA_DIR"}
    restored["DATA_DIR"] = "data"
    lines = ["# Configuration locale privée restaurée. Ne pas publier cette archive.\n"]
    for key, value in restored.items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{escaped}"\n')
    return "".join(lines).encode("utf-8")


def _missing_ids(database, present):
    with closing(_readonly(database)) as conn:
        return [row[0] for row in conn.execute("SELECT s.id,s.file_path,s.status,j.status FROM sources s LEFT JOIN jobs j ON j.source_id=s.id")
                if (row[1] and row[1] not in present) or (not row[1] and "available" in row[2:])]


def _mark_missing(database, manifest):
    present = {item["path"][len("data/audio/"):] for item in manifest["files"] if item["path"].startswith("data/audio/")}
    missing = _missing_ids(database, present)
    with closing(sqlite3.connect(database)) as conn, conn:
        for source_id in missing:
            conn.execute("UPDATE sources SET file_path=NULL,status=CASE WHEN status='available' THEN 'interrupted' ELSE status END WHERE id=?", (source_id,))
            conn.execute("UPDATE jobs SET status='interrupted',error=? WHERE source_id=? AND status='available'", ("Le fichier audio n’était pas inclus dans la sauvegarde.", source_id))
        db.set_setting(conn, "queue_paused", True)
    return len(missing)


def restore_backup(archive, new_home, *, expected_manifest_sha256=None):
    """Stage a fully verified new studio, atomically install it, retain the old one."""
    new_home = _path(new_home)
    if not new_home.parent.is_dir() or (new_home.exists() and (not new_home.is_dir() or any(new_home.iterdir()))):
        raise BackupError("Choisissez un nouveau dossier vide ; l’installation actuelle reste conservée.")
    existed = new_home.exists()
    try:
        with _verified(archive, parent=new_home.parent, expected=expected_manifest_sha256) as (opened, manifest, members, digest, canonical, values, _):
            _space(new_home.parent, sum(item["size"] for item in manifest["files"]) + canonical.stat().st_size + 8 * _CHUNK)
            with tempfile.TemporaryDirectory(prefix=".studio-restore-", dir=new_home.parent) as working:
                stage = Path(working) / "home"
                stage.mkdir(mode=0o700)
                data = stage / "data"
                data.mkdir(mode=0o700)
                (data / "audio").mkdir(mode=0o700)
                for item in manifest["files"]:
                    name = item["path"]
                    if not name.startswith("data/audio/"):
                        continue
                    target = stage / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    checksum, size = hashlib.sha256(), 0
                    with opened.open(members[name]) as source, target.open("xb") as output:
                        while chunk := source.read(_CHUNK):
                            output.write(chunk)
                            checksum.update(chunk)
                            size += len(chunk)
                    _private(target)
                    if size != item["size"] or checksum.hexdigest() != item["sha256"]:
                        raise BackupError("La sauvegarde a changé pendant la restauration.")
                database = data / "playlist.sqlite3"
                shutil.copyfile(canonical, database)
                _private(database)
                private = stage / ".env.local"
                private.write_bytes(_restored_config(values))
                _private(private)
                _settings(new_home, _configuration(private.read_bytes()))
                missing = _mark_missing(database, manifest)
                result = _summary(manifest, _path(archive), digest, missing)
                # Never replace a directory populated by another process since
                # the user selected it. Only a still-empty target may be removed.
                if existed:
                    new_home.rmdir()
                elif new_home.exists():
                    raise BackupError("Le dossier de restauration existe maintenant ; choisissez-en un autre.")
                try:
                    stage.rename(new_home)
                except BaseException:
                    if existed and not new_home.exists():
                        new_home.mkdir(mode=0o700)
                    raise
            result.update(home=str(new_home), missing_audio=missing)
            return result
    except BackupError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile):
        raise BackupError("La restauration n’a pas abouti ; l’installation actuelle est conservée.") from None
