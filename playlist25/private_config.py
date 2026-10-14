"""Validate and atomically update the private local cloud connection."""
from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from dotenv import set_key

from . import cloud_sync


class PrivateConfigError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def config_lock(cfg):
    """One lock per private file; readers copy the connection under this lock."""
    location = str(Path(cfg.root).resolve())
    with _locks_guard:
        return _locks.setdefault(location, threading.RLock())


def cloud_settings_snapshot(cfg):
    """Keep a request's URL and key together, even if configuration changes."""
    with config_lock(cfg):
        return copy.copy(cfg)


def _private_key(key: str) -> str:
    if not isinstance(key, str) or len(key) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise PrivateConfigError("La clé privée doit tenir sur une seule ligne. Recopiez la clé Secret ou service_role du projet.")
    key = key.strip()
    if not key:
        raise PrivateConfigError("Ajoutez la clé Secret ou service_role de votre projet Supabase. Aucune clé privée n’est encore configurée.")
    if key.startswith("sb_publishable_"):
        raise PrivateConfigError("Cette clé est publique. Dans Supabase → API Keys, copiez la clé Secret ou service_role dans ce champ privé.")
    if key.startswith("sbp_"):
        raise PrivateConfigError("Ce jeton appartient au compte Supabase. Utilisez la clé Secret ou service_role du projet, disponible dans API Keys.")
    if re.fullmatch(r"sb_secret_[A-Za-z0-9_-]+", key):
        return key
    parts = key.split(".")
    if len(parts) == 3 and all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts):
        try:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        except (ValueError, UnicodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("role") == "service_role":
            return key
        if isinstance(payload, dict) and payload.get("role") == "anon":
            raise PrivateConfigError("Cette clé est publique. Utilisez la clé Secret ou service_role du projet dans API Keys.")
    raise PrivateConfigError("La clé ne correspond pas à une clé Secret ou service_role Supabase. Recopiez la clé du projet depuis API Keys.")


def _connection_error(error: Exception) -> PrivateConfigError:
    # Remote error text is untrusted and may contain credentials. Return only
    # our own actionable messages, never a response body or exception details.
    message = str(error)
    if message.startswith("Supabase refuse la synchronisation."):
        text = "Supabase refuse cette clé. Vérifiez la clé Secret/service_role du projet et ses permissions. La configuration précédente est conservée."
    elif message.startswith("La fonction de collecte manque."):
        text = "Ce projet ne fournit pas la collecte attendue. Vérifiez le projet et son installation Supabase. La configuration précédente est conservée."
    elif message.startswith("Connexion à Supabase impossible."):
        text = "Connexion à Supabase impossible. Vérifiez Internet et l’adresse du projet, puis réessayez. La configuration précédente est conservée."
    else:
        text = "La connexion n’a pas pu être vérifiée. Vérifiez l’adresse du projet et la clé Secret/service_role, puis réessayez. La configuration précédente est conservée."
    return PrivateConfigError(text, 503)


def _atomic_cloud_file(cfg, url: str, key: str) -> None:
    home = Path(cfg.root).resolve()
    path = home / ".env.local"
    temporary = None
    try:
        if path.is_symlink():
            raise OSError("Private configuration must be a regular local file.")
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        home.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".env-cloud-", suffix=".tmp", dir=home)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            output.write(original)
        updates = {"SUPABASE_URL": url, "SUPABASE_SERVICE_ROLE_KEY": key}
        if not path.exists():
            # A process configured entirely through its environment must keep
            # the same administrator identity and data after its next restart.
            updates.update(ADMIN_PASSWORD=cfg.admin_password, SESSION_SECRET=cfg.session_secret,
                           DATA_DIR=str(cfg.data_dir))
        for name, value in updates.items():
            set_key(str(temporary), name, value, quote_mode="always")
        temporary.chmod(0o600)
        # Windows FlushFileBuffers needs a writable handle as well.
        with temporary.open("r+b") as written:
            os.fsync(written.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, UnicodeError):
        raise PrivateConfigError("La configuration privée n’a pas pu être enregistrée sur ce PC. Vérifiez les permissions et l’espace disque ; la configuration précédente est conservée.", 503) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def configure_cloud(cfg, project_url: str, key: str | None = None) -> dict:
    """Read the remote collection first; never reset, publish or import it."""
    with config_lock(cfg):
        if key is not None and (not isinstance(key, str) or any(ord(c) < 32 or ord(c) == 127 for c in key)):
            _private_key(key)
        raw_key = key if key and key.strip() else cfg.supabase_service_role_key
        private_key = _private_key(raw_key)
        if (not isinstance(project_url, str) or len(project_url) > 2048
                or any(ord(c) < 32 or ord(c) == 127 for c in project_url)):
            raise PrivateConfigError("Collez l’adresse HTTPS du projet Supabase dans le champ Adresse du projet.")
        url = project_url.strip().rstrip("/")
        candidate = copy.copy(cfg)
        candidate.supabase_url, candidate.supabase_service_role_key = url, private_key
        try:
            cloud_sync._project(candidate)
        except cloud_sync.CloudSyncError:
            raise PrivateConfigError("Collez l’adresse HTTPS de votre projet, sous la forme https://projet.supabase.co, sans chemin ni identifiants.") from None
        try:
            cloud_sync._validate(cloud_sync.fetch_snapshot(candidate))
        except (cloud_sync.CloudSyncError, OSError, ValueError) as error:
            raise _connection_error(error) from None
        _atomic_cloud_file(cfg, url, private_key)
        cfg.supabase_url, cfg.supabase_service_role_key = url, private_key
        return {"configured": True, "url": url, "secret_present": True,
                "message": "Connexion vérifiée et enregistrée. Vous pouvez récupérer les propositions sans arrêter le studio. La collecte publique est conservée."}
