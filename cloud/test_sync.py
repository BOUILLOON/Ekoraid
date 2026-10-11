"""SQLite cloud snapshots/publication and guest-only Pages packaging checks."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playlist25 import cloud_sync, db
from playlist25.config import Settings
from build_pages import build, check_public_key

DATE = "2026-01-01T00:00:00+00:00"
GA, GB = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"


def snapshot():
    return {"schema_version": 1, "exported_at": DATE, "settings": {"event_name": "Cloud"},
      "guests": [{"id": GA, "nickname": "Camille", "created_at": DATE}, {"id": GB, "nickname": "Camille", "created_at": DATE}],
      "sources": [{"id": 101, "video_id": None, "url": None, "title": "Idée manuelle éà", "artist": "", "created_at": DATE},
                  {"id": 102, "video_id": "abcDEF_1234", "url": "https://www.youtube.com/watch?v=abcDEF_1234", "title": "Titre cloud", "artist": "", "created_at": DATE}],
      "proposals": [{"id": 201, "source_id": 101, "guest_id": GA, "state": "pending", "sort_order": 0, "created_at": DATE},
                    {"id": 202, "source_id": 102, "guest_id": GB, "state": "pending", "sort_order": 0, "created_at": DATE}],
      "votes": [{"proposal_id": 201, "guest_id": GA, "created_at": DATE}, {"proposal_id": 202, "guest_id": GA, "created_at": DATE},
                {"proposal_id": 202, "guest_id": GB, "created_at": DATE}]}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cfg = Settings(data_dir=Path(self.directory.name), supabase_url="https://testing-project.supabase.co", session_secret="test")
        db.init_db(self.cfg)
        self.conn = db.connect(self.cfg)
        self.conn.execute("INSERT INTO guests VALUES('local','Invité local',?)", (DATE,))
        sid = db.upsert_source(self.conn, {"video_id": "abcDEF_1234", "url": "https://www.youtube.com/watch?v=abcDEF_1234", "title": "Titre local corrigé"})
        self.conn.execute("UPDATE sources SET status='available',file_path='audio/preserve.mp3' WHERE id=?", (sid,))
        self.pid = self.conn.execute("INSERT INTO proposals(source_id,guest_id,state,created_at) VALUES(?,'local','approved',?)", (sid, DATE)).lastrowid
        self.conn.execute("INSERT INTO votes VALUES(?,'local',?)", (self.pid, DATE))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.directory.cleanup()

    def test_repeatable_pull_preserves_local_audio_metadata_moderation_and_votes(self):
        data = snapshot()
        result = cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual(result["new_sources"], 1)
        self.assertEqual(result["new_proposals"], 1)
        again = cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual(again["new_proposals"], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM votes").fetchone()[0], 4)
        source = dict(self.conn.execute("SELECT * FROM sources WHERE video_id='abcDEF_1234'").fetchone())
        self.assertEqual((source["title"], source["status"], source["file_path"]), ("Titre local corrigé", "available", "audio/preserve.mp3"))
        self.assertEqual(self.conn.execute("SELECT state FROM proposals WHERE id=?", (self.pid,)).fetchone()[0], "approved")
        data["votes"].pop(1)
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM votes").fetchone()[0], 3)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM votes WHERE guest_id='local'").fetchone()[0], 1)

    def test_invalid_snapshot_has_no_local_side_effect(self):
        data = snapshot()
        data["sources"][1]["url"] = "http://169.254.169.254/latest/meta-data"
        with self.assertRaises(cloud_sync.CloudSyncError):
            cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM sources").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM guests").fetchone()[0], 1)

    def test_explicit_publish_archives_merged_remote_entries_and_sends_no_secret_or_file(self):
        cloud_sync.import_snapshot(self.cfg, self.conn, snapshot())
        self.conn.execute("UPDATE cloud_map SET local_id=? WHERE kind='proposal'", (str(self.pid),))
        captured = []
        def fake_rpc(cfg, name, body):
            captured.append((name, body))
            return {"updated": len(body["p_updates"])}
        with patch.object(cloud_sync, "_rpc", fake_rpc):
            result = cloud_sync.push(self.cfg, self.conn)
        self.assertEqual(result["updated"], 2)
        name, payload = captured[0]
        self.assertEqual(name, "playlist25_admin_publish")
        self.assertEqual([row["state"] for row in payload["p_updates"]], ["approved", "archived"])
        self.assertEqual(set(payload["p_updates"][0]), {"id", "state", "title", "artist", "sort_order"})
        self.assertEqual(payload["p_updates"][1]["merge_into"], 201)
        db.set_setting(self.conn, "offline_mode", True)
        with self.assertRaises(cloud_sync.CloudSyncError):
            cloud_sync.pull(self.cfg, self.conn)
        with self.assertRaises(cloud_sync.CloudSyncError):
            cloud_sync.push(self.cfg, self.conn)

    def test_pages_contains_only_guest_assets_and_public_configuration(self):
        output = Path(self.directory.name) / "site"
        build("https://testing-project.supabase.co", "sb_publishable_synthetic_public_test_key", output)
        names = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
        self.assertEqual(names, {"index.html", ".nojekyll", ".playlist25-generated", "static/styles.css", "static/guest.js", "static/cloud.js", "static/cloud-config.js", "static/placeholder.svg"})
        self.assertNotIn("admin.js", " ".join(names))
        self.assertIn("Content-Security-Policy", (output / "index.html").read_text())
        build("https://testing-project.supabase.co", "sb_publishable_synthetic_public_test_key", output)
        (output / "private-file.txt").write_text("do not publish")
        with self.assertRaises(ValueError):
            build("https://testing-project.supabase.co", "sb_publishable_synthetic_public_test_key", output)
        with self.assertRaises(ValueError):
            check_public_key("sb_secret_must_never_leave_the_PC")


if __name__ == "__main__":
    unittest.main()
