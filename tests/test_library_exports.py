from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace
import zipfile

from mutagen.easyid3 import EasyID3
import pytest

from playlist25.config import Settings
from playlist25.db import connect, init_db, now, upsert_source
from playlist25.exports import ExportError, catalogue_export, prepare_pack
from playlist25.library import LibraryError, available_sources, import_audio, rename_audio, resolve_audio_path, safe_filename, verify_audio
from playlist25 import exports, library


@pytest.fixture
def cfg(tmp_path):
    settings = Settings(root=tmp_path, data_dir=tmp_path / "data", min_free_mb=0, max_duration=60)
    init_db(settings)
    return settings


@pytest.fixture
def audio(cfg):
    if not shutil.which(cfg.ffmpeg_path) or not shutil.which(cfg.ffprobe_path):
        pytest.skip("The audio integration tests require ffmpeg and ffprobe")
    path = cfg.temp_dir / "original échantillon.mp3"
    subprocess.run([cfg.ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1.2", "-codec:a", "libmp3lame", "-q:a", "4", str(path)], check=True)
    return path


def add_proposal(conn, source_id, guest, sort_order=0, title=None):
    conn.execute("INSERT OR IGNORE INTO guests (id,nickname,created_at) VALUES (?,?,?)", (guest, "Invité " + guest, now()))
    cursor = conn.execute("INSERT INTO proposals (source_id,guest_id,state,sort_order,created_at) VALUES (?,?,?,?,?)",
                          (source_id, guest, "approved", sort_order, now()))
    if title:
        conn.execute("UPDATE sources SET title=? WHERE id=?", (title, source_id))
    return cursor.lastrowid


def test_real_audio_import_preserves_original_and_rename_writes_mp3_tags(cfg, audio):
    original_bytes = audio.read_bytes()
    verified = verify_audio(audio, cfg)
    assert verified["codec"] == "mp3"
    assert 1 <= verified["duration"] <= 2
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, 'Été / CON : "tête"', "Artiste réel")
        first_path = resolve_audio_path(cfg, item["file_path"])
        assert first_path.is_file()
        assert str(item["id"]).zfill(6) in first_path.name
        assert all(character not in first_path.name for character in '<>:"/\\|?*')
        assert EasyID3(first_path)["title"] == ['Été / CON : "tête"']
        renamed = rename_audio(conn, cfg, item["id"], "Nouveau titre é", "Artiste corrigé")
        new_path = resolve_audio_path(cfg, renamed["file_path"])
        assert renamed["id"] == item["id"]
        assert new_path != first_path and new_path.is_file() and not first_path.exists()
        assert EasyID3(new_path)["title"] == ["Nouveau titre é"]
        assert EasyID3(new_path)["artist"] == ["Artiste corrigé"]
        assert len(available_sources(conn, cfg)) == 1
        conn.execute("UPDATE sources SET status='interrupted' WHERE id=?", (item["id"],))
        assert available_sources(conn, cfg) == []
        conn.execute("UPDATE sources SET status='available' WHERE id=?", (item["id"],))
        new_path.unlink()
        assert available_sources(conn, cfg) == []
    assert audio.read_bytes() == original_bytes


def test_wav_import_preserves_format_and_known_tags(cfg, audio):
    wav = cfg.temp_dir / "mon audio.wav"
    subprocess.run([cfg.ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(audio), str(wav)], check=True)
    original = wav.read_bytes()
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, wav, "Titre wav", "")
        destination = resolve_audio_path(cfg, item["file_path"])
        assert destination.suffix == ".wav"
        assert verify_audio(destination, cfg)["codec"].startswith("pcm_")
    assert wav.read_bytes() == original


@pytest.mark.parametrize("relative", ["../outside.mp3", "nested/../../outside.mp3", "/etc/passwd", "C:/Windows/test.mp3", "foo\\bar.mp3", "", "file\x00.mp3"])
def test_audio_paths_cannot_escape_library(cfg, relative):
    with pytest.raises(LibraryError):
        resolve_audio_path(cfg, relative)


