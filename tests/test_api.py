from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from playlist25 import db
from playlist25.config import Settings
from playlist25.guest import create_guest_app


@pytest.fixture
def cfg(tmp_path):
    result=Settings(data_dir=tmp_path/'data',session_secret='test-signing-secret-at-least-32-characters',admin_password='test-admin-password',min_free_mb=0)
    db.init_db(result)
    return result


def guest(cfg,nickname='Alice'):
    client=TestClient(create_guest_app(cfg))
    session=client.get('/api/session').json()
    headers={'X-CSRF-Token':session['csrf_token']}
    response=client.post('/api/identity',json={'nickname':nickname},headers=headers)
    assert response.status_code==200,response.text
    return client,headers,response.json()['guest']


def manual(client,headers,title='Un titre',artist='Artiste'):
    response=client.post('/api/proposals',json={'kind':'manual','title':title,'artist':artist},headers=headers)
    assert response.status_code==200,response.text
    return response.json()


def test_public_identity_csrf_and_isolated_surface(cfg):
    client=TestClient(create_guest_app(cfg))
    assert client.post('/api/identity',json={'nickname':'Alice'}).status_code==403
    client,headers,identity=guest(cfg,'Élise <script>')
    assert client.get('/api/session').json()['guest']['id']==identity['id']
    item=manual(client,headers,'Été à Paris')['item']
    assert item['nickname']=='Élise <script>'
    assert 'file_path' not in item and 'guest_id' not in item
    for path in ['/api/dashboard','/api/jobs','/api/library','/api/library/1/audio','/admin','/static/admin.html','/static/admin.js','/data/playlist.sqlite3','/.env.local','/api/exports/anything/zip']:
        assert client.get(path).status_code==404,path
    assert client.delete(f"/api/proposals/{item['id']}").status_code in (404,405)
    assert client.post('/api/downloads',json={'source_ids':[item['source_id']]}).status_code==404


def test_same_ip_distinct_sessions_votes_concurrent_and_retraction(cfg):
    alice,ah,aid=guest(cfg)
    bob,bh,bid=guest(cfg,'Bob')
    assert aid['id']!=bid['id']
    proposal=manual(alice,ah)['item']
    endpoint=f"/api/proposals/{proposal['id']}/vote"
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses=list(pool.map(lambda _:bob.post(endpoint,json={'active':True},headers=bh),range(8)))
    assert all(response.status_code==200 for response in responses)
    assert alice.post(endpoint,json={'active':True},headers=ah).json()['votes']==2
    assert bob.post(endpoint,json={'active':False},headers=bh).json()['votes']==1
    assert bob.post(endpoint,json={'active':False},headers=bh).json()['votes']==1
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM votes').fetchone()[0]==1


def test_proposals_quota_similar_and_manual_duplicate(cfg):
    alice,headers,_=guest(cfg)
    first=manual(alice,headers,'Song Studio','Singer')['item']
    duplicate=manual(alice,headers,' song studio ','SINGER')
    assert duplicate['duplicate']['id']==first['id']
    similar=manual(alice,headers,'Song Studio Live','Singer')
    assert similar['item']['id']!=first['id']
    with db.connect(cfg) as conn: db.set_setting(conn,'max_proposals',2)
    response=alice.post('/api/proposals',json={'kind':'manual','title':'Third'},headers=headers)
    assert response.status_code==429
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT count(*) FROM proposals').fetchone()[0]==2


def test_links_canonical_duplicates_and_real_search_provider_contract(cfg,monkeypatch):
    from playlist25 import providers
    entry={'video_id':'BaW_jenozKc','url':'https://www.youtube.com/watch?v=BaW_jenozKc','title':'Test vidéo','channel':'Une chaîne','duration':10,'available':True}
    monkeypatch.setattr(providers,'analyze',lambda *a,**k:{'entries':[entry]})
    monkeypatch.setattr(providers,'search',lambda *a,**k:[entry])
    alice,ah,_=guest(cfg)
    bob,bh,_=guest(cfg,'Bob')
    first=alice.post('/api/proposals',json={'kind':'youtube','url':'https://youtu.be/BaW_jenozKc?t=20'},headers=ah)
    assert first.status_code==200,first.text
    second=bob.post('/api/proposals',json={'kind':'youtube','url':'https://www.youtube.com/watch?v=BaW_jenozKc&feature=share'},headers=bh)
    assert second.status_code==200,second.text
    assert second.json()['duplicate']['id']==first.json()['item']['id']
    assert alice.get('/api/search?q=test').json()['items'][0]['channel']=='Une chaîne'
    assert not alice.get('/api/search?q=test').json()['items'][0]['artist']


