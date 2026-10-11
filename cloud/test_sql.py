"""Real PostgreSQL security/RPC checks in a disposable Docker container.

Run: python cloud/test_sql.py. Requires Docker; no ports, external data or audio.
"""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
TOKEN_A, TOKEN_B, TOKEN_C = "a" * 64, "b" * 64, "c" * 64


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    return "'" + value.replace("'", "''") + "'"


def main():
    name = "playlist25-sql-test-" + uuid4().hex[:12]
    subprocess.run(["docker", "run", "--rm", "-d", "--network", "none", "--memory", "256m", "--cpus", "1",
                    "--name", name, "-e", "POSTGRES_PASSWORD=playlist25-disposable-test", "postgres:16-alpine"],
                   check=True, stdout=subprocess.DEVNULL)
    def sql(statement, *, role=None, success=True):
        if role:
            statement = "SET ROLE " + role + ";\n" + statement
        result = subprocess.run(["docker", "exec", "-i", name, "psql", "-X", "-qAt", "-U", "postgres",
                                 "-v", "ON_ERROR_STOP=1"], input=statement, text=True, capture_output=True)
        if success and result.returncode:
            raise AssertionError(result.stderr)
        if not success:
            assert result.returncode != 0, "An unauthorized SQL operation unexpectedly succeeded"
        return result.stdout.strip()
    def rpc(function, *parameters, role="anon"):
        return json.loads(sql("select public." + function + "(" + ",".join(map(literal, parameters)) + ");", role=role))
    try:
        for _ in range(60):
            logs = subprocess.run(["docker", "logs", name], text=True, capture_output=True)
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"], capture_output=True)
            if "PostgreSQL init process complete" in logs.stdout and ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("PostgreSQL did not start")
        sql("create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;")
        migration = (ROOT / "supabase.sql").read_text(encoding="utf-8")
        sql(migration)
        sql(migration)  # reusable without destroying data/settings
        assert rpc("playlist25_session", TOKEN_A)["guest"] is None
        assert rpc("playlist25_propose", TOKEN_C, "manual", "Sans pseudo", "", None)["status"] == 401
        first = rpc("playlist25_identity", TOKEN_A, "Camille", None)
        second = rpc("playlist25_identity", TOKEN_B, "Camille", None)
        assert first["id"] != second["id"], "Nickname must not identify a guest"
        assert rpc("playlist25_session", TOKEN_A)["guest"]["id"] == first["id"]
        assert rpc("playlist25_session", TOKEN_C)["guest"] is None, "Random token must not impersonate another guest"
        item = rpc("playlist25_propose", TOKEN_A, "youtube", None, "", "dQw4w9WgXcQ")["item"]
        pid = item["id"]
        assert "titre à vérifier" in item["title"] and item["duration"] is None
        duplicate = rpc("playlist25_propose", TOKEN_B, "youtube", "Autre titre", "", "dQw4w9WgXcQ")
        assert duplicate["duplicate"]["id"] == pid
        assert sql("select count(*) from public.playlist25_sources;") == "1"
        assert rpc("playlist25_propose", TOKEN_A, "youtube", "Injection", "", "../../etc/passwd")["status"] == 422
        manual = rpc("playlist25_propose", TOKEN_A, "manual", "Accents éà — test", "Artiste", None)["item"]
        near = rpc("playlist25_propose", TOKEN_B, "manual", "accents éà — test", "artiste", None)
        assert near.get("duplicate"), "Exact case-insensitive manual duplicate should suggest a vote"
        sql("update public.playlist25_settings set max_proposals=2 where singleton;")
        assert rpc("playlist25_propose", TOKEN_A, "manual", "Trop", "", None)["status"] == 429
        assert rpc("playlist25_propose", TOKEN_B, "manual", "Autre version live", "", None).get("item")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: rpc("playlist25_vote", TOKEN_A, pid, True), range(4)))
        assert all(result["voted"] for result in results)
        assert sql(f"select count(*) from public.playlist25_votes where proposal_id={pid};") == "1"
        assert rpc("playlist25_vote", TOKEN_B, pid, True)["votes"] == 2
        assert rpc("playlist25_vote", TOKEN_B, pid, False) == {"votes": 1, "voted": False}
        listed = rpc("playlist25_list", TOKEN_A, "", "popular", False)["items"]
        assert len(listed) == 3 and all("guest_id" not in row and "file_path" not in row and "token_hash" not in row for row in listed)
        mine = rpc("playlist25_list", TOKEN_A, "", "recent", True)["items"]
        assert len(mine) == 2 and all(row["mine"] for row in mine)
        for role in ("anon", "authenticated"):
            sql("select token_hash from public.playlist25_guests;", role=role, success=False)
            sql("select public.playlist25_snapshot();", role=role, success=False)
            sql("select public.playlist25_admin_publish('{}'::jsonb,'[]'::jsonb);", role=role, success=False)
            sql("select public.playlist25_create_invitations(1);", role=role, success=False)
            sql("insert into public.playlist25_votes values(1,gen_random_uuid(),now());", role=role, success=False)
        sql("grant select on public.playlist25_guests to anon;")
        assert sql("select count(*) from public.playlist25_guests;", role="anon") == "0", "RLS denies raw guest access even with SELECT"
        sql("revoke select on public.playlist25_guests from anon;")
        snapshot = rpc("playlist25_snapshot", role="service_role")
        assert "token_hash" not in json.dumps(snapshot) and "code_hash" not in json.dumps(snapshot)
        assert len(snapshot["votes"]) == 1
        sql("update public.playlist25_settings set invitation_required=true where singleton;")
        assert rpc("playlist25_identity", TOKEN_C, "Invité", "invalide")["status"] == 403
        code = rpc("playlist25_create_invitations", 1, role="service_role")["codes"][0]
        assert rpc("playlist25_identity", TOKEN_C, "Invité", code).get("id")
        assert rpc("playlist25_identity", "d" * 64, "Autre", code)["status"] == 403
        assert rpc("playlist25_search_budget", "f" * 64, False)["status"] == 403
        sql("update public.playlist25_settings set invitation_required=false where singleton;")
        for _ in range(20):
            assert rpc("playlist25_search_budget", "e" * 64, False)["allowed"]
        assert rpc("playlist25_search_budget", "e" * 64, False)["status"] == 429
        sql("insert into public.playlist25_search_days(day,requests) values(current_date,90) on conflict(day) do update set requests=90;")
        assert rpc("playlist25_search_budget", "f" * 64, True)["status"] == 429
        updates = [{"id": pid, "state": "approved", "title": "Titre corrigé", "artist": "Artiste", "sort_order": 2}]
        published = rpc("playlist25_admin_publish", {"proposals_open": False, "votes_open": False}, updates, role="service_role")
        assert published["updated"] == 1
        assert rpc("playlist25_vote", TOKEN_B, pid, True)["status"] == 403
        assert rpc("playlist25_propose", TOKEN_B, "manual", "Fermé", "", None)["status"] == 403
        invalid = [{**updates[0], "title": "Ne pas appliquer"}, {**updates[0], "id": 999999, "title": "Absent"}]
        assert rpc("playlist25_admin_publish", {"proposals_open": True}, invalid, role="service_role")["status"] == 409
        assert rpc("playlist25_session", TOKEN_A)["settings"]["proposals_open"] is False, "Malformed publication must remain atomic"
        assert sql(f"select title from public.playlist25_sources where id=(select source_id from public.playlist25_proposals where id={pid});") == "Titre corrigé"
        sql("update public.playlist25_settings set votes_open=true where singleton;")
        assert rpc("playlist25_vote", TOKEN_B, manual["id"], True)["votes"] == 1
        merged = [updates[0], {"id": manual["id"], "state": "archived", "title": "Fusion", "artist": "", "sort_order": 0, "merge_into": pid}]
        assert rpc("playlist25_admin_publish", {}, merged, role="service_role")["updated"] == 2
        assert sql(f"select count(*) from public.playlist25_votes where proposal_id={pid};") == "2"
        assert sql(f"select count(*) from public.playlist25_votes where proposal_id={manual['id']};") == "0"
        assert rpc("playlist25_vote", TOKEN_B, pid, False)["votes"] == 1
        assert rpc("playlist25_vote", TOKEN_B, manual["id"], True)["status"] == 404
        for _ in range(10):
            assert rpc("playlist25_identity", "9" * 64, "", None)["status"] == 422
        assert rpc("playlist25_identity", "9" * 64, "", None)["status"] == 429, "Failed actions must still consume their rate budget"
        print("PASS: PostgreSQL migration twice, persistent identities, exact duplicates, quotas, concurrent votes/removal, RLS/grants, invitations, search budgets, secret snapshot, atomic admin publication.")
    finally:
        subprocess.run(["docker", "stop", "-t", "1", name], capture_output=True)


if __name__ == "__main__":
    main()