def test_symlinks_cannot_escape_and_import_cannot_read_arbitrary_host_file(cfg, audio):
    (cfg.audio_dir / "outside.mp3").symlink_to(audio)
    with pytest.raises(LibraryError):
        resolve_audio_path(cfg, "outside.mp3")
    outside = cfg.root / "not-an-upload.mp3"
    shutil.copy2(audio, outside)
    with connect(cfg) as conn, pytest.raises(LibraryError):
        import_audio(conn, cfg, outside)
    assert outside.is_file()


def test_verify_rejects_missing_empty_invalid_duration_and_video_without_audio(cfg, audio):
    empty = cfg.temp_dir / "empty.mp3"
    empty.touch()
    corrupt = cfg.temp_dir / "corrupt.mp3"
    corrupt.write_text("This is not audio")
    for path in (cfg.temp_dir / "absent.mp3", empty, corrupt):
        with pytest.raises(LibraryError):
            verify_audio(path, cfg)
    with pytest.raises(LibraryError, match="durée"):
        verify_audio(audio, cfg, expected_duration=20)
    video = cfg.temp_dir / "silent.mp4"
    subprocess.run([cfg.ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=black:size=32x32:duration=0.2", "-an", str(video)], check=True)
    with pytest.raises(LibraryError, match="flux audio"):
        verify_audio(video, cfg)


def test_safe_filenames_are_bounded_unicode_windows_safe_and_distinct():
    filename = safe_filename(12, "CON\x00 / ../ \\\n é" + "a" * 500, ".MP3")
    assert filename.startswith("source000012 - ")
    assert filename.endswith(".mp3") and len(filename) <= 170
    assert all(c not in filename for c in '<>:"/\\|?*\x00\n')
    assert safe_filename(13, "CON", "mp3") != safe_filename(12, "CON", "mp3")
    with pytest.raises(LibraryError):
        safe_filename(1, "title", "../exe")
    for title in ("é" * 300, "音楽" * 300, "🎵" * 300):
        name = safe_filename(2**63 - 1, title, "mp3")
        assert len(name.encode("utf-8")) <= 240
        assert len(name.encode("utf-16-le")) // 2 <= 240
    with pytest.raises(LibraryError):
        safe_filename(2**63, "title", "mp3")


def _references(playlist):
    return [line for line in playlist.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]


def test_pack_portable_relative_playlists_order_duplicates_votes_zip_and_missing_files(cfg, audio):
    with connect(cfg) as conn:
        first = import_audio(conn, cfg, audio, "Premier é", "Artiste")
        second = import_audio(conn, cfg, audio, "Deuxième", "")
        missing_id = upsert_source(conn, {"title": "Absent", "status": "available"})
        conn.execute("UPDATE sources SET file_path='absent.mp3' WHERE id=?", (missing_id,))
        first_proposal = add_proposal(conn, first["id"], "a", sort_order=10)
        second_proposal = add_proposal(conn, second["id"], "b", sort_order=0)
        conn.execute("INSERT INTO votes VALUES (?,?,?)", (first_proposal, "a", now()))
        conn.execute("INSERT INTO votes VALUES (?,?,?)", (first_proposal, "b", now()))
        conn.execute("INSERT INTO votes VALUES (?,?,?)", (second_proposal, "b", now()))
        cursor = conn.execute("INSERT INTO playlists (title,source_url,analysis_complete,created_at) VALUES (?,?,?,?)",
                              ("Ma playlist / originale", "https://www.youtube.com/playlist?list=PL_example", 0, now()))
        playlist_id = cursor.lastrowid
        for position, item in enumerate((second, first, second), 1):
            conn.execute("INSERT INTO playlist_entries (playlist_id,position,source_id,title,available) VALUES (?,?,?,?,?)",
                         (playlist_id, position, item["id"], item["title"], 1))
        conn.execute("INSERT INTO playlist_entries (playlist_id,position,title,available,reason,selected) VALUES (?,?,?,?,?,?)",
                     (playlist_id, 4, "Vidéo privée", 0, "Vidéo privée", 0))
        pack = prepare_pack(conn, cfg, [second["id"], first["id"], second["id"], missing_id], make_zip=True)
        assert pack["count"] == 2
        assert any("absent" in warning.lower() for warning in pack["warnings"])
        assert any("incomplète" in warning for warning in pack["warnings"])
        assert any("privée" in warning for warning in pack["warnings"])
        directory = Path(pack["directory"])
        assert len(list((directory / "audio").iterdir())) == 2
        selection = _references(directory / "01-selection.m3u8")
        ranking = _references(directory / "02-classement-votes.m3u8")
        assert "Deuxième" in selection[0] and "Premier" in ranking[0]
        original_playlist = next((directory / "playlists-importees").iterdir())
        original_refs = _references(original_playlist)
        assert len(original_refs) == 3
        assert original_refs[0] == original_refs[2]
        assert all(reference.startswith("../audio/") for reference in original_refs)
        with zipfile.ZipFile(pack["archive"]) as archive:
            assert archive.testzip() is None
            assert len([name for name in archive.namelist() if "/audio/" in name]) == 2
            assert all(name.startswith("Anniversaire25/") and ".." not in Path(name).parts for name in archive.namelist())
        relocated = cfg.root / "sur une autre clé USB"
        shutil.move(directory, relocated)
        for playlist in relocated.rglob("*.m3u8"):
            for reference in _references(playlist):
                resolved = (playlist.parent / reference).resolve()
                assert resolved.is_relative_to(relocated) and resolved.is_file()
        manifest = json.loads((relocated / "manifeste.json").read_text(encoding="utf-8"))
        assert manifest["count"] == 2 and all((relocated / item["file"]).is_file() for item in manifest["sources"])


def test_pack_without_zip_uses_moderator_order_and_ignores_stale_files(cfg, audio):
    with connect(cfg) as conn:
        first = import_audio(conn, cfg, audio, "premier")
        second = import_audio(conn, cfg, audio, "second")
        add_proposal(conn, first["id"], "a", 10)
        add_proposal(conn, second["id"], "b", 0)
        pack = prepare_pack(conn, cfg, make_zip=False)
        assert pack["archive"] is None and pack["download_url"] is None
        assert "second" in _references(Path(pack["directory"]) / "01-selection.m3u8")[0]
        resolve_audio_path(cfg, first["file_path"]).unlink()
        with pytest.raises(ExportError, match="Aucun fichier"):
            prepare_pack(conn, cfg, [first["id"]])


def test_ready_pack_history_survives_a_new_connection_and_returns_only_downloadable_zips(cfg, audio, monkeypatch):
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Morceau conservé")
        first = prepare_pack(conn, cfg, [item["id"]])
        second = prepare_pack(conn, cfg, [item["id"]])
        no_zip = prepare_pack(conn, cfg, [item["id"]], make_zip=False)
        conn.commit()
    monkeypatch.setattr(exports, "verify_audio", lambda *args: pytest.fail("History must not probe audio again"))
    with connect(cfg) as reopened:
        history = exports.list_ready_packs(reopened, cfg)
        assert [item["id"] for item in history] == [second["id"], first["id"]]
        assert all(item["count"] == 1 and item["warnings"] == [] for item in history)
        assert all(item["size"] == Path(pack["archive"]).stat().st_size for item, pack in zip(history, (second, first)))
        assert all(set(item) == {"id", "created_at", "size", "count", "warnings", "download_url"} for item in history)
        assert cfg.root.as_posix() not in json.dumps(history)
        assert no_zip["id"] not in {item["id"] for item in history}
        Path(second["archive"]).unlink()
        assert [item["id"] for item in exports.list_ready_packs(reopened, cfg)] == [first["id"]]


def test_ready_history_omits_escaped_corrupt_missing_and_symlink_archives(cfg, tmp_path):
    outside = tmp_path / "outside.zip"
    with zipfile.ZipFile(outside, "w") as archive:
        archive.writestr("test.txt", "external")
    corrupt = cfg.export_dir / "corrupt.zip"
    corrupt.write_bytes(b"not a ZIP")
    linked = cfg.export_dir / "linked.zip"
    try:
        linked.symlink_to(outside)
    except OSError:
        linked = cfg.export_dir / "absent-link.zip"
    with connect(cfg) as conn:
        for export_id, archive in (("a1", outside), ("a2", corrupt), ("a3", cfg.export_dir / "missing.zip"), ("a4", linked)):
            conn.execute("INSERT INTO exports(id,directory,archive,created_at) VALUES(?,?,?,?)", (export_id, str(cfg.export_dir), str(archive), now()))
        assert exports.list_ready_packs(conn, cfg) == []


def test_ready_history_keeps_legacy_zip_and_bounds_optional_manifest_metadata(cfg, monkeypatch):
    legacy = cfg.export_dir / "legacy.zip"
    large = cfg.export_dir / "large.zip"
    with zipfile.ZipFile(legacy, "w") as archive:
        archive.writestr("old-pack/audio.wav", b"old fixture bytes")
    with zipfile.ZipFile(large, "w") as archive:
        archive.writestr("Anniversaire25/manifeste.json", " " * 1000)
    with connect(cfg) as conn:
        for export_id, archive in (("a1", legacy), ("a2", large)):
            conn.execute("INSERT INTO exports(id,directory,archive,created_at) VALUES(?,?,?,?)", (export_id, str(cfg.export_dir), str(archive), now()))
        monkeypatch.setattr(exports, "_HISTORY_METADATA_LIMIT", 100)
        history = exports.list_ready_packs(conn, cfg)
        assert len(history) == 2
        assert all(item["count"] is None and item["warnings"] == [] for item in history)


def test_ready_history_redacts_old_operational_warning_paths(cfg):
    archived = cfg.export_dir / "private-warning.zip"
    manifest = {"version": 1, "count": 1, "sources": [{}], "warnings": [f"Fichier absent : {cfg.audio_dir}/missing.wav"]}
    with zipfile.ZipFile(archived, "w") as archive:
        archive.writestr("Anniversaire25/manifeste.json", json.dumps(manifest))
    with connect(cfg) as conn:
        conn.execute("INSERT INTO exports(id,directory,archive,created_at) VALUES('abcd',?,?,?)", (str(cfg.export_dir), str(archived), now()))
        history = exports.list_ready_packs(conn, cfg)
        assert history[0]["count"] == 1
        assert cfg.root.as_posix() not in json.dumps(history)
        assert history[0]["warnings"] == ["Fichier absent : [dossier local]/missing.wav"]


def test_disk_failure_does_not_create_partial_pack_or_sources(cfg, audio, monkeypatch):
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "petit extrait")
        DiskUsage = type(shutil.disk_usage(cfg.data_dir))
        monkeypatch.setattr(shutil, "disk_usage", lambda _: DiskUsage(100, 100, 0))
        with pytest.raises(ExportError, match="disque insuffisant"):
            prepare_pack(conn, cfg, [item["id"]])
        assert list(cfg.export_dir.iterdir()) == []
        with pytest.raises(LibraryError, match="disque insuffisant"):
            import_audio(conn, cfg, audio, "encore")
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 0


def test_export_copy_failure_cleans_only_its_partial_pack(cfg, audio, monkeypatch):
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "petit extrait")
        preserved = cfg.export_dir / "pack existant"
        preserved.mkdir()
        (preserved / "keep.txt").write_text("keep")
        def failed_copy(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(exports, "_copy_file", failed_copy)
        with pytest.raises(ExportError):
            prepare_pack(conn, cfg, [item["id"]])
        assert list(cfg.export_dir.iterdir()) == [preserved]
        assert (preserved / "keep.txt").read_text() == "keep"


def test_import_metadata_failure_preserves_upload_and_rolls_back(cfg, audio, monkeypatch):
    from playlist25 import library
    original = audio.read_bytes()
    def fail_tags(*args):
        raise LibraryError("Échec d'écriture des métadonnées")
    monkeypatch.setattr(library, "_write_tags", fail_tags)
    with connect(cfg) as conn, pytest.raises(LibraryError, match="métadonnées"):
        import_audio(conn, cfg, audio, "Titre")
    with connect(cfg) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    assert list(cfg.audio_dir.iterdir()) == []
    assert list(cfg.temp_dir.iterdir()) == [audio]
    assert audio.read_bytes() == original


class _FailingConnection:
    def __init__(self, connection, *, statement=None, commit=False):
        self.connection = connection
        self.statement = statement
        self.fail_commit = commit

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, statement, *args):
        if self.statement is not None and statement.startswith(self.statement):
            self.statement = None
            raise sqlite3.OperationalError("injected SQLite failure")
        return self.connection.execute(statement, *args)

    def commit(self):
        if self.fail_commit:
            self.fail_commit = False
            raise sqlite3.OperationalError("injected commit failure")
        self.connection.commit()


