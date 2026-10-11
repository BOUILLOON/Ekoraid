"""Collect a playlist suggestion without expanding or downloading its contents."""
from __future__ import annotations

import unicodedata

from .providers import SourceError, parse_youtube_url


def _text(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise SourceError(f"{label} doit contenir au maximum {maximum} caractères.")
    value = value.strip()
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise SourceError(f"{label} contient un caractère non autorisé.")
    return value


def prepare_youtube_proposal(url: str, title: str = "", artist: str = "", mode: str | None = None, kind: str = "youtube") -> dict:
    """Return canonical collection metadata only; this function never uses a network."""
    if kind not in {"youtube", "playlist"}:
        raise SourceError("Choisis un lien YouTube ou une proposition manuelle.")
    if kind == "playlist":
        if mode not in {None, "playlist"}:
            raise SourceError("Une proposition de playlist doit conserver le choix « Toute la playlist ».")
        mode = "playlist"
    parsed = parse_youtube_url(url, mode=mode)
    if parsed["kind"] == "choice":
        raise SourceError("Ce lien contient un morceau et une playlist. Choisis « Ce morceau seulement » ou « Toute la playlist ».", "need_choice", status=409, need_choice=True)
    title, artist = _text(title, "Le titre", 240), _text(artist, "L’artiste", 160)
    playlist = parsed["kind"] == "playlist"
    return {"video_id": None if playlist else parsed["video_id"], "url": parsed["url"],
            "title": title or ("Playlist YouTube proposée" if playlist else f"Vidéo YouTube ({parsed['video_id']})"),
            "artist": artist, "status": "pending", "source_kind": "playlist" if playlist else "video"}


def classify_source(item) -> str:
    """Derive public type from stored canonical metadata; no schema migration needed."""
    try:
        if item["video_id"]:
            return "video"
    except (KeyError, IndexError, TypeError):
        pass
    try:
        url = item["url"]
    except (KeyError, IndexError, TypeError):
        url = None
    if url:
        try:
            if parse_youtube_url(url)["kind"] == "playlist":
                return "playlist"
        except SourceError:
            pass
    return "manual"
