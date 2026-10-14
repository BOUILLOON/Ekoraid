from pathlib import Path
import copy
import shutil
import sqlite3
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


def test_private_search_requires_admin_csrf_and_is_read_only(cfg,monkeypatch):
    calls=[]
    entry=analysis_fixture()['entries'][0]
    def search(query,config,*,offset,limit):
        calls.append((query,offset,limit))
        return {'items':[copy.deepcopy(entry)],'has_more':True,'next_offset':11}
    monkeypatch.setattr(providers,'search_page',search)
    client=TestClient(create_admin_app(cfg))
    assert client.post('/api/search',json={'query':'Dalida'}).status_code==401
    client,headers=admin(cfg)
    assert client.post('/api/search',json={'query':'Dalida'}).status_code==403
    with db.connect(cfg) as conn:
        before={name:conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in ('sources','proposals','jobs','votes')}
    response=client.post('/api/search',json={'query':'  Dalida live  ','offset':6},headers=headers)
    assert response.status_code==200,response.text
    assert response.json()=={'items':[entry],'has_more':True,'next_offset':11}
    assert calls==[('Dalida live',6,min(cfg.search_limit,10))]
    with db.connect(cfg) as conn:
        assert {name:conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in before}==before
    for body in ({'query':'  '},{'query':'a\nb'},{'query':'Dalida','offset':True}):
        assert client.post('/api/search',json=body,headers=headers).status_code==422
    assert calls==[('Dalida live',6,min(cfg.search_limit,10))]
    assert client.patch('/api/settings',json={'offline_mode':True},headers=headers).status_code==200
    assert client.post('/api/search',json={'query':'Dalida'},headers=headers).status_code==503
    assert calls==[('Dalida live',6,min(cfg.search_limit,10))]


def test_private_search_reports_provider_failure_without_changing_proposal(cfg,monkeypatch):
    def unavailable(*args,**kwargs):
        raise providers.SourceError('La recherche YouTube est temporairement indisponible.',status=503)
    monkeypatch.setattr(providers,'search_page',unavailable)
    client,headers=admin(cfg)
    result=client.post('/api/search',json={'query':'Dalida'},headers=headers)
    assert result.status_code==503 and 'temporairement indisponible' in result.json()['detail']
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0


def test_manual_proposal_association_reverifies_metadata_preserves_author_votes_and_creates_no_job(cfg,monkeypatch):
    with db.connect(cfg) as conn:
        conn.execute('INSERT INTO guests(id,nickname,created_at) VALUES(?,?,?)',('manual-owner','Camille',db.now()))
        source=db.upsert_source(conn,{'title':'Demande sans lien','artist':'Dalida'})
        proposal=conn.execute('INSERT INTO proposals(source_id,guest_id,state,sort_order,created_at) VALUES(?,?,?,?,?)',(source,'manual-owner','approved',27,db.now())).lastrowid
        conn.execute('INSERT INTO votes(proposal_id,guest_id,created_at) VALUES(?,?,?)',(proposal,'manual-owner',db.now()))
        original=dict(conn.execute('SELECT * FROM proposals WHERE id=?',(proposal,)).fetchone())
    entry={**analysis_fixture()['entries'][0],'title':'Version studio exacte','artist':'Dalida','channel':'Chaîne officielle vérifiée','duration':226}
    calls=[]
    def analyze(url,config,mode=None):
        calls.append((url,mode))
        return {'kind':'video','entries':[copy.deepcopy(entry)],'warnings':[],'complete':True}
    monkeypatch.setattr(providers,'analyze',analyze)
    client,headers=admin(cfg)
    # Metadata from a browser search may not forge the verified duration.
    assert client.patch(f'/api/proposals/{proposal}',json={'url':entry['url'],'duration':1},headers=headers).status_code==422
    result=client.patch(f'/api/proposals/{proposal}',json={'url':entry['url'],'title':entry['title'],'artist':'Dalida'},headers=headers)
    assert result.status_code==200,result.text
    assert calls==[(entry['url'],'video')]
    item=result.json()['item']
    assert item['duration']==226 and item['channel']=='Chaîne officielle vérifiée'
    assert item['guest_id']=='manual-owner' and item['nickname']=='Camille' and item['votes']==1
    assert item['state']=='approved' and item['sort_order']==27
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
        assert conn.execute('SELECT title FROM sources WHERE id=?',(source,)).fetchone()[0]=='Demande sans lien'
        saved=dict(conn.execute('SELECT * FROM proposals WHERE id=?',(proposal,)).fetchone())
        assert all(saved[key]==original[key] for key in original if key!='source_id')


