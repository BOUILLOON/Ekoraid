from pathlib import Path
import copy
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from playlist25 import db, providers
from playlist25.admin import create_admin_app
from playlist25.config import Settings
from playlist25.guest import create_guest_app

@pytest.fixture
def cfg(tmp_path):
    config=Settings(data_dir=tmp_path/'data',session_secret='test-signing-admin-secret',admin_password='test-admin-password',min_free_mb=0)
    db.init_db(config)
    return config

def admin(cfg):
    client=TestClient(create_admin_app(cfg)); csrf=client.get('/api/session').json()['csrf_token']
    result=client.post('/api/login',json={'password':cfg.admin_password},headers={'X-CSRF-Token':csrf})
    assert result.status_code==200,result.text
    return client,{'X-CSRF-Token':result.json()['csrf_token']}

def test_auth_and_session_separation(cfg):
    client=TestClient(create_admin_app(cfg))
    assert client.get('/api/dashboard').status_code==401
    assert client.get('/api/library/1/audio').status_code==401
    assert client.post('/api/login',json={'password':cfg.admin_password}).status_code==403
    csrf=client.get('/api/session').json()['csrf_token']
    assert client.post('/api/login',json={'password':'wrong'},headers={'X-CSRF-Token':csrf}).status_code==401
    client,headers=admin(cfg)
    assert client.get('/api/dashboard').status_code==200
    assert client.patch('/api/settings',json={'offline_mode':True}).status_code==403
    assert client.patch('/api/settings',json={'offline_mode':True},headers=headers).status_code==200
    public=TestClient(create_guest_app(cfg))
    public.cookies.update(client.cookies)
    assert public.get('/api/session').json()['guest'] is None
    assert client.post('/api/logout',headers=headers).status_code==200
    assert client.get('/api/dashboard').status_code==401


def analysis_fixture():
    entry={'position':1,'video_id':'BaW_jenozKc','url':'https://www.youtube.com/watch?v=BaW_jenozKc','title':'Morceau de test','artist':'','channel':'Chaîne de test','duration':3,'thumbnail':'','available':True,'reason':''}
    second=dict(entry,position=2)
    absent={'position':3,'video_id':None,'url':None,'title':'Vidéo privée','available':False,'reason':'Privée ou supprimée'}
    return {'title':'Playlist accentuée','kind':'playlist','source_url':'https://www.youtube.com/playlist?list=PL_TEST12345','complete':False,'warnings':['Un élément indisponible'], 'entries':[entry,second,absent]}


def test_playlist_whole_flow_order_unavailable_dedup_rights_and_forgery(cfg,monkeypatch):
    fixture=analysis_fixture()
    monkeypatch.setattr(providers,'analyze',lambda *a,**k:copy.deepcopy(fixture))
    client,headers=admin(cfg)
    result=client.post('/api/playlists/analyze',json={'url':fixture['source_url']},headers=headers)
    assert result.status_code==200,result.text
    analysis=result.json()
    assert analysis['complete'] is False and analysis['warnings']
    forged=copy.deepcopy(analysis); forged['entries'][0]['url']='http://169.254.169.254/'
    assert client.post('/api/playlists/import',json={'analysis':forged,'positions':[1,2]},headers=headers).status_code==422
    assert client.post('/api/playlists/import',json={'analysis':analysis,'positions':[1,2],'download':True},headers=headers).status_code==422
    result=client.post('/api/playlists/import',json={'analysis':analysis,'positions':[1,2],'download':True,'rights_confirmed':True},headers=headers)
    assert result.status_code==200,result.text
    ids=result.json()['source_ids']; assert len(ids)==1
    assert len(result.json()['jobs']['queued'])==1
    stored=client.get('/api/playlists').json()['items'][0]
    assert [row['position'] for row in stored['entries']]==[1,2,3]
    assert stored['entries'][0]['source_id']==stored['entries'][1]['source_id']
    assert stored['entries'][2]['available']==0
    assert client.post('/api/downloads',json={'source_ids':ids,'rights_confirmed':True},headers=headers).json()['queued']==[]
    with db.connect(cfg) as conn: assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
    other,other_headers=admin(cfg)
    assert other.post('/api/playlists/import',json={'analysis':analysis,'positions':[1]},headers=other_headers).status_code==410
    assert client.post('/api/queue',json={'paused':True},headers=headers).status_code==200
    assert client.get('/api/jobs').json()['paused'] is True