def test_offline_mode_requires_no_external_network_and_url_rejection(cfg,monkeypatch):
    from playlist25 import providers
    def forbidden(*a,**k): raise AssertionError('network used in offline mode')
    monkeypatch.setattr(providers,'analyze',forbidden)
    monkeypatch.setattr(providers,'search',forbidden)
    with db.connect(cfg) as conn: db.set_setting(conn,'offline_mode',True)
    alice,headers,_=guest(cfg)
    assert manual(alice,headers,'Soirée sans réseau')['item']['title']=='Soirée sans réseau'
    assert alice.get('/api/search?q=test').status_code==503
    response=alice.post('/api/proposals',json={'kind':'youtube','url':'https://youtu.be/BaW_jenozKc'},headers=headers)
    assert response.status_code==200,response.text
    assert response.json()['item']['title']=='Vidéo YouTube (BaW_jenozKc)'
    for malicious in ['http://127.0.0.1:8001','http://169.254.169.254/latest/meta-data','file:///etc/passwd','https://www.youtube.com.evil.test/watch?v=BaW_jenozKc','https://www.youtube.com@evil.test/watch?v=BaW_jenozKc']:
        r=alice.post('/api/proposals',json={'kind':'youtube','url':malicious},headers=headers)
        assert r.status_code in (400,422),r.text
    assert alice.get('/api/qr?scope=local').headers['content-type']=='image/png'


def test_search_error_honest_and_link_still_saved(cfg,monkeypatch):
    from playlist25 import providers
    def blocked(*a,**k): raise providers.SourceError('YouTube inaccessible',status=503)
    monkeypatch.setattr(providers,'search',blocked)
    monkeypatch.setattr(providers,'analyze',blocked)
    client,headers,_=guest(cfg)
    response=client.get('/api/search?q=test')
    assert response.status_code==503 and 'items' not in response.json()
    response=client.post('/api/proposals',json={'kind':'youtube','url':'https://youtu.be/BaW_jenozKc','title':'Titre saisi'},headers=headers)
    assert response.status_code==200,response.text
    assert response.json()['item']['title']=='Titre saisi'


def test_invitation_code_one_session_and_closed_settings(cfg):
    import hashlib
    with db.connect(cfg) as conn:
        db.set_setting(conn,'invitation_required',True)
        conn.execute('INSERT INTO invitations(code_hash,created_at) VALUES(?,?)',(hashlib.sha256(b'CODE-TEST').hexdigest(),db.now()))
    client=TestClient(create_guest_app(cfg)); headers={'X-CSRF-Token':client.get('/api/session').json()['csrf_token']}
    assert client.post('/api/identity',json={'nickname':'A'},headers=headers).status_code==403
    assert client.post('/api/identity',json={'nickname':'A','invitation_code':'CODE-TEST'},headers=headers).status_code==200
    other=TestClient(create_guest_app(cfg)); other_h={'X-CSRF-Token':other.get('/api/session').json()['csrf_token']}
    assert other.post('/api/identity',json={'nickname':'B','invitation_code':'CODE-TEST'},headers=other_h).status_code==400
    item=manual(client,headers)['item']
    with db.connect(cfg) as conn:
        db.set_setting(conn,'proposals_open',False); db.set_setting(conn,'votes_open',False)
    assert client.post('/api/proposals',json={'kind':'manual','title':'Other'},headers=headers).status_code==403
    assert client.post(f"/api/proposals/{item['id']}/vote",json={'active':True},headers=headers).status_code==403


