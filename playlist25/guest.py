"""Public collection surface: proposals and votes, never downloads or files."""
from __future__ import annotations

import hashlib
import io
import math
import re
import threading
import unicodedata
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import qrcode
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import db, providers
from .security import csrf_token, install_session, rate_limit, require_csrf


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityInput(_Input):
    nickname: str = Field(min_length=1, max_length=60)
    invitation_code: str | None = Field(default=None, max_length=120)


class ProposalInput(_Input):
    kind: Literal["manual", "youtube"]
    title: str = Field(default="", max_length=240)
    artist: str = Field(default="", max_length=160)
    url: str = Field(default="", max_length=2048)


class VoteInput(_Input):
    active: StrictBool


_VISIBLE_STATES = {"pending", "approved"}
_PUBLIC_FIELDS = (
    "id", "source_id", "nickname", "state", "sort_order", "title", "artist",
    "channel", "duration", "thumbnail", "url", "video_id", "status", "votes",
    "voted", "created_at", "mine",
)
_EXTERNAL_SLOTS = threading.BoundedSemaphore(4)


class _GuestStatic(StaticFiles):
    _files = {"guest.js", "styles.css", "placeholder.svg", "cloud.js", "cloud-config.js"}

    async def get_response(self, path: str, scope):
        if path not in self._files:
            raise HTTPException(404, "Not Found")
        return await super().get_response(path, scope)


@contextmanager
def _connection(cfg):
    conn = db.connect(cfg)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _text(value: str, label: str, *, required: bool = False) -> str:
    value = value.strip()
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise HTTPException(400, f"{label} contient un caractère non autorisé.")
    if required and not value:
        raise HTTPException(400, f"{label} est obligatoire.")
    return value


def _normal(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.sub(r"[^\w]+", " ", "".join(
        char for char in value if not unicodedata.combining(char)
    )).split())


def _thumbnail(value: object) -> str:
    """Metadata may expose a known YouTube image URL; the UI uses local assets."""
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname in {"i.ytimg.com", "img.youtube.com"}
                and not parsed.username and not parsed.password and parsed.port in {None, 443}):
            return value
    except ValueError:
        pass
    return ""


def _entry(entry: dict, cfg, *, expected_id: str | None = None) -> dict:
    """Keep public provider output bounded and canonical before persisting it."""
    url = entry.get("url") or (
        f"https://www.youtube.com/watch?v={entry.get('video_id', '')}"
    )
    parsed = providers.parse_youtube_url(url, mode="video")
    if expected_id and parsed["video_id"] != expected_id:
        raise HTTPException(502, "La source renvoyée ne correspond pas au lien demandé.")
    title = str(entry.get("title") or f"Vidéo YouTube ({parsed['video_id']})")[:240]
    artist = str(entry.get("artist") or "")[:160]
    channel = str(entry.get("channel") or "")[:160]
    title = _text(title, "Le titre", required=True)
    artist = _text(artist, "L’artiste")
    channel = _text(channel, "La chaîne")
    duration = entry.get("duration")
    if duration is not None:
        try:
            duration = float(duration)
        except (ValueError, TypeError):
            duration = None
        if duration is not None and (not math.isfinite(duration) or duration <= 0):
            duration = None
    if duration is not None and duration > cfg.max_duration:
        raise HTTPException(400, "Ce morceau dépasse la durée maximale autorisée.")
    return {
        "video_id": parsed["video_id"], "url": parsed["url"], "title": title,
        "artist": artist, "channel": channel, "duration": duration,
        "thumbnail": _thumbnail(entry.get("thumbnail")), "status": "pending",
    }


def _settings(conn, cfg) -> dict:
    return {
        "event_name": db.get_setting(conn, "event_name", cfg.event_name),
        "offline_mode": db.get_setting(conn, "offline_mode", False),
        "proposals_open": db.get_setting(conn, "proposals_open", True),
        "votes_open": db.get_setting(conn, "votes_open", True),
        "max_proposals": db.get_setting(conn, "max_proposals", cfg.max_proposals),
        "invitation_required": db.get_setting(conn, "invitation_required", False),
        "public_url": db.get_setting(conn, "public_url", cfg.public_url),
        "local_url": db.get_setting(conn, "local_url", cfg.local_url),
    }


