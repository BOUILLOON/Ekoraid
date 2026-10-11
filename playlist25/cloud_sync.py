"""Cloud pull + explicit metadata publication. Audio always remains on the PC."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from . import db

MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024


class CloudSyncError(ValueError):
    """An actionable message without remote response bodies or credentials."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _project(cfg):
    try:
        parsed = urlsplit(cfg.supabase_url)
        if (parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.supabase\.co", parsed.hostname or "")
                or parsed.username or parsed.password or parsed.port or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise ValueError
    except ValueError as error:
        raise CloudSyncError("Renseignez SUPABASE_URL avec l’URL HTTPS de votre projet *.supabase.co dans .env.local.") from error
    return parsed.hostname.split(".")[0]


def _rpc(cfg, name, payload):
    _project(cfg)
    key = cfg.supabase_service_role_key
    if not isinstance(key, str) or not key or "\r" in key or "\n" in key:
        raise CloudSyncError("Renseignez SUPABASE_SERVICE_ROLE_KEY dans .env.local sur votre PC uniquement, jamais sur GitHub Pages.")
    headers = {"Content-Type": "application/json", "apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = "Bearer " + key
    request = Request(cfg.supabase_url.rstrip("/") + "/rest/v1/rpc/" + name,
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(MAX_SNAPSHOT_BYTES + 1)
    except HTTPError as error:
        if error.code in {401, 403}:
            raise CloudSyncError("Supabase refuse la synchronisation. Vérifiez la clé secrète/service_role et les permissions du script SQL.") from None
        if error.code == 404:
            raise CloudSyncError("La fonction de collecte manque. Exécutez cloud/supabase.sql dans SQL Editor.") from None
        raise CloudSyncError(f"Supabase n’a pas fourni le catalogue (HTTP {error.code}). Réessayez après avoir vérifié le projet.") from None
    except (URLError, TimeoutError, OSError):
        raise CloudSyncError("Connexion à Supabase impossible. Vérifiez Internet et SUPABASE_URL ; le catalogue local est conservé.") from None
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise CloudSyncError("Le catalogue distant dépasse 64 Mio. Le catalogue local est conservé ; réduisez les limites de collecte.")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CloudSyncError("Réponse Supabase invalide. Le catalogue local est conservé.") from None
    if isinstance(data, dict) and data.get("error"):
        raise CloudSyncError(str(data["error"])[:500])
    return data


def fetch_snapshot(cfg):
    return _rpc(cfg, "playlist25_snapshot", {})


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise CloudSyncError("Identifiant invalide dans le catalogue distant.")
    return value


def _guest_identifier(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except ValueError:
        raise CloudSyncError("Identité invalide dans le catalogue distant.") from None


def _text(value, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise CloudSyncError("Métadonnée invalide dans le catalogue distant.")
    if required and not value.strip():
        raise CloudSyncError("Un titre ou pseudo manque dans le catalogue distant.")
    return value


def _date(value):
    try:
        if not isinstance(value, str) or len(value) > 80:
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.isoformat()
    except ValueError:
        raise CloudSyncError("Date invalide dans le catalogue distant.") from None


def _validate(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise CloudSyncError("Version de catalogue distante incompatible. Aucun changement local effectué.")
    for name, maximum in (("guests", 10000), ("sources", 10000), ("proposals", 10000), ("votes", 500000)):
        rows = snapshot.get(name)
        if not isinstance(rows, list) or len(rows) > maximum or any(not isinstance(row, dict) for row in rows):
            raise CloudSyncError("Catalogue distant incomplet ou trop volumineux. Aucun changement local effectué.")
    guests = []
    for row in snapshot["guests"]:
        guests.append({"id": _guest_identifier(row.get("id")),
                       "nickname": _text(row.get("nickname"), 50, required=True),
                       "created_at": _date(row.get("created_at"))})
    guest_ids = {g["id"] for g in guests}
    if len(guest_ids) != len(guests):
        raise CloudSyncError("Identités distantes dupliquées.")
    sources = []
    for row in snapshot["sources"]:
        video_id, url = row.get("video_id"), row.get("url")
        if video_id is not None:
            if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                raise CloudSyncError("Source YouTube invalide dans le catalogue distant.")
            if url != "https://www.youtube.com/watch?v=" + video_id:
                raise CloudSyncError("URL distante non autorisée. Aucun changement local effectué.")
        elif url not in {None, ""}:
            raise CloudSyncError("Une source manuelle contient une URL non autorisée.")
        duration = row.get("duration")
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                     or not math.isfinite(duration) or not 0 < duration < 1e8):
            raise CloudSyncError("Durée invalide dans le catalogue distant.")
        sources.append({"id": _identifier(row.get("id")), "video_id": video_id, "url": url or None,
                        "title": _text(row.get("title"), 300, required=True),
                        "artist": _text(row.get("artist", ""), 150),
                        "channel": _text(row.get("channel", ""), 300),
                        "duration": duration, "thumbnail": "", "status": "pending",
                        "created_at": _date(row.get("created_at"))})
    source_ids = {s["id"] for s in sources}
    if len(source_ids) != len(sources):
        raise CloudSyncError("Sources distantes dupliquées.")
    proposals = []
    for row in snapshot["proposals"]:
        gid, sid = _guest_identifier(row.get("guest_id")), _identifier(row.get("source_id"))
        state, sort_order = row.get("state"), row.get("sort_order", 0)
        if gid not in guest_ids or sid not in source_ids or state not in {"pending", "approved", "rejected", "archived"}:
            raise CloudSyncError("Proposition distante incohérente.")
        if isinstance(sort_order, bool) or not isinstance(sort_order, int) or abs(sort_order) > 2**31 - 1:
            raise CloudSyncError("Ordre distant invalide.")
        proposals.append({"id": _identifier(row.get("id")), "source_id": sid, "guest_id": gid,
                          "state": state, "sort_order": sort_order, "created_at": _date(row.get("created_at"))})
    proposal_ids = {p["id"] for p in proposals}
    if len(proposal_ids) != len(proposals):
        raise CloudSyncError("Propositions distantes dupliquées.")
    votes, vote_keys = [], set()
    for row in snapshot["votes"]:
        pid, gid = _identifier(row.get("proposal_id")), _guest_identifier(row.get("guest_id"))
        if pid not in proposal_ids or gid not in guest_ids or (pid, gid) in vote_keys:
            raise CloudSyncError("Vote distant incohérent.")
        vote_keys.add((pid, gid))
        votes.append({"proposal_id": pid, "guest_id": gid, "created_at": _date(row.get("created_at"))})
    return guests, sources, proposals, votes


def import_snapshot(cfg, conn, snapshot):
    """Validate first, then atomically replace this project's cloud votes only."""
    project = _project(cfg)
    guests, sources, proposals, votes = _validate(snapshot)
    maps = {}
    for row in conn.execute("SELECT kind,remote_id,local_id FROM cloud_map"):
        if row["remote_id"].startswith(project + ":"):
            maps[(row["kind"], row["remote_id"].split(":", 1)[1])] = row["local_id"]
    def mapped(kind, remote):
        return maps.get((kind, str(remote)))
    def remember(kind, remote, local):
        conn.execute("INSERT INTO cloud_map(kind,remote_id,local_id) VALUES(?,?,?) ON CONFLICT(kind,remote_id) DO UPDATE SET local_id=excluded.local_id",
                     (kind, project + ":" + str(remote), str(local)))
        maps[(kind, str(remote))] = str(local)
    new_sources = new_proposals = 0
    conn.execute("SAVEPOINT playlist25_cloud_sync")
    try:
        for row in guests:
            gid = "cloud:" + project + ":" + row["id"]
            conn.execute("INSERT INTO guests(id,nickname,created_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET nickname=excluded.nickname",
                         (gid, row["nickname"], row["created_at"]))
            remember("guest", row["id"], gid)
        for row in sources:
            sid = mapped("source", row["id"])
            if sid is not None and conn.execute("SELECT 1 FROM sources WHERE id=?", (sid,)).fetchone():
                continue
            already_exists = row["video_id"] and conn.execute("SELECT 1 FROM sources WHERE video_id=?", (row["video_id"],)).fetchone()
            sid = db.upsert_source(conn, row)
            remember("source", row["id"], sid)
            new_sources += int(not already_exists)
        for row in proposals:
            pid = mapped("proposal", row["id"])
            if pid is not None and conn.execute("SELECT 1 FROM proposals WHERE id=?", (pid,)).fetchone():
                # Local corrections, merges, moderation and ordering remain authoritative.
                continue
            sid = mapped("source", row["source_id"])
            existing = conn.execute("SELECT id FROM proposals WHERE source_id=? ORDER BY id LIMIT 1", (sid,)).fetchone()
            if existing:
                pid = existing["id"]
            else:
                cursor = conn.execute("INSERT INTO proposals(source_id,guest_id,state,sort_order,created_at) VALUES(?,?,?,?,?)",
                                      (sid, mapped("guest", row["guest_id"]), row["state"], row["sort_order"], row["created_at"]))
                pid = cursor.lastrowid
                new_proposals += 1
            remember("proposal", row["id"], pid)
        # DELETE is narrowly limited to imported votes, never audio/proposals/guests.
        # Includes old mapped rows if a cloud vote was removed since the prior pull.
        prefix = project + ":"
        conn.execute("""DELETE FROM votes
          WHERE proposal_id IN (SELECT CAST(local_id AS INTEGER) FROM cloud_map
            WHERE kind='proposal' AND substr(remote_id,1,length(?))=?)
          AND guest_id IN (SELECT local_id FROM cloud_map
            WHERE kind='guest' AND substr(remote_id,1,length(?))=?)""", (prefix, prefix, prefix, prefix))
        # A moderator merge can map several remote proposals to one local proposal.
        for row in votes:
            conn.execute("INSERT OR IGNORE INTO votes(proposal_id,guest_id,created_at) VALUES(?,?,?)",
                         (mapped("proposal", row["proposal_id"]), mapped("guest", row["guest_id"]), row["created_at"]))
        db.set_setting(conn, "cloud_last_sync", snapshot.get("exported_at") or db.now())
        db.set_setting(conn, "cloud_project", project)
        db.set_setting(conn, "cloud_public_settings", snapshot.get("settings", {}))
        conn.execute("RELEASE SAVEPOINT playlist25_cloud_sync")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT playlist25_cloud_sync")
        conn.execute("RELEASE SAVEPOINT playlist25_cloud_sync")
        raise
    return {"sources": len(sources), "proposals": len(proposals), "votes": len(votes),
            "new_sources": new_sources, "new_proposals": new_proposals,
            "message": "Collecte récupérée sur ce PC. Aucune proposition ne déclenche de téléchargement.",
            "exported_at": snapshot.get("exported_at")}


def pull(cfg, conn):
    if db.get_setting(conn, "offline_mode", False):
        raise CloudSyncError("Désactivez temporairement le mode soirée hors ligne pour récupérer la collecte Internet.")
    return import_snapshot(cfg, conn, fetch_snapshot(cfg))


def push(cfg, conn):
    """Explicit admin action: publish settings/moderation, never local audio."""
    if db.get_setting(conn, "offline_mode", False):
        raise CloudSyncError("Désactivez temporairement le mode soirée hors ligne pour publier sur Internet.")
    project = _project(cfg)
    prefix = project + ":"
    public_settings = {"event_name": db.get_setting(conn, "event_name", cfg.event_name),
                       "proposals_open": db.get_setting(conn, "proposals_open", True),
                       "votes_open": db.get_setting(conn, "votes_open", True),
                       "invitation_required": db.get_setting(conn, "invitation_required", False),
                       "max_proposals": db.get_setting(conn, "max_proposals", cfg.max_proposals)}
    rows = conn.execute("""SELECT m.remote_id,p.id,p.state,p.sort_order,s.title,s.artist
      FROM cloud_map m JOIN proposals p ON p.id=CAST(m.local_id AS INTEGER)
      JOIN sources s ON s.id=p.source_id
      WHERE m.kind='proposal' AND substr(m.remote_id,1,length(?))=?""", (prefix, prefix)).fetchall()
    if len(rows) > 10000:
        raise CloudSyncError("Trop de propositions à publier en une opération (maximum 10000).")
    updates, seen_local = [], {}
    for row in sorted(rows, key=lambda item: int(item["remote_id"].split(":", 1)[1])):
        remote = _identifier(int(row["remote_id"].split(":", 1)[1]))
        if len(row["title"]) > 300 or len(row["artist"]) > 150:
            raise CloudSyncError("Une métadonnée dépasse les limites publiques : titre 300 caractères, artiste 150. Corrigez-la avant de publier.")
        update = {"id": remote,
                  "state": "archived" if row["id"] in seen_local else row["state"],
                  "title": row["title"], "artist": row["artist"], "sort_order": row["sort_order"]}
        if row["id"] in seen_local and row["state"] != "archived":
            update["merge_into"] = seen_local[row["id"]]
        updates.append(update)
        seen_local.setdefault(row["id"], remote)
    result = _rpc(cfg, "playlist25_admin_publish", {"p_settings": public_settings, "p_updates": updates})
    if not isinstance(result, dict) or not isinstance(result.get("updated"), int):
        raise CloudSyncError("Réponse de publication invalide. Actualisez le site public pour vérifier son état.")
    db.set_setting(conn, "cloud_last_publish", db.now())
    return {**result, "message": "Réglages et modération publiés. Aucun fichier audio ni secret envoyé au site public."}
