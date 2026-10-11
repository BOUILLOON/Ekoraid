"""Strict YouTube sources and bounded metadata-only yt-dlp operations."""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,150}$")
ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}


class SourceError(ValueError):
    def __init__(self, message: str, code: str = "invalid_source", *, status: int = 400, need_choice: bool = False):
        super().__init__(message)
        self.message, self.code, self.status, self.need_choice = message, code, status, need_choice


def parse_youtube_url(url: str, mode: str | None = None) -> dict:
    """Reconstruct a permitted URL; never pass an untrusted URL to a fetcher."""
    if not isinstance(url, str) or not 1 <= len(url.strip()) <= 2048:
        raise SourceError("Collez un lien YouTube valide.")
    raw = url.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in raw) or "\\" in raw:
        raise SourceError("Le lien contient des caractères interdits.")
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise SourceError("Le lien YouTube est invalide.") from exc
    if parsed.scheme not in {"https", "http"} or host not in ALLOWED_HOSTS or parsed.username or parsed.password or port not in {None, 80, 443}:
        raise SourceError("Seuls les liens directs youtube.com et youtu.be sont acceptés.")
    if mode not in {None, "video", "playlist"}:
        raise SourceError("Choisissez ce morceau ou toute la playlist.")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(len(query.get(name, [])) > 1 for name in ("v", "list")):
        raise SourceError("Le lien contient plusieurs identifiants incompatibles.")
    playlist_id = query.get("list", [None])[0]
    video_id = None
    if host in {"youtu.be", "www.youtu.be"}:
        match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", parsed.path)
        if match:
            video_id = match.group(1)
        else:
            raise SourceError("Le chemin du lien youtu.be est invalide.")
    elif parsed.path in {"/watch", "/watch/"}:
        video_id = query.get("v", [None])[0]
    elif parsed.path in {"/playlist", "/playlist/"}:
        if query.get("v"):
            raise SourceError("Cette adresse de playlist est invalide.")
    else:
        match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?", parsed.path)
        if match:
            video_id = match.group(1)
        else:
            raise SourceError("Les chaînes, radios et pages YouTube générales ne sont pas acceptées.")
    if video_id is not None and not VIDEO_ID.fullmatch(video_id):
        raise SourceError("L'identifiant de la vidéo est invalide.")
    if playlist_id is not None:
        if not PLAYLIST_ID.fullmatch(playlist_id) or playlist_id.startswith(("RD", "UL", "UU")) or playlist_id in {"WL", "LL"}:
            raise SourceError("Les radios, flux de chaîne et listes personnelles sont refusés. Utilisez une playlist ordinaire accessible.")
    if not video_id and not playlist_id:
        raise SourceError("Le lien ne contient ni morceau ni playlist.")
    if mode == "video" and not video_id:
        raise SourceError("Cette adresse est une playlist. Collez le lien d'un morceau.")
    if mode == "playlist" and not playlist_id:
        raise SourceError("Cette adresse ne contient pas de playlist.")
    if mode == "video" or (video_id and not playlist_id):
        kind, canonical = "video", f"https://www.youtube.com/watch?v={video_id}"
    elif mode == "playlist" or (playlist_id and not video_id):
        kind, canonical = "playlist", f"https://www.youtube.com/playlist?list={playlist_id}"
    else:
        kind, canonical = "choice", f"https://www.youtube.com/watch?v={video_id}&list={playlist_id}"
    return {"video_id": video_id, "playlist_id": playlist_id, "kind": kind, "url": canonical}


def yt_dlp_command(cfg) -> list[str]:
    """Use the current Python environment, or an explicitly configured executable."""
    executable = getattr(cfg, "yt_dlp_path", "")
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--yt-dlp"]
    elif not executable or executable == "yt-dlp" or executable == sys.executable:
        command = [sys.executable, "-m", "yt_dlp"]
    elif isinstance(executable, (list, tuple)):
        command = list(executable)
    elif executable == f"{sys.executable} -m yt_dlp":
        command = [sys.executable, "-m", "yt_dlp"]
    else:
        command = [str(executable)]
    # No user config, cookies, proxy rotation or downloaded executable components.
    command += ["--ignore-config", "--no-plugin-dirs", "--no-cache-dir", "--no-remote-components", "--no-js-runtimes", "--js-runtimes", str(getattr(cfg, "js_runtime", "node")),
                "--xff", "never", "--socket-timeout", "20", "--retries", "1", "--fragment-retries", "1", "--extractor-retries", "1"]
    return command