def _guest(conn, request: Request, *, required: bool = True) -> dict | None:
    guest_id = request.session.get("guest_id")
    row = conn.execute("SELECT id,nickname FROM guests WHERE id=?", (guest_id,)).fetchone()
    if row:
        return dict(row)
    if required:
        raise HTTPException(401, "Choisis d’abord un pseudo pour participer.")
    return None


def _participation(conn, request: Request) -> dict:
    guest = _guest(conn, request)
    if db.get_setting(conn, "invitation_required", False):
        invited = conn.execute(
            "SELECT 1 FROM invitations WHERE guest_id=?", (guest["id"],)
        ).fetchone()
        if not invited:
            raise HTTPException(403, "Un code d’invitation est nécessaire pour participer.")
    return guest


def _public(item: dict) -> dict:
    public = {key: item.get(key) for key in _PUBLIC_FIELDS}
    public["votes"] = int(public.get("votes") or 0)
    public["voted"] = bool(public.get("voted"))
    public["mine"] = bool(public.get("mine"))
    public["thumbnail"] = _thumbnail(public.get("thumbnail"))
    return public


def _proposals(conn, guest_id: str | None = None) -> list[dict]:
    return [dict(row, mine=bool(guest_id and row["guest_id"] == guest_id))
            for row in db.list_proposals(conn, guest_id=guest_id)
            if row["state"] in _VISIBLE_STATES]


def _same_source(items: list[dict], source_id: int | None) -> dict | None:
    if source_id is None:
        return None
    return next((item for item in items if item["source_id"] == source_id), None)


def _manual_duplicate(conn, items: list[dict], title: str, artist: str) -> dict | None:
    title_key, artist_key = _normal(title), _normal(artist)
    for item in items:
        if not item.get("video_id") and (
            _normal(item.get("title") or "") == title_key
            and _normal(item.get("artist") or "") == artist_key
        ):
            return item
    return None


def _withdrawn_manual_source(conn, guest_id: str, title: str, artist: str):
    """Reuse an owner's exact text source without erasing the archived proposal."""
    rows = conn.execute(
        "SELECT s.id,s.title,s.artist FROM sources s JOIN proposals p ON p.source_id=s.id "
        "WHERE p.guest_id=? AND p.withdrawn_at IS NOT NULL AND s.video_id IS NULL "
        "ORDER BY p.id DESC", (guest_id,),
    )
    title_key, artist_key = _normal(title), _normal(artist)
    for row in rows:
        if _normal(row["title"]) == title_key and _normal(row["artist"]) == artist_key:
            return row
    return None


def _similar(items: list[dict], title: str, artist: str, source_id: int | None) -> list[dict]:
    title_key, artist_key = _normal(title), _normal(artist)
    if len(title_key) < 3:
        return []
    candidates = []
    for item in items:
        if source_id is not None and item["source_id"] == source_id:
            continue
        other_title = _normal(item.get("title") or "")
        score = SequenceMatcher(None, title_key, other_title).ratio()
        # Titles with live/remix/version markers stay separate; this is a warning only.
        if score >= .80:
            if artist_key and _normal(item.get("artist") or "") == artist_key:
                score += .10
            candidates.append((score, item))
    candidates.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return [_public(item) for _, item in candidates[:5]]


def _source_error(error: ValueError) -> HTTPException:
    status = int(getattr(error, "status", 400))
    if status not in {400, 404, 409, 413, 429, 502, 503, 504}:
        status = 400
    return HTTPException(status, str(error))


