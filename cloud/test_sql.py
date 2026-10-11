"""Real PostgreSQL security/RPC checks in a disposable Docker container.

Run: python cloud/test_sql.py. Requires Docker; no ports, external data or audio.
"""
from __future__ import annotations

import concurrent.futures
import base64
import hashlib
import io
import json
import struct
import subprocess
import time
import zlib
from pathlib import Path
from uuid import uuid4
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent
TOKEN_A, TOKEN_B, TOKEN_C = "a" * 64, "b" * 64, "c" * 64


def png_avatar(color='crimson',size=(128,128),metadata=False):
    output=io.BytesIO(); options={}
    if metadata:
        info=PngImagePlugin.PngInfo(); info.add_text('Location','private metadata')
        options['pnginfo']=info
    Image.new('RGBA',size,color).save(output,format='PNG',**options)
    return png_uri(output.getvalue())


def png_uri(raw):
    return 'data:image/png;base64,'+base64.b64encode(raw).decode('ascii')


def png_chunk(kind,data):
    return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int,float)):
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
    def avatars(ids):
        argument='NULL::uuid[]' if ids is None else 'ARRAY['+','.join(literal(value) for value in ids)+']::uuid[]'
        return json.loads(sql('select public.playlist25_avatars('+argument+');',role='anon'))
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
        assert sql("select max_proposals from public.playlist25_settings where singleton;") == "0", "New collections must have unlimited guest proposals"
        # Reproduce the old positive-only constraint on a populated installation.
        sql("update public.playlist25_settings set max_proposals=2 where singleton; "
            "alter table public.playlist25_settings alter column max_proposals set default 10; "
            "alter table public.playlist25_settings drop constraint playlist25_settings_max_proposals_check; "
            "alter table public.playlist25_settings add constraint playlist25_settings_max_proposals_check check(max_proposals between 1 and 100);")
        sql(migration)  # reusable without destroying data/settings
        assert sql("select max_proposals from public.playlist25_settings where singleton;") == "2", "Migration must preserve an existing positive limit"
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
        manual_b = rpc("playlist25_propose", TOKEN_B, "manual", "Autre version live", "", None)["item"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: rpc("playlist25_vote", TOKEN_A, pid, True), range(4)))
        assert all(result["voted"] for result in results)
        assert sql(f"select count(*) from public.playlist25_votes where proposal_id={pid};") == "1"
        assert rpc("playlist25_vote", TOKEN_B, pid, True)["votes"] == 2
        assert rpc("playlist25_vote", TOKEN_B, pid, False) == {"votes": 1, "voted": False}
        listed = rpc("playlist25_list", TOKEN_A, "", "popular", False)["items"]
        assert len(listed) == 3 and all("guest_id" not in row and "file_path" not in row and "token_hash" not in row for row in listed)
        anonymous_items = rpc("playlist25_list", "7" * 64, "", "popular", False)["items"]
        assert len(anonymous_items) == 3 and all(row["mine"] is False for row in anonymous_items), "Anonymous ownership flags must be booleans, never null"
        mine = rpc("playlist25_list", TOKEN_A, "", "recent", True)["items"]
        assert len(mine) == 2 and all(row["mine"] for row in mine)
        avatar_a,avatar_b=png_avatar(),png_avatar('blue')
        assert rpc('playlist25_profile',TOKEN_C,avatar_a)['status']==401
        photo=rpc('playlist25_profile',TOKEN_A,avatar_a)['guest']
        digest=hashlib.md5(base64.b64decode(avatar_a.split(',',1)[1]),usedforsecurity=False).hexdigest()
        assert photo['id']==first['id'] and photo['avatar_id']==first['id'] and photo['avatar_version']==digest
        assert 'avatar_data' not in photo
        assert rpc('playlist25_profile',TOKEN_A,avatar_a)['guest']['avatar_version']==digest
        assert rpc('playlist25_session',TOKEN_A)['guest']['avatar_version']==digest
        public_photo=avatars([first['id'],second['id']])['items']
        assert set(public_photo[0])=={'id','version','avatar_data'}
        assert next(row for row in public_photo if row['id']==first['id'])['avatar_data']==avatar_a
        assert rpc('playlist25_profile',TOKEN_B,avatar_b)['guest']['id']==second['id']
        assert avatars([first['id']])['items'][0]['avatar_data']==avatar_a,"Another token must never replace this owner's photo"
        for row in rpc('playlist25_list',TOKEN_A,'','popular',False)['items']:
            assert row['avatar_id'] in (first['id'],second['id']) and 'avatar_data' not in row
        assert avatars([])=={'items':[]}
        assert avatars([first['id']]*60)['items']
        assert avatars([first['id']]*61)['status']==422 and avatars(None)['status']==422
        raw=base64.b64decode(avatar_a.split(',',1)[1]); header=raw[:33]
        invalid_avatars=[None,'data:image/svg+xml;base64,PHN2Zz4=','data:image/png;base64,AAAA',avatar_a+'\n',
            'data:image/png;base64,'+'A'*200000,png_avatar(size=(129,128)),png_avatar(size=(64,64)),png_avatar(metadata=True),
            png_uri(raw[:-1]),png_uri(raw+b'private trailing data'),png_uri(raw[:8]+b'\xff\xff\xff\xff'+raw[12:]),
            png_uri(header+raw[8:33]+raw[33:]),png_uri(header+png_chunk(b'IDAT',b'')+png_chunk(b'IEND',b'')),
            png_uri(header+png_chunk(b'IDAT',b'x')+png_chunk(b'IEND',b'x')),
            png_uri(header+png_chunk(b'IDAT',b'')*257+raw[33:])]
        for invalid_avatar in invalid_avatars:
            assert sql('select public.playlist25_avatar_valid('+literal(invalid_avatar)+');')=='f'
        for invalid_avatar in invalid_avatars[:5]:
            assert rpc('playlist25_profile',TOKEN_A,invalid_avatar)['status']==422
            assert avatars([first['id']])['items'][0]['version']==digest
        before_photo_remove=sql('select (select count(*) from public.playlist25_proposals),(select count(*) from public.playlist25_votes);')
        assert rpc('playlist25_profile',TOKEN_A,'')['guest']['avatar_version']==''
        assert avatars([first['id']])['items']==[{'id':first['id'],'version':'','avatar_data':''}]
        assert sql('select (select count(*) from public.playlist25_proposals),(select count(*) from public.playlist25_votes);')==before_photo_remove
        for role in ("anon", "authenticated"):
            sql("select token_hash from public.playlist25_guests;", role=role, success=False)
            sql("select public.playlist25_snapshot();", role=role, success=False)
            sql("select public.playlist25_admin_publish('{}'::jsonb,'[]'::jsonb);", role=role, success=False)
            sql("select public.playlist25_create_invitations(1);", role=role, success=False)
            sql("select public.playlist25_avatar_valid('');",role=role,success=False)
            sql("insert into public.playlist25_votes values(1,gen_random_uuid(),now());", role=role, success=False)
        sql("grant select on public.playlist25_guests to anon;")
        assert sql("select count(*) from public.playlist25_guests;", role="anon") == "0", "RLS denies raw guest access even with SELECT"
        sql("revoke select on public.playlist25_guests from anon;")
        snapshot = rpc("playlist25_snapshot", role="service_role")
        assert "token_hash" not in json.dumps(snapshot) and "code_hash" not in json.dumps(snapshot)
        assert len(snapshot["votes"]) == 1
        assert next(row for row in snapshot['guests'] if row['id']==second['id'])['avatar_data']==avatar_b
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
        assert rpc("playlist25_withdraw", TOKEN_A, manual["id"])["status"] == 404, "Moderator archive must not be bypassed by withdrawal"
        assert rpc("playlist25_withdraw", TOKEN_B, pid)["status"] == 404, "Same nickname must not authorize another guest's withdrawal"
        assert rpc("playlist25_withdraw", "8" * 64, pid)["status"] == 401
        sql("update public.playlist25_settings set proposals_open=false,votes_open=false,invitation_required=true where singleton;")
        before_counts = sql("select (select count(*) from public.playlist25_sources),(select count(*) from public.playlist25_votes);")
        withdrawn = rpc("playlist25_withdraw", TOKEN_A, pid)
        assert withdrawn["withdrawn"] is True and withdrawn["id"] == pid and withdrawn["state"] == "archived"
        timestamp = sql(f"select withdrawn_at from public.playlist25_proposals where id={pid};")
        assert timestamp and rpc("playlist25_withdraw", TOKEN_A, pid)["withdrawn"] is True
        assert sql(f"select withdrawn_at from public.playlist25_proposals where id={pid};") == timestamp, "Repeated withdrawal must preserve audit timestamp"
        assert sql("select (select count(*) from public.playlist25_sources),(select count(*) from public.playlist25_votes);") == before_counts, "Withdrawal deleted source or vote history"
        assert all(item["id"] != pid for item in rpc("playlist25_list", TOKEN_A, "", "popular", False)["items"])
        assert rpc("playlist25_admin_publish", {"proposals_open": True, "votes_open": True, "invitation_required": False}, updates, role="service_role")["updated"] == 1
        assert sql(f"select state from public.playlist25_proposals where id={pid};") == "archived", "Admin publish resurrected a withdrawn proposal"
        assert rpc("playlist25_vote", TOKEN_A, pid, True)["status"] == 404
        sql("update public.playlist25_settings set max_proposals=2 where singleton;")
        readded = rpc("playlist25_propose", TOKEN_A, "youtube", None, "", "dQw4w9WgXcQ")["item"]
        assert readded["id"] != pid and readded["source_id"] == item["source_id"] and readded["votes"] == 0
        assert rpc("playlist25_withdraw", TOKEN_A, pid)["withdrawn"] is True
        assert sql(f"select state from public.playlist25_proposals where id={readded['id']};") == "pending", "Repeat withdrawal removed the replacement"
        assert sql(f"select count(*) from public.playlist25_votes where proposal_id={pid};") == "1", "Re-add moved historical votes"
        assert rpc("playlist25_propose", TOKEN_A, "manual", "Fusion", "", None)["status"] == 409, "Moderator archive must still block exact re-add"
        assert rpc("playlist25_vote", TOKEN_A, manual_b["id"], True)["votes"] == 1
        assert rpc("playlist25_withdraw", TOKEN_B, manual_b["id"])["withdrawn"] is True
        manual_readd = rpc("playlist25_propose", TOKEN_B, "manual", "Autre version live", "", None)["item"]
        assert manual_readd["id"] != manual_b["id"] and manual_readd["source_id"] == manual_b["source_id"] and manual_readd["votes"] == 0
        assert sql(f"select count(*) from public.playlist25_votes where proposal_id={manual_b['id']};") == "1"
        sql(migration)
        assert rpc("playlist25_session", TOKEN_A)["settings"]["proposals_open"] is True, "Migration changed existing configuration"
        assert sql(f"select withdrawn_at from public.playlist25_proposals where id={pid};") == timestamp
        for _ in range(10):
            assert rpc("playlist25_identity", "9" * 64, "", None)["status"] == 422
        assert rpc("playlist25_identity", "9" * 64, "", None)["status"] == 429, "Failed actions must still consume their rate budget"

        unlimited = rpc("playlist25_admin_publish", {"max_proposals": 0}, [], role="service_role")
        assert unlimited["settings"]["max_proposals"] == 0
        for invalid_limit in (-1, 0.5, 101, "0", True, None):
            assert rpc("playlist25_admin_publish", {"max_proposals": invalid_limit}, [], role="service_role")["status"] == 422
            assert sql("select max_proposals from public.playlist25_settings where singleton;") == "0", "Invalid limit must not change the existing setting"
        # Old settings remain readable but no longer reject accumulated objects.
        sql("update public.playlist25_settings set max_total_proposals=1,max_total_votes=1,max_guests=1 where singleton;")
        unlimited_token, volume_token = "6" * 64, "5" * 64
        unlimited_guest = rpc("playlist25_identity", unlimited_token, "Sans limite", None)
        assert unlimited_guest.get("id"), "Historical guest count must not prevent identity creation"
        for number in range(12):
            assert rpc("playlist25_propose", unlimited_token, "manual", f"Sans limite {number}", "", None).get("item")
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            continued = list(pool.map(lambda number: rpc("playlist25_propose", unlimited_token, "manual", f"Ajout concurrent {number}", "", None), range(3)))
        assert all(result.get("item") for result in continued)
        assert sql(f"select count(*) from public.playlist25_proposals where guest_id={literal(unlimited_guest['id'])}::uuid;") == "15"
        assert rpc("playlist25_propose", unlimited_token, "manual", "Trop rapide", "", None)["status"] == 429, "Short-term rate protection must remain active with unlimited history"
        volume_guest = rpc("playlist25_identity", volume_token, "Grand catalogue", None)
        assert volume_guest.get("id")
        sql("insert into public.playlist25_sources(title,artist) select 'Volume historique '||number,'' from generate_series(1,20000) number; "
            "insert into public.playlist25_proposals(source_id,guest_id) "
            f"select id,{literal(volume_guest['id'])}::uuid from public.playlist25_sources where title like 'Volume historique %';")
        after_volume = rpc("playlist25_propose", volume_token, "manual", "Après vingt mille musiques", "", None)
        assert after_volume.get("item"), "Historical proposal count must not prevent another addition"
        assert rpc("playlist25_vote", volume_token, after_volume["item"]["id"], True)["votes"] == 1, "Historical vote count must not prevent a new vote"
        assert sql(f"select jsonb_array_length(public.playlist25_list({literal(volume_token)},'','recent',true)->'items');", role="anon") == "20001", "Public listing must not truncate the catalogue"
        assert int(sql("select jsonb_array_length(public.playlist25_snapshot()->'proposals');", role="service_role")) > 20000, "Snapshot must include every proposal"
        before_migration = sql("select (select count(*) from public.playlist25_guests),(select count(*) from public.playlist25_sources),(select count(*) from public.playlist25_proposals),(select count(*) from public.playlist25_votes);")
        sql(migration)
        sql(migration)
        assert sql("select max_proposals from public.playlist25_settings where singleton;") == "0"
        assert sql("select (select count(*) from public.playlist25_guests),(select count(*) from public.playlist25_sources),(select count(*) from public.playlist25_proposals),(select count(*) from public.playlist25_votes);") == before_migration
        assert sql(f"select withdrawn_at from public.playlist25_proposals where id={pid};") == timestamp
        assert avatars([second['id']])['items'][0]['avatar_data']==avatar_b,"Migration must preserve profile photos"

        playlist_token='4'*64
        assert rpc('playlist25_identity',playlist_token,'Playlists',None).get('id')
        playlist=rpc('playlist25_propose',playlist_token,'playlist',None,'',None,'PL1234567890abc',None,'')['item']
        assert playlist['source_kind']=='playlist' and playlist['video_id'] is None and playlist['duration'] is None
        assert playlist['url']=='https://www.youtube.com/playlist?list=PL1234567890abc'
        duplicate_playlist=rpc('playlist25_propose',TOKEN_C,'playlist','Autre nom','',None,'PL1234567890abc',None,'')
        assert duplicate_playlist['duplicate']['id']==playlist['id']
        manual_named_playlist=rpc('playlist25_propose',playlist_token,'manual','Playlist YouTube proposée','',None)['item']
        assert manual_named_playlist['source_kind']=='manual' and manual_named_playlist['source_id']!=playlist['source_id']
        second_playlist=rpc('playlist25_propose',playlist_token,'playlist',None,'',None,'PL9999999990abc',None,'')['item']
        assert second_playlist['source_id']!=playlist['source_id']
        assert rpc('playlist25_vote',TOKEN_C,playlist['id'],True)['votes']==1
        assert rpc('playlist25_withdraw',playlist_token,playlist['id'])['withdrawn']
        readded_playlist=rpc('playlist25_propose',playlist_token,'playlist',None,'',None,'PL1234567890abc',None,'')['item']
        assert readded_playlist['id']!=playlist['id'] and readded_playlist['source_id']==playlist['source_id'] and readded_playlist['votes']==0
        assert sql(f"select count(*) from public.playlist25_votes where proposal_id={playlist['id']};")=='1'
        for invalid_list in ('RD1234567890abc','UU1234567890abc','../../private'):
            assert rpc('playlist25_propose',playlist_token,'playlist',None,'',None,invalid_list,None,'')['status']==422
        assert rpc('playlist25_propose',playlist_token,'playlist',None,'','dQw4w9WgXcQ','PL1234567890abc',None,'')['status']==422
        assert rpc('playlist25_propose',playlist_token,'playlist',None,'',None,'PL1234567890abc',123,'')['status']==422
        metadata_video=rpc('playlist25_propose',playlist_token,'youtube','Vrai titre connu','','abcDEF_1234',None,123.5,'Chaîne connue')['item']
        assert metadata_video['source_kind']=='video' and metadata_video['duration']==123.5 and metadata_video['channel']=='Chaîne connue'
        repeated_video=rpc('playlist25_propose',TOKEN_C,'youtube','Ne pas remplacer','','abcDEF_1234',None,456,'Autre chaîne')['duplicate']
        assert repeated_video['duration']==123.5 and repeated_video['title']=='Vrai titre connu' and repeated_video['channel']=='Chaîne connue'
        metadata_update={'id':metadata_video['id'],'state':'approved','title':metadata_video['title'],'artist':'','sort_order':0,'duration':180.25}
        assert rpc('playlist25_admin_publish',{},[metadata_update],role='service_role')['updated']==1
        for update in ({key:value for key,value in metadata_update.items() if key!='duration'},{**metadata_update,'duration':None}):
            assert rpc('playlist25_admin_publish',{},[update],role='service_role')['updated']==1
            assert sql(f"select duration from public.playlist25_sources where id={metadata_video['source_id']};")=='180.25'
        for invalid_duration in (-1,0,100000000,'NaN',True):
            assert rpc('playlist25_admin_publish',{},[{**metadata_update,'duration':invalid_duration}],role='service_role')['status']==422
            assert sql(f"select duration from public.playlist25_sources where id={metadata_video['source_id']};")=='180.25'
        for nonfinite in ('NaN','Infinity','-Infinity'):
            checked=json.loads(sql(f"select public.playlist25_propose({literal(TOKEN_C)},'youtube',null,'','abcDEF_1234',null,{literal(nonfinite)}::double precision,'');",role='anon'))
            assert checked['status']==422
        sql(migration)
        assert avatars([second['id']])['items'][0]['avatar_data']==avatar_b
        assert sql(f"select duration from public.playlist25_sources where id={metadata_video['source_id']};")=='180.25'
        assert sql(f"select state from public.playlist25_proposals where id={playlist['id']};")=='archived'

        # Separate identities with the same nickname must share one active exact
        # source, even when two independent database sessions propose together.
        race_tokens = ("2" * 64, "3" * 64)
        race_guests = [rpc("playlist25_identity", token, "Même pseudo", None) for token in race_tokens]
        assert race_guests[0]["id"] != race_guests[1]["id"]
        race_video = None
        for kind, video_id, playlist_id in (("youtube", "raceDup_001", None), ("playlist", None, "PLRaceDup0001abc")):
            def add_same_source(token):
                return rpc("playlist25_propose", token, kind, "Ajout simultané", "", video_id, playlist_id, None, "")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                simultaneous = list(pool.map(add_same_source, race_tokens))
            created = [result["item"] for result in simultaneous if "item" in result]
            duplicates = [result["duplicate"] for result in simultaneous if "duplicate" in result]
            assert len(created) == len(duplicates) == 1, "Concurrent exact sources created two active proposals"
            original, repeated = created[0], duplicates[0]
            assert original["id"] == repeated["id"] and original["source_id"] == repeated["source_id"]
            assert original["mine"] is True and repeated["mine"] is False, "Nickname must not confer ownership of a duplicate"
            assert sql(f"select count(*) from public.playlist25_sources where id={original['source_id']};") == "1"
            assert sql(f"select count(*) from public.playlist25_proposals where source_id={original['source_id']} and withdrawn_at is null;") == "1"
            if kind == "youtube":
                winner_index = next(index for index, result in enumerate(simultaneous) if "item" in result)
                race_video = original, race_tokens[winner_index], race_tokens[1 - winner_index]

        assert race_video is not None
        original, owner_token, other_token = race_video
        assert rpc("playlist25_propose", owner_token, "youtube", "Autre titre", "", "raceDup_001")["duplicate"]["mine"] is True
        assert rpc("playlist25_vote", other_token, original["id"], True)["votes"] == 1
        assert rpc("playlist25_withdraw", owner_token, original["id"])["withdrawn"] is True
        withdrawal_time = sql(f"select withdrawn_at from public.playlist25_proposals where id={original['id']};")
        # A stale visible state cannot override the permanent withdrawal marker.
        sql(f"update public.playlist25_proposals set state='approved' where id={original['id']};")
        assert all(row["id"] != original["id"] for row in rpc("playlist25_list", other_token, "Ajout simultané", "recent", False)["items"])
        replacement = rpc("playlist25_propose", other_token, "youtube", "Ajout simultané", "", "raceDup_001")["item"]
        assert replacement["id"] != original["id"] and replacement["source_id"] == original["source_id"] and replacement["votes"] == 0
        assert replacement["mine"] is True
        assert sql(f"select withdrawn_at from public.playlist25_proposals where id={original['id']};") == withdrawal_time
        assert sql(f"select count(*) from public.playlist25_votes where proposal_id={original['id']};") == "1"
        assert rpc("playlist25_propose", owner_token, "youtube", "Encore ce lien", "", "raceDup_001")["duplicate"]["mine"] is False
        distinct_recordings = [rpc("playlist25_propose", owner_token, "youtube", "Même titre de vidéo", "", video_id)["item"]
                               for video_id in ("raceVer_001", "raceVer_002")]
        assert distinct_recordings[0]["source_id"] != distinct_recordings[1]["source_id"], "Different recordings must not collapse on their title"
        print("PASS: PostgreSQL reusable migration, owner photos/bounded PNG/public batches, canonical playlists/re-add, finite durations/preservation, unlimited history beyond 20000, atomic global duplicates/ownership, distinct recordings, concurrency/rate protection, RLS, invitations, admin publication/merge and withdrawal history.")
    finally:
        subprocess.run(["docker", "stop", "-t", "1", name], capture_output=True)


if __name__ == "__main__":
    main()