def test_withdraw_owner_is_session_not_shared_nickname_and_requires_csrf(cfg):
    owner,owner_headers,owner_identity=guest(cfg,'Même pseudo')
    other,other_headers,other_identity=guest(cfg,'Même pseudo')
    assert owner_identity['id']!=other_identity['id']
    proposal=manual(owner,owner_headers,'Mon erreur')['item']
    assert proposal['mine'] is True
    owner_list=owner.get('/api/proposals').json()['items']
    other_list=other.get('/api/proposals').json()['items']
    assert owner_list[0]['mine'] is True
    assert other_list[0]['mine'] is False
    assert owner.get('/api/proposals?mine=true').json()['items'][0]['mine'] is True
    assert other.get('/api/proposals?mine=true').json()['items']==[]
    assert 'guest_id' not in other_list[0]
    endpoint=f"/api/proposals/{proposal['id']}/withdraw"
    assert owner.post(endpoint,json={}).status_code==403
    # A matching nickname, known UUID, or forged request body cannot establish ownership.
    assert other.post(endpoint,json={'guest_id':owner_identity['id']},headers=other_headers).status_code==404
    anonymous=TestClient(create_guest_app(cfg))
    anonymous_headers={'X-CSRF-Token':anonymous.get('/api/session').json()['csrf_token']}
    assert anonymous.get('/api/proposals').json()['items'][0]['mine'] is False
    assert anonymous.post(endpoint,json={},headers=anonymous_headers).status_code==401
    for missing in (0,-1,999999,10**100):
        assert owner.post(f'/api/proposals/{missing}/withdraw',json={},headers=owner_headers).status_code==404
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT state FROM proposals WHERE id=?',(proposal['id'],)).fetchone()[0]=='pending'


@pytest.mark.parametrize('state',['pending','approved'])
def test_withdraw_soft_archive_keeps_votes_audio_jobs_and_is_idempotent(cfg,state):
    owner,owner_headers,_=guest(cfg)
    voter,voter_headers,_=guest(cfg,'Bob')
    proposal=manual(owner,owner_headers,'Proposition retirée')['item']
    proposal_id,source_id=proposal['id'],proposal['source_id']
    vote_endpoint=f'/api/proposals/{proposal_id}/vote'
    assert voter.post(vote_endpoint,json={'active':True},headers=voter_headers).json()['votes']==1
    audio=cfg.audio_dir/'kept-test.mp3'
    audio.write_bytes(b'fixture preserved by withdrawal')
    with db.connect(cfg) as conn:
        conn.execute('UPDATE proposals SET state=? WHERE id=?',(state,proposal_id))
        conn.execute("UPDATE sources SET file_path=?,status='available' WHERE id=?",(audio.name,source_id))
        conn.execute("INSERT INTO jobs(source_id,status,progress,attempts,created_at,updated_at) VALUES(?,'available',100,1,?,?)",(source_id,db.now(),db.now()))
        before_source=dict(conn.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone())
        before_job=dict(conn.execute('SELECT * FROM jobs WHERE source_id=?',(source_id,)).fetchone())
        db.set_setting(conn,'proposals_open',False)
        db.set_setting(conn,'votes_open',False)
        db.set_setting(conn,'invitation_required',True)
    endpoint=f'/api/proposals/{proposal_id}/withdraw'
    first=owner.post(endpoint,json={},headers=owner_headers)
    assert first.status_code==200,first.text
    assert first.json()['withdrawn'] is True and first.json()['id']==proposal_id
    with db.connect(cfg) as conn:
        withdrawn_at=conn.execute('SELECT withdrawn_at FROM proposals WHERE id=?',(proposal_id,)).fetchone()[0]
        assert withdrawn_at
    with ThreadPoolExecutor(max_workers=6) as pool:
        repeated=list(pool.map(lambda _:owner.post(endpoint,json={},headers=owner_headers),range(6)))
    assert all(response.status_code==200 for response in repeated)
    assert owner.get('/api/proposals').json()['items']==[]
    assert owner.get('/api/proposals?mine=true').json()['items']==[]
    assert voter.get('/api/proposals').json()['items']==[]
    with db.connect(cfg) as conn:
        row=conn.execute('SELECT * FROM proposals WHERE id=?',(proposal_id,)).fetchone()
        assert row['state']=='archived' and row['withdrawn_at']==withdrawn_at
        assert dict(conn.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone())==before_source
        assert dict(conn.execute('SELECT * FROM jobs WHERE source_id=?',(source_id,)).fetchone())==before_job
        assert conn.execute('SELECT COUNT(*) FROM votes WHERE proposal_id=?',(proposal_id,)).fetchone()[0]==1
        history=db.list_proposals(conn,include_hidden=True)
        assert len(history)==1 and history[0]['state']=='archived' and history[0]['votes']==1
        db.set_setting(conn,'invitation_required',False)
        db.set_setting(conn,'votes_open',True)
    assert voter.post(vote_endpoint,json={'active':True},headers=voter_headers).status_code==404
    assert voter.post(vote_endpoint,json={'active':False},headers=voter_headers).status_code==404
    assert audio.read_bytes()==b'fixture preserved by withdrawal'