def test_ambiguous_url_choice_and_offline_downloads(cfg,monkeypatch):
    def choose(*a,**k): raise providers.SourceError('Choisissez',code='need_choice',need_choice=True,status=409)
    monkeypatch.setattr(providers,'analyze',choose)
    client,headers=admin(cfg)
    response=client.post('/api/playlists/analyze',json={'url':'https://www.youtube.com/watch?v=BaW_jenozKc&list=PL_TEST12345'},headers=headers)
    assert response.status_code==409 and response.json()['detail']['need_choice']
    client.patch('/api/settings',json={'offline_mode':True},headers=headers)
    response=client.post('/api/playlists/analyze',json={'url':'https://youtu.be/BaW_jenozKc'},headers=headers)
    assert response.status_code==503
    assert client.post('/api/downloads',json={'source_ids':[1],'rights_confirmed':True},headers=headers).status_code==503


def test_moderation_metadata_and_merge_preserve_votes_and_cloud_mapping(cfg):
    with db.connect(cfg) as conn:
        for id in ('a','b'): conn.execute('INSERT INTO guests VALUES(?,?,?)',(id,id,db.now()))
        s1=db.upsert_source(conn,{'title':'Ancien titre'}); s2=db.upsert_source(conn,{'title':'Autre titre'})
        p1=conn.execute('INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,?,?)',(s1,'a',db.now())).lastrowid
        p2=conn.execute('INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,?,?)',(s2,'b',db.now())).lastrowid
        for p,g in ((p1,'a'),(p2,'a'),(p2,'b')): conn.execute('INSERT INTO votes VALUES(?,?,?)',(p,g,db.now()))
        conn.execute('INSERT INTO cloud_map VALUES(?,?,?)',('proposal','test:remote',str(p2)))
    client,headers=admin(cfg)
    result=client.patch(f'/api/proposals/{p1}',json={'title':'Été, nouveau titre','artist':'Dalida','state':'approved','sort_order':5},headers=headers)
    assert result.status_code==200,result.text
    assert result.json()['item']['title']=='Été, nouveau titre'
    assert result.json()['item']['state']=='approved'
    assert client.post('/api/proposals/merge',json={'keep_id':p1,'merge_id':p2},headers=headers).status_code==422
    result=client.post('/api/proposals/merge',json={'keep_id':p1,'merge_id':p2,'confirmed':True},headers=headers)
    assert result.status_code==200 and result.json()['votes']==2
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT state FROM proposals WHERE id=?',(p2,)).fetchone()[0]=='archived'
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==2
        assert conn.execute('SELECT local_id FROM cloud_map').fetchone()[0]==str(p1)


def withdrawn_fixture(cfg):
    with db.connect(cfg) as conn:
        conn.execute('INSERT INTO guests VALUES(?,?,?)',('withdraw-owner','Invitée',db.now()))
        source=db.upsert_source(conn,{'title':'Demande retirée'})
        withdrawn_at=db.now()
        removed=conn.execute('INSERT INTO proposals(source_id,guest_id,state,created_at,withdrawn_at) VALUES(?,?,?,?,?)',
                             (source,'withdraw-owner','archived',db.now(),withdrawn_at)).lastrowid
        ordinary_source=db.upsert_source(conn,{'title':'Demande encore présente'})
        ordinary=conn.execute('INSERT INTO proposals(source_id,created_at) VALUES(?,?)',(ordinary_source,db.now())).lastrowid
    return removed,ordinary,withdrawn_at


@pytest.mark.parametrize('state',['pending','approved','rejected'])
def test_guest_withdrawal_cannot_be_restored_by_admin(cfg,state):
    removed,_,withdrawn_at=withdrawn_fixture(cfg)
    client,headers=admin(cfg)
    result=client.patch(f'/api/proposals/{removed}',json={'state':state,'title':'Tentative de correction'},headers=headers)
    assert result.status_code==409,result.text
    assert 'retirée' in result.json()['detail']
    item=next(item for item in client.get('/api/proposals').json()['items'] if item['id']==removed)
    assert item['state']=='archived' and item['withdrawn_at']==withdrawn_at
    assert item['title']=='Demande retirée'


