"""A single, restartable subprocess worker for authorized audio downloads."""
from __future__ import annotations

from collections import deque
from contextlib import AbstractContextManager
import json
import math
import os
from pathlib import Path
import queue as thread_queue
import shutil
import signal
import subprocess
import threading
import time
import uuid

from .db import connect, get_setting, init_db
from .library import LibraryError, _install_file, _write_tags, resolve_audio_path, safe_filename, verify_audio
from .providers import SourceError, parse_youtube_url, yt_dlp_command
from .queue import claim_job, enqueue_sources, queue_snapshot, recover_jobs, set_job_state


class TaskFailure(ValueError):
    def __init__(self, message: str, *, transient: bool = False, unavailable: bool = False):
        super().__init__(message)
        self.transient, self.unavailable = transient, unavailable


class WorkerLock(AbstractContextManager):
    """An OS file lock released even when a process is forcibly terminated."""
    def __init__(self, path: Path):
        self.path, self.file = Path(path), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        if self.path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError("Un processus de téléchargement est déjà actif. Arrêtez-le avant d'en lancer un autre.") from exc
        return self

    def __exit__(self, *args):
        if self.file is not None:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
        return False


def _stop_process(process) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            # Terminate the FFmpeg child too, using only the OS-created numeric PID.
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _classify_error(message: str) -> TaskFailure:
    lowered = message.lower()
    unavailable = any(word in lowered for word in ("private video", "video unavailable", "removed", "deleted", "sign in", "not available", "copyright", "geo", "age-restricted", "403", "duration", "match filter"))
    transient = not unavailable and any(word in lowered for word in ("timed out", "timeout", "connection reset", "temporary failure", "network is unreachable", "http error 429", "http error 500", "http error 502", "http error 503", "http error 504"))
    return TaskFailure(message[:2000] or "yt-dlp n'a pas réussi à préparer ce morceau.", transient=transient, unavailable=unavailable)


def _check_space(cfg, staging: Path, *, initial: bool = False) -> None:
    maximum = int(cfg.max_upload_mb) * 1024 * 1024
    reserve = int(cfg.min_free_mb) * 1024 * 1024
    required = maximum * 3 if initial else 0
    if shutil.disk_usage(cfg.temp_dir).free < reserve + required or shutil.disk_usage(cfg.audio_dir).free < reserve + (maximum if initial else 0):
        raise TaskFailure("Espace disque insuffisant. Libérez de l'espace puis réessayez ce morceau.")
    if not initial and sum(item.stat().st_size for item in staging.iterdir() if item.is_file()) > maximum * 3:
        raise TaskFailure("Les fichiers temporaires dépassent la limite de taille autorisée.")