def _metadata(target: str, cfg, *, playlist: bool = False, limit: int | None = None) -> tuple[dict, str]:
    command = yt_dlp_command(cfg) + ["--skip-download", "--dump-single-json", "--no-progress"]
    if playlist:
        command += ["--flat-playlist", "--ignore-errors"]
        if limit is not None:
            command += ["--playlist-end", str(limit)]
    else:
        command += ["--no-playlist"]
    command += ["--", target]
    try:
        timeout = getattr(cfg, "task_timeout", 600)
        if not playlist or limit is not None:
            timeout = min(timeout, 120)
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SourceError("L'analyse YouTube a dépassé son délai. Réessayez ou proposez le titre manuellement.", "timeout", status=503) from exc
    except OSError as exc:
        raise SourceError("yt-dlp ne démarre pas. Vérifiez les diagnostics et l'installation.", "missing_tool", status=503) from exc
    try:
        info = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        reason = _safe_reason(result.stderr)
        raise SourceError(f"YouTube n'a pas fourni de résultat exploitable. {reason}", "provider_unavailable", status=503) from exc
    if not isinstance(info, dict) or result.returncode and not playlist:
        raise SourceError(f"Ce morceau est inaccessible. {_safe_reason(result.stderr)}", "unavailable", status=503)
    diagnostic = _safe_reason(result.stderr)
    if result.returncode:
        diagnostic = f"ERROR: analyse interrompue (code {result.returncode}). {diagnostic}"
    return info, diagnostic


def _safe_reason(text: str) -> str:
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line).strip() for line in str(text).splitlines() if line.strip()]
    return " ".join(lines[-3:])[:1000]


