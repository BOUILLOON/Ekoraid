"""Small private desktop launcher; public collection is never reset or published."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4
import webbrowser

from dotenv import dotenv_values, load_dotenv, set_key

from .config import ROOT, Settings, initialize_local_config, load_settings

PUBLIC_URL = "https://bouilloon.github.io/Ekoraid/"
PROJECT_URL = "https://dpcohlyczczaqovblzin.supabase.co"
PROJECT_KEYS_URL = "https://supabase.com/dashboard/project/dpcohlyczczaqovblzin/settings/api-keys"


def user_home() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "StudioFanny"


def selected_home() -> Path:
    """Version updates keep the same data location, outside the extracted ZIP."""
    explicit = os.environ.get("PLAYLIST25_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = user_home()
    pointer = base / "installation.json"
    if pointer.is_file():
        try:
            chosen = Path(json.loads(pointer.read_text(encoding="utf-8"))["home"])
            if chosen.is_absolute() and (chosen / ".env.local").is_file():
                return chosen.resolve()
        except (OSError, ValueError, TypeError, KeyError):
            pass
    return base.resolve()


def remember_installation(home: Path) -> None:
    home = Path(home).resolve()
    if not (home / ".env.local").is_file():
        raise ValueError("Choisissez l'ancien dossier Ekoraid contenant sa configuration privée.")
    base = user_home()
    base.mkdir(parents=True, exist_ok=True)
    temporary = base / "installation.json.tmp"
    temporary.write_text(json.dumps({"home": str(home)}, ensure_ascii=False), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(base / "installation.json")


def save_private_settings(home: Path, *, password: str = "", key: str = "", project_url: str = PROJECT_URL) -> None:
    """Validate before changing configuration; retain every unspecified setting."""
    home = Path(home).resolve()
    if password and not 12 <= len(password) <= 1024:
        raise ValueError("Choisissez un mot de passe de 12 caractères minimum.")
    if any(c in password or c in key for c in ("\r", "\n", "\x00")):
        raise ValueError("Le mot de passe et la clé doivent tenir sur une seule ligne.")
    if key.startswith(("sb_publishable_", "sbp_")):
        raise ValueError("Copiez la clé Secret ou service_role du projet, pas sa clé publique ni un jeton de compte.")
    if key:
        from .cloud_sync import _project, fetch_snapshot
        candidate = SimpleNamespace(supabase_url=project_url, supabase_service_role_key=key)
        _project(candidate)
        snapshot = fetch_snapshot(candidate)
        if not isinstance(snapshot, dict) or "proposals" not in snapshot:
            raise ValueError("La connexion n'a pas renvoyé la collecte attendue.")
    path = initialize_local_config(home)
    current = dotenv_values(path)
    updates = {}
    if password:
        updates["ADMIN_PASSWORD"] = password
    if key:
        updates.update(SUPABASE_URL=project_url.rstrip("/"), SUPABASE_SERVICE_ROLE_KEY=key)
    if not current.get("PUBLIC_URL"):
        updates["PUBLIC_URL"] = PUBLIC_URL
    for name, value in updates.items():
        set_key(str(path), name, value, quote_mode="always")
    path.chmod(0o600)


def current_settings():
    # Reload after a native form saved settings, before starting child services.
    load_dotenv(ROOT / ".env.local", override=True)
    return load_settings()


def adoption_environment(home: Path) -> dict[str, str]:
    """An adopted installation reads its own settings, never the previous ones."""
    environment = os.environ.copy()
    for name in Settings.__dataclass_fields__:
        if name != "root":
            environment.pop(name.upper(), None)
    environment["PLAYLIST25_HOME"] = str(Path(home).resolve())
    return environment


def launch_installation(home: Path):
    """Open an adopted studio with valid handles, including windowed Windows EXEs."""
    home = Path(home).resolve()
    remember_installation(home)
    command = ([sys.executable] if getattr(sys, "frozen", False) else
               [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "studio_entry.py")])
    options = {"env": adoption_environment(home), "cwd": str(home),
               "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
               "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(command, **options)


def backup_summary(info: dict) -> str:
    """Describe only archive inventory, never private configuration values."""
    counts = info["counts"]
    date = datetime.fromisoformat(info["created_at"].replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y à %H:%M")
    summary = (f"Date : {date}\n"
            f"{counts['proposals']} proposition(s), {counts['guests']} invité(s), {counts['votes']} vote(s)\n"
            f"{counts['photos']} photo(s), {counts['audio_files']} fichier(s) audio\n"
            + ("Sauvegarde avec les fichiers audio présents sur ce PC." if info["include_audio"] else
               "Sauvegarde des données et de la configuration, sans les fichiers audio."))
    if info.get("missing_audio", 0):
        summary += f"\nFichiers audio déjà absents de ce PC : {info['missing_audio']}."
    return summary


def restored_home(parent: Path) -> Path:
    parent = Path(parent).resolve()
    if not parent.is_dir():
        raise ValueError("Choisissez un dossier existant pour créer le studio restauré.")
    return parent / f"StudioFanny-restaure-{datetime.now():%Y-%m-%d_%H-%M-%S}-{uuid4().hex[:6]}"


def service_command(*arguments: str) -> list[str]:
    from .__main__ import service_command as command
    return command(*arguments)


class StudioWindow:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.window = tk.Tk()
        self.window.title("Studio de Fanny")
        main_height = min(720, max(400, self.window.winfo_screenheight() - 100))
        self.window.geometry(f"620x{main_height}")
        self.window.minsize(530, 510)
        self.window.configure(bg="#faf9f5")
        self.runner = None
        self.log = None
        self.events = queue.Queue()
        self.busy = False
        self.closing = False
        style = ttk.Style(self.window)
        style.theme_use("clam")
        style.configure("TFrame", background="#faf9f5")
        style.configure("TLabel", background="#faf9f5", foreground="#24223b", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"), foreground="#85364d")
        style.configure("TButton", font=("Segoe UI", 10), padding=(14, 10))
        style.configure("Primary.TButton", background="#3934a7", foreground="white")
        style.map("Primary.TButton", background=[("active", "#292574")])
        container = ttk.Frame(self.window)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, bg="#faf9f5", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.panel = ttk.Frame(canvas, padding=28)
        panel_id = canvas.create_window((0, 0), window=self.panel, anchor="nw")
        self.panel.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(panel_id, width=event.width))
        def scroll(event):
            if event.widget.winfo_toplevel() != self.window:
                return
            delta = getattr(event, "delta", 0)
            direction = -1 if getattr(event, "num", None) == 4 else 1 if getattr(event, "num", None) == 5 else -1 if delta > 0 else 1 if delta < 0 else 0
            canvas.yview_scroll(direction, "units")
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.window.bind_all(event, scroll, add="+")
        ttk.Label(self.panel, text="Les 25 ans de Fanny", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.panel, text="Votre studio privé pour préparer la musique.").pack(anchor="w", pady=(10, 20))
        self.status = tk.StringVar(value="Le site des invités reste disponible même quand ce studio est fermé.")
        ttk.Label(self.panel, textvariable=self.status, wraplength=520).pack(anchor="w", pady=(0, 18))
        self.open_button = ttk.Button(self.panel, text="Ouvrir le studio", style="Primary.TButton", command=self.start)
        self.open_button.pack(fill="x", pady=5)
        self.stop_button = ttk.Button(self.panel, text="Arrêter le studio", command=self.stop, state="disabled")
        self.stop_button.pack(fill="x", pady=5)
        ttk.Button(self.panel, text="Ouvrir mes fichiers", command=self.open_files).pack(fill="x", pady=5)
        ttk.Button(self.panel, text="Configurer la connexion", command=lambda: self.configure("cloud")).pack(fill="x", pady=5)
        ttk.Button(self.panel, text="Changer mon mot de passe", command=lambda: self.configure("password")).pack(fill="x", pady=5)
        ttk.Button(self.panel, text="Sauvegarder mon studio", command=self.backup).pack(fill="x", pady=5)
        ttk.Button(self.panel, text="Restaurer une sauvegarde", command=self.restore).pack(fill="x", pady=5)
        ttk.Button(self.panel, text="Ouvrir le diagnostic", command=self.diagnostic).pack(fill="x", pady=5)
        ttk.Button(self.panel, text="Reprendre une ancienne installation", command=self.adopt).pack(fill="x", pady=5)
        ttk.Label(self.panel, text="Propositions et votes : aucun effacement pendant une mise à jour.\nLaissez cette fenêtre ouverte pendant la préparation.", wraplength=520).pack(anchor="w", pady=(16, 0))
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(100, self.poll)
        first = not (ROOT / ".env.local").exists()
        if first:
            self.window.after(200, lambda: self.configure("first"))
        else:
            self.window.after(200, self.start)

    def post(self, function, *args):
        self.events.put((function, args))

    def poll(self):
        try:
            while True:
                function, args = self.events.get_nowait()
                function(*args)
        except queue.Empty:
            pass
        if self.runner is not None and not self.busy and self.runner.poll() is not None:
            code = self.runner.returncode
            self.runner = None
            self.open_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            if code:
                self.status.set("Le studio s'est arrêté. Relancez-le ; les données sont conservées. Le diagnostic est dans le dossier de vos fichiers.")
        if not self.closing:
            self.window.after(150, self.poll)

    def open_files(self):
        home = ROOT / "data"
        if (ROOT / ".env.local").exists():
            try:
                home = current_settings().data_dir
            except (ValueError, OSError):
                pass
        home.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(home)
        else:
            webbrowser.open(home.as_uri())

    def configure(self, kind):
        from tkinter import messagebox
        if self.busy:
            return
        if self.runner and self.runner.poll() is None:
            messagebox.showinfo("Studio de Fanny", "Cliquez d'abord sur Arrêter le studio. Vos propositions et fichiers restent conservés.", parent=self.window)
            return
        dialog = self.tk.Toplevel(self.window)
        dialog.title("Configurer le studio de Fanny")
        dialog.transient(self.window)
        dialog.grab_set()
        width = min(600, max(420, self.window.winfo_screenwidth() - 80))
        height = min(680, max(320, self.window.winfo_screenheight() - 100))
        left = max(0, (self.window.winfo_screenwidth() - width) // 2)
        top = max(10, (self.window.winfo_screenheight() - height) // 2 - 20)
        dialog.geometry(f"{width}x{height}+{left}+{top}")
        dialog.minsize(min(560, width), min(400, height))
        dialog.maxsize(self.window.winfo_screenwidth(), max(height, self.window.winfo_screenheight() - 80))
        actions = self.ttk.Frame(dialog, padding=(25, 12, 25, 20))
        actions.pack(side="bottom", fill="x")
        body = self.ttk.Frame(dialog)
        body.pack(fill="both", expand=True)
        canvas = self.tk.Canvas(body, bg="#faf9f5", highlightthickness=0)
        scrollbar = self.ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        frame = self.ttk.Frame(canvas, padding=25)
        frame_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(frame_id, width=event.width))
        def scroll(event):
            if event.widget.winfo_toplevel() != dialog:
                return
            delta = getattr(event, "delta", 0)
            direction = -1 if getattr(event, "num", None) == 4 else 1 if getattr(event, "num", None) == 5 else -1 if delta > 0 else 1 if delta < 0 else 0
            canvas.yview_scroll(direction, "units")
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            dialog.bind(event, scroll, add="+")
        self.ttk.Label(frame, text="Bienvenue dans le studio" if kind == "first" else "Votre configuration privée", style="Title.TLabel").pack(anchor="w", pady=(0, 15))
        passwords = []
        key_var = self.tk.StringVar()
        url_var = self.tk.StringVar(value=PROJECT_URL)
        if kind in {"first", "password"}:
            for label in ("Votre mot de passe (12 caractères minimum)", "Confirmez votre mot de passe"):
                self.ttk.Label(frame, text=label).pack(anchor="w", pady=(8, 5))
                value = self.tk.StringVar()
                self.ttk.Entry(frame, textvariable=value, show="•", width=48).pack(fill="x")
                passwords.append(value)
        if kind in {"first", "cloud"}:
            self.ttk.Label(frame, text="Pour récupérer les morceaux du site public", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(22, 8))
            self.ttk.Label(frame, text="Dans Supabase → API Keys, copiez la clé Secret (ou service_role)\npuis collez-la ici. Elle reste sur ce PC. À faire une seule fois.\nVous pouvez aussi configurer cette connexion plus tard.", wraplength=500).pack(anchor="w")
            self.ttk.Button(frame, text="Ouvrir la page de ma clé Supabase", command=lambda: webbrowser.open(PROJECT_KEYS_URL)).pack(fill="x", pady=10)
            existing = dotenv_values(ROOT / ".env.local") if (ROOT / ".env.local").exists() else {}
            url_var.set(existing.get("SUPABASE_URL") or PROJECT_URL)
            self.ttk.Label(frame, text="Adresse du projet (déjà renseignée)").pack(anchor="w", pady=(5, 4))
            self.ttk.Entry(frame, textvariable=url_var, width=48).pack(fill="x")
            self.ttk.Label(frame, text="Clé privée — laisser vide pour conserver la connexion existante").pack(anchor="w", pady=(10, 4))
            self.ttk.Entry(frame, textvariable=key_var, show="•", width=48).pack(fill="x")
        feedback = self.tk.StringVar()
        self.ttk.Label(actions, textvariable=feedback, wraplength=500, foreground="#85364d").pack(anchor="w", pady=(0, 10))

        def done(error):
            self.busy = False
            button.configure(state="normal")
            if error:
                feedback.set(error)
                return
            key_var.set("")
            for value in passwords:
                value.set("")
            dialog.destroy()
            self.status.set("Configuration enregistrée sur ce PC. Les propositions du site public sont conservées.")
            self.start()

        def save():
            password = passwords[0].get() if passwords else ""
            if passwords and (len(password) < 12 or password != passwords[1].get()):
                feedback.set("Choisissez au moins 12 caractères et saisissez deux fois le même mot de passe.")
                return
            button.configure(state="disabled")
            self.busy = True
            feedback.set("Vérification de la connexion…" if key_var.get().strip() else "Enregistrement…")
            key, project = key_var.get().strip(), url_var.get().strip()

            def task():
                try:
                    save_private_settings(ROOT, password=password, key=key, project_url=project)
                    error = None
                except (ValueError, OSError) as exc:
                    error = str(exc)
                except Exception:
                    error = "La configuration n'a pas pu être enregistrée. Vérifiez la connexion Internet et réessayez."
                self.post(done, error)

            threading.Thread(target=task, daemon=True).start()

        button = self.ttk.Button(actions, text="Enregistrer et ouvrir le studio", style="Primary.TButton", command=save)
        button.pack(fill="x")
        if kind == "first":
            def restore_existing():
                if self.busy:
                    return
                dialog.destroy()
                self.restore()
            self.ttk.Button(actions, text="Restaurer une sauvegarde", command=restore_existing).pack(fill="x", pady=(10, 0))
        dialog.protocol("WM_DELETE_WINDOW", lambda: None if self.busy else dialog.destroy())

    def start(self):
        if self.busy:
            return
        if not (ROOT / ".env.local").exists():
            self.configure("first")
            return
        try:
            cfg = current_settings()
        except (ValueError, OSError):
            self.status.set("Complétez la configuration pour ouvrir le studio.")
            self.configure("first")
            return
        url = f"http://127.0.0.1:{cfg.admin_port}"
        if self.runner and self.runner.poll() is None:
            webbrowser.open(url)
            return
        self.busy = True
        self.open_button.configure(state="disabled")
        self.status.set("Ouverture du studio…")

        def task():
            try:
                import httpx
                cfg.ensure_directories()
                log_dir = cfg.data_dir / "diagnostics"
                log_dir.mkdir(parents=True, exist_ok=True)
                self.log = (log_dir / "studio.log").open("a", encoding="utf-8")
                options = {"cwd": str(ROOT), "stdout": self.log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
                if os.name == "nt":
                    options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                self.runner = subprocess.Popen(service_command("start"), **options)
                with httpx.Client(trust_env=False, timeout=1) as client:
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        if self.runner.poll() is not None:
                            raise RuntimeError("Le studio ne démarre pas. Un autre lancement ou une application peut utiliser ses ports. Consultez le diagnostic dans Ouvrir mes fichiers.")
                        try:
                            admin = client.get(url + "/api/session")
                            guest = client.get(f"http://127.0.0.1:{cfg.guest_port}/health")
                            if admin.status_code == guest.status_code == 200 and "csrf_token" in admin.json() and guest.json().get("surface") == "guest":
                                # Give a failed bind time to reach the supervisor.
                                time.sleep(0.5)
                                if self.runner.poll() is None:
                                    break
                        except (httpx.HTTPError, ValueError):
                            pass
                        time.sleep(0.25)
                    else:
                        raise RuntimeError("Le studio met trop de temps à répondre. Fermez ce lancement puis réessayez ; les données sont conservées.")
                message = "Le studio est ouvert. Laissez cette fenêtre ouverte pendant les téléchargements."
                if cfg.supabase_url and cfg.supabase_service_role_key:
                    try:
                        from . import cloud_sync, db
                        with closing(db.connect(cfg)) as conn, conn:
                            if not db.get_setting(conn, "offline_mode", False):
                                result = cloud_sync.pull(cfg, conn)
                                message = f"Le studio est ouvert. {result.get('proposals', 0)} proposition(s) récupérée(s) ; aucun audio lancé automatiquement."
                    except (ValueError, OSError):
                        message = "Le studio est ouvert. La récupération du site public a échoué ; réessayez dans Réglages. Votre collecte reste conservée."
                else:
                    message += " Pour récupérer les idées du site public, utilisez Configurer la connexion."
                self.post(self.started, url, message)
            except Exception as exc:
                self.post(self.failed, str(exc) if isinstance(exc, RuntimeError) else "Le studio n'a pas démarré. Consultez le diagnostic dans Ouvrir mes fichiers, puis réessayez.")

        threading.Thread(target=task, daemon=True).start()

    def started(self, url, message):
        self.busy = False
        self.open_button.configure(state="normal")
        self.stop_button.configure(state="normal")
        self.status.set(message)
        webbrowser.open(url)

    def failed(self, message):
        self.busy = False
        self.open_button.configure(state="normal")
        self.stop_button.configure(state="normal" if self.runner and self.runner.poll() is None else "disabled")
        self.status.set(message)

    def stop(self, after=None):
        if self.busy:
            return
        running = bool(self.runner and self.runner.poll() is None)
        recorded = False
        if not running:
            try:
                recorded = (current_settings().data_dir / "processes.json").is_file()
            except (ValueError, OSError):
                pass
        if not running and not recorded:
            if after:
                after()
            return
        self.busy = True
        self.status.set("Arrêt du studio… Les propositions et les fichiers restent conservés.")
        self.open_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")

        def task():
            options = {"cwd": str(ROOT), "stdout": self.log if self.log else subprocess.DEVNULL,
                       "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                subprocess.run(service_command("stop"), **options, timeout=60, check=True)
                if self.runner:
                    self.runner.wait(timeout=10)
                error = None
            except (OSError, subprocess.SubprocessError):
                error = "L'arrêt n'est pas terminé. Vérifiez le diagnostic avant de relancer le studio."
            self.post(self.stopped, error, after)

        threading.Thread(target=task, daemon=True).start()

    def stopped(self, error, after):
        self.busy = False
        if error:
            self.failed(error)
            return
        self.runner = None
        if self.log:
            self.log.close()
            self.log = None
        self.open_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set("Studio arrêté. Propositions, votes et fichiers conservés. Les invités peuvent continuer sur le site public.")
        if after:
            after()

    def _backup_task(self, message, task, done):
        self.busy = True
        self.open_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.status.set(message)
        def run():
            try:
                result, error = task(), None
            except (ValueError, OSError) as exc:
                result, error = None, str(exc)
            except Exception:
                result, error = None, "L'opération n'a pas pu se terminer. Les données de votre installation sont conservées."
            self.post(done, result, error)
        threading.Thread(target=run, daemon=True).start()

    def _backup_idle(self):
        self.busy = False
        self.open_button.configure(state="normal")
        self.stop_button.configure(state="normal" if self.runner and self.runner.poll() is None else "disabled")

    def backup(self):
        from tkinter import filedialog
        from .backups import create_backup
        if self.busy:
            return
        selected = filedialog.asksaveasfilename(
            title="Sauvegarder mon studio — configuration privée, données et audio",
            initialfile=f"StudioFanny-sauvegarde-{datetime.now():%Y-%m-%d_%H-%M-%S}.zip",
            defaultextension=".zip", filetypes=[("Sauvegarde du studio", "*.zip")], parent=self.window)
        if not selected:
            return
        destination = Path(selected).resolve()
        resume = bool(self.runner and self.runner.poll() is None)
        def done(result, error):
            from tkinter import messagebox
            self._backup_idle()
            if error:
                self.status.set("La sauvegarde n'a pas été créée. Votre studio est conservé.")
                messagebox.showerror("Sauvegarder mon studio", error, parent=self.window)
                if resume:
                    self.start()
                return
            self.status.set("Sauvegarde enregistrée.")
            messagebox.showinfo("Sauvegarde terminée", backup_summary(result) +
                                f"\n\nArchive : {result['archive']}\nCette archive contient votre configuration privée. Conservez-la dans un endroit sûr.", parent=self.window)
            if resume:
                self.start()
        self.stop(after=lambda: self._backup_task("Sauvegarde du studio et des fichiers audio…",
                                                lambda: create_backup(ROOT, destination, include_audio=True), done))

    def restore(self):
        from tkinter import filedialog
        from .backups import inspect_backup
        if self.busy:
            return
        selected = filedialog.askopenfilename(title="Choisissez une sauvegarde du studio", filetypes=[("Sauvegarde du studio", "*.zip")], parent=self.window)
        if not selected:
            return
        archive = Path(selected).resolve()
        self._backup_task("Vérification de la sauvegarde…", lambda: inspect_backup(archive),
                          lambda info, error: self._restore_preview(archive, info, error))

    def _restore_preview(self, archive, info, error):
        from tkinter import filedialog, messagebox
        from .backups import restore_backup
        self._backup_idle()
        if error:
            self.status.set("Cette sauvegarde n'a pas pu être ouverte. Votre studio est conservé.")
            messagebox.showerror("Restaurer une sauvegarde", error, parent=self.window)
            return
        selected = filedialog.askdirectory(title="Choisissez où créer le nouveau studio restauré", parent=self.window)
        if not selected:
            self.status.set("Restauration annulée. Votre studio est conservé.")
            return
        try:
            home = restored_home(Path(selected))
        except (ValueError, OSError) as exc:
            messagebox.showerror("Restaurer une sauvegarde", str(exc), parent=self.window)
            return
        summary = backup_summary(info) + f"\n\nNouveau studio : {home}\nLa restauration utilisera le mot de passe et la connexion de cette sauvegarde. Votre installation actuelle restera conservée."
        if not messagebox.askokcancel("Restaurer cette sauvegarde ?", summary, parent=self.window):
            self.status.set("Restauration annulée. Votre studio est conservé.")
            return
        def done(result, failure):
            self._backup_idle()
            if failure:
                self.status.set("La restauration n'a pas terminé. Votre installation actuelle est conservée.")
                messagebox.showerror("Restaurer une sauvegarde", failure, parent=self.window)
                return
            if result.get("missing_audio", 0):
                messagebox.showinfo("Studio restauré", f"{result['missing_audio']} fichier(s) audio étaient absents de cette sauvegarde et restent à préparer.\nPropositions, votes et photos ont été conservés.", parent=self.window)
            try:
                launch_installation(Path(result["home"]))
            except (ValueError, OSError):
                self.status.set("Le nouveau studio a été restauré. Fermez cette fenêtre puis rouvrez l'application pour l'utiliser.")
                messagebox.showinfo("Studio restauré", "La sauvegarde est restaurée et votre installation actuelle reste conservée. Le nouveau studio n'a pas pu s'ouvrir automatiquement ; fermez cette fenêtre et relancez l'application.", parent=self.window)
                return
            self.destroy()
        self.stop(after=lambda: self._backup_task("Restauration dans un nouveau studio…",
                                                lambda: restore_backup(archive, home, expected_manifest_sha256=info["manifest_sha256"]), done))

    def diagnostic(self):
        from tkinter import messagebox
        from .diagnostics import open_diagnostic, write_diagnostic
        if self.busy:
            return
        def done(path, error):
            self._backup_idle()
            if error:
                self.status.set("Le diagnostic n'a pas pu être créé. Votre studio est conservé.")
                messagebox.showerror("Ouvrir le diagnostic", "Le rapport local n'a pas pu être créé. Vérifiez que le dossier de l'application est accessible.", parent=self.window)
                return
            try:
                open_diagnostic(path)
            except OSError:
                messagebox.showinfo("Diagnostic créé", f"Le diagnostic est enregistré ici :\n{path}\n\nL'ouverture automatique n'a pas abouti.", parent=self.window)
            self.status.set("Diagnostic local créé. Il ne contient aucun mot de passe ni clé privée.")
        self._backup_task("Préparation du diagnostic local…", lambda: write_diagnostic(ROOT), done)

    def adopt(self):
        from tkinter import filedialog, messagebox
        if self.busy or self.runner and self.runner.poll() is None:
            messagebox.showinfo("Studio de Fanny", "Arrêtez d'abord le studio avant de choisir une autre installation.", parent=self.window)
            return
        selected = filedialog.askdirectory(title="Choisissez l'ancien dossier Ekoraid contenant .env.local", parent=self.window)
        if not selected:
            return
        try:
            home = Path(selected).resolve()
            if home == ROOT.resolve():
                messagebox.showinfo("Studio de Fanny", "Cette installation est déjà sélectionnée. Le studio s'ouvre avec vos données actuelles.", parent=self.window)
                self.start()
                return
            launch_installation(home)
            self.destroy()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Studio de Fanny", str(exc), parent=self.window)

    def destroy(self):
        self.closing = True
        self.window.destroy()

    def close(self):
        from tkinter import messagebox
        if self.busy:
            messagebox.showinfo("Studio de Fanny", "Attendez la fin de l'opération en cours avant de fermer cette fenêtre.", parent=self.window)
            return
        if self.runner and self.runner.poll() is None:
            if not messagebox.askokcancel("Arrêter le studio ?", "Les préparations en cours seront interrompues et pourront être reprises. Vos propositions et fichiers seront conservés.", parent=self.window):
                return
            self.stop(after=self.destroy)
        else:
            self.destroy()

    def run(self):
        self.window.mainloop()


def main():
    from .worker import WorkerLock
    ROOT.mkdir(parents=True, exist_ok=True)
    lock = WorkerLock(ROOT / ".studio.lock")
    try:
        lock.__enter__()
    except RuntimeError:
        from tkinter import messagebox
        try:
            cfg = current_settings()
            webbrowser.open(f"http://127.0.0.1:{cfg.admin_port}")
        except (ValueError, OSError):
            pass
        messagebox.showinfo("Studio de Fanny", "Le studio est déjà ouvert. Utilisez sa fenêtre pour le configurer ou l'arrêter.")
        return 0
    try:
        StudioWindow().run()
    finally:
        lock.__exit__(None, None, None)
    return 0