@pytest.mark.parametrize("same_title", [False, True])
def test_rename_release_failure_preserves_original_bytes_and_database(cfg, audio, same_title):
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Ancien titre", "Ancien artiste")
        original = resolve_audio_path(cfg, item["file_path"])
        before = original.read_bytes()
        failing = _FailingConnection(conn, statement="RELEASE SAVEPOINT library_rename")
        with pytest.raises(sqlite3.OperationalError):
            rename_audio(failing, cfg, item["id"], item["title"] if same_title else "Nouveau titre", "Nouvel artiste")
        assert dict(conn.execute("SELECT * FROM sources WHERE id=?", (item["id"],)).fetchone()) == item
        assert original.read_bytes() == before
        assert list(cfg.audio_dir.iterdir()) == [original]
        assert list(cfg.temp_dir.iterdir()) == [audio]


@pytest.mark.parametrize("same_title", [False, True])
def test_file_transaction_commit_failure_rolls_back_rename_and_keeps_original(cfg, audio, same_title):
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Ancien titre", "Ancien artiste")
        original = resolve_audio_path(cfg, item["file_path"])
        before = original.read_bytes()
        failing = _FailingConnection(conn, commit=True)
        with pytest.raises(sqlite3.OperationalError):
            with library.file_transaction(failing):
                renamed = rename_audio(failing, cfg, item["id"], item["title"] if same_title else "Nouveau titre", "Nouvel artiste")
                assert original.read_bytes() == before
                assert resolve_audio_path(cfg, renamed["file_path"]).is_file()
        assert dict(conn.execute("SELECT * FROM sources WHERE id=?", (item["id"],)).fetchone()) == item
        assert original.read_bytes() == before
        assert list(cfg.audio_dir.iterdir()) == [original]


