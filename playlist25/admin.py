"""Private administration surface. This app is bound to loopback by the launcher."""
from __future__ import annotations

import copy
import hashlib
import hmac
import importlib.metadata
import json
import math
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartException

from . import db, exports, library, providers
from .config import Settings
from .guest_playlists import classify_source
from .security import csrf_token, install_session, rate_limit, require_admin, require_csrf
from .queue import enqueue_sources, queue_snapshot


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    password: str = Field(min_length=1, max_length=1024)


class ProposalPatch(Input):
    state: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)
    artist: str | None = Field(default=None, max_length=300)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    sort_order: int | None = Field(default=None, ge=-1000000, le=1000000)


class ProposalBatchPatch(Input):
    proposal_ids: list[StrictInt] = Field(min_length=1)
    state: str


class ProposalOrderInput(Input):
    proposal_ids: list[StrictInt] = Field(min_length=1)


class MergeInput(Input):
    keep_id: StrictInt = Field(gt=0)
    merge_id: StrictInt = Field(gt=0)
    confirmed: StrictBool = False


class InvitationInput(Input):
    count: int = Field(default=1, ge=1, le=100)


class AnalyzeInput(Input):
    url: str = Field(min_length=1, max_length=2048)
    mode: str | None = None

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str | None) -> str | None:
        if value not in (None, "video", "playlist"):
            raise ValueError("Choisissez « video » ou « playlist ».")
        return value


class LinksInput(Input):
    text: str = Field(min_length=1, max_length=1000000)


class PlaylistImport(Input):
    analysis: dict[str, Any]
    positions: list[StrictInt] | None = None
    download: StrictBool = False
    rights_confirmed: StrictBool = False


class DownloadInput(Input):
    source_ids: list[StrictInt] = Field(min_length=1)
    rights_confirmed: StrictBool = False


class RetryInput(Input):
    source_ids: list[StrictInt] | None = None
    rights_confirmed: StrictBool = False


class QueueInput(Input):
    paused: StrictBool


class LibraryPatch(Input):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    artist: str | None = Field(default=None, max_length=300)


class ExportInput(Input):
    source_ids: list[StrictInt] | None = None
    make_zip: StrictBool = True


class SettingsPatch(Input):
    event_name: str | None = Field(default=None, min_length=1, max_length=150)
    proposals_open: StrictBool | None = None
    votes_open: StrictBool | None = None
    offline_mode: StrictBool | None = None
    invitation_required: StrictBool | None = None
    public_url: str | None = Field(default=None, max_length=2048)
    local_url: str | None = Field(default=None, min_length=1, max_length=2048)
    max_playlist_items: int | None = Field(default=None, ge=0)
    max_proposals: int | None = Field(default=None, ge=0, le=1000)
    output_format: str | None = None

    @field_validator("output_format")
    @classmethod
    def valid_format(cls, value: str | None) -> str | None:
        if value not in (None, "mp3", "source"):
            raise ValueError("Le format doit être mp3 ou source.")
        return value

    @field_validator("public_url", "local_url")
    @classmethod
    def valid_link(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        parts = urlsplit(value.strip())
        if (parts.scheme not in ("https", "http") or not parts.hostname
                or parts.username or parts.password or parts.fragment):
            raise ValueError("Utilisez une adresse HTTP ou HTTPS sans identifiants ni fragment.")
        return value.strip().rstrip("/")


_SETTINGS_DEFAULTS = {
    "proposals_open": True,
    "votes_open": True,
    "offline_mode": False,
    "invitation_required": False,
}


class _UploadRequestLimit:
    """Bound multipart input before Starlette writes uploaded files to its spool."""

    def __init__(self, app: Any, maximum: int):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") != "/api/library/import":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length")
        if length:
            try:
                too_large = int(length) > self.maximum
            except ValueError:
                await JSONResponse({"detail": "Taille de requête invalide."}, status_code=400)(scope, receive, send)
                return
            if too_large:
                await JSONResponse({"detail": "Le fichier dépasse la taille maximale autorisée."}, status_code=413)(scope, receive, send)
                return
        received = 0
        exceeded = False
        replaced_body = False
        rejection = json.dumps({"detail": "Le fichier dépasse la taille maximale autorisée."}, ensure_ascii=False).encode("utf-8")

        async def bounded_receive() -> dict:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.maximum:
                    exceeded = True
                    # The multipart parser closes any partial spool files for
                    # MultiPartException. Its ordinary 400 is mapped to 413 below.
                    raise MultiPartException("Le fichier dépasse la taille maximale autorisée.")
            return message

        async def bounded_send(message: dict) -> None:
            nonlocal replaced_body
            if exceeded and message["type"] == "http.response.start":
                message = {"type": "http.response.start", "status": 413,
                           "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                       (b"content-length", str(len(rejection)).encode("ascii"))]}
            elif exceeded and message["type"] == "http.response.body":
                if replaced_body:
                    return
                replaced_body = True
                message = {"type": "http.response.body", "body": rejection, "more_body": False}
            await send(message)

        await self.app(scope, bounded_receive, bounded_send)


@contextmanager
def _connection(cfg: Settings):
    conn = db.connect(cfg)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _settings(conn: sqlite3.Connection, cfg: Settings) -> dict[str, Any]:
    values = dict(_SETTINGS_DEFAULTS)
    for key in ("event_name", "public_url", "local_url", "max_playlist_items",
                "max_proposals", "output_format"):
        values[key] = getattr(cfg, key)
    return {key: db.get_setting(conn, key, value) for key, value in values.items()}


def _effective_cfg(conn: sqlite3.Connection, cfg: Settings) -> Settings:
    return replace(cfg, max_playlist_items=int(db.get_setting(conn, "max_playlist_items", cfg.max_playlist_items)),
                   output_format=db.get_setting(conn, "output_format", cfg.output_format))


def _online(conn: sqlite3.Connection) -> None:
    if db.get_setting(conn, "offline_mode", False):
        raise HTTPException(503, "Le mode soirée hors ligne désactive les recherches et les nouveaux téléchargements externes.")


def _clean_text(value: str, label: str = "Le texte") -> str:
    value = value.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(422, f"{label} contient un caractère de contrôle.")
    return value


def _source_ids(ids: list[int], cfg: Settings) -> list[int]:
    if any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in ids):
        raise HTTPException(422, "La sélection contient un identifiant de morceau invalide.")
    ids = list(dict.fromkeys(ids))
    if cfg.max_queue_size and len(ids) > cfg.max_queue_size:
        raise HTTPException(422, f"Sélection limitée à {cfg.max_queue_size} morceaux par opération.")
    return ids


