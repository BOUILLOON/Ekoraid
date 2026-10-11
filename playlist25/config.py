from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

SOURCE_ROOT = Path(__file__).resolve().parent.parent
# The Windows executable keeps its private configuration and music outside the
# read-only PyInstaller bundle. The launcher supplies this path before import.
ROOT = Path(os.environ.get("PLAYLIST25_HOME") or SOURCE_ROOT).expanduser().resolve()

@dataclass
class Settings:
    root: Path = ROOT
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    event_name: str = "Les 25 ans de Fanny"
    guest_host: str = "0.0.0.0"
    guest_port: int = 8000
    admin_host: str = "127.0.0.1"
    admin_port: int = 8001
    admin_password: str = ""
    session_secret: str = ""
    cookie_secure: bool = False
    public_url: str = ""
    local_url: str = "http://127.0.0.1:8000"
    max_proposals: int = 0
    max_playlist_items: int = 0
    max_duration: int = 1800
    max_upload_mb: int = 100
    max_queue_size: int = 0
    min_free_mb: int = 256
    task_timeout: int = 600
    max_attempts: int = 2
    search_limit: int = 5
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    js_runtime: str = "node"
    yt_dlp_path: str = sys.executable
    output_format: str = "mp3"
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.data_dir = Path(self.data_dir).resolve()
        if self.admin_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("ADMIN_HOST doit rester une adresse de boucle locale. Exposez seulement le service invités.")
        if self.guest_port == self.admin_port:
            raise ValueError("Les ports invités et administrateur doivent être différents.")
        for name in ("max_duration", "max_upload_mb", "task_timeout", "max_attempts", "search_limit"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} doit être positif.")
        for name in ("max_proposals", "max_playlist_items", "max_queue_size"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} doit être positif ou nul (0 signifie illimité).")
        if self.min_free_mb < 0:
            raise ValueError("min_free_mb doit être positif ou nul.")
        if self.output_format not in {"mp3", "source"}:
            raise ValueError("OUTPUT_FORMAT doit être mp3 ou source.")
        if self.supabase_url:
            url = urlsplit(self.supabase_url)
            if url.scheme != "https" or not url.hostname or not url.hostname.endswith(".supabase.co") or url.username or url.password or url.port or url.path not in {"", "/"}:
                raise ValueError("SUPABASE_URL doit être l’URL HTTPS de votre projet *.supabase.co.")
            self.supabase_url = self.supabase_url.rstrip("/")

    @property
    def db_path(self): return self.data_dir / "playlist.sqlite3"
    @property
    def audio_dir(self): return self.data_dir / "audio"
    @property
    def download_dir(self): return self.audio_dir
    @property
    def temp_dir(self): return self.data_dir / "temporary"
    @property
    def export_dir(self): return self.data_dir / "exports"

    def ensure_directories(self):
        for directory in (self.data_dir, self.audio_dir, self.temp_dir, self.export_dir):
            directory.mkdir(parents=True, exist_ok=True)


def initialize_local_config(root: Path = ROOT) -> Path:
    """Create first-run random credentials privately; never overwrite user configuration."""
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".env.local"
    if not path.exists():
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return path
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write("# Configuration locale privée. Ne pas publier ni partager ce fichier.\n")
            output.write(f"ADMIN_PASSWORD={secrets.token_urlsafe(24)}\nSESSION_SECRET={secrets.token_urlsafe(48)}\n")
    return path


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env.local", override=False)
    kwargs = {}
    for name, definition in Settings.__dataclass_fields__.items():
        if name == "root": continue
        value = os.environ.get(name.upper())
        if value is None: continue
        if name == "data_dir":
            candidate = Path(value).expanduser()
            kwargs[name] = candidate if candidate.is_absolute() else ROOT / candidate
        elif definition.type == "int":
            kwargs[name] = int(value)
        elif definition.type == "bool":
            kwargs[name] = value.lower() in {"1", "true", "yes", "oui"}
        else:
            kwargs[name] = value
    result = Settings(**kwargs)
    result.ensure_directories()
    if not result.session_secret:
        raise ValueError("SESSION_SECRET absent : lancez python -m playlist25 init pour créer la configuration privée.")
    return result