def test_file_transaction_defers_old_file_removal_until_commit_and_keeps_writer_lock(cfg, audio):
    with connect(cfg) as conn, connect(cfg) as observer:
        item = import_audio(conn, cfg, audio, "Ancien titre")
        original = resolve_audio_path(cfg, item["file_path"])
        observer.execute("PRAGMA busy_timeout=0")
        with library.file_transaction(conn):
            renamed = rename_audio(conn, cfg, item["id"], "Titre corrigé", "Artiste")
            destination = resolve_audio_path(cfg, renamed["file_path"])
            assert original.is_file() and destination.is_file()
            assert observer.execute("SELECT file_path FROM sources WHERE id=?", (item["id"],)).fetchone()[0] == item["file_path"]
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                observer.execute("UPDATE sources SET artist='Concurrent' WHERE id=?", (item["id"],))
        assert not original.exists() and destination.is_file()
        assert observer.execute("SELECT file_path FROM sources WHERE id=?", (item["id"],)).fetchone()[0] == renamed["file_path"]


def test_file_transaction_rolls_back_import_after_later_sql_error(cfg, audio):
    original = audio.read_bytes()
    with connect(cfg) as conn:
        with pytest.raises(sqlite3.OperationalError):
            with library.file_transaction(conn):
                import_audio(conn, cfg, audio, "Import annulé")
                conn.execute("INSERT INTO table_inexistante VALUES(1)")
        assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
        assert not list(cfg.audio_dir.iterdir())
    assert audio.read_bytes() == original