def create_guest_app(cfg) -> FastAPI:
    db.init_db(cfg)
    app = FastAPI(title="La playlist des 25 ans — invités", docs_url=None, redoc_url=None,
                  openapi_url=None)
    install_session(app, cfg, "playlist_guest")
    static = Path(__file__).resolve().parent / "static"
    app.mount("/static", _GuestStatic(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "guest.html", media_type="text/html")

    @app.get("/health")
    def health():
        return {"status": "ok", "surface": "guest"}

    @app.get("/api/session")
    def session(request: Request):
        rate_limit(request, "session", 120)
        token = csrf_token(request)
        with _connection(cfg) as conn:
            return {"guest": _guest(conn, request, required=False), "csrf_token": token,
                    "settings": _settings(conn, cfg)}

    @app.post("/api/identity")
    def identity(payload: IdentityInput, request: Request):
        require_csrf(request)
        rate_limit(request, "identity", 15, window=600)
        nickname = _text(payload.nickname, "Le pseudo", required=True)
        code = _text(payload.invitation_code or "", "Le code d’invitation")
        with _connection(cfg) as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = _guest(conn, request, required=False)
            guest_id = current["id"] if current else str(uuid4())
            bound = conn.execute(
                "SELECT 1 FROM invitations WHERE guest_id=?", (guest_id,)
            ).fetchone()
            invitation = None
            if code:
                code_hash = hashlib.sha256(code.upper().encode("utf-8")).hexdigest()
                invitation = conn.execute(
                    "SELECT code_hash,guest_id FROM invitations WHERE code_hash=?", (code_hash,)
                ).fetchone()
                if not invitation or invitation["guest_id"] not in {None, guest_id}:
                    raise HTTPException(400, "Ce code d’invitation est invalide ou déjà utilisé.")
            if db.get_setting(conn, "invitation_required", False) and not (bound or invitation):
                raise HTTPException(403, "Saisis ton code d’invitation pour participer.")
            if current:
                conn.execute("UPDATE guests SET nickname=? WHERE id=?", (nickname, guest_id))
            else:
                conn.execute("INSERT INTO guests(id,nickname,created_at) VALUES(?,?,?)",
                             (guest_id, nickname, db.now()))
            if invitation and not bound:
                conn.execute("UPDATE invitations SET guest_id=? WHERE code_hash=?",
                             (guest_id, invitation["code_hash"]))
            conn.commit()
        request.session["guest_id"] = guest_id
        return {"guest": {"id": guest_id, "nickname": nickname}}

    @app.get("/api/proposals")
    def proposals_list(request: Request, q: str = Query(default="", max_length=120),
                       sort: Literal["popular", "recent"] = "popular", mine: bool = False):
        rate_limit(request, "proposals_read", 120)
        with _connection(cfg) as conn:
            guest = _guest(conn, request, required=mine)
            # Visitors can read the shared list before choosing a nickname.
            if not mine:
                guest = _guest(conn, request, required=False)
            items = _proposals(conn, guest["id"] if guest else None)
            if mine:
                items = [item for item in items if item.get("guest_id") == guest["id"]]
            needle = _normal(q)
            if needle:
                items = [item for item in items if needle in _normal(" ".join(
                    str(item.get(key) or "") for key in ("title", "artist", "channel", "nickname")
                ))]
            if sort == "recent":
                items.sort(key=lambda item: (item.get("created_at") or "", item["id"]), reverse=True)
            else:
                items.sort(key=lambda item: (-int(item.get("votes") or 0), item["id"]))
            return {"items": [_public(item) for item in items]}

    @app.get("/api/search")
    def search(request: Request, q: str = Query(min_length=2, max_length=180)):
        rate_limit(request, "search", 15)
        query = _text(q, "La recherche", required=True)
        if len(query) < 2:
            raise HTTPException(400, "Saisis au moins deux caractères.")
        with _connection(cfg) as conn:
            if db.get_setting(conn, "offline_mode", False):
                raise HTTPException(503, "La recherche YouTube est désactivée en mode soirée hors ligne. "
                                    "Tu peux proposer un titre manuellement ou chercher dans la liste.")
        if not _EXTERNAL_SLOTS.acquire(blocking=False):
            raise HTTPException(503, "La recherche est occupée. Réessaie dans quelques instants.")
        try:
            entries = providers.search(query, cfg)
            items = []
            for entry in entries[:cfg.search_limit]:
                try:
                    clean = _entry(entry, cfg)
                except (ValueError, HTTPException):
                    continue
                items.append(clean)
            return {"items": items}
        except ValueError as error:
            raise _source_error(error) from error
        finally:
            _EXTERNAL_SLOTS.release()

    @app.post("/api/proposals")
    def propose(payload: ProposalInput, request: Request):
        require_csrf(request)
        rate_limit(request, "propose", 20)
        title = _text(payload.title, "Le titre", required=payload.kind == "manual")
        artist = _text(payload.artist, "L’artiste")
        url = _text(payload.url, "Le lien")
        warning = ""
        with _connection(cfg) as conn:
            guest = _participation(conn, request)
            settings = _settings(conn, cfg)
            if not settings["proposals_open"]:
                raise HTTPException(403, "Les propositions sont fermées pour le moment.")
            items = _proposals(conn, guest["id"])
            if payload.kind == "manual":
                if url:
                    raise HTTPException(400, "Utilise « Coller un lien YouTube » pour ajouter une source.")
                duplicate = _manual_duplicate(conn, items, title, artist)
                entry = {"title": title, "artist": artist, "status": "pending"}
            else:
                if not url:
                    raise HTTPException(400, "Colle le lien d’un morceau YouTube.")
                try:
                    parsed = providers.parse_youtube_url(url, mode="video")
                except ValueError as error:
                    raise _source_error(error) from error
                known = conn.execute("SELECT id FROM sources WHERE video_id=?",
                                     (parsed["video_id"],)).fetchone()
                duplicate = _same_source(items, known["id"] if known else None)
                entry = {"video_id": parsed["video_id"], "url": parsed["url"],
                         "title": title or f"Vidéo YouTube ({parsed['video_id']})",
                         "artist": artist, "status": "pending"}
                if not duplicate and not known:
                    if settings["offline_mode"]:
                        warning = "Le lien a été enregistré. Ses informations seront vérifiées "                                   "par l’administrateur lorsque Internet sera disponible."
                    elif _EXTERNAL_SLOTS.acquire(blocking=False):
                        try:
                            analysis = providers.analyze(parsed["url"], cfg, mode="video")
                            entries = analysis.get("entries") or []
                            if not entries:
                                warning = "Le lien est enregistré, mais aucune information n’a pu "                                           "être obtenue. L’administrateur pourra le vérifier."
                            else:
                                entry = _entry(entries[0], cfg, expected_id=parsed["video_id"])
                                if not entries[0].get("available", True):
                                    warning = "Le lien a été enregistré, mais YouTube signale ce morceau "                                               "comme indisponible. L’administrateur devra le vérifier."
                        except ValueError as error:
                            if int(getattr(error, "status", 400)) < 500:
                                raise _source_error(error) from error
                            warning = "Le lien a été enregistré. YouTube n’a pas répondu ; "                                       "les informations devront être vérifiées par l’administrateur."
                        finally:
                            _EXTERNAL_SLOTS.release()
                    else:
                        warning = "Le lien est enregistré. La recherche est occupée ; "                                   "l’administrateur pourra vérifier ses informations."
            if duplicate:
                return {"duplicate": _public(duplicate), "similar": [],
                        "message": "Ce morceau est déjà proposé. Tu peux voter pour lui."}

        # External metadata lookup never holds a write lock. Recheck settings, quota and
        # duplicates inside one transaction to prevent concurrent duplicate submissions.
        with _connection(cfg) as conn:
            conn.execute("BEGIN IMMEDIATE")
            guest = _participation(conn, request)
            settings = _settings(conn, cfg)
            if not settings["proposals_open"]:
                raise HTTPException(403, "Les propositions sont fermées pour le moment.")
            items = _proposals(conn, guest["id"])
            known = None
            if entry.get("video_id"):
                known = conn.execute("SELECT id FROM sources WHERE video_id=?",
                                     (entry["video_id"],)).fetchone()
                duplicate = _same_source(items, known["id"] if known else None)
            else:
                duplicate = _manual_duplicate(conn, items, entry["title"], entry.get("artist", ""))
                known = _withdrawn_manual_source(conn, guest["id"], entry["title"], entry.get("artist", ""))
            if duplicate:
                return {"duplicate": _public(duplicate), "similar": [],
                        "message": "Ce morceau est déjà proposé. Tu peux voter pour lui."}
            count = conn.execute("SELECT COUNT(*) FROM proposals WHERE guest_id=? AND withdrawn_at IS NULL",
                                 (guest["id"],)).fetchone()[0]
            maximum = int(settings["max_proposals"])
            if count >= maximum:
                raise HTTPException(429, f"Tu as atteint la limite de {maximum} propositions. "
                                    "Tu peux encore voter pour les morceaux de la liste.")
            total = conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
            if total >= int(db.get_setting(conn, "max_total_proposals", 5000)):
                raise HTTPException(429, "La liste a atteint sa capacité. Contacte l’administrateur.")
            source_id = known["id"] if known else db.upsert_source(conn, entry)
            # Administrator moderation remains effective. A voluntary withdrawal
            # permits a fresh proposal while its archived history and votes survive.
            previous = conn.execute(
                "SELECT state FROM proposals WHERE source_id=? AND guest_id=? AND withdrawn_at IS NULL",
                (source_id, guest["id"]),
            ).fetchone()
            if previous:
                raise HTTPException(409, "Tu as déjà proposé ce morceau. Son état est géré "
                                    "par l’administrateur.")
            similar = _similar(items, entry["title"], entry.get("artist", ""), source_id)
            cursor = conn.execute(
                "INSERT INTO proposals(source_id,guest_id,state,created_at) VALUES(?,?,'pending',?)",
                (source_id, guest["id"], db.now()),
            )
            proposal_id = cursor.lastrowid
            conn.commit()
            item = next(item for item in _proposals(conn, guest["id"]) if item["id"] == proposal_id)
            return {"item": _public(item), "similar": similar,
                    "message": warning or "Le morceau a été ajouté à la liste."}

    @app.post("/api/proposals/{proposal_id}/vote")
    def vote(proposal_id: int, payload: VoteInput, request: Request):
        require_csrf(request)
        rate_limit(request, "vote", 90)
        if proposal_id <= 0:
            raise HTTPException(404, "Ce morceau n’est pas disponible dans la liste.")
        with _connection(cfg) as conn:
            conn.execute("BEGIN IMMEDIATE")
            guest = _participation(conn, request)
            if not db.get_setting(conn, "votes_open", True):
                raise HTTPException(403, "Les votes sont fermés pour le moment.")
            proposal = conn.execute("SELECT state FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not proposal or proposal["state"] not in _VISIBLE_STATES:
                raise HTTPException(404, "Ce morceau n’est pas disponible dans la liste.")
            if payload.active:
                conn.execute("INSERT OR IGNORE INTO votes(proposal_id,guest_id,created_at) VALUES(?,?,?)",
                             (proposal_id, guest["id"], db.now()))
            else:
                conn.execute("DELETE FROM votes WHERE proposal_id=? AND guest_id=?",
                             (proposal_id, guest["id"]))
            votes = conn.execute("SELECT COUNT(*) FROM votes WHERE proposal_id=?", (proposal_id,)).fetchone()[0]
            conn.commit()
            return {"votes": votes, "voted": payload.active}

    @app.post("/api/proposals/{proposal_id}/withdraw")
    def withdraw(proposal_id: int, request: Request):
        require_csrf(request)
        rate_limit(request, "withdraw", 30)
        if not 0 < proposal_id < 2 ** 63:
            raise HTTPException(404, "Cette proposition n’est pas disponible.")
        with _connection(cfg) as conn:
            conn.execute("BEGIN IMMEDIATE")
            # An existing owner may correct a mistake even after participation
            # closes or individual invitations become required.
            guest = _guest(conn, request)
            proposal = conn.execute(
                "SELECT guest_id,state,withdrawn_at FROM proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            if not proposal or proposal["guest_id"] != guest["id"]:
                raise HTTPException(404, "Cette proposition n’est pas disponible.")
            if proposal["state"] not in _VISIBLE_STATES and not proposal["withdrawn_at"]:
                raise HTTPException(404, "Cette proposition n’est pas disponible.")
            if proposal["state"] != "archived" or not proposal["withdrawn_at"]:
                conn.execute(
                    "UPDATE proposals SET state='archived',withdrawn_at=COALESCE(withdrawn_at,?) "
                    "WHERE id=? AND guest_id=?",
                    (db.now(), proposal_id, guest["id"]),
                )
            conn.commit()
            return {"id": proposal_id, "withdrawn": True,
                    "message": "Ta proposition a été retirée de la liste."}

    @app.get("/api/qr")
    def qr(request: Request, scope: Literal["local", "public"] = "local"):
        rate_limit(request, "qr", 30)
        with _connection(cfg) as conn:
            settings = _settings(conn, cfg)
            url = settings[f"{scope}_url"]
        if not isinstance(url, str) or not url:
            raise HTTPException(404, "Ce lien n’est pas encore configuré par l’administrateur.")
        try:
            parsed = urlsplit(url)
            if (len(url) > 2048 or parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password):
                raise ValueError("invalid")
        except ValueError:
            raise HTTPException(400, "Le lien configuré n’est pas une adresse HTTP valide.") from None
        image = qrcode.make(url)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return Response(output.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    return app
