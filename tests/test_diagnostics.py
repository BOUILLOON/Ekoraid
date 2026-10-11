"""Diagnostics stay usable before GUI setup and never disclose private data."""
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

from playlist25 import diagnostics


def test_read_only_report_omits_secrets_names_and_raw_errors(tmp_path, monkeypatch):
    home = tmp_path / "studio"
    data = home / "personal-data"
    data.mkdir(parents=True)
    configuration = home / ".env.local"
    configuration.write_text("ADMIN_PASSWORD='private-password-fixture'\nSUPABASE_URL='https://private-project.supabase.co'\n"
                             "SUPABASE_SERVICE_ROLE_KEY='sb_secret_fixture'\nDATA_DIR='personal-data'\n", encoding="utf-8")
    database = data / "playlist.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE proposals(title TEXT); INSERT INTO proposals VALUES('private-track');"
                                 "CREATE TABLE guests(name TEXT); INSERT INTO guests VALUES('private-guest');")
    before = {path: path.read_bytes() for path in (configuration, database)}
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setattr(diagnostics, "_tool_version", lambda name: {"available": True, "version": "1.2.3"})
    report_path = diagnostics.write_diagnostic(home, destination=tmp_path / "report.txt",
                                               error=OSError(6, "private-password-fixture sb_secret_fixture"))
    report = report_path.read_text(encoding="utf-8")
    for secret in ("private-password-fixture", "sb_secret_fixture", "private-track", "private-guest", "private-project"):
        assert secret not in report
    assert '"proposals": 1' in report and '"guests": 1' in report
    assert '"errno": 6' in report and '"network_requests": false' in report
    assert {path: path.read_bytes() for path in before} == before
    assert not (data / "processes.json").exists()


def test_no_config_or_bad_database_does_not_initialize_a_studio(tmp_path, monkeypatch):
    home = tmp_path / "new"
    home.mkdir()
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setattr(diagnostics, "_tool_version", lambda name: {"available": False})
    report = diagnostics.collect_diagnostic(home)
    assert not report["configuration_present"] and not report["database_present"]
    assert list(home.iterdir()) == []
    (home / "data").mkdir()
    database = home / "data/playlist.sqlite3"
    database.write_bytes(b"not a SQLite file")
    broken = diagnostics.collect_diagnostic(home)
    assert broken["database_present"] and broken["data_error"]["type"] == "DatabaseError"
    assert database.read_bytes() == b"not a SQLite file"
    assert not (home / ".env.local").exists()


def test_versions_ignore_arbitrary_output_and_use_explicit_standard_handles(tmp_path, monkeypatch):
    tool = tmp_path / "ffmpeg"
    tool.write_text("fixture")
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: str(tool))
    calls = []
    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(returncode=0, stdout="ffmpeg version 8.1.2 PRIVATE_PASSWORD=secret\n")
    monkeypatch.setattr(diagnostics.subprocess, "run", run)
    assert diagnostics._tool_version("ffmpeg") == {"available": True, "version": "8.1.2"}
    assert calls[0][1]["stdin"] == diagnostics.subprocess.DEVNULL
    assert calls[0][1]["capture_output"] is True


def test_diagnostic_entry_runs_without_importing_or_opening_tk(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("diagnostic_entry", Path(__file__).resolve().parents[1] / "scripts/studio_entry.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    home = tmp_path / "home"
    home.mkdir()
    output = tmp_path / "standalone-report.txt"
    monkeypatch.setenv("PLAYLIST25_HOME", str(home))
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setattr(diagnostics, "_tool_version", lambda name: {"available": False})
    monkeypatch.setitem(sys.modules, "tkinter", None)
    monkeypatch.setattr(sys, "argv", ["Studio-Fanny.exe", "--diagnostic", "--output", str(output)])
    assert entry.main() == 0
    response = json.loads(capsys.readouterr().out)
    assert response["secrets_included"] is False and Path(response["report"]).is_file()
    assert list(home.iterdir()) == []


def test_startup_failure_report_drops_unsafe_exception_message(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("failure_entry", Path(__file__).resolve().parents[1] / "scripts/studio_entry.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PLAYLIST25_HOME", str(home))
    monkeypatch.setattr(diagnostics, "_tool_version", lambda name: {"available": False})
    report = entry.startup_diagnostic(RuntimeError("sb_secret_unsafe private-password"))
    assert report.is_file()
    assert "sb_secret_unsafe" not in report.read_text(encoding="utf-8")
    assert list(home.iterdir()) == []


def test_invalid_home_or_unwritable_default_folder_still_produces_report(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(diagnostics, "_tool_version", lambda name: {"available": False})
    original = diagnostics.os.open
    def denied(path, *args, **kwargs):
        if str(path).startswith(str(tmp_path / "StudioFanny")):
            raise PermissionError(13, "private-key")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(diagnostics.os, "open", denied)
    report = diagnostics.write_diagnostic(home="broken\x00private-key")
    try:
        assert report.is_file() and report.parent != tmp_path / "StudioFanny/diagnostics"
        assert "private-key" not in report.read_text(encoding="utf-8")
        assert '"configuration_readable": false' in report.read_text(encoding="utf-8")
    finally:
        report.unlink()
        report.parent.rmdir()


def test_bad_runtime_home_cannot_prevent_diagnostic_dispatch(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bad_home_entry", Path(__file__).resolve().parents[1] / "scripts/studio_entry.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    monkeypatch.setenv("PLAYLIST25_HOME", str(tmp_path))
    def broken_runtime():
        raise ValueError("bad private configuration")
    monkeypatch.setattr(entry, "prepare_runtime", broken_runtime)
    monkeypatch.setitem(sys.modules, "tkinter", None)
    monkeypatch.setattr(diagnostics, "_tool_version", lambda name: {"available": False})
    output = tmp_path / "bad-home-report.txt"
    monkeypatch.setattr(sys, "argv", ["Studio-Fanny.exe", "--diagnostic", "--output", str(output)])
    assert entry.main() == 0
    assert output.is_file() and "bad private configuration" not in output.read_text(encoding="utf-8")
    assert '"startup_error"' in output.read_text(encoding="utf-8")


def test_damaged_dependency_metadata_does_not_hide_report(tmp_path, monkeypatch):
    def unreadable(package):
        raise PermissionError(13, "sb_secret_private")
    monkeypatch.setattr(diagnostics.importlib.metadata, "version", unreadable)
    monkeypatch.setattr(diagnostics, "_tool_version", lambda name: {"available": False})
    report = diagnostics.write_diagnostic(home=tmp_path, destination=tmp_path / "dependency-report.txt")
    assert '"dependency_errors"' in report.read_text(encoding="utf-8")
    assert "sb_secret_private" not in report.read_text(encoding="utf-8")


def test_windows_missing_text_association_opens_the_actual_report_in_notepad(tmp_path, monkeypatch):
    def no_association(path):
        raise OSError(1155, "No application associated")
    monkeypatch.setattr(diagnostics, "os", SimpleNamespace(name="nt", startfile=no_association, environ={"SystemRoot": str(tmp_path)}))
    calls = []
    monkeypatch.setattr(diagnostics.subprocess, "Popen", lambda command, **options: calls.append((command, options)))
    report = tmp_path / "my diagnostic.txt"
    diagnostics.open_diagnostic(report)
    assert calls[0][0] == [str(tmp_path / "System32/notepad.exe"), str(report)]
    assert calls[0][1]["stdin"] == calls[0][1]["stdout"] == calls[0][1]["stderr"] == diagnostics.subprocess.DEVNULL
