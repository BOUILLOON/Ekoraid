"""SQLite cloud snapshots/publication and guest-only Pages packaging checks."""
from __future__ import annotations

import copy
import base64
import hashlib
import io
import json
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


def avatar(color="blue", size=(128, 128)):
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    binary = output.getvalue()
    return {"avatar_data": "data:image/png;base64," + base64.b64encode(binary).decode(),
            "avatar_version": hashlib.md5(binary, usedforsecurity=False).hexdigest()}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cfg = Settings(data_dir=Path(self.directory.name), supabase_url="https://testing-project.supabase.co", session_secret="test")
        db.init_db(self.cfg)
        self.conn = db.connect(self.cfg)
        self.conn.execute("INSERT INTO guests(id,nickname,created_at) VALUES('local','Invité local',?)", (DATE,))
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

    def test_avatar_import_updates_clears_and_preserves_older_snapshots_and_withdrawal_history(self):
        data = snapshot()
        blue, red = avatar(), avatar("red")
        data["guests"][0].update(blue)
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        gid = "cloud:testing-project:" + GA
        def stored():
            return dict(self.conn.execute("SELECT avatar_data,avatar_version FROM guests WHERE id=?", (gid,)).fetchone())
        self.assertEqual(stored(), blue)
        data["guests"][0].update(red)
        data["proposals"][0].update(state="archived", withdrawn_at=DATE)
        data["proposals"].append({"id": 203, "source_id": 101, "guest_id": GA,
                                 "state": "pending", "created_at": DATE})
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual(stored(), red)
        legacy = copy.deepcopy(data)
        legacy["guests"][0].pop("avatar_data")
        legacy["guests"][0].pop("avatar_version")
        cloud_sync.import_snapshot(self.cfg, self.conn, legacy)
        self.assertEqual(stored(), red)
        data["guests"][0].update(avatar_data="", avatar_version="")
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual(stored(), {"avatar_data": "", "avatar_version": ""})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM proposals WHERE guest_id=?", (gid,)).fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT nickname FROM guests WHERE id='local'").fetchone()[0], "Invité local")

    def test_invalid_avatar_and_hash_never_partially_import_remote_guests(self):
        invalids = [{"avatar_data": "data:image/svg+xml;base64,PHN2Zy8+", "avatar_version": "a" * 32},
                    {"avatar_data": "x" * 200001, "avatar_version": "a" * 32},
                    {**avatar(), "avatar_version": "0" * 32}, avatar(size=(64, 64)),
                    {"avatar_data": "data:image/png;base64,%%%", "avatar_version": "a" * 32},
                    {"avatar_data": "", "avatar_version": "a" * 32},
                    {"avatar_data": None, "avatar_version": ""}]
        for invalid in invalids:
            with self.subTest(invalid_version=invalid["avatar_version"]):
                data = snapshot()
                data["guests"][1].update(invalid)
                with self.assertRaises(cloud_sync.CloudSyncError):
                    cloud_sync.import_snapshot(self.cfg, self.conn, data)
                self.assertEqual(self.conn.execute("SELECT count(*) FROM guests").fetchone()[0], 1)
                self.assertEqual(self.conn.execute("SELECT count(*) FROM sources").fetchone()[0], 1)

    def test_remote_withdrawal_archives_local_and_readd_keeps_distinct_history(self):
        self.conn.execute("DELETE FROM votes")
        self.conn.execute("DELETE FROM proposals")
        data = snapshot()
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        old_local = self.conn.execute("SELECT local_id FROM cloud_map WHERE kind='proposal' AND remote_id='testing-project:202'").fetchone()[0]
        self.conn.execute("UPDATE proposals SET state='approved' WHERE id=?", (old_local,))
        data["proposals"][1].update(state="archived", withdrawn_at=DATE)
        data["proposals"].append({"id": 203, "source_id": 102, "guest_id": GB, "state": "pending", "sort_order": 0, "created_at": DATE})
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        old = dict(self.conn.execute("SELECT * FROM proposals WHERE id=?", (old_local,)).fetchone())
        new_local = self.conn.execute("SELECT local_id FROM cloud_map WHERE kind='proposal' AND remote_id='testing-project:203'").fetchone()[0]
        new = dict(self.conn.execute("SELECT * FROM proposals WHERE id=?", (new_local,)).fetchone())
        self.assertNotEqual(str(old_local), str(new_local))
        self.assertEqual((old["state"], old["withdrawn_at"]), ("archived", DATE))
        self.assertEqual((new["state"], new["withdrawn_at"], new["source_id"]), ("pending", None, old["source_id"]))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM votes WHERE proposal_id=?", (old_local,)).fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM votes WHERE proposal_id=?", (new_local,)).fetchone()[0], 0)
        captured = []
        def fake_rpc(cfg, name, body):
            captured.append(body)
            return {"updated": len(body["p_updates"])}
        with patch.object(cloud_sync, "_rpc", fake_rpc):
            cloud_sync.push(self.cfg, self.conn)
        updates = {item["id"]: item for item in captured[0]["p_updates"]}
        self.assertEqual(updates[202]["state"], "archived")
        self.assertEqual(updates[203]["state"], "pending")
        self.assertNotIn("merge_into", updates[202])
        # An older snapshot cannot reset the sticky local withdrawal audit marker.
        cloud_sync.import_snapshot(self.cfg, self.conn, snapshot())
        self.assertEqual(self.conn.execute("SELECT withdrawn_at FROM proposals WHERE id=?", (old_local,)).fetchone()[0], DATE)

    def test_withdrawn_merged_secondary_does_not_hide_active_canonical(self):
        data = snapshot()
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.conn.execute("UPDATE cloud_map SET local_id=? WHERE kind='proposal'", (str(self.pid),))
        data["proposals"][0].update(state="archived", withdrawn_at=DATE)
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        local = self.conn.execute("SELECT state,withdrawn_at FROM proposals WHERE id=?", (self.pid,)).fetchone()
        self.assertEqual((local["state"], local["withdrawn_at"]), ("approved", None))
        captured = []
        def fake_rpc(cfg, name, body):
            captured.append(body)
            return {"updated": len(body["p_updates"])}
        with patch.object(cloud_sync, "_rpc", fake_rpc):
            cloud_sync.push(self.cfg, self.conn)
        updates = {item["id"]: item for item in captured[0]["p_updates"]}
        self.assertEqual(updates[202]["state"], "approved")
        self.assertEqual(updates[201]["state"], "archived")
        self.assertNotIn("merge_into", updates[201])

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

    def test_publication_sends_known_duration_without_clearing_unknown_duration(self):
        cloud_sync.import_snapshot(self.cfg, self.conn, snapshot())
        self.conn.execute("UPDATE sources SET duration=222 WHERE video_id='abcDEF_1234'")
        captured = []
        def rpc(cfg, name, body):
            captured.extend(body["p_updates"])
            return {"updated": len(body["p_updates"])}
        with patch.object(cloud_sync, "_rpc", rpc):
            cloud_sync.push(self.cfg, self.conn)
        updates = {item["id"]: item for item in captured}
        self.assertEqual(updates[202]["duration"], 222)
        self.assertNotIn("duration", updates[201])

    def test_pull_adopts_new_known_duration_and_preserves_locally_verified_duration(self):
        data = snapshot()
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        data["sources"][1]["duration"] = 222
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual(self.conn.execute("SELECT duration FROM sources WHERE video_id='abcDEF_1234'").fetchone()[0], 222)
        self.conn.execute("UPDATE sources SET duration=223 WHERE video_id='abcDEF_1234'")
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual(self.conn.execute("SELECT duration FROM sources WHERE video_id='abcDEF_1234'").fetchone()[0], 223)

    def test_cloud_playlist_import_reuses_existing_url_and_keeps_duration_unknown(self):
        url = "https://www.youtube.com/playlist?list=PL1234567890abc"
        sid = db.upsert_source(self.conn, {"url": url, "title": "Playlist corrigée localement"})
        data = snapshot()
        data["sources"][0].update(url=url, source_kind="playlist", duration=999, title=None)
        first = cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual(first["new_sources"], 0)
        source_map = self.conn.execute("SELECT local_id FROM cloud_map WHERE kind='source' AND remote_id='testing-project:101'").fetchone()[0]
        self.assertEqual(source_map, str(sid))
        cloud_sync.import_snapshot(self.cfg, self.conn, data)
        local = self.conn.execute("SELECT title,duration,url FROM sources WHERE id=?", (sid,)).fetchone()
        self.assertEqual(tuple(local), ("Playlist corrigée localement", None, url))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM sources WHERE url=?", (url,)).fetchone()[0], 1)
        for bad in ("https://www.youtube.com/playlist?list=RD1234567890", "https://example.com/playlist?list=PL1234567890abc", url + "&feature=share"):
            data["sources"][0]["url"] = bad
            with self.assertRaises(cloud_sync.CloudSyncError):
                cloud_sync.import_snapshot(self.cfg, self.conn, data)

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

    def test_pull_and_publish_twenty_thousand_distinct_proposals_without_quantity_caps(self):
        count = 20_001
        data = {"schema_version": 1, "exported_at": DATE, "settings": {},
                "guests": [{"id": GA, "nickname": "Test volume", "created_at": DATE}],
                "sources": [{"id": i, "video_id": f"{i:011d}",
                    "url": f"https://www.youtube.com/watch?v={i:011d}",
                    "title": f"Generated test {i}", "created_at": DATE} for i in range(1, count + 1)],
                "proposals": [{"id": i, "source_id": i, "guest_id": GA, "state": "pending",
                               "created_at": DATE} for i in range(1, count + 1)],
                "votes": [{"proposal_id": i, "guest_id": GA, "created_at": DATE} for i in range(1, count + 1)]}
        photo = avatar()
        data["guests"][0].update(photo)
        result = cloud_sync.import_snapshot(self.cfg, self.conn, data)
        self.assertEqual((result["new_sources"], result["new_proposals"], result["votes"]), (count, count, count))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM cloud_map WHERE kind='proposal'").fetchone()[0], count)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM guests WHERE avatar_data<>''").fetchone()[0], 1)
        published, lengths = set(), []
        def rpc(cfg, name, body):
            self.assertNotIn("avatar_data", json.dumps(body))
            lengths.append(len(body["p_updates"]))
            published.update(entry["id"] for entry in body["p_updates"])
            return {"updated": len(body["p_updates"])}
        with patch.object(cloud_sync, "_rpc", rpc):
            result = cloud_sync.push(self.cfg, self.conn)
        self.assertEqual(published, set(range(1, count + 1)))
        self.assertEqual(result["updated"], count)
        self.assertEqual(lengths, [1000] * 20 + [1])

    def test_publication_batches_include_merge_targets_and_do_not_lose_entries(self):
        target = {"id": 1, "state": "approved"}
        updates = [target] + [{"id": i, "state": "archived", "merge_into": 1} for i in range(2, 2501)]
        batches = list(cloud_sync._publication_batches(updates))
        self.assertEqual({item["id"] for batch in batches for item in batch}, set(range(1, 2501)))
        for batch in batches:
            self.assertLessEqual(len(batch), 1000)
            self.assertIn(target, batch)
            self.assertEqual(len({item["id"] for item in batch}), len(batch))

    def test_failed_later_batch_reports_progress_and_does_not_mark_publication_complete(self):
        cloud_sync.import_snapshot(self.cfg, self.conn, snapshot())
        def rpc(cfg, name, body):
            if body["p_updates"][0]["id"] == 202:
                raise cloud_sync.CloudSyncError("Network test failure")
            return {"updated": len(body["p_updates"])}
        batches = [[{"id": 201}], [{"id": 202}]]
        with patch.object(cloud_sync, "_publication_batches", return_value=iter(batches)), patch.object(cloud_sync, "_rpc", rpc):
            with self.assertRaisesRegex(cloud_sync.CloudSyncError, "après 1 propositions"):
                cloud_sync.push(self.cfg, self.conn)
        self.assertIsNone(db.get_setting(self.conn, "cloud_last_publish"))

    def test_all_metadata_is_validated_before_any_batch_is_published(self):
        cloud_sync.import_snapshot(self.cfg, self.conn, snapshot())
        self.conn.execute("UPDATE sources SET title=? WHERE video_id='abcDEF_1234'", ("Invalid\ntitle",))
        with patch.object(cloud_sync, "_rpc") as rpc:
            with self.assertRaises(cloud_sync.CloudSyncError):
                cloud_sync.push(self.cfg, self.conn)
        rpc.assert_not_called()

    def test_http_snapshot_transfer_uses_bounded_read_buffers(self):
        class Response(io.BytesIO):
            def __init__(self):
                super().__init__(json.dumps({"marker": "x" * (2 * 1024 * 1024)}).encode())
                self.sizes = []
            def read(self, size=-1):
                self.sizes.append(size)
                self.assert_size(size)
                return super().read(size)
            @staticmethod
            def assert_size(size):
                assert 0 < size <= 1024 * 1024
        response = Response()
        self.cfg.supabase_service_role_key = "sb_secret_generated_test_fixture"
        with patch.object(cloud_sync, "build_opener") as opener:
            opener.return_value.open.return_value = response
            result = cloud_sync.fetch_snapshot(self.cfg)
        self.assertEqual(len(result["marker"]), 2 * 1024 * 1024)
        self.assertGreater(len(response.sizes), 2)


if __name__ == "__main__":
    unittest.main()
