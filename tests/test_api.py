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
