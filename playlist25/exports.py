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
from .library import LibraryError, resolve_audio_path, safe_filename, verify_audio


class ExportError(ValueError):
    """A portable pack could not be prepared safely."""


CATALOGUE_FIELDS = ("id", "source_id", "title", "artist", "channel", "nickname", "state", "status", "votes", "duration", "url", "created_at")


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
            verified = verify_audio(original, cfg, item.get("duration"))
            item["duration"] = verified["duration"]
            filename = safe_filename(source_id, item["title"], original.suffix)
            item["votes"] = conn.execute("""
                SELECT COUNT(DISTINCT v.guest_id) FROM votes v
                JOIN proposals p ON p.id=v.proposal_id WHERE p.source_id=?
            """, (source_id,)).fetchone()[0]
            total_audio += original.stat().st_size
            originals[source_id] = original
            names[source_id] = filename
            selected.append(item)
        except (LibraryError, OSError) as exc:
            warnings.append(f"Source {source_id} ({_label(item)}) : {exc}.")
    if not selected:
        raise ExportError("Aucun fichier audio vérifié dans la sélection. " + " ".join(warnings[:5]))
    # Audio cannot compress predictably. Budget two copies, ZIP headers, metadata
    # and a reserve, rather than claiming the compressed size is known in advance.
    estimate = total_audio * (2 if make_zip else 1) + 2 * 1024 * 1024 + len(selected) * 4096
    _required_space(base, estimate, cfg)
    export_id = uuid.uuid4().hex
    directory = base / export_id
    archive = base / f"{export_id}.zip" if make_zip else None
    archive_partial = base / f".{export_id}.zip.part" if make_zip else None
    installed = False
    directory_created = False
    try:
        directory.mkdir()
        directory_created = True
        (directory / "audio").mkdir()
        imported_dir = directory / "playlists-importees"
        imported_dir.mkdir()
        for item in selected:
            source_id = item["id"]
            destination = directory / "audio" / names[source_id]
            shutil.copy2(originals[source_id], destination)
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
            with zipfile.ZipFile(archive_partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
                for path in sorted(directory.rglob("*")):
                    if path.is_file():
                        output.write(path, (Path("Anniversaire25") / path.relative_to(directory)).as_posix())
            os.replace(archive_partial, archive)
        conn.execute("INSERT INTO exports (id,directory,archive,created_at) VALUES (?,?,?,?)",
                     (export_id, str(directory), str(archive) if archive else None, now()))
        installed = True
        return {"id": export_id, "count": len(selected), "size": size, "warnings": warnings,
                "directory": str(directory), "archive": str(archive) if archive else None,
                "download_url": f"/api/exports/{export_id}/zip" if archive else None}
    except OSError as exc:
        raise ExportError("Impossible de préparer le pack : vérifiez l'espace disque et les droits du dossier d'export.") from exc
    finally:
        if not installed:
            # Only UUID destinations created by this function may be removed.
            if directory_created and directory.is_dir():
                shutil.rmtree(directory)
            if archive:
                archive.unlink(missing_ok=True)
        if archive_partial:
            archive_partial.unlink(missing_ok=True)
