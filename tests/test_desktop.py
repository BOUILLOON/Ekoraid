"""Native setup keeps local/private configuration and existing music intact."""
from pathlib import Path
import json
import os
import subprocess
import sys

from dotenv import dotenv_values
import pytest

from playlist25 import cloud_sync, desktop
from playlist25.config import initialize_local_config


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
