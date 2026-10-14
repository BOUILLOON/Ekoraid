"""Category ownership, safe sync, additive upgrades and old archive compatibility."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from playlist25 import backups, categories, cloud_sync, db
from playlist25.admin import create_admin_app
from playlist25.config import Settings, initialize_local_config
from playlist25.guest import create_guest_app

DATE = "2026-10-01T00:00:00+00:00"
GID = "11111111-1111-4111-8111-111111111111"
CAT_A, CAT_B = "a" * 32, "b" * 32


@pytest.fixture
def cfg(tmp_path):
    home = tmp_path / "studio"
    initialize_local_config(home)
    result = Settings(root=home, data_dir=home / "data", admin_password="category-admin-fixture", session_secret="category-signed-session-fixture", min_free_mb=0,
                      supabase_url="https://testing-project.supabase.co")
    db.init_db(result)
    return result


def admin(cfg):
    client = TestClient(create_admin_app(cfg))
    csrf = client.get("/api/session").json()["csrf_token"]
    login = client.post("/api/login", json={"password": cfg.admin_password}, headers={"X-CSRF-Token": csrf})
    assert login.status_code == 200, login.text
    return client, {"X-CSRF-Token": login.json()["csrf_token"]}


def guest(cfg):
    client = TestClient(create_guest_app(cfg))
    headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}
    assert client.post("/api/identity", json={"nickname": "Même pseudo"}, headers=headers).status_code == 200
    return client, headers


def propose(client, headers, title="Musique"):
    result = client.post("/api/proposals", json={"kind": "manual", "title": title}, headers=headers)
    assert result.status_code == 200, result.text
    return result.json()["item"]


def create_category(client, headers, name="Danse"):
    response = client.post("/api/categories", json={"name": name}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["category"]


def snapshot():
    return {"schema_version": 1, "exported_at": DATE, "settings": {},
            "guests": [{"id": GID, "nickname": "Camille", "created_at": DATE}],
            "sources": [{"id": 101, "video_id": None, "url": None, "title": "Remote", "artist": "", "created_at": DATE}],
            "proposals": [{"id": 201, "source_id": 101, "guest_id": GID, "state": "pending", "sort_order": 0, "created_at": DATE, "category_ids": [CAT_A]}],
            "votes": [{"proposal_id": 201, "guest_id": GID, "created_at": DATE}],
            "categories": [{"id": CAT_A, "name": "Danse", "sort_order": 0, "active": True},
                           {"id": CAT_B, "name": "Calme", "sort_order": 1, "active": True}]}


def test_category_catalogue_requires_admin_csrf_and_archives_without_deleting(cfg):
    public, _ = guest(cfg)
    assert public.post("/api/categories", json={"name": "Inventée"}).status_code == 404
    client = TestClient(create_admin_app(cfg))
    assert client.get("/api/categories").status_code == 401
    client, headers = admin(cfg)
    assert client.post("/api/categories", json={"name": "Danse"}).status_code == 403
    category = create_category(client, headers)
    assert len(category["id"]) == 32 and category["active"] is True
    assert client.post("/api/categories", json={"name": " danse "}, headers=headers).status_code == 409
    assert client.patch(f"/api/categories/{category['id']}", json={"name": "Classiques", "sort_order": 7, "active": False}, headers=headers).status_code == 200
    assert client.delete(f"/api/categories/{category['id']}", headers=headers).status_code == 405
    catalogue = public.get("/api/proposals").json()["categories"]
    assert catalogue == [{"id": category["id"], "name": "Classiques", "sort_order": 7, "active": False}]
    assert client.patch(f"/api/categories/{category['id']}", json={"active": True}, headers=headers).status_code == 200


def test_guest_category_choices_are_owner_only_not_nickname_and_preserve_files_votes(cfg):
    studio, studio_headers = admin(cfg)
    category = create_category(studio, studio_headers)
    alice, ah = guest(cfg)
    bob, bh = guest(cfg)
    item = propose(alice, ah)
    path = cfg.audio_dir / "retained.bin"
    path.write_bytes(b"audio unchanged")
    with closing(db.connect(cfg)) as conn, conn:
        conn.execute("UPDATE sources SET file_path=?,status='available' WHERE id=?", (path.name, item["source_id"]))
    alice.post(f"/api/proposals/{item['id']}/vote", json={"active": True}, headers=ah)
    endpoint = f"/api/proposals/{item['id']}/categories"
    payload = {"category_ids": [category["id"]]}
    anonymous = TestClient(create_guest_app(cfg))
    anonymous_headers = {"X-CSRF-Token": anonymous.get("/api/session").json()["csrf_token"]}
    assert anonymous.patch(endpoint, json=payload, headers=anonymous_headers).status_code == 401
    assert alice.patch(endpoint, json=payload).status_code == 403
    assert bob.patch(endpoint, json=payload, headers=bh).status_code == 404
    assert bob.patch(endpoint, json={**payload, "guest_id": item.get("guest_id")}, headers=bh).status_code == 422
    response = alice.patch(endpoint, json=payload, headers=ah)
    assert response.status_code == 200, response.text
    assert response.json()["item"]["category_ids"] == [category["id"]]
    assert response.json()["item"]["mine"] is True and "guest_id" not in response.json()["item"]
    assert response.json()["item"]["votes"] == 1 and response.json()["item"]["status"] == "available"
    assert bob.get("/api/proposals").json()["items"][0]["category_ids"] == [category["id"]]
    assert path.read_bytes() == b"audio unchanged"
    with closing(db.connect(cfg)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_archived_choices_can_be_retained_but_never_newly_assigned(cfg):
    studio, sh = admin(cfg)
    first, second = create_category(studio, sh), create_category(studio, sh, "Calme")
    alice, ah = guest(cfg)
    item, other = propose(alice, ah), propose(alice, ah, "Autre")
    endpoint = f"/api/proposals/{item['id']}/categories"
    assert alice.patch(endpoint, json={"category_ids": [first["id"]]}, headers=ah).status_code == 200
    assert studio.patch(f"/api/categories/{first['id']}", json={"active": False}, headers=sh).status_code == 200
    assert alice.patch(endpoint, json={"category_ids": [first["id"], second["id"]]}, headers=ah).status_code == 200
    assert alice.patch(f"/api/proposals/{other['id']}/categories", json={"category_ids": [first["id"]]}, headers=ah).status_code == 422
    assert alice.patch(endpoint, json={"category_ids": [second["id"]]}, headers=ah).status_code == 200
    assert alice.patch(endpoint, json={"category_ids": [first["id"]]}, headers=ah).status_code == 422


@pytest.mark.parametrize("selected", [["bad"], [CAT_A, CAT_A], [CAT_A], [False], None, "bad", [CAT_A] * 101])
def test_invalid_category_choices_never_change_proposal(cfg, selected):
    alice, ah = guest(cfg)
    item = propose(alice, ah)
    result = alice.patch(f"/api/proposals/{item['id']}/categories", json={"category_ids": selected}, headers=ah)
    assert result.status_code == 422
    assert alice.get("/api/proposals").json()["items"][0]["category_ids"] == []


def test_category_edits_work_when_collection_closes_but_withdrawn_proposals_are_locked(cfg):
    studio, sh = admin(cfg)
    category = create_category(studio, sh)
    alice, ah = guest(cfg)
    item = propose(alice, ah)
    studio.patch("/api/settings", json={"proposals_open": False, "votes_open": False, "invitation_required": True}, headers=sh)
    endpoint = f"/api/proposals/{item['id']}/categories"
    assert alice.patch(endpoint, json={"category_ids": [category["id"]]}, headers=ah).status_code == 200
    assert alice.post(f"/api/proposals/{item['id']}/withdraw", json={}, headers=ah).status_code == 200
    assert alice.patch(endpoint, json={"category_ids": []}, headers=ah).status_code == 404


def test_private_assignment_changes_only_that_proposal_not_the_shared_source(cfg):
    studio, sh = admin(cfg)
    category = create_category(studio, sh)
    alice, ah = guest(cfg)
    item = propose(alice, ah)
    with closing(db.connect(cfg)) as conn, conn:
        conn.execute("INSERT INTO guests(id,nickname,created_at) VALUES('other-owner','Même pseudo',?)", (DATE,))
        other_id = conn.execute("INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,'other-owner',?)", (item["source_id"], DATE)).lastrowid
    assert studio.patch(f"/api/proposals/{item['id']}/categories", json={"category_ids": [category["id"]]}, headers=sh).status_code == 200
    with closing(db.connect(cfg)) as conn:
        assert conn.execute("SELECT category_ids FROM proposals WHERE id=?", (other_id,)).fetchone()[0] == "[]"
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1


def test_cloud_category_pull_preserves_pending_changes_old_snapshots_and_guest_updates(cfg):
    data = snapshot()
    with closing(db.connect(cfg)) as conn, conn:
        cloud_sync.import_snapshot(cfg, conn, data)
        pid = int(conn.execute("SELECT local_id FROM cloud_map WHERE kind='proposal'").fetchone()[0])
        assert json.loads(conn.execute("SELECT category_ids FROM proposals WHERE id=?", (pid,)).fetchone()[0]) == [CAT_A]
        data["proposals"][0]["category_ids"] = [CAT_B]
        cloud_sync.import_snapshot(cfg, conn, data)
        assert json.loads(conn.execute("SELECT category_ids FROM proposals WHERE id=?", (pid,)).fetchone()[0]) == [CAT_B]
        categories.assign(conn, pid, [CAT_A])
        conn.execute("UPDATE categories SET name='Correction locale',dirty=1 WHERE id=?", (CAT_A,))
        cloud_sync.import_snapshot(cfg, conn, data)
        assert json.loads(conn.execute("SELECT category_ids FROM proposals WHERE id=?", (pid,)).fetchone()[0]) == [CAT_A]
        assert conn.execute("SELECT name FROM categories WHERE id=?", (CAT_A,)).fetchone()[0] == "Correction locale"
        old = copy.deepcopy(data)
        del old["categories"]
        del old["proposals"][0]["category_ids"]
        cloud_sync.import_snapshot(cfg, conn, old)
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 2
        assert json.loads(conn.execute("SELECT category_ids FROM proposals WHERE id=?", (pid,)).fetchone()[0]) == [CAT_A]
        assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 1


def test_invalid_remote_categories_leave_all_local_data_unchanged(cfg):
    data = snapshot()
    data["proposals"][0]["category_ids"] = ["f" * 32]
    with closing(db.connect(cfg)) as conn, conn:
        with pytest.raises(cloud_sync.CloudSyncError): cloud_sync.import_snapshot(cfg, conn, data)
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM guests").fetchone()[0] == 0


def test_main_publication_never_overwrites_guest_categories_and_separate_publish_targets_dirty_only(cfg, monkeypatch):
    calls = []
    with closing(db.connect(cfg)) as conn, conn:
        cloud_sync.import_snapshot(cfg, conn, snapshot())
        pid = int(conn.execute("SELECT local_id FROM cloud_map WHERE kind='proposal'").fetchone()[0])
        categories.assign(conn, pid, [CAT_B])
        def rpc(_cfg, name, payload):
            calls.append((name, copy.deepcopy(payload)))
            if name == "playlist25_admin_publish":
                assert all("category_ids" not in row for row in payload["p_updates"])
                return {"updated": len(payload["p_updates"])}
            assert name == "playlist25_admin_categories"
            return {"updated_categories": len(payload["p_categories"]), "updated_assignments": len(payload["p_assignments"])}
        monkeypatch.setattr(cloud_sync, "_rpc", rpc)
        cloud_sync.push(cfg, conn)
        assert conn.execute("SELECT categories_dirty FROM proposals WHERE id=?", (pid,)).fetchone()[0] == 1
        cloud_sync.publish_categories(cfg, conn)
        assert calls[-1][1] == {"p_categories": [], "p_assignments": [{"id": 201, "category_ids": [CAT_B]}]}
        assert conn.execute("SELECT categories_dirty FROM proposals WHERE id=?", (pid,)).fetchone()[0] == 0


def test_category_publish_does_not_acknowledge_an_edit_made_during_network_response(cfg, monkeypatch):
    with closing(db.connect(cfg)) as conn, conn:
        cloud_sync.import_snapshot(cfg, conn, snapshot())
        pid = int(conn.execute("SELECT local_id FROM cloud_map WHERE kind='proposal'").fetchone()[0])
        categories.assign(conn, pid, [CAT_B])
        def rpc(_cfg, _name, payload):
            categories.assign(conn, pid, [CAT_A])
            return {"updated_categories": len(payload["p_categories"]), "updated_assignments": len(payload["p_assignments"])}
        monkeypatch.setattr(cloud_sync, "_rpc", rpc)
        cloud_sync.publish_categories(cfg, conn)
        assert conn.execute("SELECT categories_dirty FROM proposals WHERE id=?", (pid,)).fetchone()[0] == 1
        assert json.loads(conn.execute("SELECT category_ids FROM proposals WHERE id=?", (pid,)).fetchone()[0]) == [CAT_A]


def test_private_category_publication_requires_csrf_and_uses_config_snapshot(cfg, monkeypatch):
    studio, sh = admin(cfg)
    create_category(studio, sh)
    calls = []
    def rpc(copy_cfg, name, payload):
        assert copy_cfg is not cfg and copy_cfg.supabase_url == cfg.supabase_url
        calls.append((name, payload))
        return {"updated_categories": len(payload["p_categories"]), "updated_assignments": len(payload["p_assignments"])}
    monkeypatch.setattr(cloud_sync, "_rpc", rpc)
    assert studio.post("/api/categories/publish").status_code == 403
    result = studio.post("/api/categories/publish", headers=sh)
    assert result.status_code == 200, result.text
    assert result.json()["updated_categories"] == 1 and calls[0][0] == "playlist25_admin_categories"
    assert calls[0][1]["p_assignments"] == []


def test_category_name_changed_during_publish_remains_pending_for_next_pull(cfg, monkeypatch):
    with closing(db.connect(cfg)) as conn, conn:
        cloud_sync.import_snapshot(cfg, conn, snapshot())
        conn.execute("UPDATE categories SET name='Avant',dirty=1 WHERE id=?", (CAT_A,))
        def rpc(_cfg, _name, payload):
            conn.execute("UPDATE categories SET name='Après',dirty=1 WHERE id=?", (CAT_A,))
            return {"updated_categories": len(payload["p_categories"]), "updated_assignments": len(payload["p_assignments"])}
        monkeypatch.setattr(cloud_sync, "_rpc", rpc)
        cloud_sync.publish_categories(cfg, conn)
        cloud_sync.import_snapshot(cfg, conn, snapshot())
        assert tuple(conn.execute("SELECT name,dirty FROM categories WHERE id=?", (CAT_A,)).fetchone()) == ("Après", 1)


def test_additive_category_upgrade_and_old_backup_restore_preserve_ids_votes_and_files(cfg, tmp_path):
    alice, ah = guest(cfg)
    item = propose(alice, ah)
    alice.post(f"/api/proposals/{item['id']}/vote", json={"active": True}, headers=ah)
    audio = cfg.audio_dir / "preserved.bin"
    audio.write_bytes(b"audio preserved")
    with closing(db.connect(cfg)) as conn, conn:
        conn.execute("UPDATE sources SET file_path=? WHERE id=?", (audio.name, item["source_id"]))
        conn.execute("DROP TABLE categories")
        conn.execute("ALTER TABLE proposals DROP COLUMN category_ids")
        conn.execute("ALTER TABLE proposals DROP COLUMN categories_dirty")
    archive = tmp_path / "old studio.zip"
    backups.create_backup(cfg.root, archive)
    assert backups.inspect_backup(archive)["counts"]["votes"] == 1
    new_home = tmp_path / "restored"
    backups.restore_backup(archive, new_home)
    with closing(sqlite3.connect(new_home / "data" / "playlist.sqlite3")) as conn:
        assert conn.execute("SELECT id,category_ids,categories_dirty FROM proposals").fetchone() == (item["id"], "[]", 0)
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 1
    db.init_db(cfg); db.init_db(cfg)
    with closing(db.connect(cfg)) as conn:
        assert conn.execute("SELECT id,category_ids FROM proposals").fetchone()[0] == item["id"]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert audio.read_bytes() == (new_home / "data" / "audio" / audio.name).read_bytes() == b"audio preserved"


def test_new_backup_retains_category_catalogue_archives_and_pending_assignments(cfg, tmp_path):
    studio, sh = admin(cfg)
    category = create_category(studio, sh)
    alice, ah = guest(cfg)
    item = propose(alice, ah)
    assert alice.patch(f"/api/proposals/{item['id']}/categories", json={"category_ids": [category["id"]]}, headers=ah).status_code == 200
    studio.patch(f"/api/categories/{category['id']}", json={"active": False}, headers=sh)
    archive, restored = tmp_path / "new backup.zip", tmp_path / "restored"
    backups.create_backup(cfg.root, archive)
    backups.restore_backup(archive, restored)
    with closing(sqlite3.connect(restored / "data" / "playlist.sqlite3")) as conn:
        assert conn.execute("SELECT id,name,active,dirty FROM categories").fetchone() == (category["id"], "Danse", 0, 1)
        assert conn.execute("SELECT id,category_ids,categories_dirty FROM proposals").fetchone() == (item["id"], json.dumps([category["id"]], separators=(",", ":")), 1)


def test_category_name_can_be_reused_after_archive_but_conflicting_reactivation_is_atomic(cfg):
    studio, sh = admin(cfg)
    old = create_category(studio, sh)
    assert studio.patch(f"/api/categories/{old['id']}", json={"active": False}, headers=sh).status_code == 200
    new = create_category(studio, sh, "danse")
    assert new["id"] != old["id"]
    assert studio.patch(f"/api/categories/{old['id']}", json={"active": True}, headers=sh).status_code == 409
    assert studio.patch(f"/api/categories/{old['id']}", json={"sort_order": 4}, headers=sh).status_code == 200
    with closing(db.connect(cfg)) as conn:
        assert conn.execute("SELECT active,sort_order FROM categories WHERE id=?", (old["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 2


@pytest.mark.parametrize("order", [-1, True, 2147483648])
def test_category_order_matches_cloud_nonnegative_integer_contract(cfg, order):
    studio, sh = admin(cfg)
    original = create_category(studio, sh)
    assert studio.post("/api/categories", json={"name": "Refusée", "sort_order": order}, headers=sh).status_code == 422
    assert studio.patch(f"/api/categories/{original['id']}", json={"sort_order": order}, headers=sh).status_code == 422
    assert studio.get("/api/categories").json()["categories"] == [original]


def test_negative_remote_or_pending_category_order_never_changes_local_or_publishes(cfg, monkeypatch):
    data = snapshot()
    data["categories"][0]["sort_order"] = -1
    with closing(db.connect(cfg)) as conn, conn:
        with pytest.raises(cloud_sync.CloudSyncError): cloud_sync.import_snapshot(cfg, conn, data)
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0
        conn.execute("INSERT INTO categories(id,name,sort_order,dirty) VALUES(?,'Ancien ordre',-1,1)", (CAT_A,))
        monkeypatch.setattr(cloud_sync, "_rpc", lambda *_args: pytest.fail("Invalid local order reached a cloud mutation"))
        with pytest.raises(cloud_sync.CloudSyncError): cloud_sync.publish_categories(cfg, conn)
        assert conn.execute("SELECT sort_order,dirty FROM categories").fetchone()[0] == -1


def test_distinct_sql_unicode_names_are_preserved_without_casefold_or_normalization_collisions(cfg, tmp_path):
    studio, sh = admin(cfg)
    names = ["Maße", "MASSE", "Cafe\u0301", "Café", "Soirée 👩\u200d🎤"]
    created = [create_category(studio, sh, name) for name in names]
    assert [item["name"] for item in created] == names
    data = snapshot()
    data["categories"][0].update(name="Cafe\u0301", active=False)
    data["categories"][1].update(name="Café", active=True)
    with closing(db.connect(cfg)) as conn, conn:
        cloud_sync.import_snapshot(cfg, conn, data)
        assert conn.execute("SELECT name FROM categories WHERE id=?", (CAT_A,)).fetchone()[0] == "Cafe\u0301"
        assert conn.execute("SELECT name FROM categories WHERE id=?", (CAT_B,)).fetchone()[0] == "Café"
    archive = tmp_path / "unicode archive.zip"
    backups.create_backup(cfg.root, archive)
    assert backups.inspect_backup(archive)["counts"]["proposals"] == 1


def test_cloud_catalogue_with_archive_reused_name_and_over_hundred_tags_round_trips(cfg, tmp_path):
    data = snapshot()
    data["categories"] = [{"id": f"{number:032x}", "name": f"Catégorie {number}", "sort_order": number, "active": True} for number in range(1, 102)]
    data["categories"].append({"id": CAT_A, "name": "Catégorie 1", "sort_order": 0, "active": False})
    selected = [row["id"] for row in data["categories"]]
    data["proposals"][0]["category_ids"] = selected
    with closing(db.connect(cfg)) as conn, conn:
        cloud_sync.import_snapshot(cfg, conn, data)
        pid = int(conn.execute("SELECT local_id FROM cloud_map WHERE kind='proposal'").fetchone()[0])
        assert len(json.loads(conn.execute("SELECT category_ids FROM proposals WHERE id=?", (pid,)).fetchone()[0])) == 102
    studio, sh = admin(cfg)
    assert studio.patch(f"/api/proposals/{pid}/categories", json={"category_ids": selected}, headers=sh).status_code == 200
    archive, restored = tmp_path / "many tags.zip", tmp_path / "restored many tags"
    backups.create_backup(cfg.root, archive)
    backups.restore_backup(archive, restored)
    with closing(sqlite3.connect(restored / "data" / "playlist.sqlite3")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 102
        assert json.loads(conn.execute("SELECT category_ids FROM proposals WHERE id=?", (pid,)).fetchone()[0]) == selected


def test_archive_before_reused_name_remains_publishable_across_category_batches(cfg, monkeypatch):
    old_id = "f" * 32
    server = {old_id: {"id": old_id, "name": "Nom réutilisé", "sort_order": 0, "active": True}}
    calls = []
    with closing(db.connect(cfg)) as conn, conn:
        conn.execute("INSERT INTO categories(id,name,active,dirty) VALUES(?,'Nom réutilisé',0,1)", (old_id,))
        conn.executemany("INSERT INTO categories(id,name,dirty) VALUES(?,?,1)",
                         ((f"{number:032x}", "Nom réutilisé" if number == 1 else f"Nouveau {number}") for number in range(1, 1001)))
        def rpc(_cfg, _name, payload):
            after = {**server, **{row["id"]: row for row in payload["p_categories"]}}
            active_names = [row["name"].lower() for row in after.values() if row["active"]]
            if len(active_names) != len(set(active_names)):
                raise cloud_sync.CloudSyncError("Un nom actif existe déjà.")
            server.update(after)
            calls.append(payload)
            return {"updated_categories": len(payload["p_categories"]), "updated_assignments": 0}
        monkeypatch.setattr(cloud_sync, "_rpc", rpc)
        result = cloud_sync.publish_categories(cfg, conn)
        assert result["updated_categories"] == 1001 and len(calls) == 2
        assert calls[0]["p_categories"][0]["id"] == old_id
        assert conn.execute("SELECT COUNT(*) FROM categories WHERE dirty=1").fetchone()[0] == 0
        assert server[old_id]["active"] is False