def test_rename_does_not_remove_an_original_still_used_by_another_source(cfg, audio):
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Original partagé")
        original = resolve_audio_path(cfg, item["file_path"])
        shared = upsert_source(conn, {"title": "Autre source", "status": "available"})
        conn.execute("UPDATE sources SET file_path=?,duration=? WHERE id=?", (item["file_path"], item["duration"], shared))
        conn.commit()
        with library.file_transaction(conn):
            renamed = rename_audio(conn, cfg, item["id"], "Titre corrigé", "")
        assert original.is_file()
        assert resolve_audio_path(cfg, renamed["file_path"]).is_file()
        assert conn.execute("SELECT file_path FROM sources WHERE id=?", (shared,)).fetchone()[0] == item["file_path"]


def test_exception_after_a_successful_commit_never_deletes_committed_audio(cfg, audio):
    class PostCommitFailure(_FailingConnection):
        def commit(self):
            self.connection.commit()
            raise sqlite3.OperationalError("commit outcome is already durable")
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Original conservé")
        original = resolve_audio_path(cfg, item["file_path"])
        failing = PostCommitFailure(conn)
        with pytest.raises(sqlite3.OperationalError):
            with library.file_transaction(failing):
                renamed = rename_audio(failing, cfg, item["id"], "Titre déjà enregistré", "")
        saved = conn.execute("SELECT file_path FROM sources WHERE id=?", (item["id"],)).fetchone()[0]
        assert saved == renamed["file_path"]
        assert resolve_audio_path(cfg, saved).is_file()
        assert original.is_file()  # Uncertain outcome preserves both versions.


