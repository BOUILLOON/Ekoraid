"""Short-lived direct-LAN transfer of one prepared ZIP, never public audio access."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import formatdate, parsedate_to_datetime
import hashlib
from html import escape
import io
import ipaddress
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import uuid
from urllib.parse import urlsplit
import zipfile

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field, StrictStr
import psutil
import qrcode

from .db import connect, now
from .security import rate_limit, require_admin, require_csrf

TRANSFERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS transfers(
 id TEXT PRIMARY KEY,export_id TEXT NOT NULL REFERENCES exports(id),
 token_hash TEXT UNIQUE NOT NULL,host TEXT NOT NULL,archive_relative TEXT NOT NULL,
 size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,device INTEGER NOT NULL,inode INTEGER NOT NULL,
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS transfers_token_idx ON transfers(token_hash);
"""
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-f0-9]{32}$")
PRIVATE_V4 = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
PRIVATE_V6 = ipaddress.ip_network("fc00::/7")
MAX_LIFETIME = 600
MAX_ACTIVE_TRANSFERS = 20
SECURITY_HEADERS = {
    "Cache-Control": "no-store, private", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
}


class TransferError(ValueError):
    pass


@contextmanager
def _connection(cfg):
    conn = connect(cfg)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _address(value):
    try:
        if not isinstance(value, str) or "%" in value:
            return None
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address
    except ValueError:
        return None


def _private(address) -> bool:
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in PRIVATE_V4)
    return isinstance(address, ipaddress.IPv6Address) and address in PRIVATE_V6


def _direct_client(value) -> bool:
    address = _address(value)
    return bool(address and (_private(address) or address.is_loopback or isinstance(address, ipaddress.IPv6Address) and address.is_link_local))


def transfer_addresses(cfg) -> list[dict]:
    """No network calls: offer actual active private interfaces the guest socket binds."""
    result = []
    try:
        interfaces, statuses = psutil.net_if_addrs(), psutil.net_if_stats()
    except (OSError, RuntimeError):
        return []
    bound = str(cfg.guest_host)
    for interface, records in interfaces.items():
        label = interface.lower()
        if label.startswith(("lo", "docker", "veth", "br-", "virbr", "tun", "tap", "tailscale", "wg")) or any(word in label for word in ("loopback", "vpn", "virtualbox", "vethernet", "hyper-v")):
            continue
        status = statuses.get(interface)
        if status is not None and not status.isup:
            continue
        for record in records:
            if record.family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            address = _address(record.address)
            if not address or not _private(address):
                continue
            if bound == "0.0.0.0" and address.version != 4 or bound == "::" and address.version != 6:
                continue
            if bound not in {"0.0.0.0", "::"} and _address(bound) != address:
                continue
            host = str(address)
            if not any(item["host"] == host for item in result):
                result.append({"host": host, "label": f"{interface} — {host}"})
    preferred = _address(urlsplit(str(getattr(cfg, "local_url", ""))).hostname)
    result.sort(key=lambda item: (item["host"] != str(preferred), item["host"]))
    return result


