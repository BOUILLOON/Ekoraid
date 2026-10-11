"""Internal backup commands never initialize the current studio or leak keys."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from playlist25 import __main__ as runtime
from playlist25 import db
from playlist25.config import Settings


@pytest.fixture
def no_current_studio_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Backup CLI loaded or modified the current studio")
    monkeypatch.setattr(runtime, "initialize_local_config", forbidden)
    monkeypatch.setattr(runtime, "load_settings", forbidden)
    monkeypatch.setattr(runtime, "init_db", forbidden)


def inventory(**extra):
    return {"archive": Path("fixture.zip"), "created_at": "2026-10-11T12:00:00Z", "include_audio": True,
            "confidential": True, "counts": {"sources": 2, "proposals": 2, "guests": 1, "votes": 1, "photos": 1, "audio_files": 2,
                                             "private_key": "never-display-private-key"},
            "file_count": 4, "uncompressed_bytes": 1000, "archive_bytes": 200, "manifest_sha256": "a" * 64,
            "config": {"ADMIN_PASSWORD": "never-display-password", "SUPABASE_SERVICE_ROLE_KEY": "never-display-private-key"}, **extra}


def set_command(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["Studio-Fanny.exe", *arguments])


def test_backup_requires_explicit_source_and_destination_before_current_io(monkeypatch, no_current_studio_io):
    for arguments in (("backup",), ("backup", "--home", "source"), ("backup", "--destination", "archive.zip")):
        set_command(monkeypatch, *arguments)
        with pytest.raises(SystemExit) as error:
            runtime.main()
        assert error.value.code == 2


def test_complete_backup_dispatch_and_json_are_confidential(tmp_path, monkeypatch, capsys, no_current_studio_io):
    calls = []
    def create(home, destination, *, include_audio):
        calls.append((home, destination, include_audio))
        return inventory()
    monkeypatch.setitem(sys.modules, "playlist25.backups", SimpleNamespace(create_backup=create))
    source, destination = tmp_path / "source", tmp_path / "studio.zip"
    set_command(monkeypatch, "backup", "--home", str(source), "--destination", str(destination))
    assert runtime.main() == 0
    assert calls == [(source, destination, True)]
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["include_audio"] is True and summary["counts"]["photos"] == 1
    assert summary["archive"] == "fixture.zip"
    assert "never-display" not in output and "config" not in summary
    assert "private_key" not in summary["counts"]


def test_audio_omission_is_only_explicit_opt_out(monkeypatch, capsys, no_current_studio_io):
    calls = []
    monkeypatch.setitem(sys.modules, "playlist25.backups", SimpleNamespace(create_backup=lambda *args, **kwargs: calls.append(kwargs) or inventory(include_audio=False)))
    set_command(monkeypatch, "backup", "--home", "source", "--destination", "studio.zip", "--without-audio")
    assert runtime.main() == 0
    assert calls == [{"include_audio": False}]
    assert json.loads(capsys.readouterr().out)["include_audio"] is False


def test_inspect_uses_only_archive_and_does_not_adopt_its_configuration(monkeypatch, capsys, no_current_studio_io):
    calls = []
    monkeypatch.setitem(sys.modules, "playlist25.backups", SimpleNamespace(inspect_backup=lambda archive: calls.append(archive) or inventory()))
    set_command(monkeypatch, "inspect-backup", "--archive", "studio.zip")
    assert runtime.main() == 0
    assert calls == [Path("studio.zip")]
    assert json.loads(capsys.readouterr().out)["manifest_sha256"] == "a" * 64


@pytest.mark.parametrize("arguments", [("inspect-backup",), ("restore-backup", "--archive", "studio.zip"),
                                       ("restore-backup", "--home", "new-home")])
def test_inspect_restore_missing_paths_fail_before_current_io(monkeypatch, no_current_studio_io, arguments):
    set_command(monkeypatch, *arguments)
    with pytest.raises(SystemExit) as error:
        runtime.main()
    assert error.value.code == 2


def test_restore_dispatches_explicit_new_home_and_expected_digest(monkeypatch, capsys, no_current_studio_io):
    calls = []
    def restore(archive, new_home, *, expected_manifest_sha256):
        calls.append((archive, new_home, expected_manifest_sha256))
        return inventory(home=Path("new-home"), missing_audio=["one", "two"])
    monkeypatch.setitem(sys.modules, "playlist25.backups", SimpleNamespace(restore_backup=restore))
    set_command(monkeypatch, "restore-backup", "--archive", "studio.zip", "--home", "new-home", "--expected-manifest-sha256", "b" * 64)
    assert runtime.main() == 0
    assert calls == [(Path("studio.zip"), Path("new-home"), "b" * 64)]
    output = capsys.readouterr().out
    assert json.loads(output)["missing_audio"] == 2
    assert "never-display" not in output


def test_invalid_digest_cannot_reach_restore(monkeypatch, no_current_studio_io):
    set_command(monkeypatch, "restore-backup", "--archive", "studio.zip", "--home", "new-home", "--expected-manifest-sha256", "invalid")
    with pytest.raises(SystemExit) as error:
        runtime.main()
    assert error.value.code == 2


def test_real_cli_round_trip_restores_explicit_copy_without_touching_current_studio(tmp_path):
    import wave
    source = tmp_path / "Source privé"
    source.mkdir()
    config = ("ADMIN_PASSWORD='private-cli-password-fixture'\nSESSION_SECRET='private-cli-session-fixture-at-least-32-chars'\n"
              "SUPABASE_URL='https://private-cli.supabase.co'\nSUPABASE_SERVICE_ROLE_KEY='private-cli-key-never-display'\n")
    (source / ".env.local").write_text(config, encoding="utf-8")
    cfg = Settings(root=source, data_dir=source / "data")
    db.init_db(cfg)
    conn = db.connect(cfg)
    try:
        with conn:
            conn.execute("INSERT INTO guests(id,nickname,created_at,avatar_data,avatar_version) VALUES(?,?,?,?,?)",
                         ("guest", "Camille", db.now(), "local-photo-fixture", "version"))
            source_id = db.upsert_source(conn, {"title": "Morceau local", "status": "available"})
            proposal_id = conn.execute("INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,?,?)", (source_id, "guest", db.now())).lastrowid
            conn.execute("INSERT INTO votes VALUES(?,?,?)", (proposal_id, "guest", db.now()))
            conn.execute("UPDATE sources SET file_path='silence.wav' WHERE id=?", (source_id,))
    finally:
        conn.close()
    audio = cfg.audio_dir / "silence.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b"\x00\x00" * 800)
    original_config, original_audio = (source / ".env.local").read_bytes(), audio.read_bytes()
    current = tmp_path / "Current must remain untouched"
    current.mkdir()
    guard_config = current / ".env.local"
    guard_config.write_text("ADMIN_HOST=0.0.0.0\n# Intentionally invalid if current config were loaded.\n", encoding="utf-8")
    current_bytes = guard_config.read_bytes()
    environment = {key: value for key, value in os.environ.items() if key not in {name.upper() for name in Settings.__dataclass_fields__}}
    environment.update(PLAYLIST25_HOME=str(current), PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONUTF8="1")
    def command(*arguments, expect=0):
        process = subprocess.run([sys.executable, "-m", "playlist25", *arguments], cwd=current, env=environment,
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert process.returncode == expect, process.stderr
        assert "private-cli-key-never-display" not in process.stdout + process.stderr
        assert "private-cli-password-fixture" not in process.stdout + process.stderr
        assert guard_config.read_bytes() == current_bytes
        assert not (current / "data").exists()
        return json.loads(process.stdout) if expect == 0 else process
    archive = tmp_path / "Backup complet.zip"
    backup = command("backup", "--home", str(source), "--destination", str(archive))
    inspected = command("inspect-backup", "--archive", str(archive))
    assert backup["include_audio"] is True
    assert inspected["counts"]["proposals"] == inspected["counts"]["votes"] == inspected["counts"]["photos"] == inspected["counts"]["audio_files"] == 1
    restored = tmp_path / "Restored copy"
    result = command("restore-backup", "--archive", str(archive), "--home", str(restored),
                     "--expected-manifest-sha256", inspected["manifest_sha256"])
    assert Path(result["home"]) == restored
    assert (restored / "data" / "audio" / "silence.wav").read_bytes() == original_audio
    assert (source / ".env.local").read_bytes() == original_config and audio.read_bytes() == original_audio
    restored_cfg = Settings(root=restored, data_dir=restored / "data")
    conn = db.connect(restored_cfg)
    try:
        assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 1
        assert conn.execute("SELECT avatar_data FROM guests").fetchone()[0] == "local-photo-fixture"
    finally:
        conn.close()
    restored_bytes = (restored / ".env.local").read_bytes()
    command("restore-backup", "--archive", str(archive), "--home", str(restored), expect=1)
    assert (restored / ".env.local").read_bytes() == restored_bytes
