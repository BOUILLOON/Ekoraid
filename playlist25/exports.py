"""Portable local audio packs and safely escaped catalogue exports."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid
import zipfile

from .db import list_proposals, now, source_dict
from .library import LibraryError, _confined, _copy_file, _discard_owned, _file_signature, _install_file, resolve_audio_path, safe_filename, verify_audio


class ExportError(ValueError):
    """A portable pack could not be prepared safely."""


CATALOGUE_FIELDS = ("id", "source_id", "title", "artist", "channel", "nickname", "state", "status", "votes", "duration", "url", "created_at")
_HISTORY_METADATA_LIMIT = 32 * 1024 * 1024


def list_ready_packs(conn: sqlite3.Connection, cfg) -> list[dict]:
    """Rediscover completed ZIPs without scanning their audio or exposing paths."""
    base = Path(cfg.export_dir).resolve()
    items = []
    private_paths = set()
    for value in (cfg.root, cfg.data_dir, cfg.audio_dir, cfg.temp_dir, cfg.export_dir):
        private = Path(value).resolve()
        text = str(private)
        private_paths.update((text, private.as_posix(), repr(text)[1:-1]))
    private_paths = sorted(private_paths, key=len, reverse=True)
    for row in conn.execute("SELECT id,archive,created_at FROM exports WHERE archive IS NOT NULL ORDER BY created_at DESC,id DESC"):
        export_id = row["id"]
        if not isinstance(export_id, str) or not export_id or len(export_id) > 80 or any(char not in "0123456789abcdef-" for char in export_id):
            continue
        try:
            path = Path(row["archive"])
            # Use the same direct-path confinement as the audio library. Old
            # restored metadata may refer to a previous installation: omit it.
            path = _confined(base, path.relative_to(base).as_posix())
            if path.suffix.lower() != ".zip":
                continue
            signature = _file_signature(path)
            if not signature[2]:
                continue
            count, warnings = None, []
            with path.open("rb") as raw:
                opened = os.fstat(raw.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != signature:
                    continue
                with zipfile.ZipFile(raw) as archive:
                    try:
                        metadata = archive.getinfo("Anniversaire25/manifeste.json")
                    except KeyError:
                        metadata = None
                    if metadata is not None and metadata.file_size <= _HISTORY_METADATA_LIMIT:
                        try:
                            with archive.open(metadata) as source:
                                manifest = json.loads(source.read(_HISTORY_METADATA_LIMIT + 1).decode("utf-8"))
                            if (isinstance(manifest, dict) and type(manifest.get("version")) is int and manifest["version"] == 1
                                    and type(manifest.get("count")) is int and manifest["count"] >= 0
                                    and isinstance(manifest.get("sources"), list) and len(manifest["sources"]) == manifest["count"]
                                    and isinstance(manifest.get("warnings"), list) and all(isinstance(warning, str) for warning in manifest["warnings"])):
                                count, warnings = manifest["count"], manifest["warnings"]
                        except (ValueError, UnicodeError, RuntimeError, KeyError):
                            pass  # Older/manual packs can still be downloaded.
            if _file_signature(path) != signature:
                continue
            # Operational diagnostics may originate in an older version which
            # included absolute local filenames. Keep these private in history.
            cleaned = []
            for warning in warnings:
                for private in private_paths:
                    warning = warning.replace(private, "[dossier local]")
                cleaned.append(warning)
            items.append({"id": export_id, "created_at": row["created_at"], "size": signature[2], "count": count,
                          "warnings": cleaned, "download_url": f"/api/exports/{export_id}/zip"})
        except (OSError, LibraryError, ValueError, zipfile.BadZipFile, RuntimeError, EOFError):
            continue
    return items


def _csv_cell(value) -> str:
    value = "" if value is None else str(value)
    # Whitespace is not a safe escape: spreadsheet readers may ignore it.
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def catalogue_export(conn: sqlite3.Connection, format: str) -> str:
    items = list_proposals(conn, include_hidden=True)
    if format == "json":
        return json.dumps(items, ensure_ascii=False, indent=2) + "\n"
    if format != "csv":
        raise ExportError("Choisissez le format CSV ou JSON.")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CATALOGUE_FIELDS)
    for item in items:
        writer.writerow([_csv_cell(item.get(key)) for key in CATALOGUE_FIELDS])
    return output.getvalue()


def _label(item: dict) -> str:
    artist = str(item.get("artist") or "")
    title = str(item.get("title") or "Sans titre")
    label = f"{artist} - {title}" if artist else title
    # EXTINF metadata occupies one line; it must never add playlist directives.
    return " ".join("".join(c if c.isprintable() else " " for c in label).split())


def _write_playlist(path: Path, items: list[dict], names: dict[int, str], prefix: str = "audio/") -> None:
    lines = ["#EXTM3U"]
    for item in items:
        source_id = item["id"]
        if source_id not in names:
            continue
        duration = round(item["duration"]) if item.get("duration") is not None else -1
        lines.extend([f"#EXTINF:{duration},{_label(item)}", prefix + names[source_id]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _playlist_filename(playlist_id: int, title: str) -> str:
    # Reuse the tested Windows sanitizer, retaining a stable playlist id.
    name = safe_filename(playlist_id, title, "mp3")
    return "playlist" + name.removeprefix("source").removesuffix(".mp3") + ".m3u8"


def _required_space(directory: Path, size: int, cfg) -> None:
    minimum = int(cfg.min_free_mb) * 1024 * 1024
    if shutil.disk_usage(directory).free < size + minimum:
        raise ExportError(f"Espace disque insuffisant : le pack nécessite environ {size / (1024 * 1024):.1f} Mo supplémentaires, en plus de la réserve de sécurité.")


def prepare_pack(conn: sqlite3.Connection, cfg, source_ids: list[int] | None = None, make_zip: bool = True) -> dict:
    """Create an independent directory and optional disk-backed ZIP archive."""
    Path(cfg.export_dir).mkdir(parents=True, exist_ok=True)
    base = Path(cfg.export_dir).resolve()
    if source_ids is not None:
        if not isinstance(source_ids, (list, tuple)) or cfg.max_queue_size > 0 and len(source_ids) > int(cfg.max_queue_size):
            raise ExportError("La sélection dépasse la limite technique d'export.")
        if any(isinstance(sid, bool) or not isinstance(sid, int) or sid < 1 for sid in source_ids):
            raise ExportError("La sélection contient un identifiant invalide.")
        ids = list(dict.fromkeys(source_ids))
    else:
        # Moderator ordering first, then imported files which have no proposal.
        ids = [row[0] for row in conn.execute("""
            SELECT s.id FROM sources s LEFT JOIN proposals p ON p.source_id=s.id
            WHERE s.file_path IS NOT NULL
            GROUP BY s.id ORDER BY CASE WHEN MIN(p.sort_order) IS NULL THEN 1 ELSE 0 END, MIN(p.sort_order), s.id
        """)]
        if cfg.max_queue_size > 0 and len(ids) > int(cfg.max_queue_size):
            raise ExportError("La bibliothèque dépasse la limite technique d'export ; choisissez une sélection.")
    warnings = []
    selected = []
    originals = {}
    signatures = {}
    names = {}
    total_audio = 0
    for source_id in ids:
        row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            warnings.append(f"Source {source_id} : introuvable.")
            continue
        item = source_dict(row)
        try:
            if not item.get("file_path"):
                raise LibraryError("aucun fichier disponible")
            original = resolve_audio_path(cfg, item["file_path"])
            signature = _file_signature(original)
            verified = verify_audio(original, cfg, item.get("duration"))
            if _file_signature(original) != signature:
                raise LibraryError("le fichier audio a changé pendant sa vérification")
            item["duration"] = verified["duration"]
            filename = safe_filename(source_id, item["title"], original.suffix)
            item["votes"] = conn.execute("""
                SELECT COUNT(DISTINCT v.guest_id) FROM votes v
                JOIN proposals p ON p.id=v.proposal_id WHERE p.source_id=?
            """, (source_id,)).fetchone()[0]
            total_audio += signature[2]
            originals[source_id] = original
            signatures[source_id] = signature
            names[source_id] = filename
            selected.append(item)
        except (LibraryError, OSError) as exc:
            reason = str(exc) if isinstance(exc, LibraryError) else "fichier absent, inaccessible ou modifié"
            warnings.append(f"Source {source_id} ({_label(item)}) : {reason}.")
    if not selected:
        raise ExportError("Aucun fichier audio vérifié dans la sélection. " + " ".join(warnings[:5]))
    # Audio cannot compress predictably. Budget two copies, ZIP headers, metadata
    # and a reserve, rather than claiming the compressed size is known in advance.
    estimate = total_audio * (2 if make_zip else 1) + 2 * 1024 * 1024 + len(selected) * 4096
    _required_space(base, estimate, cfg)
    export_id = uuid.uuid4().hex
    if conn.execute("SELECT 1 FROM exports WHERE id=?", (export_id,)).fetchone():
        raise ExportError("Un export existe déjà avec cet identifiant ; réessayez.")
    directory = base / export_id
    archive = base / f"{export_id}.zip" if make_zip else None
    archive_partial = base / f".{export_id}.zip.part" if make_zip else None
    installed = False
    directory_created = False
    directory_identity = None
    archive_signature = None
    partial_created = False
    partial_identity = None
    savepoint = False
    try:
        directory.mkdir()
        directory_created = True
        info = directory.stat()
        directory_identity = info.st_dev, info.st_ino
        if archive is not None and archive.exists():
            raise ExportError("Un export existe déjà à cet emplacement ; réessayez.")
        (directory / "audio").mkdir()
        imported_dir = directory / "playlists-importees"
        imported_dir.mkdir()
        for item in selected:
            source_id = item["id"]
            destination = directory / "audio" / names[source_id]
            _copy_file(originals[source_id], destination, signatures[source_id])
            # Rechecking catches a missing/replaced/truncated original during copy.
            verify_audio(destination, cfg, item["duration"])
        _write_playlist(directory / "01-selection.m3u8", selected, names)
        ranked = sorted(enumerate(selected), key=lambda pair: (-pair[1]["votes"], pair[0]))
        _write_playlist(directory / "02-classement-votes.m3u8", [item for _, item in ranked], names)
        by_id = {item["id"]: item for item in selected}
        included_playlists = []
        for playlist in conn.execute("SELECT * FROM playlists ORDER BY id"):
            entries = list(conn.execute("SELECT * FROM playlist_entries WHERE playlist_id=? ORDER BY position", (playlist["id"],)))
            playlist_items = []
            playlist_warnings = []
            for entry in entries:
                if not entry["selected"]:
                    # Unavailable entries cannot be selected in the admin UI.
                    # They remain part of the imported playlist's error report.
                    if not entry["available"]:
                        reason = entry["reason"] or "élément indisponible"
                        playlist_warnings.append(f"Playlist {playlist['title']}, position {entry['position']} ({entry['title']}) : {reason}.")
                    continue
                source_id = entry["source_id"]
                if source_id in by_id:
                    playlist_items.append(by_id[source_id])
                else:
                    reason = entry["reason"] or "fichier absent de la sélection exportée"
                    playlist_warnings.append(f"Playlist {playlist['title']}, position {entry['position']} ({entry['title']}) : {reason}.")
            if playlist_items:
                filename = _playlist_filename(playlist["id"], playlist["title"])
                _write_playlist(imported_dir / filename, playlist_items, names, prefix="../audio/")
                included_playlists.append({"id": playlist["id"], "title": playlist["title"], "file": "playlists-importees/" + filename,
                                           "count": len(playlist_items), "analysis_complete": bool(playlist["analysis_complete"])})
                if not playlist["analysis_complete"]:
                    warnings.append(f"Playlist {playlist['title']} : l'analyse d'origine était incomplète.")
            # Missing/private entries still matter when this playlist has no
            # surviving files; report them rather than silently omitting them.
            warnings.extend(playlist_warnings)
        (directory / "catalogue.csv").write_text(catalogue_export(conn, "csv"), encoding="utf-8-sig", newline="")
        manifest = {
            "version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "event_name": cfg.event_name,
            "count": len(selected), "audio_bytes": total_audio,
            "sources": [{"id": item["id"], "title": item["title"], "artist": item.get("artist", ""),
                         "duration": item["duration"], "votes": item["votes"], "url": item.get("url"),
                         "file": "audio/" + names[item["id"]]} for item in selected],
            "playlists": included_playlists, "warnings": warnings,
        }
        (directory / "manifeste.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (directory / "rapport-erreurs.txt").write_text("\n".join(warnings) + "\n" if warnings else "Aucune erreur : tous les fichiers de ce pack ont été vérifiés.\n", encoding="utf-8")
        size = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
        if make_zip:
            _required_space(base, size + 1024 * 1024, cfg)
            # The exclusive partial file belongs only to this call. Publish
            # after ZipFile has written its central directory and closed cleanly.
            with archive_partial.open("xb") as raw:
                partial_created = True
                info = os.fstat(raw.fileno())
                partial_identity = info.st_dev, info.st_ino
                with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
                    for path in sorted(directory.rglob("*")):
                        if path.is_file():
                            name = (Path("Anniversaire25") / path.relative_to(directory)).as_posix()
                            with path.open("rb") as source, output.open(name, "w", force_zip64=True) as destination:
                                shutil.copyfileobj(source, destination, length=1024 * 1024)
                raw.flush()
                os.fsync(raw.fileno())
            _install_file(archive_partial, archive)
            archive_signature = _file_signature(archive)
        conn.execute("SAVEPOINT export_publish")
        savepoint = True
        conn.execute("INSERT INTO exports (id,directory,archive,created_at) VALUES (?,?,?,?)",
                     (export_id, str(directory), str(archive) if archive else None, now()))
        conn.execute("RELEASE SAVEPOINT export_publish")
        savepoint = False
        installed = True
        return {"id": export_id, "count": len(selected), "size": size, "warnings": warnings,
                "directory": str(directory), "archive": str(archive) if archive else None,
                "download_url": f"/api/exports/{export_id}/zip" if archive else None}
    except OSError as exc:
        raise ExportError("Impossible de préparer le pack : vérifiez l'espace disque et les droits du dossier d'export.") from exc
    except LibraryError as exc:
        raise ExportError("Impossible de préparer le pack : " + str(exc)) from exc
    except (sqlite3.Error, zipfile.BadZipFile, RuntimeError) as exc:
        raise ExportError("Le pack n’a pas pu être enregistré ; les fichiers de la bibliothèque sont conservés. Réessayez après les diagnostics.") from exc
    finally:
        if savepoint:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT export_publish")
                conn.execute("RELEASE SAVEPOINT export_publish")
            except sqlite3.Error:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
        if not installed and directory_created:
            try:
                saved = conn.execute("SELECT directory,archive FROM exports WHERE id=?", (export_id,)).fetchone()
                installed = bool(saved and saved["directory"] == str(directory) and saved["archive"] == (str(archive) if archive else None))
            except sqlite3.Error:
                # An uncertain DB outcome must not destroy a fully published
                # ZIP that may already be referenced by a durable export row.
                installed = savepoint and archive_signature is not None
        if not installed:
            # Only UUID destinations created by this function may be removed.
            if directory_created:
                try:
                    info = directory.lstat()
                    if (info.st_dev, info.st_ino) == directory_identity and not directory.is_symlink():
                        shutil.rmtree(directory)
                except OSError:
                    pass
            if archive_signature is not None:
                _discard_owned(archive, archive_signature)
        if partial_created:
            try:
                signature = _file_signature(archive_partial)
                if signature[:2] == partial_identity:
                    _discard_owned(archive_partial, signature)
            except (OSError, LibraryError):
                pass
