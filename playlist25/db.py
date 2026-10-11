from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from .config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS guests(id TEXT PRIMARY KEY,nickname TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invitations(code_hash TEXT PRIMARY KEY,guest_id TEXT UNIQUE REFERENCES guests(id),created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY,video_id TEXT UNIQUE,url TEXT,title TEXT NOT NULL,artist TEXT NOT NULL DEFAULT '',channel TEXT NOT NULL DEFAULT '',duration REAL,thumbnail TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending',file_path TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY,source_id INTEGER NOT NULL REFERENCES sources(id),guest_id TEXT REFERENCES guests(id),state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','archived')),sort_order INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,UNIQUE(source_id,guest_id));
CREATE TABLE IF NOT EXISTS votes(proposal_id INTEGER NOT NULL REFERENCES proposals(id),guest_id TEXT NOT NULL REFERENCES guests(id),created_at TEXT NOT NULL,PRIMARY KEY(proposal_id,guest_id));
CREATE TABLE IF NOT EXISTS playlists(id INTEGER PRIMARY KEY,title TEXT NOT NULL,source_url TEXT,analysis_complete INTEGER NOT NULL,warnings TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS playlist_entries(playlist_id INTEGER NOT NULL REFERENCES playlists(id),position INTEGER NOT NULL,source_id INTEGER REFERENCES sources(id),title TEXT NOT NULL,available INTEGER NOT NULL,reason TEXT NOT NULL DEFAULT '',selected INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(playlist_id,position));
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,source_id INTEGER UNIQUE NOT NULL REFERENCES sources(id),status TEXT NOT NULL DEFAULT 'queued',progress REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job_events(id INTEGER PRIMARY KEY,job_id INTEGER NOT NULL REFERENCES jobs(id),status TEXT NOT NULL,message TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY,directory TEXT NOT NULL,archive TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cloud_map(kind TEXT NOT NULL,remote_id TEXT NOT NULL,local_id TEXT NOT NULL,PRIMARY KEY(kind,remote_id));
CREATE INDEX IF NOT EXISTS proposals_state_idx ON proposals(state);
CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(status);
"""

def now(): return datetime.now(timezone.utc).isoformat()

def connect(cfg: Settings):
    conn = sqlite3.connect(cfg.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db(cfg: Settings):
    cfg.ensure_directories()
    with connect(cfg) as conn:
        conn.executescript(SCHEMA)
        defaults = {"event_name":cfg.event_name,"proposals_open":True,"votes_open":True,"offline_mode":False,"invitation_required":False,"public_url":cfg.public_url,"local_url":cfg.local_url,"max_proposals":cfg.max_proposals,"max_playlist_items":cfg.max_playlist_items,"output_format":cfg.output_format,"queue_paused":False}
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(key,json.dumps(value,ensure_ascii=False)))

def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return json.loads(row[0]) if row else default

def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,json.dumps(value,ensure_ascii=False)))

def source_dict(row): return dict(row) if row is not None else None

def upsert_source(conn, entry):
    video_id = entry.get("video_id")
    if video_id:
        row = conn.execute("SELECT id FROM sources WHERE video_id=?",(video_id,)).fetchone()
        if row:
            # User-corrected metadata and downloaded files survive repeated imports.
            return row["id"]
    cursor = conn.execute("INSERT INTO sources(video_id,url,title,artist,channel,duration,thumbnail,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(video_id,entry.get("url"),str(entry.get("title") or (f"Vidéo YouTube ({video_id})" if video_id else "Titre à préciser"))[:500],str(entry.get("artist") or "")[:300],str(entry.get("channel") or "")[:300],entry.get("duration"),str(entry.get("thumbnail") or "")[:2000],entry.get("status") or "pending",now()))
    return cursor.lastrowid

def list_proposals(conn, guest_id=None, include_hidden=False):
    query = """SELECT p.id,p.source_id,p.guest_id,p.state,p.sort_order,p.created_at,
       COALESCE(g.nickname,'Administration') AS nickname,s.video_id,s.url,s.title,s.artist,s.channel,s.duration,s.thumbnail,s.status,s.file_path,
       (SELECT count(*) FROM votes v WHERE v.proposal_id=p.id) AS votes,
       EXISTS(SELECT 1 FROM votes v WHERE v.proposal_id=p.id AND v.guest_id=?) AS voted
       FROM proposals p JOIN sources s ON s.id=p.source_id LEFT JOIN guests g ON g.id=p.guest_id"""
    if not include_hidden: query += " WHERE p.state IN ('pending','approved')"
    query += " ORDER BY p.sort_order,p.created_at,p.id"
    return [dict(row) for row in conn.execute(query,(guest_id or "",))]