def test_associating_existing_audio_keeps_both_original_files_and_prepared_metadata(cfg,monkeypatch):
    entry={**analysis_fixture()['entries'][0],'title':'Titre de résultat YouTube','artist':'Artiste du résultat'}
    with db.connect(cfg) as conn:
        original_source=db.upsert_source(conn,{'title':'Ancienne demande et audio'})
        prepared=db.upsert_source(conn,{**entry,'title':'Titre déjà corrigé','artist':'Artiste déjà corrigé','duration':210})
        for source,filename in ((original_source,'original.mp3'),(prepared,'prepared.mp3')):
            (cfg.audio_dir/filename).write_bytes(b'local fixture, must remain untouched')
            conn.execute('UPDATE sources SET file_path=?,status=? WHERE id=?',(filename,'available',source))
        proposal=conn.execute('INSERT INTO proposals(source_id,created_at) VALUES(?,?)',(original_source,db.now())).lastrowid
    monkeypatch.setattr(providers,'analyze',lambda *args,**kwargs:{'kind':'video','entries':[copy.deepcopy(entry)]})
    monkeypatch.setattr('playlist25.library.rename_audio',lambda *args,**kwargs:pytest.fail('Associating a recording must not rename prepared files'))
    client,headers=admin(cfg)
    result=client.patch(f'/api/proposals/{proposal}',json={'url':entry['url'],'title':entry['title'],'artist':entry['artist']},headers=headers)
    assert result.status_code==200,result.text
    assert result.json()['item']['source_id']==prepared
    assert result.json()['item']['title']=='Titre déjà corrigé'
    assert result.json()['item']['artist']=='Artiste déjà corrigé'
    assert any('Renommer dans la bibliothèque' in warning for warning in result.json()['warnings'])
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
        assert conn.execute('SELECT file_path FROM sources WHERE id=?',(original_source,)).fetchone()[0]=='original.mp3'
        assert conn.execute('SELECT file_path FROM sources WHERE id=?',(prepared,)).fetchone()[0]=='prepared.mp3'
    assert (cfg.audio_dir/'original.mp3').read_bytes()==(cfg.audio_dir/'prepared.mp3').read_bytes()==b'local fixture, must remain untouched'


@pytest.mark.parametrize('unknown_duration',[None,0])
def test_dashboard_duration_counts_distinct_known_sources_and_reports_unknown(cfg,unknown_duration):
    with db.connect(cfg) as conn:
        known=db.upsert_source(conn,{'title':'Morceau connu','duration':245.5})
        unknown=db.upsert_source(conn,{'title':'Playlist encore inconnue','url':'https://www.youtube.com/playlist?list=PL_TEST12345','duration':unknown_duration})
        pending=db.upsert_source(conn,{'title':'Pas encore validé','duration':1000})
        for source,state in ((known,'approved'),(known,'approved'),(unknown,'approved'),(pending,'pending')):
            conn.execute('INSERT INTO proposals(source_id,state,created_at) VALUES(?,?,?)',(source,state,db.now()))
    client,_=admin(cfg)
    dashboard=client.get('/api/dashboard').json()
    assert dashboard['approved']==3
    assert dashboard['total_duration']==245.5
    assert dashboard['total_duration_known_count']==1
    assert dashboard['total_duration_unknown_count']==1


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
        for id in ('a','b'): conn.execute('INSERT INTO guests(id,nickname,created_at) VALUES(?,?,?)',(id,id,db.now()))
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
        conn.execute('INSERT INTO guests(id,nickname,created_at) VALUES(?,?,?)',('withdraw-owner','Invitée',db.now()))
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