@pytest.mark.parametrize("collision", ["directory", "zip", "partial"])
def test_export_collision_never_removes_a_previous_directory_zip_or_partial(cfg, audio, monkeypatch, collision):
    export_id = "f" * 32
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Original conservé")
        original = resolve_audio_path(cfg, item["file_path"])
        before = original.read_bytes()
        directory = cfg.export_dir / export_id
        archive = cfg.export_dir / f"{export_id}.zip"
        partial = cfg.export_dir / f".{export_id}.zip.part"
        if collision == "directory":
            directory.mkdir()
            (directory / "keep.txt").write_bytes(b"older directory")
        archive.write_bytes(b"older ZIP") if collision in {"directory", "zip"} else None
        partial.write_bytes(b"older partial") if collision in {"directory", "partial"} else None
        before_exports = {path.relative_to(cfg.export_dir): path.read_bytes() for path in cfg.export_dir.rglob("*") if path.is_file()}
        monkeypatch.setattr(exports.uuid, "uuid4", lambda: SimpleNamespace(hex=export_id))
        with pytest.raises(ExportError):
            prepare_pack(conn, cfg, [item["id"]])
        after_exports = {path.relative_to(cfg.export_dir): path.read_bytes() for path in cfg.export_dir.rglob("*") if path.is_file()}
        assert after_exports == before_exports
        assert original.read_bytes() == before
        assert conn.execute("SELECT count(*) FROM exports").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["insert", "release"])
def test_sqlite_export_failure_cleans_new_zip_and_rows_without_altering_audio(cfg, audio, failure):
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Original conservé")
        original = resolve_audio_path(cfg, item["file_path"])
        before = original.read_bytes()
        failing = _FailingConnection(conn, statement="INSERT INTO exports" if failure == "insert" else "RELEASE SAVEPOINT export_publish")
        with pytest.raises(ExportError, match="enregistré"):
            prepare_pack(failing, cfg, [item["id"]])
        assert conn.execute("SELECT count(*) FROM exports").fetchone()[0] == 0
        assert not list(cfg.export_dir.iterdir())
        assert original.read_bytes() == before


