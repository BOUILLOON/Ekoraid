"""A single, restartable subprocess worker for authorized audio downloads."""
from __future__ import annotations

from collections import deque
from contextlib import AbstractContextManager
import hashlib
import json
import math
import os
from pathlib import Path
import queue as thread_queue
import shutil
import signal
import subprocess
import threading
import tempfile
import time
import uuid

from .db import connect, get_setting, init_db
from .library import LibraryError, _install_file, _write_tags, resolve_audio_path, safe_filename, verify_audio
from .providers import SourceError, parse_youtube_url, yt_dlp_command
from .processes import background_process_options
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


class _WindowsProcessJob:
    """Keep ownership of this download's descendants after its parent exits."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes
        class Limits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                        ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", Limits), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel, self.handle = kernel, kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "Impossible de créer l'isolation du téléchargement.")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            code = ctypes.get_last_error()
            self.close()
            raise OSError(code, "Impossible d'isoler les sous-processus du téléchargement.")

    def close(self):
        if self.handle:
            # Closing the owned job kills descendants even when yt-dlp has
            # exited, unlike taskkill /PID on an already terminated parent.
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _stop_process(process, process_job=None) -> None:
    if process_job is not None:
        process_job.close()
    try:
        if os.name == "nt" and process.poll() is None:
            # Terminate the FFmpeg child too, using only the OS-created numeric PID.
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
                           **background_process_options())
        elif os.name != "nt":
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
    finally:
        if os.name != "nt":
            # A parent can exit before its FFmpeg child, which may also ignore
            # SIGTERM. The complete owned session must release the output pipe.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass


_OUTPUT_LINE_LIMIT = 16000


def _read_output(stream, lines, cancelled, finished):
    """Bound each read and allow cancellation while the output queue is full."""
    discarding = False
    try:
        while not cancelled.is_set():
            line = stream.readline(_OUTPUT_LINE_LIMIT + 1)
            if not line:
                break
            if discarding:
                discarding = not line.endswith("\n")
                continue
            oversized = len(line) > _OUTPUT_LINE_LIMIT
            discarding = oversized and not line.endswith("\n")
            line = line[:_OUTPUT_LINE_LIMIT]
            while not cancelled.is_set():
                try:
                    lines.put(line, timeout=0.1)
                    break
                except thread_queue.Full:
                    continue
    except (OSError, ValueError):
        # The owner can terminate a process and close its pipe during shutdown.
        pass
    finally:
        finished.set()


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
        kwargs.update(background_process_options(new_process_group=True))
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        raise TaskFailure("yt-dlp ne démarre pas. Vérifiez les diagnostics et relancez l'installation.") from exc
    process_job = None
    if os.name == "nt":
        try:
            process_job = _WindowsProcessJob(process)
        except OSError as exc:
            _stop_process(process)
            if process.stdout is not None:
                process.stdout.close()
            raise TaskFailure("Impossible d'isoler le téléchargement sous Windows. Relancez le studio et vérifiez les diagnostics.") from exc
    lines = thread_queue.Queue(maxsize=256)
    reader_finished = threading.Event()
    reader_cancelled = threading.Event()
    reader = threading.Thread(target=_read_output, args=(process.stdout, lines, reader_cancelled, reader_finished),
                              name=f"playlist25-reader-{job['id']}", daemon=True)
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
                    if not isinstance(data, dict):
                        continue
                    total, downloaded = float(data.get("total") or 0), float(data.get("downloaded") or 0)
                    if not math.isfinite(total) or not math.isfinite(downloaded):
                        continue
                    progress = min(90.0, max(0.0, downloaded / total * 90.0)) if total > 0 else 0.0
                    if abs(progress - last_progress) >= 0.5:
                        set_job_state(conn, job["id"], "downloading", progress=progress)
                        conn.commit()
                        last_progress = progress
                except (ValueError, TypeError, KeyError, OverflowError):
                    pass
            elif line.startswith("__PLAYLIST25_INFO__"):
                try:
                    data = json.loads(line.removeprefix("__PLAYLIST25_INFO__"))
                    if not isinstance(data, dict):
                        raise ValueError("invalid metadata")
                    if data.get("id") != parsed["video_id"]:
                        raise TaskFailure("YouTube a renvoyé un autre identifiant de vidéo.")
                    measured_duration = float(data.get("duration"))
                    if not math.isfinite(measured_duration) or measured_duration <= 0 or measured_duration > cfg.max_duration:
                        raise TaskFailure("Durée absente ou supérieure à la limite autorisée.", unavailable=True)
                except (ValueError, TypeError, OverflowError) as exc:
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
        reader_cancelled.set()
        _stop_process(process, process_job)
        reader.join(timeout=2)
        if process.stdout is not None and not reader.is_alive():
            process.stdout.close()


def _perform_job(conn, cfg, job: dict, stop: threading.Event) -> None:
    staging = Path(cfg.temp_dir) / f"job-{job['id']}"
    cleanup = False
    try:
        staged, metadata = _download(job, cfg, conn, stop)
        # Moderation can correct a title/artist while a download is running.
        # Use the current source metadata when publishing its final file.
        current = conn.execute("SELECT title,artist,file_path,duration FROM sources WHERE id=?", (job["source_id"],)).fetchone()
        if current is None:
            raise TaskFailure("La source à publier n'existe plus.")
        title, artist = current["title"], current["artist"] or ""
        _write_tags(staged, title, artist)
        metadata = verify_audio(staged, cfg, metadata["duration"])
        if staged.stat().st_size > int(cfg.max_upload_mb) * 1024 * 1024:
            raise TaskFailure("Le fichier audio final dépasse la limite de taille configurée.")
        digest = hashlib.sha256()
        with staged.open("rb") as audio:
            for block in iter(lambda: audio.read(1024 * 1024), b""):
                digest.update(block)
        for attempt in range(5):
            suffix = "" if attempt == 0 else f" - reprise {uuid.uuid4().hex[:8]}"
            filename = safe_filename(job["source_id"], title + suffix, staged.suffix)
            destination = resolve_audio_path(cfg, filename)
            if destination.exists():
                continue
            # The hash checkpoint proves file identity across a crash between
            # path persistence and publication, including a late name collision.
            checkpoint = {"job_id": job["id"], "source_id": job["source_id"], "filename": filename,
                          "size": staged.stat().st_size, "sha256": digest.hexdigest()}
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix=".publication-", dir=staging, delete=False) as marker:
                marker_path = Path(marker.name)
                json.dump(checkpoint, marker)
                marker.flush()
                os.fsync(marker.fileno())
            try:
                os.replace(marker_path, staging / "publication.json")
            finally:
                marker_path.unlink(missing_ok=True)
            conn.execute("UPDATE sources SET file_path=?,duration=? WHERE id=?", (filename, metadata["duration"], job["source_id"]))
            conn.commit()
            try:
                _install_file(staged, destination)
                break
            except BaseException as error:
                conn.execute("UPDATE sources SET file_path=?,duration=? WHERE id=? AND file_path=?",
                             (current["file_path"], current["duration"], job["source_id"], filename))
                conn.commit()
                if isinstance(error, FileExistsError):
                    continue
                raise
        else:
            raise TaskFailure("Impossible de réserver un nom de fichier libre. Réessayez ce morceau.")
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