def _duration(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _entry(info, position: int, cfg) -> dict:
    if not isinstance(info, dict):
        return {"position": position, "video_id": None, "url": None, "title": "Élément non résolu", "artist": "", "channel": "", "duration": None, "thumbnail": "", "available": False, "reason": "YouTube n'a pas retourné cet élément.", "already_available": False}
    video_id = str(info.get("id") or "")
    valid_id = bool(VIDEO_ID.fullmatch(video_id))
    title = str(info.get("title") or "Titre non communiqué")[:500]
    duration = _duration(info.get("duration"))
    available, reason = valid_id, "" if valid_id else "Identifiant de vidéo absent ou invalide."
    availability = info.get("availability")
    if availability in {"private", "premium_only", "subscriber_only", "needs_auth", "unavailable"} or title.lower() in {"[private video]", "private video", "[deleted video]", "deleted video"}:
        available, reason = False, "Vidéo privée, supprimée ou réservée à un accès authentifié."
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        available, reason = False, "Les directs en cours et à venir sont refusés."
    if duration is not None and duration > getattr(cfg, "max_duration", 1800):
        available, reason = False, "Durée supérieure à la limite configurée."
    artist = info.get("artist") or ""
    if not artist and isinstance(info.get("artists"), list):
        artist = ", ".join(str(a) for a in info["artists"] if a)
    return {"position": position, "video_id": video_id if valid_id else None, "url": f"https://www.youtube.com/watch?v={video_id}" if valid_id else None,
            "title": title, "artist": str(artist)[:300], "channel": str(info.get("channel") or info.get("uploader") or "")[:300],
            "duration": duration, "thumbnail": str(info.get("thumbnail") or "")[:2048], "available": available, "reason": reason, "already_available": False}


def search(query: str, cfg) -> list[dict]:
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200 or any(ord(c) < 32 for c in query):
        raise SourceError("Saisissez un artiste ou un titre de 2 à 200 caractères.")
    limit = max(1, min(int(getattr(cfg, "search_limit", 5)), 10))
    info, _ = _metadata(f"ytsearch{limit}:{query.strip()}", cfg, playlist=True, limit=limit)
    return [entry for i, item in enumerate(info.get("entries") or [], 1) if (entry := _entry(item, i, cfg))["available"]]


def _playlist_entries(raw_entries: list, cfg, cap: int | None) -> tuple[list[dict], list[str], bool, int]:
    """Keep reported original positions; incomplete responses never invent certainty."""
    entries, warnings, gaps = [], [], []
    previous_position, known_indices, unknown_indices = 0, 0, 0
    unresolved = []
    for item in raw_entries if cap is None else raw_entries[:cap]:
        original = item.get("playlist_index") if isinstance(item, dict) else None
        if original is not None:
            if isinstance(original, str) and original.isascii() and original.isdecimal():
                original = int(original)
            if isinstance(original, bool) or not isinstance(original, int) or original < 1:
                raise SourceError("YouTube a retourné une position de playlist invalide. Réessayez l'analyse.", "invalid_provider_result", status=502)
            if original <= previous_position:
                raise SourceError("YouTube a retourné des positions de playlist répétées ou dans le désordre. Réessayez l'analyse.", "invalid_provider_result", status=502)
            position = original
            known_indices += 1
        else:
            position = previous_position + 1
            unknown_indices += 1
        if position > previous_position + 1:
            gaps.append((previous_position + 1, position - 1))
        entry = _entry(item, position, cfg)
        entries.append(entry)
        if not isinstance(item, dict) or not entry["video_id"]:
            unresolved.append(position)
        previous_position = position
    complete = not gaps and not unresolved
    if gaps:
        ranges = ", ".join(str(first) if first == last else f"{first} à {last}" for first, last in gaps[:10])
        suffix = " ; d'autres plages manquent également" if len(gaps) > 10 else ""
        warnings.append(f"Positions non résolues dans la playlist d'origine : {ranges}{suffix}. Les numéros connus sont conservés ; l'analyse est incomplète.")
    if unresolved:
        positions = ", ".join(map(str, unresolved[:20]))
        suffix = "…" if len(unresolved) > 20 else ""
        warnings.append(f"Éléments non résolus aux positions {positions}{suffix}. Ils ne peuvent pas être sélectionnés pour téléchargement.")
    if known_indices and unknown_indices:
        complete = False
        warnings.append("Certaines positions d'origine ne sont pas communiquées par YouTube. Leurs numéros sont déduits de l'ordre reçu ; l'analyse est incomplète.")
    return entries, warnings, complete, known_indices


def analyze(url: str, cfg, mode: str | None = None) -> dict:
    parsed = parse_youtube_url(url, mode)
    if parsed["kind"] == "choice":
        raise SourceError("Ce lien contient un morceau et une playlist. Choisissez ce morceau seulement ou toute la playlist.", "need_choice", status=409, need_choice=True)
    is_playlist = parsed["kind"] == "playlist"
    configured_limit = int(getattr(cfg, "max_playlist_items", 0))
    cap = configured_limit if configured_limit > 0 else None
    info, diagnostic = _metadata(parsed["url"], cfg, playlist=is_playlist, limit=cap + 1 if cap is not None else None)
    raw_entries = info.get("entries") if is_playlist else [info]
    if not isinstance(raw_entries, list):
        raise SourceError("La playlist n'a pas fourni de liste exploitable.", "unavailable", status=503)
    if is_playlist:
        entries, warnings, complete, known_indices = _playlist_entries(raw_entries, cfg, cap)
    else:
        entries, warnings, complete, known_indices = [_entry(info, 1, cfg)], [], True, 0
    raw_count = info.get("playlist_count") or info.get("n_entries")
    count = int(raw_count) if isinstance(raw_count, (int, float)) and not isinstance(raw_count, bool) and math.isfinite(raw_count) and raw_count >= 0 and int(raw_count) == raw_count else None
    if cap is not None and (len(raw_entries) > cap or count is not None and count > cap):
        complete = False
        warnings.append(f"Limite de {cap} éléments atteinte : cette analyse n'est pas complète. Augmentez la limite explicitement pour récupérer la suite.")
    elif count is not None and count > len(raw_entries):
        complete = False
        message = f"YouTube annonce {count} éléments, mais seulement {len(raw_entries)} ont été retournés."
        if not known_indices:
            message += " Les positions d'origine manquantes ne sont pas connues."
        elif entries and entries[-1]["position"] < count:
            first = entries[-1]["position"] + 1
            message += f" Positions finales non résolues : {first}" + (f" à {count}." if first != count else ".")
        warnings.append(message)
    if diagnostic and ("ERROR:" in diagnostic or "unavailable" in diagnostic.lower()):
        complete = False
        warnings.append(f"L'analyse a signalé une difficulté : {diagnostic}")
    if not entries:
        complete = False
        warnings.append("Aucun élément résolu ; la playlist peut être vide ou inaccessible.")
    return {"kind": parsed["kind"], "title": str(info.get("title") or "Playlist YouTube")[:500], "source_url": parsed["url"], "complete": complete, "warnings": warnings, "entries": entries}


def analyze_links(text: str, cfg) -> dict:
    if not isinstance(text, str):
        raise SourceError("Collez un lien YouTube par ligne.")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise SourceError("Collez au moins un lien YouTube.")
    configured_limit = int(getattr(cfg, "max_playlist_items", 0))
    cap = configured_limit if configured_limit > 0 else None
    entries, warnings = [], []
    complete = cap is None or len(lines) <= cap
    if not complete:
        warnings.append(f"Le texte contient {len(lines)} liens ; seuls les {cap} premiers sont analysés. Augmentez la limite explicitement pour la suite.")
    for position, line in enumerate(lines if cap is None else lines[:cap], 1):
        try:
            parsed = parse_youtube_url(line)
            if parsed["kind"] == "choice":
                raise SourceError("Un lien contient aussi une playlist. Choisissez un import de playlist ou retirez le paramètre list.", "need_choice", status=409, need_choice=True)
            if parsed["kind"] != "video":
                raise SourceError("Ce champ accepte une URL de morceau par ligne, pas des playlists.")
            result = analyze(parsed["url"], cfg, mode="video")
            entry = result["entries"][0]
            entry["position"] = position
            entries.append(entry)
        except SourceError as exc:
            if exc.need_choice:
                raise
            entry = _entry(None, position, cfg)
            entry["reason"] = str(exc)
            entries.append(entry)
            complete = False
    return {"kind": "links", "title": "Liens importés", "source_url": "", "complete": complete, "warnings": warnings, "entries": entries}