def test_withdrawn_metadata_remains_editable_without_losing_history(cfg):
    removed,_,withdrawn_at=withdrawn_fixture(cfg)
    client,headers=admin(cfg)
    result=client.patch(f'/api/proposals/{removed}',json={'title':'Titre corrigé','artist':'Artiste précisé'},headers=headers)
    assert result.status_code==200,result.text
    assert result.json()['item']['title']=='Titre corrigé'
    assert result.json()['item']['artist']=='Artiste précisé'
    assert result.json()['item']['state']=='archived'
    assert result.json()['item']['withdrawn_at']==withdrawn_at
    assert client.patch(f'/api/proposals/{removed}',json={'state':'archived'},headers=headers).status_code==200


def test_bulk_moderation_rejects_entire_mixed_selection_and_keeps_withdrawal(cfg):
    removed,ordinary,withdrawn_at=withdrawn_fixture(cfg)
    client,headers=admin(cfg)
    payload={'proposal_ids':[ordinary,removed],'state':'approved'}
    assert client.patch('/api/proposals',json=payload).status_code==403
    for state in ('pending','approved','rejected'):
        result=client.patch('/api/proposals',json={**payload,'state':state},headers=headers)
        assert result.status_code==409,result.text
        with db.connect(cfg) as conn:
            assert conn.execute('SELECT state FROM proposals WHERE id=?',(ordinary,)).fetchone()[0]=='pending'
            row=conn.execute('SELECT state,withdrawn_at FROM proposals WHERE id=?',(removed,)).fetchone()
            assert tuple(row)==('archived',withdrawn_at)
    result=client.patch('/api/proposals',json={'proposal_ids':[ordinary,ordinary],'state':'approved'},headers=headers)
    assert result.status_code==200 and result.json()['count']==1
    result=client.patch('/api/proposals',json={**payload,'state':'archived'},headers=headers)
    assert result.status_code==200 and result.json()['count']==2
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM proposals WHERE state="archived"').fetchone()[0]==2
        assert conn.execute('SELECT withdrawn_at FROM proposals WHERE id=?',(removed,)).fetchone()[0]==withdrawn_at


def test_bulk_moderation_missing_proposal_does_not_change_existing_one(cfg):
    _,ordinary,_=withdrawn_fixture(cfg)
    client,headers=admin(cfg)
    result=client.patch('/api/proposals',json={'proposal_ids':[ordinary,9999],'state':'approved'},headers=headers)
    assert result.status_code==404
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT state FROM proposals WHERE id=?',(ordinary,)).fetchone()[0]=='pending'


def test_real_audio_upload_rename_pack_download_and_guest_cannot_fetch(cfg):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'): pytest.skip('FFmpeg required')
    original=cfg.temp_dir/'test-original.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-y',str(original)],check=True)
    initial=original.read_bytes()
    client,headers=admin(cfg)
    response=client.post('/api/library/import',files={'file':('téste.wav',initial,'audio/wav')},data={'title':'Été : / Test','artist':'Artiste'},headers=headers)
    assert response.status_code==200,response.text
    body=response.json(); item=body.get('item',body); source_id=item['id']
    assert original.read_bytes()==initial
    result=client.patch(f'/api/library/{source_id}',json={'title':'Nouveau titre','artist':'Autre artiste'},headers=headers)
    assert result.status_code==200,result.text
    assert client.get(f'/api/library/{source_id}/audio').status_code==200
    result=client.post('/api/exports',json={'source_ids':[source_id],'make_zip':True},headers=headers)
    assert result.status_code==200,result.text
    exported=result.json()
    assert exported['count']==1
    download=client.get(exported['download_url'])
    assert download.status_code==200 and download.content[:2]==b'PK'
    assert TestClient(create_guest_app(cfg)).get(exported['download_url']).status_code==404
    assert client.get('/api/catalogue?format=csv').status_code==200
    assert client.get('/api/catalogue?format=json').status_code==200
