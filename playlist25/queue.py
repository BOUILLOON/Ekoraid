"""Persistent job queue, with an atomic claim and a separately locked worker."""
from __future__ import annotations

from .db import get_setting, now
from .library import LibraryError, resolve_audio_path, verify_audio
from .providers import SourceError, parse_youtube_url

ACTIVE_STATES = ("downloading", "converting", "verifying")
FAILED_STATES = ("failed", "interrupted", "unavailable")


def event(conn, job_id: int, status: str, message: str = "") -> None:
    conn.execute("INSERT INTO job_events(job_id,status,message,created_at) VALUES(?,?,?,?)", (job_id, status, str(message)[:2000], now()))


def set_job_state(conn, job_id: int, status: str, *, progress: float | None = None, error: str = "", message: str | None = None) -> None:
    if progress is None:
        conn.execute("UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?", (status, str(error)[:2000], now(), job_id))
    else:
        conn.execute("UPDATE jobs SET status=?,progress=?,error=?,updated_at=? WHERE id=?", (status, max(0.0, min(float(progress), 100.0)), str(error)[:2000], now(), job_id))
    conn.execute("UPDATE sources SET status=? WHERE id=(SELECT source_id FROM jobs WHERE id=?)", (status, job_id))
    if message is not None:
        event(conn, job_id, status, message)


def _existing_valid(source, cfg) -> bool:
    if not source["file_path"]:
        return False
    try:
        verify_audio(resolve_audio_path(cfg, source["file_path"]), cfg, source["duration"])
        return True
    except (LibraryError, OSError, ValueError):
        return False


def enqueue_sources(conn, cfg, ids, retry: bool = False) -> dict:
    """Reserve each source once. Caller commits; available files never download again."""
    if not isinstance(ids, (list, tuple)) or len(ids) > int(cfg.max_queue_size):
        raise SourceError("La sélection dépasse la taille maximale de la file.")
    if any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in ids):
        raise SourceError("La sélection contient un identifiant invalide.")
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    queued, skipped = [], []
    count = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','downloading','converting','verifying')").fetchone()[0]
    for source_id in dict.fromkeys(ids):
        source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        job = conn.execute("SELECT * FROM jobs WHERE source_id=?", (source_id,)).fetchone()
        reason = ""
        if source is None:
            reason = "Morceau introuvable."
        elif _existing_valid(source, cfg):
            conn.execute("UPDATE sources SET status='available' WHERE id=?", (source_id,))
            if job and job["status"] not in ACTIVE_STATES:
                set_job_state(conn, job["id"], "available", progress=100, message="Fichier déjà présent et vérifié ; aucun nouveau téléchargement.")
            reason = "Fichier déjà disponible et vérifié."
        elif not source["url"]:
            reason = "Associez d'abord cette demande à un morceau YouTube précis."
        elif job and job["status"] in ("queued", *ACTIVE_STATES):
            reason = "Ce morceau est déjà dans la file."
        elif retry and (job is None or job["status"] not in FAILED_STATES):
            reason = "Seuls les éléments en échec peuvent être réessayés."
        elif count >= int(cfg.max_queue_size):
            reason = "La file est pleine."
        elif source["duration"] is not None and source["duration"] > int(cfg.max_duration):
            reason = "La durée dépasse la limite configurée."
        else:
            try:
                parsed = parse_youtube_url(source["url"], mode="video")
                if source["video_id"] and parsed["video_id"] != source["video_id"]:
                    raise SourceError("L'identifiant ne correspond pas à la source enregistrée.")
            except SourceError as exc:
                reason = str(exc)
        if reason:
            skipped.append({"source_id": source_id, "reason": reason})
            continue
        if job:
            conn.execute("UPDATE jobs SET status='queued',progress=0,attempts=0,error='',updated_at=? WHERE id=?", (now(), job["id"]))
            job_id = job["id"]
        else:
            stamp = now()
            job_id = conn.execute("INSERT INTO jobs(source_id,status,created_at,updated_at) VALUES(?,'queued',?,?)", (source_id, stamp, stamp)).lastrowid
        conn.execute("UPDATE sources SET status='queued' WHERE id=?", (source_id,))
        event(conn, job_id, "queued", "Téléchargement confirmé par l'administration." if not retry else "Nouvelle tentative explicitement demandée par l'administration.")
        queued.append(source_id)
        count += 1
    return {"queued": queued, "skipped": skipped}


def claim_job(conn) -> dict | None:
    """Claim only one task under SQLite's writer lock; no network in this transaction."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    if get_setting(conn, "queue_paused", False) or get_setting(conn, "offline_mode", False):
        conn.commit()
        return None
    row = conn.execute("SELECT j.*,s.url,s.video_id,s.title,s.artist,s.duration,s.file_path FROM jobs j JOIN sources s ON s.id=j.source_id WHERE j.status='queued' ORDER BY j.id LIMIT 1").fetchone()
    if row is None:
        conn.commit()
        return None
    conn.execute("UPDATE jobs SET status='downloading',progress=0,attempts=attempts+1,error='',updated_at=? WHERE id=? AND status='queued'", (now(), row["id"]))
    conn.execute("UPDATE sources SET status='downloading' WHERE id=?", (row["source_id"],))
    event(conn, row["id"], "downloading", "Démarrage du traitement ; le navigateur peut être fermé.")
    conn.commit()
    result = dict(row)
    result["attempts"] += 1
    return result


def recover_jobs(conn, cfg) -> dict:
    """Called with the worker lock: reconcile statuses with actual verified files."""
    recovered, interrupted = [], []
    for source in conn.execute("SELECT * FROM sources WHERE status='available' OR file_path IS NOT NULL OR id IN (SELECT source_id FROM jobs WHERE status IN ('downloading','converting','verifying','interrupted'))").fetchall():
        job = conn.execute("SELECT * FROM jobs WHERE source_id=?", (source["id"],)).fetchone()
        if _existing_valid(source, cfg):
            conn.execute("UPDATE sources SET status='available' WHERE id=?", (source["id"],))
            if job and job["status"] != "available":
                set_job_state(conn, job["id"], "available", progress=100, message="Fichier existant vérifié après redémarrage.")
            recovered.append(source["id"])
        elif job and job["status"] in (*ACTIVE_STATES, "interrupted", "available"):
            message = "Traitement interrompu ou fichier disponible absent/invalide après redémarrage."
            set_job_state(conn, job["id"], "interrupted", progress=0, error=message, message=message)
            interrupted.append(source["id"])
            if job["attempts"] < int(cfg.max_attempts):
                set_job_state(conn, job["id"], "queued", progress=0, message="Reprise dans la limite des tentatives configurées.")
            else:
                set_job_state(conn, job["id"], "failed", error="Nombre maximal de tentatives atteint après interruption.", message="Utilisez Réessayer pour relancer explicitement cet élément.")
        elif source["status"] == "available":
            conn.execute("UPDATE sources SET status='interrupted' WHERE id=?", (source["id"],))
    conn.commit()
    return {"recovered": recovered, "interrupted": interrupted}


def queue_snapshot(conn) -> list[dict]:
    items = []
    for row in conn.execute("SELECT j.*,s.title,s.artist,s.video_id,s.duration FROM jobs j JOIN sources s ON s.id=j.source_id ORDER BY j.id DESC"):
        item = dict(row)
        item["events"] = [dict(event_row) for event_row in conn.execute("SELECT status,message,created_at FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 20", (row["id"],))][::-1]
        items.append(item)
    return items
