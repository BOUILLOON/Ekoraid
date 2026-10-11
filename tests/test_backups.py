"""Real private archives, database preservation, hostile inputs and disk commits."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import wave
import zipfile

from dotenv import dotenv_values
import pytest

from playlist25 import backups, db
from playlist25.backups import BackupError, create_backup, inspect_backup, restore_backup
from playlist25.config import Settings, initialize_local_config


@pytest.fixture
def studio(tmp_path):
    home = tmp_path / "Studio existant"
    config = initialize_local_config(home)
    with config.open("a", encoding="utf-8") as output:
        output.write("DATA_DIR='musique privée'\nSUPABASE_URL='https://fixture.supabase.co'\nSUPABASE_SERVICE_ROLE_KEY='fixture-private-no-network'\nFFMPEG_PATH='/previous-install/ffmpeg'\nJS_RUNTIME='/previous-install/node'\nMIN_FREE_MB=0\n")
    cfg = Settings(root=home, data_dir=home / "musique privée", min_free_mb=0)
    db.init_db(cfg)
    audio = cfg.audio_dir / "source000007 - Café.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * 100)
    with closing(db.connect(cfg)) as conn, conn:
        conn.execute("INSERT INTO guests(id,nickname,created_at,avatar_data,avatar_version) VALUES('guest-1','Camille',?,'photo-fixture','version-1')", (db.now(),))
        conn.execute("INSERT INTO sources(id,title,status,file_path,created_at) VALUES(7,'Café','available',?,?)", (audio.name, db.now()))
        conn.execute("INSERT INTO proposals(id,source_id,guest_id,state,sort_order,created_at,withdrawn_at) VALUES(13,7,'guest-1','approved',4,?,?)", (db.now(), db.now()))
        conn.execute("INSERT INTO votes(proposal_id,guest_id,created_at) VALUES(13,'guest-1',?)", (db.now(),))
        conn.execute("INSERT INTO cloud_map(kind,remote_id,local_id) VALUES('proposal','42','13')")
        conn.execute("INSERT INTO jobs(id,source_id,status,created_at,updated_at) VALUES(3,7,'available',?,?)", (db.now(), db.now()))
        conn.execute("INSERT INTO job_events(id,job_id,status,message,created_at) VALUES(4,3,'available','historique conservé',?)", (db.now(),))
        conn.execute("INSERT INTO exports(id,directory,created_at) VALUES('old-pack',?,?)", (str(cfg.export_dir / 'old-pack'), db.now()))
    (cfg.export_dir / "not-in-backup.txt").write_text("Export déjà transportable")
    (cfg.temp_dir / "not-in-backup.tmp").write_bytes(b"unfinished download")
    return home, cfg, audio


def _rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def _snapshot(home, cfg, audio):
    return (home.joinpath('.env.local').read_bytes(), audio.read_bytes(),
            {name: _rows(cfg.db_path, name) for name in ('guests', 'sources', 'proposals', 'votes', 'cloud_map', 'jobs', 'job_events')})


def _rewrite(path, *, files=None, mutate_manifest=None, additional=()):
    with zipfile.ZipFile(path) as source:
        content = {info.filename: source.read(info) for info in source.infolist()}
    manifest = json.loads(content['manifest.json'])
    if files:
        for name, data in files.items():
            content[name] = data
            for entry in manifest['files']:
                if entry['path'] == name:
                    entry.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    if mutate_manifest:
        mutate_manifest(manifest)
    content['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, 'w') as target:
        for name, data in content.items():
            target.writestr(name, data)
        for name, data in additional:
            target.writestr(name, data)


def test_full_backup_preview_restore_preserves_private_state_in_new_home(studio, tmp_path):
    home, cfg, audio = studio
    original = _snapshot(home, cfg, audio)
    archive = tmp_path / "Sauvegarde confidentielle.zip"
    info = create_backup(home, archive)
    assert info['confidential'] is True and info['include_audio'] is True
    assert info['counts'] == dict(sources=1, proposals=1, guests=1, votes=1, photos=1, audio_files=1)
    assert info['missing_audio'] == 0
    assert 'fixture-private-no-network' not in json.dumps(info)
    assert inspect_backup(archive) == info
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {'manifest.json', '.env.local', 'data/playlist.sqlite3', f'data/audio/{audio.name}'}
        assert zipped.read('.env.local') == original[0]
    restored = tmp_path / 'Nouveau studio'
    result = restore_backup(archive, restored, expected_manifest_sha256=info['manifest_sha256'])
    assert result['home'] == str(restored) and result['missing_audio'] == 0
    assert (restored / 'data' / 'audio' / audio.name).read_bytes() == original[1]
    for table in ('guests', 'sources', 'proposals', 'votes', 'cloud_map', 'jobs', 'job_events'):
        assert _rows(restored / 'data/playlist.sqlite3', table) == original[2][table]
    values = dotenv_values(restored / '.env.local')
    original_values = dotenv_values(home / '.env.local')
    assert values['DATA_DIR'] == 'data'
    assert 'FFMPEG_PATH' not in values and 'JS_RUNTIME' not in values
    for name in ('ADMIN_PASSWORD', 'SESSION_SECRET', 'SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY'):
        assert values[name] == original_values[name]
    with closing(sqlite3.connect(restored / 'data/playlist.sqlite3')) as conn:
        assert db.get_setting(conn, 'queue_paused') is True
    assert _snapshot(home, cfg, audio) == original


def test_audio_omitted_preserves_identity_votes_history_and_marks_files_missing(studio, tmp_path):
    home, cfg, audio = studio
    original = _snapshot(home, cfg, audio)
    archive = tmp_path / 'sans audio.zip'
    info = create_backup(home, archive, include_audio=False)
    assert info['counts']['audio_files'] == 0 and info['missing_audio'] == 1
    assert inspect_backup(archive)['missing_audio'] == 1
    restored = tmp_path / 'Copie sans audio'
    result = restore_backup(archive, restored)
    assert result['missing_audio'] == 1
    assert not list((restored / 'data/audio').iterdir())
    for table in ('guests', 'proposals', 'votes', 'cloud_map', 'job_events'):
        assert _rows(restored / 'data/playlist.sqlite3', table) == original[2][table]
    with closing(sqlite3.connect(restored / 'data/playlist.sqlite3')) as conn:
        assert conn.execute('SELECT file_path,status FROM sources').fetchone() == (None, 'interrupted')
        assert conn.execute('SELECT status,error FROM jobs').fetchone() == ('interrupted', 'Le fichier audio n’était pas inclus dans la sauvegarde.')
    assert _snapshot(home, cfg, audio) == original


def test_missing_original_audio_is_reported_before_restore(studio, tmp_path):
    home, cfg, audio = studio
    audio.unlink()
    archive = tmp_path / 'already missing.zip'
    assert create_backup(home, archive)['missing_audio'] == 1
    assert inspect_backup(archive)['missing_audio'] == 1
    restored = tmp_path / 'restored'
    assert restore_backup(archive, restored)['missing_audio'] == 1
    with closing(sqlite3.connect(restored / 'data/playlist.sqlite3')) as conn:
        assert conn.execute('SELECT status,file_path FROM sources').fetchone() == ('interrupted', None)


def test_sqlite_backup_captures_wal_without_changing_source(studio, tmp_path):
    home, cfg, audio = studio
    with closing(db.connect(cfg)) as live, live:
        live.execute("INSERT INTO guests(id,nickname,created_at) VALUES('wal-guest','Dernier invité',?)", (db.now(),))
        live.commit()
        assert cfg.db_path.with_name('playlist.sqlite3-wal').exists()
        info = create_backup(home, tmp_path / 'snapshot.zip')
        assert info['counts']['guests'] == 2
        assert live.execute("SELECT nickname FROM guests WHERE id='wal-guest'").fetchone()[0] == 'Dernier invité'
    assert inspect_backup(tmp_path / 'snapshot.zip')['counts']['guests'] == 2


@pytest.mark.parametrize('denied', [False, True])
def test_backup_requires_stopped_services_and_fails_closed_when_status_is_unknown(studio, tmp_path, monkeypatch, denied):
    from playlist25 import __main__
    import psutil
    home, cfg, _ = studio
    (cfg.data_dir / 'processes.json').write_text(json.dumps([{'pid': os.getpid(), 'service': 'worker'}]))
    monkeypatch.setattr(__main__, '_matches_service', lambda *args: True)
    class Process:
        def is_running(self):
            if denied:
                raise psutil.AccessDenied(os.getpid())
            return True
        def status(self):
            return psutil.STATUS_RUNNING
    monkeypatch.setattr(psutil, 'Process', lambda _: Process())
    with pytest.raises(BackupError, match='Arrêtez|confirmé'):
        create_backup(home, tmp_path / 'backup.zip')
    assert not (tmp_path / 'backup.zip').exists()


def test_existing_archive_and_nonempty_restore_target_are_never_overwritten(studio, tmp_path):
    home, cfg, audio = studio
    archive = tmp_path / 'archive.zip'
    info = create_backup(home, archive)
    original_zip = archive.read_bytes()
    with pytest.raises(BackupError, match='existe déjà'):
        create_backup(home, archive)
    assert archive.read_bytes() == original_zip
    occupied = tmp_path / 'Installation actuelle'
    occupied.mkdir()
    (occupied / '.env.local').write_text('current secret stays')
    with pytest.raises(BackupError, match='dossier vide'):
        restore_backup(archive, occupied)
    assert (occupied / '.env.local').read_text() == 'current secret stays'
    assert inspect_backup(archive) == info


def test_empty_restore_directory_is_supported(studio, tmp_path):
    home, _, _ = studio
    archive = tmp_path / 'archive.zip'
    create_backup(home, archive)
    destination = tmp_path / 'empty'
    destination.mkdir()
    restore_backup(archive, destination)
    assert (destination / '.env.local').is_file()


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/Windows/escape', r'\\server\share\escape',
                                 'data/audio/../escape', 'data/audio/file:stream', 'data/audio/CON.wav',
                                 'data/audio/COM¹.wav', 'data/audio/LPT².mp3', 'data/audio/CONIN$.wav',
                                 'data/audio/name.', 'data/audio/name ', 'data/audio/file\x00.mp3'])
def test_unsafe_archive_names_are_rejected_without_restore_writes(studio, tmp_path, name):
    home, _, _ = studio
    archive = tmp_path / 'bad.zip'
    create_backup(home, archive)
    _rewrite(archive, additional=[(name, b'unsafe')])
    target = tmp_path / 'new studio'
    with pytest.raises(BackupError):
        restore_backup(archive, target)
    assert not target.exists()
    assert not (tmp_path / 'escape').exists()


def test_symlink_members_and_unknown_files_are_rejected(studio, tmp_path):
    home, _, _ = studio
    archive = tmp_path / 'bad.zip'
    create_backup(home, archive)
    info = zipfile.ZipInfo('data/audio/linked.wav')
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, 'a') as output:
        output.writestr(info, '/outside/private-file')
    with pytest.raises(BackupError, match='lien'):
        inspect_backup(archive)


@pytest.mark.parametrize('malformation', ['backslash', 'nul'])
def test_raw_zip_member_names_cannot_be_normalized_into_allowed_payloads(studio, tmp_path, malformation):
    home, _, audio = studio
    archive = tmp_path / 'raw names.zip'
    create_backup(home, archive)
    original = f'data/audio/{audio.name}'.encode('utf-8')
    malformed = original.replace(b'/', b'\\') if malformation == 'backslash' else original[:11] + b'\x00' + original[12:]
    raw = bytearray(archive.read_bytes())
    first, last = raw.find(original), raw.rfind(original)
    assert 0 <= first < last  # Local header and central directory.
    for position in (first, last):
        raw[position:position + len(original)] = malformed
    archive.write_bytes(raw)  # Leave the checksum manifest unchanged.
    target = tmp_path / 'must not exist'
    with pytest.raises(BackupError):
        restore_backup(archive, target)
    assert not target.exists()


def test_payload_checksum_corruption_and_manifest_counter_tampering_are_rejected(studio, tmp_path):
    home, _, audio = studio
    archive = tmp_path / 'bad.zip'
    create_backup(home, archive)
    # Replace a byte while deliberately leaving the recorded checksum unchanged.
    with zipfile.ZipFile(archive) as source:
        contents = {name: source.read(name) for name in source.namelist()}
    member = f'data/audio/{audio.name}'
    contents[member] = b'X' + contents[member][1:]
    with zipfile.ZipFile(archive, 'w') as output:
        for name, value in contents.items():
            output.writestr(name, value)
    with pytest.raises(BackupError, match='endommagé'):
        inspect_backup(archive)
    archive.unlink()
    create_backup(home, archive)
    _rewrite(archive, mutate_manifest=lambda manifest: manifest['counts'].update(votes=999))
    with pytest.raises(BackupError, match='compteurs'):
        inspect_backup(archive)


def test_changed_archive_since_preview_cannot_create_a_restored_home(studio, tmp_path):
    home, _, _ = studio
    archive = tmp_path / 'backup.zip'
    info = create_backup(home, archive)
    _rewrite(archive, mutate_manifest=lambda manifest: manifest.update(created_at='2020-01-01T00:00:00+00:00'))
    target = tmp_path / 'restore'
    with pytest.raises(BackupError, match='changé depuis'):
        restore_backup(archive, target, expected_manifest_sha256=info['manifest_sha256'])
    assert not target.exists()


def test_hostile_sqlite_trigger_is_refused_even_with_valid_checksum(studio, tmp_path):
    home, _, _ = studio
    archive = tmp_path / 'database.zip'
    create_backup(home, archive)
    database = tmp_path / 'malicious.sqlite3'
    with zipfile.ZipFile(archive) as source:
        database.write_bytes(source.read('data/playlist.sqlite3'))
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TRIGGER hidden_side_effect AFTER INSERT ON guests BEGIN DELETE FROM votes; END")
    _rewrite(archive, files={'data/playlist.sqlite3': database.read_bytes()})
    with pytest.raises(BackupError, match='schéma SQLite'):
        inspect_backup(archive)
    assert not (tmp_path / 'restore').exists()


def test_invalid_database_and_dangerous_config_are_rejected_without_logging_secret(studio, tmp_path):
    home, _, _ = studio
    archive = tmp_path / 'bad.zip'
    create_backup(home, archive)
    _rewrite(archive, files={'data/playlist.sqlite3': b'not sqlite'})
    with pytest.raises(BackupError, match='SQLite'):
        inspect_backup(archive)
    archive.unlink()
    create_backup(home, archive)
    content = (home / '.env.local').read_bytes() + b'PYTHONPATH="/execute-this-on-start"\n'
    _rewrite(archive, files={'.env.local': content})
    with pytest.raises(BackupError) as error:
        inspect_backup(archive)
    assert 'fixture-private-no-network' not in str(error.value)
    assert '/execute-this-on-start' not in str(error.value)


def test_source_symlink_and_destination_inside_data_are_refused(studio, tmp_path):
    home, cfg, audio = studio
    outside = tmp_path / 'private elsewhere'
    outside.write_bytes(b'private outside bytes')
    link = cfg.audio_dir / 'link.wav'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('Creating symlinks requires permissions on this machine.')
    with pytest.raises(BackupError, match='lien|pris en charge'):
        create_backup(home, tmp_path / 'backup.zip')
    assert not (tmp_path / 'backup.zip').exists()
    link.unlink()
    with pytest.raises(BackupError, match='en dehors'):
        create_backup(home, cfg.audio_dir / 'backup.zip')
    assert audio.is_file()


@pytest.mark.skipif(os.name == 'nt', reason='Windows cannot create two files differing only in case.')
def test_source_names_colliding_on_windows_are_refused(studio, tmp_path):
    home, cfg, _ = studio
    (cfg.audio_dir / 'duplicate.mp3').write_bytes(b'a')
    (cfg.audio_dir / 'DUPLICATE.mp3').write_bytes(b'b')
    with pytest.raises(BackupError, match='même nom sur Windows'):
        create_backup(home, tmp_path / 'backup.zip')


def test_disk_failure_and_final_commit_failure_leave_original_and_empty_target_intact(studio, tmp_path, monkeypatch):
    home, cfg, audio = studio
    original = _snapshot(home, cfg, audio)
    archive = tmp_path / 'backup.zip'
    create_backup(home, archive)
    target = tmp_path / 'empty target'
    target.mkdir()
    original_rename = Path.rename
    def cannot_install(self, destination):
        if self.name == 'home':
            raise OSError('test disk commit failure')
        return original_rename(self, destination)
    monkeypatch.setattr(Path, 'rename', cannot_install)
    with pytest.raises(BackupError, match='installation actuelle est conservée'):
        restore_backup(archive, target)
    assert target.is_dir() and not list(target.iterdir())
    assert _snapshot(home, cfg, audio) == original
    assert not list(tmp_path.glob('.studio-restore-*'))
    assert not list(tmp_path.glob('.studio-verify-*'))
    monkeypatch.setattr(backups.shutil, 'disk_usage', lambda _: type('Disk', (), {'free': 0})())
    with pytest.raises(BackupError, match='espace disque'):
        create_backup(home, tmp_path / 'no-space.zip')
    assert not (tmp_path / 'no-space.zip').exists()


def test_failed_archive_commit_cleans_only_its_placeholder(studio, tmp_path, monkeypatch):
    home, _, _ = studio
    archive = tmp_path / 'backup.zip'
    original_replace = Path.replace
    def fail(self, destination):
        if self.name == 'backup.zip':
            raise OSError('commit failed')
        return original_replace(self, destination)
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(BackupError, match='créée'):
        create_backup(home, archive)
    assert not archive.exists()
    assert not list(tmp_path.glob('.studio-backup-*'))


@pytest.mark.parametrize('existing_empty', [False, True])
def test_restore_never_replaces_a_directory_populated_after_user_selection(studio, tmp_path, monkeypatch, existing_empty):
    home, cfg, audio = studio
    original = _snapshot(home, cfg, audio)
    archive = tmp_path / 'backup.zip'
    create_backup(home, archive)
    target = tmp_path / 'selected'
    if existing_empty:
        target.mkdir()
    original_mark = backups._mark_missing
    def populated_by_other_process(database, manifest):
        result = original_mark(database, manifest)
        target.mkdir(exist_ok=True)
        (target / 'newer-file.txt').write_bytes(b'newer data must stay')
        return result
    monkeypatch.setattr(backups, '_mark_missing', populated_by_other_process)
    with pytest.raises(BackupError):
        restore_backup(archive, target)
    assert (target / 'newer-file.txt').read_bytes() == b'newer data must stay'
    assert not (target / '.env.local').exists()
    assert _snapshot(home, cfg, audio) == original


def test_backup_of_twenty_thousand_files_uses_streaming_zip64_and_has_no_song_count_limit(studio, tmp_path):
    home, cfg, audio = studio
    count = 20001
    for index in range(1, count):
        (cfg.audio_dir / f'source-{index:06d}.wav').write_bytes(b'generated fixture bytes')
    archive = tmp_path / 'large.zip'
    info = create_backup(home, archive)
    assert info['counts']['audio_files'] == count
    inspected = inspect_backup(archive)
    assert inspected['counts']['audio_files'] == count
    assert inspected['manifest_sha256'] == info['manifest_sha256']
    with zipfile.ZipFile(archive) as source:
        assert sum(name.startswith('data/audio/') for name in source.namelist()) == count
        entry = source.getinfo(f'data/audio/{audio.name}')
        with archive.open('rb') as raw:
            raw.seek(entry.header_offset)
            header = raw.read(30)
            name_len, extra_len = struct.unpack('<HH', header[26:30])
            raw.read(name_len)
            extra = raw.read(extra_len)
            assert b'\x01\x00' in extra  # ZIP64 local header from force_zip64.
    restored = tmp_path / 'large restored'
    result = restore_backup(archive, restored, expected_manifest_sha256=inspected['manifest_sha256'])
    restored_audio = restored / 'data/audio'
    assert result['counts']['audio_files'] == count and result['missing_audio'] == 0
    assert sum(1 for _ in restored_audio.iterdir()) == count
    assert (restored_audio / audio.name).read_bytes() == audio.read_bytes()
    for path in restored_audio.iterdir():
        assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256((cfg.audio_dir / path.name).read_bytes()).digest()
    with closing(sqlite3.connect(restored / 'data/playlist.sqlite3')) as conn:
        assert db.get_setting(conn, 'queue_paused') is True