def _existing_sources(conn: sqlite3.Connection, ids: list[int]) -> None:
    # Individual selects avoid SQLite's variable-count limit for large selections.
    if any(conn.execute("SELECT 1 FROM sources WHERE id=?", (i,)).fetchone() is None for i in ids):
        raise HTTPException(404, "Un morceau sélectionné n'existe plus. Actualisez la sélection.")


def _provider_error(exc: Exception) -> HTTPException:
    if getattr(exc, "need_choice", False) or getattr(exc, "code", "") == "need_choice":
        return HTTPException(409, {"need_choice": True,
                                   "message": "Ce lien contient une vidéo et une playlist. Choisissez le morceau ou toute la playlist."})
    status = getattr(exc, "status", 400)
    if not isinstance(status, int) or not 400 <= status <= 599:
        status = 400
    return HTTPException(status, str(exc))


def _validated_playlist_analysis(result: Any, cfg: Settings, source_url: str) -> dict[str, Any]:
    """Validate the complete provider result before writing any playlist rows."""
    if not isinstance(result, dict) or result.get("kind") != "playlist" or not isinstance(result.get("entries"), list):
        raise ValueError("Le fournisseur a retourné une analyse de playlist invalide.")
    result = copy.deepcopy(result)
    if cfg.max_playlist_items and len(result["entries"]) > cfg.max_playlist_items:
        raise ValueError("L’analyse dépasse la limite de playlist configurée.")
    positions = set()
    for entry in result["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("L’analyse contient une entrée invalide.")
        position = entry.get("position")
        if not isinstance(position, int) or isinstance(position, bool) or position < 1 or position in positions:
            raise ValueError("L’analyse contient des positions invalides ou répétées.")
        positions.add(position)
        if entry.get("available"):
            parsed = providers.parse_youtube_url(entry.get("url", ""), mode="video")
            if not parsed.get("video_id") or parsed["video_id"] != entry.get("video_id"):
                raise ValueError("Une vidéo analysée ne correspond pas à sa source.")
            entry["url"] = parsed["url"]
            duration = entry.get("duration")
            if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                         or not math.isfinite(duration) or duration < 0):
                raise ValueError("Une vidéo analysée contient une durée invalide.")
            # Status is derived from our queue and verified files, never metadata.
            entry.pop("status", None)
    result["title"] = str(result.get("title") or "Playlist YouTube")[:500]
    result["source_url"] = source_url
    result["complete"] = bool(result.get("complete", False))
    warnings = result.get("warnings", [])
    result["warnings"] = [str(warning)[:2000] for warning in (warnings if isinstance(warnings, list) else [warnings])]
    return result


def _store_playlist(conn: sqlite3.Connection, analysis: dict[str, Any], selected: set[int]) -> tuple[int, list[int]]:
    playlist_id = conn.execute("INSERT INTO playlists(title,source_url,analysis_complete,warnings,created_at) VALUES(?,?,?,?,?)",
                               (str(analysis.get("title") or "Playlist importée")[:500], str(analysis.get("source_url") or "")[:1000000],
                                int(analysis.get("complete", False)), json.dumps(analysis.get("warnings", []), ensure_ascii=False), db.now())).lastrowid
    ids = []
    for entry in analysis["entries"]:
        source_id = db.upsert_source(conn, entry) if entry.get("available") else None
        chosen = entry["position"] in selected and bool(entry.get("available"))
        if chosen:
            ids.append(source_id)
        conn.execute("INSERT INTO playlist_entries(playlist_id,position,source_id,title,available,reason,selected) VALUES(?,?,?,?,?,?,?)",
                     (playlist_id, entry["position"], source_id, str(entry.get("title") or "Élément indisponible")[:1000],
                      int(bool(entry.get("available"))), str(entry.get("reason") or "")[:2000], int(chosen)))
    return playlist_id, list(dict.fromkeys(ids))


def _present(conn: sqlite3.Connection, cfg: Settings, row: Any) -> bool:
    if not row or not row["file_path"]:
        return False
    try:
        path = library.resolve_audio_path(cfg, row["file_path"])
        return path.is_file() and path.stat().st_size > 0
    except (ValueError, OSError):
        return False


def _version(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        output = (result.stdout or result.stderr).splitlines()
        return {"available": result.returncode == 0, "version": output[0][:350] if output else "",
                "error": "" if result.returncode == 0 else "Commande indisponible ou en échec."}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "version": "", "error": str(exc)}