def _download(job: dict, cfg, conn, stop: threading.Event) -> tuple[Path, dict]:
    parsed = parse_youtube_url(job["url"], mode="video")
    if job.get("video_id") and job["video_id"] != parsed["video_id"]:
        raise TaskFailure("L'identifiant de la vidéo ne correspond pas à la source enregistrée.")
    staging = Path(cfg.temp_dir) / f"job-{job['id']}"
    staging.mkdir(parents=True, exist_ok=True)
    if not staging.resolve().is_relative_to(Path(cfg.temp_dir).resolve()):
        raise TaskFailure("Le dossier temporaire sort de l'emplacement autorisé.")
    _check_space(cfg, staging, initial=True)
    output_format = get_setting(conn, "output_format", cfg.output_format)
    if output_format not in {"mp3", "source"}:
        raise TaskFailure("Le profil audio configuré est invalide.")
    command = yt_dlp_command(cfg) + [
        "--no-playlist", "--abort-on-error", "--no-simulate", "--newline", "--progress", "--progress-delta", "0.5",
        "--progress-template", 'download:__PLAYLIST25_PROGRESS__{"downloaded":%(progress.downloaded_bytes|0)j,"total":%(progress.total_bytes,progress.total_bytes_estimate|0)j,"status":%(progress.status)j}',
        "--progress-template", "postprocess:__PLAYLIST25_POST__%(progress.status)s",
        "--print", 'before_dl:__PLAYLIST25_INFO__{"id":%(id)j,"duration":%(duration)j}',
        "--print", "after_move:__PLAYLIST25_FILE__%(filepath)j",
        "--max-filesize", str(int(cfg.max_upload_mb) * 1024 * 1024),
        "--match-filters", f"duration <= {int(cfg.max_duration)} & !is_live",
        "--format", "bestaudio/best" if output_format == "mp3" else "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio[ext=opus]/bestaudio[ext=ogg]/bestaudio",
        "--extract-audio", "--audio-format", "mp3" if output_format == "mp3" else "best",
        "--audio-quality", "0", "--ffmpeg-location", shutil.which(str(cfg.ffmpeg_path)) or str(cfg.ffmpeg_path),
        "--output", str(staging / "track.%(ext)s"), "--", parsed["url"],
    ]
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
              "text": True, "encoding": "utf-8", "errors": "replace", "bufsize": 1}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        raise TaskFailure("yt-dlp ne démarre pas. Vérifiez les diagnostics et relancez l'installation.") from exc
    lines = thread_queue.Queue(maxsize=256)
    reader_finished = threading.Event()

    def read_output():
        try:
            for line in process.stdout:
                lines.put(line[:16000])
        finally:
            reader_finished.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + int(cfg.task_timeout)
    logs, file_path, measured_duration = deque(maxlen=12), None, None
    last_check, last_progress, current_state = 0.0, -1.0, "downloading"
    try:
        while not (process.poll() is not None and reader_finished.is_set() and lines.empty()):
            now_clock = time.monotonic()
            if stop.is_set():
                raise InterruptedError("Le processus de téléchargement a été arrêté ; reprise au prochain démarrage.")
            if now_clock >= deadline:
                raise TaskFailure("Le traitement a dépassé le délai configuré.", transient=True)
            if now_clock - last_check >= 1:
                _check_space(cfg, staging)
                if get_setting(conn, "offline_mode", False):
                    raise InterruptedError("Traitement interrompu : le mode hors ligne est activé.")
                last_check = now_clock
            try:
                line = lines.get(timeout=0.2).strip()
            except thread_queue.Empty:
                continue
            if line.startswith("__PLAYLIST25_PROGRESS__"):
                try:
                    data = json.loads(line.removeprefix("__PLAYLIST25_PROGRESS__"))
                    total, downloaded = float(data.get("total") or 0), float(data.get("downloaded") or 0)
                    progress = min(90.0, max(0.0, downloaded / total * 90.0)) if total > 0 else 0.0
                    if abs(progress - last_progress) >= 0.5:
                        set_job_state(conn, job["id"], "downloading", progress=progress)
                        conn.commit()
                        last_progress = progress
                except (ValueError, TypeError, KeyError):
                    pass
            elif line.startswith("__PLAYLIST25_INFO__"):
                try:
                    data = json.loads(line.removeprefix("__PLAYLIST25_INFO__"))
                    if data.get("id") != parsed["video_id"]:
                        raise TaskFailure("YouTube a renvoyé un autre identifiant de vidéo.")
                    measured_duration = float(data.get("duration"))
                    if not math.isfinite(measured_duration) or measured_duration <= 0 or measured_duration > cfg.max_duration:
                        raise TaskFailure("Durée absente ou supérieure à la limite autorisée.", unavailable=True)
                except (ValueError, TypeError) as exc:
                    if isinstance(exc, TaskFailure):
                        raise
                    raise TaskFailure("Impossible de contrôler la durée du morceau avant téléchargement.", unavailable=True) from exc
            elif line.startswith("__PLAYLIST25_FILE__"):
                try:
                    file_path = Path(json.loads(line.removeprefix("__PLAYLIST25_FILE__")))
                except (ValueError, TypeError):
                    raise TaskFailure("yt-dlp n'a pas retourné de chemin de fichier valide.")
            elif line.startswith("__PLAYLIST25_POST__") or "[ExtractAudio]" in line:
                if current_state != "converting":
                    set_job_state(conn, job["id"], "converting", progress=92, message="Traitement audio en cours.")
                    conn.commit()
                    current_state = "converting"
            elif line:
                logs.append(line)
        return_code = process.wait(timeout=5)
        if return_code:
            raise _classify_error(" ".join(logs))
        if file_path is None:
            raise _classify_error(" ".join(logs) or "Aucun fichier préparé : morceau indisponible ou exclu par les limites.")
        file_path = file_path.resolve()
        if not file_path.is_relative_to(staging.resolve()) or not file_path.is_file() or file_path.stat().st_size > int(cfg.max_upload_mb) * 1024 * 1024:
            raise TaskFailure("Le fichier produit dépasse les limites de chemin ou de taille.")
        set_job_state(conn, job["id"], "verifying", progress=96, message="Contrôle du fichier avec ffprobe ; le morceau n'est pas encore disponible.")
        conn.commit()
        metadata = verify_audio(file_path, cfg, job.get("duration") or measured_duration)
        return file_path, metadata
    finally:
        _stop_process(process)
        if process.stdout is not None:
            process.stdout.close()
        reader.join(timeout=1)


