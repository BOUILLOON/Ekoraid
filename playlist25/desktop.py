"""Small private desktop launcher; public collection is never reset or published."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
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
        self.window.geometry("600x560")
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
        self.panel = ttk.Frame(self.window, padding=28)
        self.panel.pack(fill="both", expand=True)
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
        frame = self.ttk.Frame(dialog, padding=25)
        frame.pack(fill="both", expand=True)
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
        self.ttk.Label(frame, textvariable=feedback, wraplength=500, foreground="#85364d").pack(anchor="w", pady=12)

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

        button = self.ttk.Button(frame, text="Enregistrer et ouvrir le studio", style="Primary.TButton", command=save)
        button.pack(fill="x")
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
        if not self.runner or self.runner.poll() is not None:
            if after:
                after()
            return
        self.busy = True
        self.status.set("Arrêt du studio… Les propositions et les fichiers restent conservés.")
        self.open_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")

        def task():
            options = {"cwd": str(ROOT), "stdout": self.log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                subprocess.run(service_command("stop"), **options, timeout=60, check=True)
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
            remember_installation(home)
            environment = adoption_environment(home)
            command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "studio_entry.py")]
            subprocess.Popen(command, env=environment, cwd=str(home))
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