def test_bulk_moderation_over_twenty_thousand_uses_one_atomic_transaction(cfg,monkeypatch):
    count=20001
    with db.connect(cfg) as conn:
        source=db.upsert_source(conn,{'title':'Catalogue de grande soirée'})
        conn.executemany('INSERT INTO proposals(source_id,created_at) VALUES(?,?)',[(source,db.now())]*count)
    original_connect=db.connect
    def limited_connection(config):
        connection=original_connect(config)
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,999)
        return connection
    monkeypatch.setattr(db,'connect',limited_connection)
    client,headers=admin(cfg)
    ids=list(range(1,count+1))
    result=client.patch('/api/proposals',json={'proposal_ids':ids,'state':'approved'},headers=headers)
    assert result.status_code==200,result.text
    assert result.json()['count']==count
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM proposals WHERE state="approved"').fetchone()[0]==count
        conn.execute('UPDATE proposals SET state="archived",withdrawn_at=? WHERE id=?',(db.now(),count))
    result=client.patch('/api/proposals',json={'proposal_ids':ids,'state':'pending'},headers=headers)
    assert result.status_code==409,result.text
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM proposals WHERE state="approved"').fetchone()[0]==count-1
        assert conn.execute('SELECT COUNT(*) FROM proposals WHERE state="pending"').fetchone()[0]==0


def test_playlist_over_twenty_thousand_preserves_all_positions_when_unlimited(cfg,monkeypatch):
    count=20001
    fixture=analysis_fixture()
    fixture['complete']=True
    fixture['warnings']=[]
    fixture['entries']=[dict(fixture['entries'][0],position=position) for position in range(1,count+1)]
    monkeypatch.setattr(providers,'analyze',lambda *a,**k:copy.deepcopy(fixture))
    client,headers=admin(cfg)
    result=client.post('/api/playlists/analyze',json={'url':fixture['source_url']},headers=headers)
    assert result.status_code==200,result.text
    token=result.json()['token']
    result=client.post('/api/playlists/import',json={'analysis':{'token':token},'positions':list(range(1,count+1))},headers=headers)
    assert result.status_code==200,result.text
    assert len(result.json()['source_ids'])==1
    with db.connect(cfg) as conn:
        assert tuple(conn.execute('SELECT COUNT(*),MIN(position),MAX(position),SUM(selected) FROM playlist_entries').fetchone())==(count,1,count,count)
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==1
    assert client.patch('/api/settings',json={'max_playlist_items':count-1},headers=headers).status_code==200
    assert client.post('/api/playlists/analyze',json={'url':fixture['source_url']},headers=headers).status_code==422
    assert client.patch('/api/settings',json={'max_playlist_items':0,'max_proposals':0},headers=headers).status_code==200


def test_large_download_and_export_selections_reach_business_validation_without_count_cap(cfg):
    client,headers=admin(cfg)
    ids=list(range(1,20002))
    for route,payload in (('/api/downloads',{'source_ids':ids,'rights_confirmed':True}),
                          ('/api/exports',{'source_ids':ids,'make_zip':True})):
        result=client.post(route,json=payload,headers=headers)
        assert result.status_code==404,result.text


def test_reorder_over_twenty_thousand_is_atomic_and_preserves_withdrawal_and_votes(cfg,monkeypatch):
    count=20001
    with db.connect(cfg) as conn:
        source=db.upsert_source(conn,{'title':'Grand catalogue à réordonner'})
        conn.executemany('INSERT INTO proposals(source_id,created_at) VALUES(?,?)',[(source,db.now())]*count)
        withdrawn_at=db.now()
        conn.execute('UPDATE proposals SET state="archived",withdrawn_at=? WHERE id=?',(withdrawn_at,count))
        conn.execute('INSERT INTO guests(id,nickname,created_at) VALUES(?,?,?)',('voter','Invitée',db.now()))
        conn.execute('INSERT INTO votes VALUES(?,?,?)',(1,'voter',db.now()))
    original_connect=db.connect
    def limited_connection(config):
        connection=original_connect(config)
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,999)
        return connection
    monkeypatch.setattr(db,'connect',limited_connection)
    client,headers=admin(cfg)
    ids=list(range(count,0,-1))
    assert client.put('/api/proposals/order',json={'proposal_ids':ids}).status_code==403
    result=client.put('/api/proposals/order',json={'proposal_ids':ids},headers=headers)
    assert result.status_code==200,result.text
    assert result.json()['count']==count
    with db.connect(cfg) as conn:
        assert [row[0] for row in conn.execute('SELECT id FROM proposals ORDER BY sort_order')]==ids
        assert tuple(conn.execute('SELECT state,withdrawn_at FROM proposals WHERE id=?',(count,)).fetchone())==('archived',withdrawn_at)
        assert conn.execute('SELECT COUNT(*) FROM proposals WHERE state="pending"').fetchone()[0]==count-1
        assert conn.execute('SELECT COUNT(*) FROM votes').fetchone()[0]==1
    assert client.put('/api/proposals/order',json={'proposal_ids':[1,1]},headers=headers).status_code==422
    assert client.put('/api/proposals/order',json={'proposal_ids':[True]},headers=headers).status_code==422
    result=client.put('/api/proposals/order',json={'proposal_ids':list(range(1,count))+[9999999]},headers=headers)
    assert result.status_code==404,result.text
    with db.connect(cfg) as conn:
        assert [row[0] for row in conn.execute('SELECT id FROM proposals ORDER BY sort_order')]==ids
    result=client.put('/api/proposals/order',json={'proposal_ids':[2,1]},headers=headers)
    assert result.status_code==200 and result.json()['count']==2


