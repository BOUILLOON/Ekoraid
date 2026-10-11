"""Existing collections retain history, votes and files during schema upgrades."""
from contextlib import closing
import sqlite3

import pytest

from playlist25 import db
from playlist25.config import Settings


LEGACY_PROPOSALS = """CREATE TABLE proposals(
    id INTEGER PRIMARY KEY,source_id INTEGER NOT NULL REFERENCES sources(id),
    guest_id TEXT REFERENCES guests(id),state TEXT NOT NULL DEFAULT 'pending'
    CHECK(state IN ('pending','approved','rejected','archived')),
    sort_order INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,
    UNIQUE(source_id,guest_id))"""


def legacy_collection(tmp_path):
    cfg = Settings(data_dir=tmp_path / 'data', session_secret='migration-test-secret')
    cfg.ensure_directories()
    retained = cfg.audio_dir / 'retained-file.bin'
    retained.write_bytes(b'user file must remain unchanged')
    with closing(db.connect(cfg)) as conn:
        conn.executescript(db.SCHEMA)
        conn.execute('DROP TABLE proposals')
        conn.execute(LEGACY_PROPOSALS)
        with conn:
            conn.execute("INSERT INTO guests VALUES('owner','Fanny','2026-10-01')")
            conn.execute("INSERT INTO guests VALUES('voter','Fanny','2026-10-02')")
            conn.execute("""INSERT INTO sources(id,title,file_path,status,created_at)
                VALUES(41,'Une proposition existante',?,'completed','2026-10-01')""", (str(retained),))
            conn.execute("""INSERT INTO proposals(id,source_id,guest_id,state,sort_order,created_at)
                VALUES(73,41,'owner','approved',5,'2026-10-01')""")
            conn.execute("INSERT INTO votes VALUES(73,'voter','2026-10-02')")
            conn.execute("""INSERT INTO jobs(id,source_id,status,created_at,updated_at)
                VALUES(89,41,'completed','2026-10-01','2026-10-02')""")
            conn.execute("""INSERT INTO cloud_map VALUES('proposal','project:123','73')""")
    return cfg, retained


def test_upgrade_preserves_history_and_allows_distinct_readd(tmp_path):
    cfg, retained = legacy_collection(tmp_path)
    db.init_db(cfg)
    with closing(db.connect(cfg)) as conn, conn:
        row = dict(conn.execute('SELECT * FROM proposals WHERE id=73').fetchone())
        assert row == dict(id=73, source_id=41, guest_id='owner', state='approved',
                           sort_order=5, created_at='2026-10-01', withdrawn_at=None)
        assert list(map(tuple, conn.execute('SELECT * FROM votes'))) == [(73, 'voter', '2026-10-02')]
        assert conn.execute('SELECT status FROM jobs WHERE id=89').fetchone()[0] == 'completed'
        assert conn.execute("SELECT local_id FROM cloud_map WHERE remote_id='project:123'").fetchone()[0] == '73'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        conn.execute("UPDATE proposals SET state='archived',withdrawn_at='2026-10-11' WHERE id=73")
        new_id = conn.execute("INSERT INTO proposals(source_id,guest_id,created_at) VALUES(41,'owner','2026-10-11')").lastrowid
        assert new_id != 73
        assert conn.execute('SELECT count(*) FROM votes WHERE proposal_id=?', (new_id,)).fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO proposals(source_id,guest_id,created_at) VALUES(41,'owner','2026-10-11')")
    db.init_db(cfg)
    db.init_db(cfg)
    with closing(db.connect(cfg)) as conn:
        assert conn.execute('SELECT count(*) FROM proposals').fetchone()[0] == 2
        assert conn.execute('SELECT withdrawn_at FROM proposals WHERE id=73').fetchone()[0] == '2026-10-11'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert retained.read_bytes() == b'user file must remain unchanged'


def test_upgrade_refuses_unknown_columns_without_losing_them(tmp_path):
    cfg, retained = legacy_collection(tmp_path)
    with closing(db.connect(cfg)) as conn, conn:
        conn.execute('ALTER TABLE proposals ADD COLUMN private_note TEXT')
        conn.execute("UPDATE proposals SET private_note='À garder' WHERE id=73")
    with pytest.raises(RuntimeError, match='colonnes personnalisées'):
        db.init_db(cfg)
    with closing(db.connect(cfg)) as conn:
        assert conn.execute('SELECT private_note FROM proposals WHERE id=73').fetchone()[0] == 'À garder'
        assert 'withdrawn_at' not in {r['name'] for r in conn.execute('PRAGMA table_info(proposals)')}
        assert conn.execute('SELECT count(*) FROM votes').fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='proposals_withdrawal_migration'").fetchone()[0] == 0
    assert retained.read_bytes() == b'user file must remain unchanged'


def test_upgrade_rolls_back_when_existing_references_are_invalid(tmp_path):
    cfg, _ = legacy_collection(tmp_path)
    with closing(db.connect(cfg)) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        with conn:
            conn.execute("INSERT INTO votes VALUES(999,'voter','2026-10-02')")
    with pytest.raises(RuntimeError, match='références'):
        db.init_db(cfg)
    with closing(db.connect(cfg)) as conn:
        assert conn.execute('SELECT count(*) FROM proposals').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM votes').fetchone()[0] == 2
        assert 'withdrawn_at' not in {r['name'] for r in conn.execute('PRAGMA table_info(proposals)')}
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='proposals_withdrawal_migration'").fetchone()[0] == 0