def test_export_with_error_after_durable_savepoint_commit_remains_in_ready_history(cfg, audio):
    class PostReleaseFailure(_FailingConnection):
        def execute(self, statement, *args):
            result = self.connection.execute(statement, *args)
            if statement.startswith("RELEASE SAVEPOINT export_publish"):
                raise sqlite3.OperationalError("durable commit reported an error")
            return result
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Original conservé")
        with pytest.raises(ExportError):
            prepare_pack(PostReleaseFailure(conn), cfg, [item["id"]])
        ready = exports.list_ready_packs(conn, cfg)
        assert len(ready) == 1 and ready[0]["count"] == 1
        record = conn.execute("SELECT * FROM exports").fetchone()
        assert Path(record["archive"]).is_file()
        assert Path(record["directory"]).is_dir()


def test_export_zip_is_closed_and_complete_before_atomic_publication(cfg, audio, monkeypatch):
    publish = exports._install_file
    observed = []
    def inspect_then_publish(staged, destination):
        assert not destination.exists()
        with zipfile.ZipFile(staged) as archive:
            assert archive.testzip() is None
            assert "Anniversaire25/01-selection.m3u8" in archive.namelist()
            assert "Anniversaire25/manifeste.json" in archive.namelist()
        observed.append(True)
        publish(staged, destination)
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Fichier prêt")
        monkeypatch.setattr(exports, "_install_file", inspect_then_publish)
        pack = prepare_pack(conn, cfg, [item["id"]])
        assert observed == [True]
        assert Path(pack["archive"]).is_file()
        assert not list(cfg.export_dir.glob("*.part"))


@pytest.mark.parametrize("failure", ["zip_write", "publish"])
def test_late_zip_failure_preserves_previous_pack_and_original_audio(cfg, audio, monkeypatch, failure):
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Original conservé")
        original = resolve_audio_path(cfg, item["file_path"])
        before_audio = original.read_bytes()
        old_pack = prepare_pack(conn, cfg, [item["id"]])
        before_exports = {path.relative_to(cfg.export_dir): path.read_bytes() for path in cfg.export_dir.rglob("*") if path.is_file()}
        if failure == "zip_write":
            opening = zipfile.ZipFile.open
            writes = []
            def failed_write(archive, name, mode="r", *args, **kwargs):
                if mode == "w":
                    writes.append(name)
                    if len(writes) == 2:
                        raise OSError("disk full while writing ZIP")
                return opening(archive, name, mode, *args, **kwargs)
            monkeypatch.setattr(zipfile.ZipFile, "open", failed_write)
        else:
            replace = library.os.replace
            def failed_publish(source, destination):
                if Path(source).suffix == ".part" and Path(destination).suffix == ".zip":
                    raise OSError("disk failure during ZIP publication")
                return replace(source, destination)
            monkeypatch.setattr(library.os, "replace", failed_publish)
        with pytest.raises(ExportError):
            prepare_pack(conn, cfg, [item["id"]])
        after_exports = {path.relative_to(cfg.export_dir): path.read_bytes() for path in cfg.export_dir.rglob("*") if path.is_file()}
        assert after_exports == before_exports
        assert original.read_bytes() == before_audio
        assert conn.execute("SELECT id FROM exports").fetchall()[0][0] == old_pack["id"]
        assert conn.execute("SELECT count(*) FROM exports").fetchone()[0] == 1


def test_export_detects_same_duration_source_replacement_before_copy(cfg, audio, monkeypatch):
    with connect(cfg) as conn:
        item = import_audio(conn, cfg, audio, "Original à exporter")
        original = resolve_audio_path(cfg, item["file_path"])
        replacement = cfg.temp_dir / "replacement.mp3"
        shutil.copy2(original, replacement)
        copy = exports._copy_file
        def replace_then_copy(source, destination, expected=None):
            replacement.replace(original)
            return copy(source, destination, expected)
        monkeypatch.setattr(exports, "_copy_file", replace_then_copy)
        with pytest.raises(ExportError, match="changé"):
            prepare_pack(conn, cfg, [item["id"]])
        assert original.is_file() and verify_audio(original, cfg)["duration"] > 0
        assert conn.execute("SELECT count(*) FROM exports").fetchone()[0] == 0
        assert not list(cfg.export_dir.iterdir())