def _archive_path(cfg, value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise TransferError("Le chemin de l'archive est invalide.")
    base = Path(cfg.export_dir).resolve()
    raw = Path(value)
    # Internal export rows use native absolute paths on Windows; stored transfer
    # paths are always relative POSIX paths, never user-supplied Windows paths.
    if "\\" in value and (os.name != "nt" or not raw.is_absolute()):
        raise TransferError("Le chemin de l'archive est invalide.")
    if any(part in {"..", "."} for part in raw.parts):
        raise TransferError("L'archive doit rester dans le dossier des exports.")
    candidate = raw if raw.is_absolute() else base / raw
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise TransferError("L'archive doit rester dans le dossier des exports.") from exc
    if not relative.parts or candidate.suffix.lower() != ".zip":
        raise TransferError("Ce partage exige un pack ZIP déjà préparé.")
    component = base
    try:
        for part in relative.parts:
            component = component / part
            if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
                raise TransferError("Une archive partagée ne peut pas être un lien symbolique.")
        metadata = candidate.stat(follow_symlinks=False)
    except OSError as exc:
        raise TransferError("L'archive n'existe plus. Préparez un nouveau pack ZIP.") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size == 0 or candidate.resolve() != candidate:
        raise TransferError("L'archive doit être un fichier ZIP normal dans le dossier des exports.")
    return candidate


def _open_archive(cfg, value: str):
    path = _archive_path(cfg, value)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        file = os.fdopen(descriptor, "rb")
    except OSError as exc:
        raise TransferError("Impossible de lire cette archive.") from exc
    try:
        metadata = os.fstat(file.fileno())
        named = path.stat(follow_symlinks=False)
    except OSError as exc:
        file.close()
        raise TransferError("Impossible de lire cette archive.") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or _signature(metadata) != _signature(named):
        file.close()
        raise TransferError("L'archive n'est pas un fichier lisible.")
    return path, file, metadata


def _signature(metadata) -> tuple[int, int, int, int]:
    return metadata.st_size, metadata.st_mtime_ns, metadata.st_dev, metadata.st_ino


def _download_range(header: str | None, size: int):
    """Support one bounded byte range, including suffix/resume requests."""
    if header is None:
        return None
    if len(header) > 256:
        raise ValueError("Range too long")
    if not header.lower().startswith("bytes="):
        return None
    match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", header, flags=re.I)
    if not match or not any(match.groups()):
        raise ValueError("Invalid byte range")
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError("Empty suffix")
        return max(0, size - suffix), size - 1
    start, end = int(first), int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable range")
    return start, min(end, size - 1)


def _if_range_matches(value: str | None, etag: str, modified: int) -> bool:
    if value is None:
        return True
    if value == etag:
        return True
    try:
        date = parsedate_to_datetime(value)
        return date.tzinfo is not None and int(date.timestamp()) >= modified
    except (ValueError, TypeError, OverflowError):
        return False


def archive_response(request: Request, file, metadata, filename: str):
    """Stream from the authorized open descriptor, with resumable HTTP ranges."""
    signature = _signature(metadata)
    size = metadata.st_size
    modified = metadata.st_mtime_ns // 1_000_000_000
    etag = '"' + hashlib.sha256(repr(signature).encode("ascii")).hexdigest() + '"'
    headers = {**SECURITY_HEADERS, "Accept-Ranges": "bytes", "ETag": etag,
               "Last-Modified": formatdate(modified, usegmt=True),
               "Content-Disposition": f'attachment; filename="{filename}"'}
    try:
        ranges = request.headers.getlist("range")
        if_range = request.headers.get("if-range")
        bounds = None
        if _if_range_matches(if_range, etag, modified):
            if len(ranges) > 1:
                raise ValueError("Multiple range headers")
            bounds = _download_range(ranges[0] if ranges else None, size)
    except ValueError:
        file.close()
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}", "Content-Length": "0"})
    start, end = bounds if bounds is not None else (0, size - 1)
    headers["Content-Length"] = str(end - start + 1)
    if bounds is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        file.close()
        return Response(status_code=206 if bounds is not None else 200, headers=headers, media_type="application/zip")
    def chunks():
        remaining = end - start + 1
        try:
            file.seek(start)
            while remaining:
                if _signature(os.fstat(file.fileno())) != signature:
                    raise OSError("Prepared ZIP changed during download")
                chunk = file.read(min(64 * 1024, remaining))
                if not chunk:
                    raise OSError("Prepared ZIP ended before its declared length")
                remaining -= len(chunk)
                yield chunk
        finally:
            file.close()
    return StreamingResponse(chunks(), status_code=206 if bounds is not None else 200,
                             media_type="application/zip", headers=headers, background=BackgroundTask(file.close))