def playlist_proposal_fixture(cfg):
    fixture=analysis_fixture()
    with db.connect(cfg) as conn:
        direct=db.upsert_source(conn,fixture['entries'][0])
        playlist=db.upsert_source(conn,{'url':fixture['source_url'],'title':'Playlist de l’invitée'})
        proposal=conn.execute('INSERT INTO proposals(source_id,created_at) VALUES(?,?)',(playlist,db.now())).lastrowid
    return fixture,direct,playlist,proposal


def test_selected_guest_playlist_expands_only_after_rights_and_deduplicates_mixed_tracks(cfg,monkeypatch):
    fixture,direct,playlist,proposal=playlist_proposal_fixture(cfg)
    calls=[]
    def analyze(url,config,mode=None):
        calls.append((url,mode))
        return copy.deepcopy(fixture)
    monkeypatch.setattr(providers,'analyze',analyze)
    client,headers=admin(cfg)
    assert client.post('/api/downloads',json={'source_ids':[direct,playlist]},headers=headers).status_code==422
    assert calls==[]
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM playlists').fetchone()[0]==0
    result=client.post('/api/downloads',json={'source_ids':[direct,playlist],'rights_confirmed':True},headers=headers)
    assert result.status_code==200,result.text
    body=result.json()
    assert body['queued']==[direct]
    assert calls==[(fixture['source_url'],'playlist')]
    assert body['expanded_playlists'][0]['source_ids']==[direct]
    assert body['expanded_playlists'][0]['complete'] is False
    assert any('incomplète' in warning for warning in body['warnings'])
    assert any('indisponible' in warning for warning in body['warnings'])
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT source_id FROM proposals WHERE id=?',(proposal,)).fetchone()[0]==playlist
        assert conn.execute('SELECT video_id FROM sources WHERE id=?',(playlist,)).fetchone()[0] is None
        assert [tuple(row) for row in conn.execute('SELECT position,source_id,available,selected FROM playlist_entries ORDER BY position')]==[(1,direct,1,1),(2,direct,1,1),(3,None,0,0)]
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
    item=next(item for item in client.get('/api/proposals').json()['items'] if item['id']==proposal)
    assert item['source_kind']=='playlist' and item['playlist_source_ids']==[direct]
    repeated=client.post('/api/downloads',json={'source_ids':[playlist],'rights_confirmed':True},headers=headers)
    assert repeated.status_code==200 and repeated.json()['queued']==[]
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1


def test_inaccessible_selected_playlist_warns_without_losing_direct_track(cfg,monkeypatch):
    fixture,direct,playlist,_=playlist_proposal_fixture(cfg)
    def inaccessible(*args,**kwargs):
        raise providers.SourceError('Playlist privée ou inaccessible',code='unavailable',status=503)
    monkeypatch.setattr(providers,'analyze',inaccessible)
    client,headers=admin(cfg)
    result=client.post('/api/downloads',json={'source_ids':[playlist,direct],'rights_confirmed':True},headers=headers)
    assert result.status_code==200,result.text
    body=result.json()
    assert body['queued']==[direct] and body['expanded_playlists']==[]
    assert body['skipped'][0]['source_id']==playlist
    assert 'Playlist de l’invitée' in body['warnings'][0] and 'inaccessible' in body['warnings'][0]
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM playlists').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==2