def _diagnostics(cfg: Settings) -> dict[str, Any]:
    try:
        ytdlp = {"available": True, "version": importlib.metadata.version("yt-dlp"), "error": ""}
    except importlib.metadata.PackageNotFoundError:
        ytdlp = {"available": False, "version": "", "error": "Installez les dépendances Python du projet."}
    try:
        components = {"available": True, "version": importlib.metadata.version("yt-dlp-ejs"), "error": ""}
    except importlib.metadata.PackageNotFoundError:
        components = {"available": False, "version": "", "error": "Réinstallez yt-dlp[default] pour fournir les composants JavaScript nécessaires à YouTube."}
    writable: dict[str, bool] = {}
    for label in ("data_dir", "audio_dir", "temp_dir", "export_dir"):
        path = Path(getattr(cfg, label))
        probe = path / (".write-check-" + uuid.uuid4().hex)
        try:
            path.mkdir(parents=True, exist_ok=True)
            with probe.open("xb") as handle:
                handle.write(b"ok")
            probe.unlink()
            writable[label] = True
        except OSError:
            writable[label] = False
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        usage = shutil.disk_usage(cfg.data_dir)
        disk = {"total": usage.total, "used": usage.used, "free": usage.free,
                "minimum_free": cfg.min_free_mb * 1024 * 1024,
                "enough_space": usage.free >= cfg.min_free_mb * 1024 * 1024}
    except OSError:
        disk = {"enough_space": False, "error": "Impossible de mesurer l'espace disque."}
    with _connection(cfg) as conn:
        snapshot = queue_snapshot(conn)
        queue = {"items": snapshot, "paused": db.get_setting(conn, "queue_paused", False)}
        offline = db.get_setting(conn, "offline_mode", False)
    return {"python": {"available": sys.version_info >= (3, 12), "version": sys.version.split()[0]},
            "yt_dlp": ytdlp, "yt_dlp_ejs": components, "ffmpeg": _version([cfg.ffmpeg_path, "-version"]),
            "ffprobe": _version([cfg.ffprobe_path, "-version"]),
            "javascript": _version([cfg.js_runtime, "--version"]),
            "writeable": writable, "disk": disk, "queue": queue, "offline": offline,
            "guest_url": cfg.local_url, "admin_url": f"http://127.0.0.1:{cfg.admin_port}",
            "note": "Ces contrôles locaux ne vérifient pas l'accès actuel à YouTube. Aucun téléchargement n'est lancé."}