def _qr_data_url(url: str) -> str:
    buffer = io.BytesIO()
    qrcode.make(url).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def create_transfer(conn, cfg, export_id: str, host: str) -> dict:
    if not isinstance(export_id, str) or not IDENTIFIER_PATTERN.fullmatch(export_id):
        raise TransferError("Choisissez un pack ZIP déjà préparé.")
    address = _address(host)
    if not address or not _private(address) or str(address) not in {entry["host"] for entry in transfer_addresses(cfg)}:
        raise TransferError("Choisissez une adresse privée active de ce PC dans la liste. Connectez le PC et le téléphone au même Wi-Fi.")
    host = str(address)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    created = datetime.now(timezone.utc)
    if conn.execute("SELECT count(*) FROM transfers WHERE revoked=0 AND expires_at>?", (created.isoformat(),)).fetchone()[0] >= MAX_ACTIVE_TRANSFERS:
        raise TransferError("Trop de partages actifs. Fermez un partage ou attendez son expiration.")
    row = conn.execute("SELECT archive FROM exports WHERE id=?", (export_id,)).fetchone()
    if row is None or not row["archive"]:
        raise TransferError("Ce pack n'a pas d'archive ZIP. Préparez d'abord le pack avec l'option ZIP.")
    path, file, metadata = _open_archive(cfg, row["archive"])
    try:
        if not zipfile.is_zipfile(file) or _signature(metadata) != _signature(os.fstat(file.fileno())):
            raise TransferError("L'archive ZIP est invalide ou a changé. Préparez un nouveau pack.")
    finally:
        file.close()
    token, transfer_id = secrets.token_urlsafe(32), uuid.uuid4().hex
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    expiration = created + timedelta(seconds=MAX_LIFETIME)
    relative = path.relative_to(Path(cfg.export_dir).resolve()).as_posix()
    conn.execute("INSERT INTO transfers(id,export_id,token_hash,host,archive_relative,size,mtime_ns,device,inode,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (transfer_id, export_id, token_hash, host, relative, *_signature(metadata), created.isoformat(), expiration.isoformat()))
    url_host = f"[{host}]" if address.version == 6 else host
    url = f"http://{url_host}:{int(cfg.guest_port)}/transfer/{token}"
    return {"id": transfer_id, "url": url, "expires_at": expiration.isoformat(), "size": metadata.st_size,
            "filename": f"Anniversaire25-{export_id[:8]}.zip", "qr_data_url": _qr_data_url(url)}


def revoke_transfer(conn, transfer_id: str) -> bool:
    if not isinstance(transfer_id, str) or not IDENTIFIER_PATTERN.fullmatch(transfer_id):
        raise TransferError("Identifiant de partage invalide.")
    return conn.execute("UPDATE transfers SET revoked=1 WHERE id=? AND revoked=0", (transfer_id,)).rowcount == 1


def _request_allowed(request: Request, host: str, cfg) -> bool:
    if request.client is None or not _direct_client(request.client.host):
        return False
    raw_headers = request.scope.get("headers", [])
    proxy_headers = {b"forwarded", b"via", b"x-real-ip", b"true-client-ip", b"cf-connecting-ip", b"cf-visitor", b"cf-ray", b"cloudfront-viewer-address", b"fastly-client-ip", b"x-cluster-client-ip"}
    for name, _ in raw_headers:
        name = name.lower()
        if name in proxy_headers or name.startswith(b"cf-") or b"forwarded" in name or b"proxy" in name:
            return False
    authorities = [value.decode("latin-1") for name, value in raw_headers if name.lower() == b"host"]
    expected = f"[{host}]:{cfg.guest_port}" if ":" in host else f"{host}:{cfg.guest_port}"
    return len(authorities) == 1 and authorities[0].lower() == expected.lower()


def _authorized_archive(request: Request, cfg, token: str):
    # Invalid, expired, revoked and non-LAN attempts all have the same response.
    denied = HTTPException(404, "Partage indisponible. Demandez un nouveau QR code à l'organisation et connectez-vous au même Wi-Fi.")
    if not TOKEN_PATTERN.fullmatch(token):
        raise denied
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    with _connection(cfg) as conn:
        row = conn.execute("SELECT * FROM transfers WHERE token_hash=?", (token_hash,)).fetchone()
    if row is None:
        raise denied
    try:
        expiration = datetime.fromisoformat(row["expires_at"])
        if expiration.tzinfo is None:
            raise ValueError("missing timezone")
        active = not row["revoked"] and expiration > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        active = False
    if not active or not _request_allowed(request, row["host"], cfg):
        raise denied
    try:
        path, file, metadata = _open_archive(cfg, row["archive_relative"])
    except (TransferError, OSError):
        raise denied
    if _signature(metadata) != (row["size"], row["mtime_ns"], row["device"], row["inode"]):
        file.close()
        raise denied
    return row, file


class _TransferHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/transfer/"):
            return await self.app(scope, receive, send)
        async def protected_send(message):
            if message["type"] == "http.response.start":
                controlled = {key.lower().encode() for key in SECURITY_HEADERS}
                headers = [(name, value) for name, value in message.get("headers", []) if name.lower() not in controlled]
                headers.extend((key.lower().encode(), value.encode()) for key, value in SECURITY_HEADERS.items())
                message = {**message, "headers": headers}
            await send(message)
        await self.app(scope, receive, protected_send)


def attach_guest_routes(app, cfg) -> None:
    app.add_middleware(_TransferHeaders)

    @app.get("/transfer/{token}", response_class=HTMLResponse, include_in_schema=False)
    def transfer_page(token: str, request: Request):
        row, file = _authorized_archive(request, cfg, token)
        file.close()
        filename = f"Anniversaire25-{row['export_id'][:8]}.zip"
        # All variable text is escaped; the sole URL is a fixed route + strict token.
        html = f'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>Récupérer le pack musical</title><link rel="icon" href="/static/placeholder.svg" type="image/svg+xml"><link rel="stylesheet" href="/static/styles.css"></head><body class="transfer-page"><header class="topbar shell"><div class="brand"><span class="brand-icon" aria-hidden="true">♪</span><span>la playlist<span class="brand-sub">{escape(str(cfg.event_name))}</span></span></div></header><main class="shell"><section class="panel transfer-panel"><span class="eyebrow">Le pack pour la soirée</span><h1>Récupérer le pack musical</h1><p>L'organisation partage ce pack sur votre Wi-Fi. Ce lien expire après dix minutes.</p><p class="transfer-file">{escape(filename)} · {row['size'] / 1048576:.1f} Mo</p><p><a class="button primary transfer-download" href="/transfer/{escape(token, quote=True)}/download" download>Télécharger le pack ZIP <span aria-hidden="true">↓</span></a></p><p class="muted">Sur téléphone ou tablette, ouvrez ensuite le ZIP dans l'application Fichiers et extrayez son dossier audio.</p></section></main></body></html>'''
        return HTMLResponse(html, headers=SECURITY_HEADERS)

    @app.api_route("/transfer/{token}/download", methods=["GET", "HEAD"], include_in_schema=False)
    def transfer_download(token: str, request: Request):
        row, file = _authorized_archive(request, cfg, token)
        metadata = os.fstat(file.fileno())
        if _signature(metadata) != (row["size"], row["mtime_ns"], row["device"], row["inode"]):
            file.close()
            raise HTTPException(404, "Le pack a changé. Demandez un nouveau partage.")
        return archive_response(request, file, metadata, f"Anniversaire25-{row['export_id'][:8]}.zip")


class _CreateTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    export_id: StrictStr = Field(pattern=r"^[a-f0-9]{32}$")
    host: StrictStr = Field(min_length=3, max_length=64)


class _RevokeTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr = Field(pattern=r"^[a-f0-9]{32}$")


def attach_admin_routes(app, cfg) -> None:
    def admin(request: Request):
        require_admin(request, cfg)
    def mutation(request: Request):
        require_admin(request, cfg)
        require_csrf(request)
        rate_limit(request, "transfer-create", 30)

    @app.get("/api/transfers/addresses", dependencies=[Depends(admin)])
    def addresses():
        return {"addresses": transfer_addresses(cfg), "expires_in": MAX_LIFETIME}

    @app.post("/api/transfers", dependencies=[Depends(mutation)])
    def create(payload: _CreateTransfer):
        with _connection(cfg) as conn:
            try:
                return create_transfer(conn, cfg, payload.export_id, payload.host)
            except TransferError as exc:
                raise HTTPException(400, str(exc)) from exc

    @app.post("/api/transfers/revoke", dependencies=[Depends(mutation)])
    def revoke(payload: _RevokeTransfer):
        with _connection(cfg) as conn:
            try:
                revoked = revoke_transfer(conn, payload.id)
            except TransferError as exc:
                raise HTTPException(400, str(exc)) from exc
        return {"revoked": revoked}