def _perform_job(conn, cfg, job: dict, stop: threading.Event) -> None:
    staging = Path(cfg.temp_dir) / f"job-{job['id']}"
    cleanup = False
    try:
        staged, metadata = _download(job, cfg, conn, stop)
        # Moderation can correct a title/artist while a download is running.
        # Use the current source metadata when publishing its final file.
        current = conn.execute("SELECT title,artist FROM sources WHERE id=?", (job["source_id"],)).fetchone()
        if current is None:
            raise TaskFailure("La source à publier n'existe plus.")
        title, artist = current["title"], current["artist"] or ""
        _write_tags(staged, title, artist)
        metadata = verify_audio(staged, cfg, metadata["duration"])
        filename = safe_filename(job["source_id"], title, staged.suffix)
        destination = resolve_audio_path(cfg, filename)
        if destination.exists():
            # Preserve existing user data, even when a previous file is invalid.
            filename = safe_filename(job["source_id"], f"{title} - reprise {uuid.uuid4().hex[:8]}", staged.suffix)
            destination = resolve_audio_path(cfg, filename)
        # Persist the intended destination while still "verifying". A crash after
        # the atomic move can then recover the file instead of orphaning it.
        conn.execute("UPDATE sources SET file_path=?,duration=? WHERE id=?", (filename, metadata["duration"], job["source_id"]))
        conn.commit()
        _install_file(staged, destination)
        # Publish only after an actual check of the installed file.
        verify_audio(destination, cfg, metadata["duration"])
        set_job_state(conn, job["id"], "available", progress=100, message="Fichier audio disponible et vérifié.")
        conn.commit()
        cleanup = True
    except InterruptedError as exc:
        set_job_state(conn, job["id"], "interrupted", error=str(exc), message=str(exc))
        conn.commit()
    except (TaskFailure, SourceError, LibraryError, OSError) as exc:
        transient = getattr(exc, "transient", False)
        unavailable = getattr(exc, "unavailable", False)
        message = str(exc)[:2000]
        if isinstance(exc, OSError):
            message = "Impossible d'écrire le fichier audio. Vérifiez les permissions et l'espace disque."
        status = "unavailable" if unavailable else "failed"
        set_job_state(conn, job["id"], status, error=message, message=message)
        if transient and job["attempts"] < int(cfg.max_attempts) and not stop.is_set():
            set_job_state(conn, job["id"], "queued", progress=0, error=message, message=f"Erreur transitoire : nouvelle tentative autorisée ({job['attempts']}/{cfg.max_attempts}).")
        else:
            cleanup = True
        conn.commit()
    finally:
        if cleanup and staging.exists() and staging.resolve().is_relative_to(Path(cfg.temp_dir).resolve()):
            shutil.rmtree(staging, ignore_errors=True)


def run(cfg, once: bool = False) -> int:
    """Run outside HTTP processes. Pause means stop claiming after the current track."""
    init_db(cfg)
    stop = threading.Event()
    previous_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM, *([signal.SIGBREAK] if hasattr(signal,"SIGBREAK") else [])):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda *_: stop.set())
    try:
        with WorkerLock(Path(cfg.temp_dir) / "worker.lock"), connect(cfg) as conn:
            recover_jobs(conn, cfg)
            while not stop.is_set():
                job = claim_job(conn)
                if job is None:
                    if once:
                        break
                    stop.wait(1)
                    continue
                _perform_job(conn, cfg, job, stop)
                if once:
                    break
        return 0
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