def test_bad_playlist_provider_metadata_leaves_no_partial_import(cfg,monkeypatch):
    fixture,direct,playlist,_=playlist_proposal_fixture(cfg)
    fixture['entries'][1]['url']='https://example.com/not-a-video'
    monkeypatch.setattr(providers,'analyze',lambda *args,**kwargs:copy.deepcopy(fixture))
    client,headers=admin(cfg)
    result=client.post('/api/downloads',json={'source_ids':[playlist,direct],'rights_confirmed':True},headers=headers)
    assert result.status_code==200,result.text
    assert result.json()['queued']==[direct] and result.json()['warnings']
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM playlists').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM playlist_entries').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==2


def test_playlist_storage_failure_rolls_back_its_rows_and_keeps_other_selection(cfg,monkeypatch):
    fixture,direct,playlist,_=playlist_proposal_fixture(cfg)
    fixture['entries'][0].update(video_id='jNQXAC9IVRw',url='https://www.youtube.com/watch?v=jNQXAC9IVRw')
    monkeypatch.setattr(providers,'analyze',lambda *args,**kwargs:copy.deepcopy(fixture))
    original_upsert=db.upsert_source
    calls=[]
    def fail_second_entry(conn,entry):
        calls.append(entry)
        if len(calls)==2: raise ValueError('Métadonnée invalide à la seconde entrée')
        return original_upsert(conn,entry)
    monkeypatch.setattr(db,'upsert_source',fail_second_entry)
    client,headers=admin(cfg)
    result=client.post('/api/downloads',json={'source_ids':[playlist,direct],'rights_confirmed':True},headers=headers)
    assert result.status_code==200,result.text
    assert result.json()['queued']==[direct] and result.json()['warnings']
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM playlists').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM playlist_entries').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1


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


@pytest.mark.parametrize('route',['library','proposal','import'])
def test_admin_audio_change_failed_commit_preserves_original_files_and_rows(cfg,monkeypatch,route):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'): pytest.skip('FFmpeg required')
    original=cfg.temp_dir/'commit-test.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-y',str(original)],check=True)
    client,headers=admin(cfg)
    uploaded=client.post('/api/library/import',files={'file':('original.wav',original.read_bytes(),'audio/wav')},data={'title':'Original title'},headers=headers)
    assert uploaded.status_code==200,uploaded.text
    source_id=uploaded.json()['item']['id']
    real_connect=db.connect
    with real_connect(cfg) as conn:
        proposal_id=conn.execute('INSERT INTO proposals(source_id,created_at) VALUES(?,?)',(source_id,db.now())).lastrowid
        before_source=dict(conn.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone())
        before_proposal=dict(conn.execute('SELECT * FROM proposals WHERE id=?',(proposal_id,)).fetchone())
    before_files={path.name:path.read_bytes() for path in cfg.audio_dir.iterdir() if path.is_file()}
    class FailedCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError('simulated commit failure')
    def failing_connect(config):
        conn=sqlite3.connect(config.db_path,factory=FailedCommit)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn
    app=create_admin_app(cfg)
    failing_client=TestClient(app,raise_server_exceptions=False)
    failing_client.cookies.update(client.cookies)
    monkeypatch.setattr(db,'connect',failing_connect)
    if route=='import':
        response=failing_client.post('/api/library/import',files={'file':('new.wav',original.read_bytes(),'audio/wav')},data={'title':'New import'},headers=headers)
    else:
        endpoint=f'/api/library/{source_id}' if route=='library' else f'/api/proposals/{proposal_id}'
        response=failing_client.patch(endpoint,json={'title':'New title','artist':'New artist'},headers=headers)
    assert response.status_code==500
    with real_connect(cfg) as conn:
        assert dict(conn.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone())==before_source
        assert dict(conn.execute('SELECT * FROM proposals WHERE id=?',(proposal_id,)).fetchone())==before_proposal
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==1
    assert {path.name:path.read_bytes() for path in cfg.audio_dir.iterdir() if path.is_file()}==before_files
    assert original.is_file()
