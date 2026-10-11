"""Native setup keeps local/private configuration and existing music intact."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
import wave

from dotenv import dotenv_values
import pytest

from playlist25 import cloud_sync, db, desktop
from playlist25.config import Settings, initialize_local_config


def test_password_change_preserves_private_settings_and_music(tmp_path):
    home = tmp_path / "Installation existante"
    private = initialize_local_config(home)
    with private.open("a", encoding="utf-8") as output:
        output.write("DATA_DIR='musique existante'\nSUPABASE_URL='https://original.supabase.co'\nSUPABASE_SERVICE_ROLE_KEY='private-existing-fixture'\nPUBLIC_URL='https://example.com/fete'\n")
    audio = home / "musique existante" / "audio" / "favori.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"existing audio must stay")
    before = dotenv_values(private)
    desktop.save_private_settings(home, password="ma nouvelle phrase secrete")
    after = dotenv_values(private)
    assert after["ADMIN_PASSWORD"] == "ma nouvelle phrase secrete"
    assert {k: v for k, v in before.items() if k != "ADMIN_PASSWORD"} == {k: v for k, v in after.items() if k != "ADMIN_PASSWORD"}
    assert audio.read_bytes() == b"existing audio must stay"


def test_cloud_validation_failure_leaves_configuration_unchanged(tmp_path, monkeypatch):
    private = initialize_local_config(tmp_path)
    before = private.read_bytes()
    def denied(*args):
        raise cloud_sync.CloudSyncError("Connexion refusée.")
    monkeypatch.setattr(cloud_sync, "_rpc", denied)
    with pytest.raises(ValueError, match="Connexion refusée"):
        desktop.save_private_settings(tmp_path, password="a sufficiently long password", key="sb_secret_fixture-only")
    assert private.read_bytes() == before


def test_new_setup_checks_cloud_read_only_and_keeps_session_secret(tmp_path, monkeypatch):
    private = initialize_local_config(tmp_path)
    secret = dotenv_values(private)["SESSION_SECRET"]
    calls = []
    def snapshot(cfg, name, payload):
        calls.append((cfg.supabase_url, name, payload))
        return {"proposals": [{"id": 1}]}
    monkeypatch.setattr(cloud_sync, "_rpc", snapshot)
    desktop.save_private_settings(tmp_path, password="phrase pour la soiree", key="sb_secret_test-fixture", project_url="https://project.supabase.co")
    values = dotenv_values(private)
    assert calls == [("https://project.supabase.co", "playlist25_snapshot", {})]
    assert values["SESSION_SECRET"] == secret
    assert values["ADMIN_PASSWORD"] == "phrase pour la soiree"
    assert values["SUPABASE_SERVICE_ROLE_KEY"] == "sb_secret_test-fixture"
    assert values["PUBLIC_URL"] == desktop.PUBLIC_URL


@pytest.mark.parametrize("changes", [
    {"password": "short"}, {"password": "long password\nsecond line"},
    {"key": "sbp_personal-account-token"}, {"key": "sb_publishable_public-fixture"},
    {"key": "secret\rheader"},
])
def test_bad_credentials_never_create_or_change_configuration(tmp_path, changes):
    with pytest.raises(ValueError):
        desktop.save_private_settings(tmp_path, **changes)
    assert not (tmp_path / ".env.local").exists()


def test_adoption_changes_only_pointer_and_never_moves_old_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "App data"))
    monkeypatch.delenv("PLAYLIST25_HOME", raising=False)
    previous = tmp_path / "ancien dossier Ekoraid"
    private = initialize_local_config(previous)
    before = private.read_bytes()
    audio = previous / "data" / "audio" / "ancien.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"old audio")
    desktop.remember_installation(previous)
    assert desktop.selected_home() == previous.resolve()
    assert private.read_bytes() == before
    assert audio.read_bytes() == b"old audio"
    assert not (desktop.user_home() / "data").exists()
    with pytest.raises(ValueError):
        desktop.remember_installation(tmp_path / "dossier vide")
    assert desktop.selected_home() == previous.resolve()


def test_adoption_does_not_inherit_previous_database_or_cloud(tmp_path, monkeypatch):
    previous = tmp_path / "previous installation"
    adopted = tmp_path / "adopted installation"
    initialize_local_config(adopted)
    monkeypatch.setenv("DATA_DIR", str(previous / "separate data"))
    monkeypatch.setenv("SUPABASE_URL", "https://old.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "old-private-fixture")
    environment = desktop.adoption_environment(adopted)
    assert environment["PATH"] == os.environ["PATH"]
    result = subprocess.run(
        [sys.executable, "-c", "import json; from playlist25.config import load_settings; c=load_settings(); print(json.dumps({'data_dir':str(c.data_dir),'cloud':bool(c.supabase_url or c.supabase_service_role_key)}))"],
        env=environment, capture_output=True, text=True, check=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert json.loads(result.stdout) == {"data_dir": str(adopted.resolve() / "data"), "cloud": False}


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("frozen", [False, True])
def test_native_adoption_passes_explicit_valid_handles_and_new_configuration(tmp_path, monkeypatch, windows, frozen):
    home = tmp_path / "restored installation"
    private = initialize_local_config(home)
    before = private.read_bytes()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "App data"))
    monkeypatch.delenv("PLAYLIST25_HOME", raising=False)
    monkeypatch.setenv("DATA_DIR", "wrong-old-data")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "wrong-old-key")
    monkeypatch.setattr(desktop, "os", SimpleNamespace(name="nt" if windows else "posix", environ=os.environ))
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **options: calls.append((command, options)))
    desktop.launch_installation(home)
    command, options = calls[0]
    assert command[0] == sys.executable
    assert len(command) == (1 if frozen else 2)
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert options["close_fds"] is True and options["cwd"] == str(home.resolve())
    assert options["env"]["PLAYLIST25_HOME"] == str(home.resolve())
    assert "DATA_DIR" not in options["env"] and "SUPABASE_SERVICE_ROLE_KEY" not in options["env"]
    assert bool(options.get("creationflags")) is windows
    assert private.read_bytes() == before and desktop.selected_home() == home.resolve()


@pytest.fixture
def private_studio(tmp_path):
    home = tmp_path / "Current studio"
    private = initialize_local_config(home)
    with private.open("a", encoding="utf-8") as output:
        output.write("SUPABASE_URL='https://private-fixture.supabase.co'\nSUPABASE_SERVICE_ROLE_KEY='private-backup-fixture-never-display'\n")
    cfg = Settings(root=home, data_dir=home / "data")
    db.init_db(cfg)
    with db.connect(cfg) as conn:
        conn.execute("INSERT INTO guests(id,nickname,created_at,avatar_data,avatar_version) VALUES(?,?,?,?,?)",
                     ("guest-fixture", "Camille", db.now(), "stored-photo-fixture", "photo-version-fixture"))
        source_id = db.upsert_source(conn, {"title": "Morceau conservé", "status": "available"})
        proposal_id = conn.execute("INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,?,?)",
                                   (source_id, "guest-fixture", db.now())).lastrowid
        conn.execute("INSERT INTO votes VALUES(?,?,?)", (proposal_id, "guest-fixture", db.now()))
        conn.execute("UPDATE sources SET file_path='fete.wav' WHERE id=?", (source_id,))
    audio = cfg.audio_dir / "fete.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b"\x00\x00" * 800)
    return home, cfg, private.read_bytes(), audio.read_bytes()


@pytest.fixture
def native_window(monkeypatch):
    """Exercise native workflows without a display server or external processes."""
    from tkinter import filedialog, messagebox
    class State:
        def __init__(self): self.value = None
        def set(self, value): self.value = value
        def configure(self, **options): self.value = options.get("state")
    class ImmediateThread:
        def __init__(self, target, **_options): self.target = target
        def start(self): self.target()
    window = desktop.StudioWindow.__new__(desktop.StudioWindow)
    window.window = object()
    window.busy = False
    window.runner = SimpleNamespace(poll=lambda: None)
    window.log = None
    window.status, window.open_button, window.stop_button = State(), State(), State()
    window.events = []
    window.post = lambda function, *args: function(*args)
    def stop(after=None):
        window.events.append("stop")
        window.runner = None
        if after: after()
    window.stop = stop
    window.start = lambda: window.events.append("start")
    window.destroy = lambda: window.events.append("destroy")
    messages = []
    monkeypatch.setattr(desktop.threading, "Thread", ImmediateThread)
    monkeypatch.delenv("PLAYLIST25_HOME", raising=False)
    monkeypatch.setattr(messagebox, "showinfo", lambda title, text, **_kw: messages.append((title, text)))
    monkeypatch.setattr(messagebox, "showerror", lambda title, text, **_kw: messages.append((title, text)))
    monkeypatch.setattr(messagebox, "askokcancel", lambda title, text, **_kw: messages.append((title, text)) or True)
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **_kw: "")
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_kw: "")
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_kw: "")
    return window, messages


def test_native_backup_stops_first_and_saves_complete_private_inventory(tmp_path, private_studio, native_window, monkeypatch):
    from tkinter import filedialog
    from playlist25 import backups
    home, cfg, original_private, original_audio = private_studio
    window, messages = native_window
    archive = tmp_path / "Mon studio.zip"
    monkeypatch.setattr(desktop, "ROOT", home)
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **_kw: str(archive))
    create = backups.create_backup
    def checked_create(*args, **kwargs):
        assert window.events == ["stop"] and window.runner is None
        assert kwargs == {"include_audio": True}
        return create(*args, **kwargs)
    monkeypatch.setattr(backups, "create_backup", checked_create)
    window.backup()
    inventory = backups.inspect_backup(archive)
    assert inventory["include_audio"] is True
    assert inventory["counts"]["proposals"] == inventory["counts"]["votes"] == inventory["counts"]["photos"] == inventory["counts"]["audio_files"] == 1
    assert (home / ".env.local").read_bytes() == original_private
    assert (cfg.audio_dir / "fete.wav").read_bytes() == original_audio
    assert not window.busy and window.open_button.value == "normal"
    assert messages[-1][0] == "Sauvegarde terminée"
    assert window.events == ["stop", "start"]
    assert "private-backup-fixture-never-display" not in str(messages)
    assert dotenv_values(home / ".env.local")["ADMIN_PASSWORD"] not in str(messages)


def test_native_backup_cancel_does_not_stop_or_write(native_window):
    window, messages = native_window
    window.backup()
    assert not window.events and not messages and not window.busy


@pytest.mark.parametrize("running", [True, False])
def test_native_backup_failure_keeps_old_archive_and_resumes_only_running_studio(tmp_path, private_studio, native_window, monkeypatch, running):
    from tkinter import filedialog
    home, _cfg, original_private, _audio = private_studio
    window, messages = native_window
    if not running:
        window.runner = None
    archive = tmp_path / "existing-backup.zip"
    archive.write_bytes(b"previous backup must never be replaced")
    monkeypatch.setattr(desktop, "ROOT", home)
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **_kw: str(archive))
    window.backup()
    assert archive.read_bytes() == b"previous backup must never be replaced"
    assert (home / ".env.local").read_bytes() == original_private
    assert window.events == (["stop", "start"] if running else ["stop"])
    assert messages[-1][0] == "Sauvegarder mon studio" and not window.busy


def test_native_backup_does_not_run_after_failed_stop(tmp_path, native_window, monkeypatch):
    from tkinter import filedialog
    from playlist25 import backups
    window, _messages = native_window
    archive = tmp_path / "must-not-exist.zip"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **_kw: str(archive))
    window.stop = lambda after=None: window.events.append("stop failed")
    monkeypatch.setattr(backups, "create_backup", lambda *_args, **_kw: pytest.fail("Backup began before services stopped"))
    window.backup()
    assert window.events == ["stop failed"] and not archive.exists()


def test_native_stop_cleans_recorded_services_without_owned_process_or_console(tmp_path, native_window, monkeypatch):
    window, _messages = native_window
    window.runner = None
    (tmp_path / "processes.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(desktop, "current_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(desktop, "service_command", lambda *args: ["fixture-studio", *args])
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda command, **options: calls.append((command, options)))
    desktop.StudioWindow.stop(window, after=lambda: window.events.append("after clean stop"))
    assert calls[0][0] == ["fixture-studio", "stop"]
    assert calls[0][1]["stdin"] == calls[0][1]["stdout"] == subprocess.DEVNULL
    assert calls[0][1]["stderr"] == subprocess.STDOUT
    assert calls[0][1]["check"] is True
    assert window.events == ["after clean stop"] and not window.busy


def test_native_stop_failure_never_begins_backup_or_restore(tmp_path, native_window, monkeypatch):
    window, _messages = native_window
    window.runner = None
    (tmp_path / "processes.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(desktop, "current_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(desktop, "service_command", lambda *args: ["fixture-studio", *args])
    def failed_stop(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "fixture-studio")
    monkeypatch.setattr(subprocess, "run", failed_stop)
    desktop.StudioWindow.stop(window, after=lambda: pytest.fail("Operation began while services may still be writing"))
    assert not window.events and not window.busy and "L'arrêt n'est pas terminé" in window.status.value


def test_native_restore_cancel_keeps_running_studio_and_pointer(tmp_path, private_studio, native_window, monkeypatch):
    from tkinter import filedialog, messagebox
    from playlist25 import backups
    home, _cfg, original_private, _audio = private_studio
    window, messages = native_window
    archive = tmp_path / "studio.zip"
    backups.create_backup(home, archive)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "App data"))
    desktop.remember_installation(home)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_kw: str(archive))
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_kw: str(tmp_path))
    monkeypatch.setattr(messagebox, "askokcancel", lambda title, text, **_kw: messages.append((title, text)) or False)
    window.restore()
    assert window.runner is not None and not window.events and not window.busy
    assert desktop.selected_home() == home.resolve() and (home / ".env.local").read_bytes() == original_private
    assert not list(tmp_path.glob("StudioFanny-restaure-*"))
    assert "1 proposition(s)" in messages[-1][1] and "1 photo(s)" in messages[-1][1]
    assert "Nouveau studio :" in messages[-1][1] and "private-backup-fixture-never-display" not in str(messages)


def test_native_restore_reopens_new_copy_and_preserves_original_and_cloud_credentials(tmp_path, private_studio, native_window, monkeypatch):
    from tkinter import filedialog
    from playlist25 import backups
    home, cfg, original_private, original_audio = private_studio
    window, messages = native_window
    archive = tmp_path / "studio.zip"
    backups.create_backup(home, archive)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "App data"))
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_kw: str(archive))
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_kw: str(tmp_path))
    original_restore = backups.restore_backup
    expected_digest = backups.inspect_backup(archive)["manifest_sha256"]
    def checked_restore(*args, **kwargs):
        assert window.events == ["stop"] and window.runner is None
        assert kwargs == {"expected_manifest_sha256": expected_digest}
        return original_restore(*args, **kwargs)
    monkeypatch.setattr(backups, "restore_backup", checked_restore)
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **options: calls.append((command, options)))
    monkeypatch.setattr(cloud_sync, "_rpc", lambda *_args, **_kw: pytest.fail("Restore must never publish or reset public data"))
    window.restore()
    restored = desktop.selected_home()
    assert restored != home.resolve() and restored.parent == tmp_path.resolve()
    assert window.events == ["stop", "destroy"] and len(calls) == 1
    assert calls[0][1]["env"]["PLAYLIST25_HOME"] == str(restored)
    assert calls[0][1]["stdin"] == calls[0][1]["stdout"] == calls[0][1]["stderr"] == subprocess.DEVNULL
    old_values, new_values = dotenv_values(home / ".env.local"), dotenv_values(restored / ".env.local")
    for key in ("ADMIN_PASSWORD", "SESSION_SECRET", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        assert new_values[key] == old_values[key]
    with db.connect(Settings(root=restored, data_dir=restored / "data")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 1
        assert conn.execute("SELECT avatar_data FROM guests").fetchone()[0] == "stored-photo-fixture"
        assert db.get_setting(conn, "queue_paused") is True
    assert (restored / "data" / "audio" / "fete.wav").read_bytes() == original_audio
    assert (home / ".env.local").read_bytes() == original_private and (cfg.audio_dir / "fete.wav").read_bytes() == original_audio
    assert "private-backup-fixture-never-display" not in str(messages)


def test_native_restore_rejects_invalid_archive_without_stopping(tmp_path, native_window, monkeypatch):
    from tkinter import filedialog
    window, messages = native_window
    archive = tmp_path / "not-a-backup.zip"
    archive.write_bytes(b"not a zip")
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_kw: str(archive))
    window.restore()
    assert not window.events and window.runner is not None and not window.busy
    assert messages[-1][0] == "Restaurer une sauvegarde"


def test_native_restore_failure_never_adopts_or_overwrites_current_home(tmp_path, private_studio, native_window, monkeypatch):
    from tkinter import filedialog
    from playlist25 import backups
    home, cfg, original_private, original_audio = private_studio
    window, messages = native_window
    archive = tmp_path / "studio.zip"
    backups.create_backup(home, archive)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "App data"))
    desktop.remember_installation(home)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_kw: str(archive))
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_kw: str(tmp_path))
    monkeypatch.setattr(desktop, "restored_home", lambda parent: home)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kw: pytest.fail("Failed restore must not launch a new installation"))
    window.restore()
    assert window.events == ["stop"] and desktop.selected_home() == home.resolve()
    assert (home / ".env.local").read_bytes() == original_private and (cfg.audio_dir / "fete.wav").read_bytes() == original_audio
    assert not window.busy and messages[-1][0] == "Restaurer une sauvegarde"


def test_native_restore_reports_audio_missing_from_archive_before_and_after_copy(tmp_path, private_studio, native_window, monkeypatch):
    from tkinter import filedialog
    from playlist25 import backups
    home, cfg, original_private, _audio = private_studio
    window, messages = native_window
    (cfg.audio_dir / "fete.wav").unlink()
    archive = tmp_path / "studio missing audio.zip"
    inventory = backups.create_backup(home, archive)
    assert inventory["missing_audio"] == 1
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "App data"))
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_kw: str(archive))
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_kw: str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kw: None)
    window.restore()
    assert "Fichiers audio déjà absents de ce PC : 1" in messages[0][1]
    assert messages[-1][0] == "Studio restauré" and "1 fichier(s) audio" in messages[-1][1]
    assert "Sauvegarde complète" not in str(messages)
    assert window.events == ["stop", "destroy"]
    assert (home / ".env.local").read_bytes() == original_private


def test_native_restore_refuses_archive_replaced_after_inventory_confirmation(tmp_path, private_studio, native_window, monkeypatch):
    from tkinter import filedialog, messagebox
    from playlist25 import backups
    home, cfg, original_private, original_audio = private_studio
    window, messages = native_window
    archive, replacement_archive = tmp_path / "studio.zip", tmp_path / "replacement.zip"
    backups.create_backup(home, archive)
    alternate_home = tmp_path / "Different valid backup"
    backups.restore_backup(archive, alternate_home)
    backups.create_backup(alternate_home, replacement_archive)
    assert backups.inspect_backup(archive)["manifest_sha256"] != backups.inspect_backup(replacement_archive)["manifest_sha256"]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "App data"))
    desktop.remember_installation(home)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **_kw: str(archive))
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_kw: str(tmp_path))
    def confirm_then_replace(_title, _text, **_kw):
        shutil.copyfile(replacement_archive, archive)
        return True
    monkeypatch.setattr(messagebox, "askokcancel", confirm_then_replace)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kw: pytest.fail("Changed archive must never be adopted"))
    window.restore()
    assert window.events == ["stop"] and desktop.selected_home() == home.resolve()
    assert not list(tmp_path.glob("StudioFanny-restaure-*"))
    assert (home / ".env.local").read_bytes() == original_private and (cfg.audio_dir / "fete.wav").read_bytes() == original_audio
    assert messages[-1][0] == "Restaurer une sauvegarde" and not window.busy


def test_restored_home_is_new_and_does_not_create_anything_before_confirmation(tmp_path):
    first, second = desktop.restored_home(tmp_path), desktop.restored_home(tmp_path)
    assert first != second and first.parent == second.parent == tmp_path.resolve()
    assert not first.exists() and not second.exists()
    with pytest.raises(ValueError):
        desktop.restored_home(tmp_path / "does-not-exist")


def test_adopting_current_installation_keeps_its_window_and_opens_current_studio(tmp_path, native_window, monkeypatch):
    from tkinter import filedialog
    home = tmp_path / "Current studio"
    private = initialize_local_config(home)
    original = private.read_bytes()
    window, messages = native_window
    window.runner = None
    monkeypatch.setattr(desktop, "ROOT", home)
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_kw: str(home))
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kw: pytest.fail("Current installation must not launch another desktop competing for its lock"))
    window.adopt()
    assert window.events == ["start"] and window.busy is False
    assert private.read_bytes() == original
    assert "déjà sélectionnée" in messages[-1][1]


def test_native_diagnostic_works_before_setup_without_starting_or_stopping_services(tmp_path, native_window, monkeypatch):
    from playlist25 import diagnostics
    window, messages = native_window
    window.runner = None
    unconfigured = tmp_path / "not configured"
    diagnostic = tmp_path / "local diagnostic.txt"
    calls = []
    monkeypatch.setattr(desktop, "ROOT", unconfigured)
    def write(home):
        assert window.busy is True
        calls.append(("write", home))
        diagnostic.write_text("Safe local diagnostic", encoding="utf-8")
        return diagnostic
    monkeypatch.setattr(diagnostics, "write_diagnostic", write)
    monkeypatch.setattr(diagnostics, "open_diagnostic", lambda path: calls.append(("open", path)))
    window.diagnostic()
    assert calls == [("write", unconfigured), ("open", diagnostic)]
    assert not window.events and not messages and not unconfigured.exists()
    assert not window.busy and window.open_button.value == "normal"
    assert "Diagnostic local créé" in window.status.value


def test_native_diagnostic_error_never_displays_raw_exception_or_changes_services(native_window, monkeypatch):
    from playlist25 import diagnostics
    window, messages = native_window
    def denied(_home):
        raise OSError("private-fixture-must-not-appear")
    monkeypatch.setattr(diagnostics, "write_diagnostic", denied)
    monkeypatch.setattr(diagnostics, "open_diagnostic", lambda _path: pytest.fail("Failed diagnostic was opened"))
    window.diagnostic()
    assert window.runner is not None and not window.events and not window.busy
    assert messages[-1][0] == "Ouvrir le diagnostic"
    assert "private-fixture-must-not-appear" not in str(messages)


def test_native_diagnostic_keeps_created_report_if_os_cannot_open_it(tmp_path, native_window, monkeypatch):
    from playlist25 import diagnostics
    window, messages = native_window
    diagnostic = tmp_path / "diagnostic.txt"
    diagnostic.write_text("Safe report", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "write_diagnostic", lambda _home: diagnostic)
    def no_default_program(_path):
        raise OSError("no default reader")
    monkeypatch.setattr(diagnostics, "open_diagnostic", no_default_program)
    window.diagnostic()
    assert diagnostic.read_text(encoding="utf-8") == "Safe report"
    assert str(diagnostic) in messages[-1][1] and not window.events and not window.busy