def test_cross_volume_install_reports_success_when_only_staging_cleanup_fails(cfg, monkeypatch):
    import errno
    staged = cfg.temp_dir / "staged.wav"
    destination = cfg.audio_dir / "installed.wav"
    staged.write_bytes(b"generated fixture bytes")
    replace = library.os.replace
    unlink = Path.unlink
    def cross_volume(first, second):
        if Path(first) == staged:
            raise OSError(errno.EXDEV, "cross-volume fixture")
        return replace(first, second)
    def locked_staging(path, *args, **kwargs):
        if path == staged:
            raise PermissionError("locked staged file")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(library.os, "replace", cross_volume)
    monkeypatch.setattr(Path, "unlink", locked_staging)
    library._install_file(staged, destination)
    assert destination.read_bytes() == b"generated fixture bytes"
    assert staged.is_file()
    assert not list(cfg.audio_dir.glob(".audio-*.part"))


def test_audio_install_collision_preserves_both_existing_files(cfg):
    staged = cfg.temp_dir / "new.wav"
    destination = cfg.audio_dir / "existing.wav"
    staged.write_bytes(b"new audio")
    destination.write_bytes(b"existing audio")
    with pytest.raises(FileExistsError):
        library._install_file(staged, destination)
    assert staged.read_bytes() == b"new audio"
    assert destination.read_bytes() == b"existing audio"


def test_duration_upload_and_probe_timeout_limits(cfg, audio, monkeypatch):
    from dataclasses import replace
    from playlist25 import library
    long_audio = cfg.temp_dir / "long.mp3"
    subprocess.run([cfg.ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3.2", "-codec:a", "libmp3lame", str(long_audio)], check=True)
    with pytest.raises(LibraryError, match="durée maximale"):
        verify_audio(long_audio, replace(cfg, max_duration=1))
    huge_audio = cfg.temp_dir / "huge.mp3"
    huge_audio.write_bytes(audio.read_bytes() + b"\x00" * (1024 * 1024))
    with connect(cfg) as conn, pytest.raises(LibraryError, match="taille maximale"):
        import_audio(conn, replace(cfg, max_upload_mb=1), huge_audio)
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(library.subprocess, "run", timeout)
    with pytest.raises(LibraryError, match="délai"):
        verify_audio(audio, cfg)


def test_audio_probe_rejects_disguised_network_and_file_playlists(cfg, audio):
    """An uploaded filename must not authorize ffprobe to fetch referenced URLs."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(audio.read_bytes())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hls = cfg.temp_dir / "disguised-stream.mp3"
        hls.write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:1.2,\nhttp://127.0.0.1:{server.server_port}/audio.mp3\n#EXT-X-ENDLIST\n")
        concat = cfg.temp_dir / "disguised-files.mp3"
        concat.write_text(f"ffconcat version 1.0\nfile '{audio.as_posix()}'\n")
        for disguised in (hls, concat):
            with pytest.raises(LibraryError):
                verify_audio(disguised, cfg)
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.mark.parametrize("malicious", ['=HYPERLINK("https://evil")', " +SUM(1,2)", "\t@evil", "-1+1", "\r=1", "@SUM(A1:A2)"])
def test_catalogue_csv_neutralizes_spreadsheet_formulas_and_json_preserves_text(cfg, malicious):
    with connect(cfg) as conn:
        source_id = upsert_source(conn, {"title": malicious, "artist": 'Éléonore, "Artiste"'})
        add_proposal(conn, source_id, "a")
        csv_rows = list(csv.DictReader(io.StringIO(catalogue_export(conn, "csv"))))
        assert csv_rows[0]["title"] == "'" + malicious
        assert csv_rows[0]["artist"] == 'Éléonore, "Artiste"'
        assert json.loads(catalogue_export(conn, "json"))[0]["title"] == malicious
        with pytest.raises(ExportError):
            catalogue_export(conn, "html")