def create_admin_app(cfg: Settings) -> FastAPI:
    app = FastAPI(title="La playlist des 25 ans — administration", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg
    install_session(app, cfg, "playlist_admin")
    app.add_middleware(_UploadRequestLimit, maximum=cfg.max_upload_mb * 1024 * 1024 + 65536)
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Results are bound to the authenticated browser, expire, and are never accepted
    # from a client-created object. Cache eviction bounds metadata memory usage.
    analyses: dict[str, tuple[float, str, dict[str, Any]]] = {}
    analysis_lock = threading.Lock()

    def admin(request: Request) -> None:
        require_admin(request, cfg)

    def mutation(request: Request) -> None:
        require_admin(request, cfg)
        require_csrf(request)

    protected = [Depends(admin)]
    modifying = [Depends(mutation)]

    def cache_analysis(request: Request, result: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(result)
        if not isinstance(result.get("entries"), list):
            raise HTTPException(502, "L'analyse du fournisseur est invalide.")
        with _connection(cfg) as conn:
            limit = int(db.get_setting(conn, "max_playlist_items", cfg.max_playlist_items))
            if limit and len(result["entries"]) > limit:
                raise HTTPException(422, f"L'analyse dépasse la limite de {limit} éléments. Relevez-la explicitement dans les réglages.")
            positions: set[int] = set()
            for entry in result["entries"]:
                position = entry.get("position")
                if not isinstance(position, int) or isinstance(position, bool) or position < 1 or position in positions:
                    raise HTTPException(502, "L'analyse contient des positions invalides.")
                positions.add(position)
                if entry.get("available"):
                    try:
                        parsed = providers.parse_youtube_url(entry.get("url", ""), mode="video")
                    except ValueError as exc:
                        raise HTTPException(502, "Le fournisseur a retourné une source invalide.") from exc
                    if not parsed.get("video_id") or parsed["video_id"] != entry.get("video_id"):
                        raise HTTPException(502, "L'identifiant vidéo ne correspond pas à sa source.")
                    entry["url"] = parsed["url"]
                    duration = entry.get("duration")
                    if duration is not None and (not isinstance(duration, (int, float)) or isinstance(duration, bool)
                                                 or not math.isfinite(duration) or duration < 0):
                        raise HTTPException(502, "La durée retournée par le fournisseur est invalide.")
                    row = conn.execute("SELECT * FROM sources WHERE video_id=?", (entry["video_id"],)).fetchone()
                    entry["already_available"] = _present(conn, cfg, row)
                    entry["source_id"] = row["id"] if row else None
                else:
                    entry["already_available"] = False
            result["complete"] = bool(result.get("complete", False))
            result["warnings"] = result.get("warnings", [])
            if not isinstance(result["warnings"], list):
                result["warnings"] = [str(result["warnings"])]
        owner = request.session.setdefault("analysis_owner", secrets.token_urlsafe(24))
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with analysis_lock:
            for old in list(analyses):
                if analyses[old][0] < now - 1800:
                    del analyses[old]
            while len(analyses) >= 30:
                del analyses[min(analyses, key=lambda key: analyses[key][0])]
            analyses[token] = (now, owner, copy.deepcopy(result))
        result["token"] = token
        return result

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "admin.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "surface": "admin"}

    @app.get("/api/session")
    def session(request: Request) -> dict[str, Any]:
        authenticated = request.session.get("admin") is True
        result: dict[str, Any] = {"authenticated": authenticated, "csrf_token": csrf_token(request)}
        if authenticated:
            with _connection(cfg) as conn:
                result["settings"] = _settings(conn, cfg)
        else:
            result["settings"] = {"event_name": cfg.event_name}
        return result

    @app.post("/api/login")
    def login(payload: LoginInput, request: Request) -> dict[str, Any]:
        require_csrf(request)
        rate_limit(request, "admin_login", 8, window=60)
        if not cfg.admin_password or not hmac.compare_digest(payload.password.encode(), cfg.admin_password.encode()):
            raise HTTPException(401, "Mot de passe administrateur incorrect.")
        request.session.clear()
        request.session["admin"] = True
        request.session["analysis_owner"] = secrets.token_urlsafe(24)
        return {"authenticated": True, "csrf_token": csrf_token(request)}

    @app.post("/api/logout", dependencies=modifying)
    def logout(request: Request) -> dict[str, Any]:
        owner = request.session.get("analysis_owner")
        with analysis_lock:
            for token in list(analyses):
                if analyses[token][1] == owner:
                    del analyses[token]
        request.session.clear()
        return {"success": True, "csrf_token": csrf_token(request)}

    @app.get("/api/dashboard", dependencies=protected)
    def dashboard() -> dict[str, Any]:
        with _connection(cfg) as conn:
            states = {row["state"]: row["n"] for row in conn.execute("SELECT state,COUNT(*) n FROM proposals GROUP BY state")}
            jobs = {row["status"]: row["n"] for row in conn.execute("SELECT status,COUNT(*) n FROM jobs GROUP BY status")}
            duration = conn.execute("SELECT SUM(duration) FROM sources WHERE id IN (SELECT source_id FROM proposals WHERE state='approved')").fetchone()[0]
            return {"proposals": sum(states.values()), "pending": states.get("pending", 0),
                    "approved": states.get("approved", 0), "rejected": states.get("rejected", 0),
                    "archived": states.get("archived", 0), "available": len(library.available_sources(conn, cfg)),
                    "votes": conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0],
                    "total_duration": duration or 0,
                    "jobs": {"queued": jobs.get("queued", 0),
                             "active": sum(jobs.get(key, 0) for key in ("downloading", "converting", "verifying")),
                             "failed": sum(jobs.get(key, 0) for key in ("failed", "error", "unavailable", "interrupted")),
                             "available": jobs.get("available", 0)},
                    "playlists": conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0],
                    "guests": conn.execute("SELECT COUNT(*) FROM guests").fetchone()[0]}

    @app.get("/api/proposals", dependencies=protected)
    def proposals_list() -> dict[str, Any]:
        with _connection(cfg) as conn:
            items = db.list_proposals(conn, include_hidden=True)
            playlist_sources = {}
            for item in items:
                item["source_kind"] = classify_source(item)
                if item["source_kind"] == "playlist":
                    url = item["url"]
                    if url not in playlist_sources:
                        latest = conn.execute("SELECT id FROM playlists WHERE source_url=? ORDER BY id DESC LIMIT 1", (url,)).fetchone()
                        playlist_sources[url] = [row[0] for row in conn.execute("SELECT DISTINCT source_id FROM playlist_entries WHERE playlist_id=? AND selected=1 AND available=1 AND source_id IS NOT NULL", (latest["id"],))] if latest else []
                    item["playlist_source_ids"] = playlist_sources[url]
            return {"items": items}

    @app.patch("/api/proposals", dependencies=modifying)
    def proposals_update(payload: ProposalBatchPatch) -> dict[str, Any]:
        if payload.state not in ("pending", "approved", "rejected", "archived"):
            raise HTTPException(422, "État de modération invalide.")
        if any(proposal_id <= 0 for proposal_id in payload.proposal_ids):
            raise HTTPException(422, "Identifiant de proposition invalide.")
        ids = list(dict.fromkeys(payload.proposal_ids))
        with _connection(cfg) as conn:
            conn.execute("BEGIN IMMEDIATE")
            chunks = [ids[offset:offset + 900] for offset in range(0, len(ids), 900)]
            rows = []
            for chunk in chunks:
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(conn.execute(f"SELECT id,withdrawn_at FROM proposals WHERE id IN ({placeholders})", chunk).fetchall())
            if len(rows) != len(ids):
                raise HTTPException(404, "Une proposition de la sélection est introuvable. Actualisez la liste.")
            if payload.state != "archived" and any(row["withdrawn_at"] for row in rows):
                raise HTTPException(409, "Une proposition a été retirée par l’invité·e. Son retrait reste enregistré ; aucune proposition de cette sélection n’a été modifiée.")
            for chunk in chunks:
                placeholders = ",".join("?" for _ in chunk)
                conn.execute(f"UPDATE proposals SET state=? WHERE id IN ({placeholders})", [payload.state, *chunk])
            conn.commit()
            return {"count": len(ids), "state": payload.state}

    @app.put("/api/proposals/order", dependencies=modifying)
    def proposals_order(payload: ProposalOrderInput) -> dict[str, int]:
        ids = payload.proposal_ids
        if any(proposal_id <= 0 for proposal_id in ids) or len(ids) != len(set(ids)):
            raise HTTPException(422, "L’ordre contient des identifiants invalides ou répétés.")
        with _connection(cfg) as conn:
            conn.execute("BEGIN IMMEDIATE")
            found = 0
            for offset in range(0, len(ids), 900):
                chunk = ids[offset:offset + 900]
                placeholders = ",".join("?" for _ in chunk)
                found += conn.execute(f"SELECT COUNT(*) FROM proposals WHERE id IN ({placeholders})", chunk).fetchone()[0]
            if found != len(ids):
                raise HTTPException(404, "Une proposition de l’ordre est introuvable. Actualisez la liste ; aucun ordre n’a été modifié.")
            conn.executemany("UPDATE proposals SET sort_order=? WHERE id=?", ((position, proposal_id) for position, proposal_id in enumerate(ids)))
            conn.commit()
            return {"count": len(ids)}

    @app.patch("/api/proposals/{proposal_id}", dependencies=modifying)
    def proposal_update(proposal_id: int, payload: ProposalPatch) -> dict[str, Any]:
        changes = payload.model_dump(exclude_unset=True)
        if not changes or any(value is None for value in changes.values()):
            raise HTTPException(422, "Indiquez au moins un champ avec une valeur valide.")
        if "state" in changes and changes["state"] not in ("pending", "approved", "rejected", "archived"):
            raise HTTPException(422, "État de modération invalide.")
        for key in ("title", "artist"):
            if key in changes:
                changes[key] = _clean_text(changes[key], "Le titre" if key == "title" else "L'artiste")
                if key == "title" and not changes[key]:
                    raise HTTPException(422, "Le titre est obligatoire.")
        warnings: list[str] = []
        with _connection(cfg) as conn:
            conn.execute("BEGIN IMMEDIATE")
            proposal = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not proposal:
                raise HTTPException(404, "Proposition introuvable.")
            if ("withdrawn_at" in proposal.keys() and proposal["withdrawn_at"]
                    and changes.get("state", "archived") != "archived"):
                raise HTTPException(409, "Cette proposition a été retirée par l’invité·e. Son retrait reste enregistré ; ses métadonnées peuvent encore être corrigées.")
            source_id = proposal["source_id"]
            if "url" in changes:
                _online(conn)
                try:
                    result = providers.analyze(changes["url"], _effective_cfg(conn, cfg), mode="video")
                except ValueError as exc:
                    raise _provider_error(exc) from exc
                entries = [entry for entry in result.get("entries", []) if entry.get("available")]
                if result.get("kind") not in ("video", "single") or len(entries) != 1:
                    raise HTTPException(422, "Associez une vidéo précise à cette proposition.")
                entry = entries[0]
                parsed = providers.parse_youtube_url(entry.get("url", ""), mode="video")
                if not parsed.get("video_id") or parsed["video_id"] != entry.get("video_id"):
                    raise HTTPException(502, "La source résolue est invalide.")
                entry["url"] = parsed["url"]
                source_id = db.upsert_source(conn, entry)
                conflict = conn.execute("SELECT id FROM proposals WHERE source_id=? AND guest_id IS ? AND id<>?", (source_id, proposal["guest_id"], proposal_id)).fetchone()
                if conflict:
                    raise HTTPException(409, {"message": "Cette personne a déjà une proposition pour cette source. Fusionnez après confirmation.", "duplicate_id": conflict["id"]})
                if source_id != proposal["source_id"]:
                    warnings.append("La proposition est désormais associée à la source choisie ; son ancien fichier est conservé.")
                same = conn.execute("SELECT COUNT(*) FROM proposals WHERE source_id=? AND id<>?", (source_id, proposal_id)).fetchone()[0]
                if same:
                    warnings.append("Cette source figure également dans d'autres propositions. Une fusion reste une action manuelle.")
                if _present(conn, cfg, conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()):
                    warnings.append("Le fichier de cette source est déjà disponible dans la bibliothèque.")
            source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            title = changes.get("title", source["title"])
            artist = changes.get("artist", source["artist"])
            if "title" in changes or "artist" in changes:
                if _present(conn, cfg, source):
                    try:
                        library.rename_audio(conn, cfg, source_id, title, artist)
                    except (ValueError, OSError) as exc:
                        raise HTTPException(422, str(exc)) from exc
                else:
                    conn.execute("UPDATE sources SET title=?,artist=? WHERE id=?", (title, artist, source_id))
            conn.execute("UPDATE proposals SET source_id=?,state=?,sort_order=? WHERE id=?",
                         (source_id, changes.get("state", proposal["state"]), changes.get("sort_order", proposal["sort_order"]), proposal_id))
            conn.commit()
            item = next(item for item in db.list_proposals(conn, include_hidden=True) if item["id"] == proposal_id)
            return {"item": item, "warnings": warnings}

    @app.post("/api/proposals/merge", dependencies=modifying)
    def merge_proposals(payload: MergeInput) -> dict[str, Any]:
        if not payload.confirmed:
            raise HTTPException(422, "Confirmez explicitement la fusion des deux propositions.")
        if payload.keep_id == payload.merge_id:
            raise HTTPException(422, "Choisissez deux propositions distinctes.")
        with _connection(cfg) as conn:
            conn.execute("BEGIN IMMEDIATE")
            keep = conn.execute("SELECT * FROM proposals WHERE id=?", (payload.keep_id,)).fetchone()
            merged = conn.execute("SELECT * FROM proposals WHERE id=?", (payload.merge_id,)).fetchone()
            if not keep or not merged:
                raise HTTPException(404, "Une des propositions est introuvable.")
            if keep["state"] == "archived" or merged["state"] == "archived":
                raise HTTPException(409, "Une proposition archivée ne peut pas être fusionnée. Réactivez-la explicitement si nécessaire.")
            conn.execute("INSERT OR IGNORE INTO votes(proposal_id,guest_id,created_at) SELECT ?,guest_id,created_at FROM votes WHERE proposal_id=?", (payload.keep_id, payload.merge_id))
            conn.execute("DELETE FROM votes WHERE proposal_id=?", (payload.merge_id,))
            conn.execute("UPDATE proposals SET state='archived' WHERE id=?", (payload.merge_id,))
            conn.execute("UPDATE cloud_map SET local_id=CAST(? AS TEXT) WHERE kind='proposal' AND local_id=CAST(? AS TEXT)",
                         (payload.keep_id, payload.merge_id))
            conn.commit()
            return {"keep_id": payload.keep_id, "archived_id": payload.merge_id,
                    "votes": conn.execute("SELECT COUNT(*) FROM votes WHERE proposal_id=?", (payload.keep_id,)).fetchone()[0],
                    "message": "Votes réunis, seconde proposition archivée. Les sources et fichiers audio sont conservés."}

    @app.get("/api/settings", dependencies=protected)
    def settings_get() -> dict[str, Any]:
        with _connection(cfg) as conn:
            return _settings(conn, cfg)

    @app.patch("/api/settings", dependencies=modifying)
    def settings_update(payload: SettingsPatch) -> dict[str, Any]:
        values = payload.model_dump(exclude_unset=True)
        if not values or any(value is None for value in values.values()):
            raise HTTPException(422, "Indiquez au moins un réglage valide.")
        if "event_name" in values:
            values["event_name"] = _clean_text(values["event_name"], "Le nom de l'événement")
            if not values["event_name"]:
                raise HTTPException(422, "Le nom de l'événement est obligatoire.")
        with _connection(cfg) as conn:
            for key, value in values.items():
                db.set_setting(conn, key, value)
            conn.commit()
            return _settings(conn, cfg)

    @app.post("/api/invitations", dependencies=modifying)
    def invitations(payload: InvitationInput) -> dict[str, Any]:
        codes: list[str] = []
        with _connection(cfg) as conn:
            for _ in range(payload.count):
                code = secrets.token_hex(6).upper()
                code = "-".join(code[index:index + 4] for index in range(0, len(code), 4))
                digest = hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()
                conn.execute("INSERT INTO invitations(code_hash,guest_id,created_at) VALUES(?,NULL,?)", (digest, db.now()))
                codes.append(code)
            conn.commit()
        return {"codes": codes, "message": "Conservez ces codes maintenant : seule leur empreinte est enregistrée."}

    @app.post("/api/playlists/analyze", dependencies=modifying)
    def playlist_analyze(payload: AnalyzeInput, request: Request) -> dict[str, Any]:
        rate_limit(request, "admin_analysis", 20, window=60)
        with _connection(cfg) as conn:
            _online(conn)
            effective = _effective_cfg(conn, cfg)
        try:
            result = providers.analyze(payload.url, effective, mode=payload.mode)
        except ValueError as exc:
            raise _provider_error(exc) from exc
        return cache_analysis(request, result)

    @app.post("/api/links/analyze", dependencies=modifying)
    def links_analyze(payload: LinksInput, request: Request) -> dict[str, Any]:
        rate_limit(request, "admin_analysis", 20, window=60)
        with _connection(cfg) as conn:
            _online(conn)
            effective = _effective_cfg(conn, cfg)
        try:
            result = providers.analyze_links(payload.text, effective)
        except ValueError as exc:
            raise _provider_error(exc) from exc
        return cache_analysis(request, result)

    @app.post("/api/playlists/import", dependencies=modifying)
    def playlist_import(payload: PlaylistImport, request: Request) -> dict[str, Any]:
        token = payload.analysis.get("token")
        owner = request.session.get("analysis_owner")
        if not isinstance(token, str) or not owner:
            raise HTTPException(422, "Analysez les liens avant de les importer.")
        with analysis_lock:
            saved = analyses.get(token)
            if not saved or saved[0] < time.monotonic() - 1800 or saved[1] != owner:
                raise HTTPException(410, "L'analyse a expiré ou appartient à une autre session. Relancez l'analyse.")
            analysis = copy.deepcopy(saved[2])
        # A token-only request is valid. If the client sends the displayed result,
        # it must still be exactly the server result rather than edited metadata.
        for key, value in payload.analysis.items():
            if key != "token" and (key not in analysis or value != analysis[key]):
                raise HTTPException(422, "Le contenu analysé a été modifié. Relancez l'analyse avant l'import.")
        positions = payload.positions
        if positions is not None:
            if any(isinstance(p, bool) or p < 1 for p in positions) or len(positions) != len(set(positions)):
                raise HTTPException(422, "La sélection contient des positions invalides ou répétées.")
            known_positions = {entry["position"] for entry in analysis["entries"]}
            if not set(positions).issubset(known_positions):
                raise HTTPException(422, "La sélection ne correspond pas au contenu analysé.")
        selected = set(positions if positions is not None else (entry["position"] for entry in analysis["entries"] if entry.get("available")))
        if not any(entry["position"] in selected and entry.get("available") for entry in analysis["entries"]):
            raise HTTPException(422, "Sélectionnez au moins un morceau disponible.")
        if payload.download and not payload.rights_confirmed:
            raise HTTPException(422, "Confirmez que vous disposez des autorisations nécessaires avant le téléchargement.")
        with _connection(cfg) as conn:
            limit = int(db.get_setting(conn, "max_playlist_items", cfg.max_playlist_items))
            if limit and len(analysis["entries"]) > limit:
                raise HTTPException(422, "L'analyse dépasse la limite actuelle. Modifiez les réglages puis réanalysez.")
            if payload.download:
                _online(conn)
            playlist_id, ids = _store_playlist(conn, analysis, selected)
            queue_result = None
            if payload.download:
                queue_result = enqueue_sources(conn, cfg, _source_ids(ids, cfg))
            conn.commit()
            return {"playlist_id": playlist_id, "source_ids": ids, "jobs": queue_result,
                    "complete": analysis.get("complete", False), "warnings": analysis.get("warnings", [])}

    @app.get("/api/playlists", dependencies=protected)
    def playlists_list() -> dict[str, Any]:
        with _connection(cfg) as conn:
            items = []
            for row in conn.execute("SELECT * FROM playlists ORDER BY id DESC"):
                item = dict(row)
                item["warnings"] = json.loads(item.get("warnings") or "[]")
                item["entries"] = [dict(entry) for entry in conn.execute("SELECT e.*,s.status,s.file_path FROM playlist_entries e LEFT JOIN sources s ON s.id=e.source_id WHERE playlist_id=? ORDER BY position", (row["id"],))]
                items.append(item)
            return {"items": items}

    @app.post("/api/downloads", dependencies=modifying)
    def downloads(payload: DownloadInput) -> dict[str, Any]:
        if not payload.rights_confirmed:
            raise HTTPException(422, "Confirmez que vous disposez des autorisations nécessaires avant le téléchargement.")
        ids = _source_ids(payload.source_ids, cfg)
        with _connection(cfg) as conn:
            _online(conn)
            _existing_sources(conn, ids)
            effective = _effective_cfg(conn, cfg)
            sources = [dict(conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()) for source_id in ids]
        prepared = {}
        direct_ids, warnings, skipped = [], [], []
        for source in sources:
            if classify_source(source) != "playlist":
                direct_ids.append(source["id"])
                continue
            url = source["url"]
            if url in prepared:
                prepared[url]["origins"].append(source["id"])
                continue
            try:
                analysis = _validated_playlist_analysis(providers.analyze(url, effective, mode="playlist"), effective, url)
            except (ValueError, TypeError) as exc:
                reason = f"Playlist « {source['title']} » non analysée : {exc}"
                warnings.append(reason)
                skipped.append({"source_id": source["id"], "reason": reason})
                continue
            prepared[url] = {"analysis": analysis, "origins": [source["id"]]}
        expanded = []
        with _connection(cfg) as conn:
            _online(conn)
            _existing_sources(conn, ids)
            conn.execute("BEGIN IMMEDIATE")
            queue_ids = list(direct_ids)
            for prepared_playlist in prepared.values():
                analysis = prepared_playlist["analysis"]
                selected = {entry["position"] for entry in analysis["entries"] if entry.get("available")}
                # A failed expansion cannot leave partial playlist/source rows.
                conn.execute("SAVEPOINT playlist_expansion")
                try:
                    playlist_id, track_ids = _store_playlist(conn, analysis, selected)
                except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
                    conn.execute("ROLLBACK TO SAVEPOINT playlist_expansion")
                    conn.execute("RELEASE SAVEPOINT playlist_expansion")
                    reason = f"Playlist « {analysis['title']} » non enregistrée : {exc}"
                    warnings.append(reason)
                    skipped.extend({"source_id": origin, "reason": reason} for origin in prepared_playlist["origins"])
                    continue
                conn.execute("RELEASE SAVEPOINT playlist_expansion")
                queue_ids.extend(track_ids)
                origins = prepared_playlist["origins"]
                expanded.append({"source_id": origins[0], "proposal_source_ids": origins, "playlist_id": playlist_id,
                                 "title": analysis["title"], "source_ids": track_ids,
                                 "complete": analysis["complete"], "warnings": analysis["warnings"]})
                warnings.extend(f"Playlist « {analysis['title']} » : {warning}" for warning in analysis["warnings"])
                if not analysis["complete"]:
                    warnings.append(f"Playlist « {analysis['title']} » : analyse incomplète ; seuls les éléments retournés et disponibles ont été ajoutés.")
                absent = sum(not entry.get("available") for entry in analysis["entries"])
                if absent:
                    warnings.append(f"Playlist « {analysis['title']} » : {absent} entrée(s) indisponible(s) conservée(s) dans le rapport, sans téléchargement.")
                if not track_ids:
                    reason = f"Playlist « {analysis['title']} » : aucun morceau disponible à préparer."
                    warnings.append(reason)
                    skipped.extend({"source_id": origin, "reason": reason} for origin in origins)
            try:
                result = enqueue_sources(conn, cfg, _source_ids(list(dict.fromkeys(queue_ids)), cfg))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            conn.commit()
            result["skipped"] = [*result.get("skipped", []), *skipped]
            result["warnings"] = list(dict.fromkeys(warnings))
            result["expanded_playlists"] = expanded
            return result

    @app.post("/api/downloads/retry", dependencies=modifying)
    def downloads_retry(payload: RetryInput) -> dict[str, Any]:
        if not payload.rights_confirmed:
            raise HTTPException(422, "Confirmez les autorisations avant de relancer les téléchargements en échec.")
        with _connection(cfg) as conn:
            _online(conn)
            failed = [row[0] for row in conn.execute("SELECT source_id FROM jobs WHERE status IN ('failed','error','unavailable','interrupted')")]
            if payload.source_ids is not None:
                requested = _source_ids(payload.source_ids, cfg)
                _existing_sources(conn, requested)
                failed = [source_id for source_id in requested if source_id in failed]
            if not failed:
                return {"queued": [], "skipped": [], "message": "Aucun téléchargement en échec à relancer."}
            try:
                result = enqueue_sources(conn, cfg, _source_ids(failed, cfg), retry=True)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            conn.commit()
            return result

    @app.get("/api/jobs", dependencies=protected)
    def jobs_list() -> dict[str, Any]:
        with _connection(cfg) as conn:
            return {"items": queue_snapshot(conn), "paused": db.get_setting(conn, "queue_paused", False),
                    "offline": db.get_setting(conn, "offline_mode", False)}

    @app.post("/api/queue", dependencies=modifying)
    def queue_update(payload: QueueInput) -> dict[str, Any]:
        with _connection(cfg) as conn:
            db.set_setting(conn, "queue_paused", payload.paused)
            conn.commit()
        return {"paused": payload.paused,
                "message": "La file sera suspendue après le morceau en cours." if payload.paused else "La file reprend lorsque le mode hors ligne est désactivé."}

    @app.get("/api/library", dependencies=protected)
    def library_list() -> dict[str, Any]:
        with _connection(cfg) as conn:
            return {"items": library.available_sources(conn, cfg)}

    @app.post("/api/library/import", dependencies=modifying)
    async def library_import(file: Annotated[UploadFile, File()], title: Annotated[str, Form()] = "",
                             artist: Annotated[str, Form()] = "") -> dict[str, Any]:
        title = _clean_text(title, "Le titre")
        artist = _clean_text(artist, "L'artiste")
        if len(title) > 500 or len(artist) > 300:
            raise HTTPException(422, "Le titre ou le nom d'artiste est trop long.")
        suffix = Path(file.filename or "audio").suffix.lower()
        if suffix not in (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac", ".webm", ".mp4", ".aiff", ".aif", ".wma"):
            raise HTTPException(422, "Choisissez un fichier audio pris en charge (MP3, M4A, WAV, FLAC, OGG…).")
        cfg.temp_dir.mkdir(parents=True, exist_ok=True)
        if not title:
            original_name = Path((file.filename or "audio").replace("\\", "/")).stem
            title = _clean_text(original_name[:500], "Le nom du fichier") or "Audio importé"
        temporary = cfg.temp_dir / ("import-" + uuid.uuid4().hex + suffix)
        size = 0
        try:
            free_before_upload = shutil.disk_usage(cfg.temp_dir).free
            with temporary.open("xb") as target:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > cfg.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, f"Le fichier dépasse la limite de {cfg.max_upload_mb} Mo.")
                    if free_before_upload - size < cfg.min_free_mb * 1024 * 1024 + 16384:
                        raise HTTPException(507, "Espace disque insuffisant pour importer ce fichier. Libérez de l'espace puis réessayez.")
                    target.write(chunk)
            if not size:
                raise HTTPException(422, "Le fichier est vide.")

            def import_file() -> dict[str, Any]:
                with _connection(cfg) as conn:
                    imported = library.import_audio(conn, cfg, temporary, title=title, artist=artist)
                    conn.commit()
                    return imported

            try:
                item = await run_in_threadpool(import_file)
            except (ValueError, OSError) as exc:
                raise HTTPException(422, str(exc)) from exc
            return {"item": item}
        finally:
            temporary.unlink(missing_ok=True)
            await file.close()

    @app.patch("/api/library/{source_id}", dependencies=modifying)
    def library_update(source_id: int, payload: LibraryPatch) -> dict[str, Any]:
        changes = payload.model_dump(exclude_unset=True)
        if not changes or any(value is None for value in changes.values()):
            raise HTTPException(422, "Indiquez un titre ou un artiste valide.")
        with _connection(cfg) as conn:
            source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if not source or not _present(conn, cfg, source):
                raise HTTPException(404, "Ce morceau ne possède pas de fichier local disponible.")
            title = _clean_text(changes.get("title", source["title"]), "Le titre")
            artist = _clean_text(changes.get("artist", source["artist"]), "L'artiste")
            if not title:
                raise HTTPException(422, "Le titre est obligatoire.")
            try:
                item = library.rename_audio(conn, cfg, source_id, title, artist)
            except (ValueError, OSError) as exc:
                raise HTTPException(422, str(exc)) from exc
            conn.commit()
            return {"item": item}

    @app.get("/api/library/{source_id}/audio", dependencies=protected)
    def audio(source_id: int) -> FileResponse:
        with _connection(cfg) as conn:
            row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if not _present(conn, cfg, row):
                raise HTTPException(404, "Le fichier audio n'est pas disponible.")
            try:
                path = library.resolve_audio_path(cfg, row["file_path"])
            except ValueError as exc:
                raise HTTPException(404, "Chemin audio invalide.") from exc
        return FileResponse(path, filename=path.name)

    @app.post("/api/exports", dependencies=modifying)
    def export_prepare(payload: ExportInput) -> dict[str, Any]:
        ids = _source_ids(payload.source_ids, cfg) if payload.source_ids is not None else None
        with _connection(cfg) as conn:
            if ids is not None:
                if not ids:
                    raise HTTPException(422, "Sélectionnez au moins un morceau à exporter.")
                _existing_sources(conn, ids)
            try:
                pack = exports.prepare_pack(conn, cfg, source_ids=ids, make_zip=payload.make_zip)
            except (ValueError, OSError) as exc:
                raise HTTPException(422, str(exc)) from exc
            conn.commit()
            return pack

    @app.get("/api/exports/{export_id}/zip", dependencies=protected)
    def export_download(export_id: str) -> FileResponse:
        if not export_id or len(export_id) > 80 or any(char not in "0123456789abcdef-" for char in export_id):
            raise HTTPException(404, "Export introuvable.")
        with _connection(cfg) as conn:
            row = conn.execute("SELECT archive FROM exports WHERE id=?", (export_id,)).fetchone()
        if not row or not row["archive"]:
            raise HTTPException(404, "Cet export ne possède pas d'archive ZIP.")
        path = Path(row["archive"]).resolve()
        root = cfg.export_dir.resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() != ".zip":
            raise HTTPException(404, "L'archive ZIP n'est plus disponible.")
        return FileResponse(path, media_type="application/zip", filename=f"Anniversaire25-{export_id[:8]}.zip")

    @app.get("/api/catalogue", dependencies=protected)
    def catalogue(format: str = "json") -> Response:
        if format not in ("csv", "json"):
            raise HTTPException(422, "Choisissez le format csv ou json.")
        with _connection(cfg) as conn:
            content = exports.catalogue_export(conn, format)
        return Response(content, media_type="text/csv; charset=utf-8" if format == "csv" else "application/json",
                        headers={"Content-Disposition": f'attachment; filename="catalogue.{format}"'})

    @app.get("/api/diagnostics", dependencies=protected)
    def diagnostics() -> dict[str, Any]:
        return _diagnostics(cfg)

    return app