@pytest.mark.parametrize('kind',['manual','youtube'])
def test_withdraw_releases_quota_and_same_source_can_be_proposed_with_fresh_history(cfg,kind):
    owner,headers,_=guest(cfg)
    voter,voter_headers,_=guest(cfg,'Bob')
    with db.connect(cfg) as conn:
        db.set_setting(conn,'max_proposals',1)
        db.set_setting(conn,'offline_mode',True)
    payload=({'kind':'manual','title':'Un titre à remettre','artist':'Artiste'} if kind=='manual'
             else {'kind':'youtube','url':'https://youtu.be/BaW_jenozKc'})
    first=owner.post('/api/proposals',json=payload,headers=headers)
    assert first.status_code==200,first.text
    old=first.json()['item']
    assert voter.post(f"/api/proposals/{old['id']}/vote",json={'active':True},headers=voter_headers).status_code==200
    assert owner.post('/api/proposals',json={'kind':'manual','title':'Quota dépassé'},headers=headers).status_code==429
    assert owner.post(f"/api/proposals/{old['id']}/withdraw",json={},headers=headers).status_code==200
    replacement=owner.post('/api/proposals',json=payload,headers=headers)
    assert replacement.status_code==200,replacement.text
    new=replacement.json()['item']
    assert new['id']!=old['id'] and new['source_id']==old['source_id']
    assert new['mine'] is True and new['votes']==0
    assert [item['id'] for item in owner.get('/api/proposals').json()['items']]==[new['id']]
    # Retrying the old withdrawal cannot affect its replacement or erase old votes.
    assert owner.post(f"/api/proposals/{old['id']}/withdraw",json={},headers=headers).status_code==200
    with db.connect(cfg) as conn:
        rows=conn.execute('SELECT id,state,withdrawn_at FROM proposals ORDER BY id').fetchall()
        assert len(rows)==2
        assert rows[0]['state']=='archived' and rows[0]['withdrawn_at']
        assert rows[1]['state']=='pending' and rows[1]['withdrawn_at'] is None
        assert conn.execute('SELECT COUNT(*) FROM votes WHERE proposal_id=?',(old['id'],)).fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==1


@pytest.mark.parametrize('state',['rejected','archived'])
def test_withdraw_does_not_turn_moderation_into_permission_to_repropose(cfg,state):
    owner,headers,_=guest(cfg)
    with db.connect(cfg) as conn: db.set_setting(conn,'offline_mode',True)
    payload={'kind':'youtube','url':'https://youtu.be/BaW_jenozKc'}
    proposal=owner.post('/api/proposals',json=payload,headers=headers).json()['item']
    with db.connect(cfg) as conn:
        conn.execute('UPDATE proposals SET state=? WHERE id=?',(state,proposal['id']))
    response=owner.post(f"/api/proposals/{proposal['id']}/withdraw",json={},headers=headers)
    assert response.status_code==404
    assert owner.post('/api/proposals',json=payload,headers=headers).status_code==409
    with db.connect(cfg) as conn:
        row=conn.execute('SELECT state,withdrawn_at FROM proposals WHERE id=?',(proposal['id'],)).fetchone()
        assert row['state']==state and row['withdrawn_at'] is None
