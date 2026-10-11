from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import threading
import wave

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

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


def avatar_uri(*,size=(128,128),color='crimson',format='PNG',metadata=False):
    image=Image.new('RGBA' if format=='PNG' else 'RGB',size,color)
    options={}
    if metadata:
        info=PngImagePlugin.PngInfo(); info.add_text('Location','private original metadata')
        options['pnginfo']=info
    output=io.BytesIO(); image.save(output,format=format,**options)
    return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode('ascii')


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


def test_zero_quota_allows_many_proposals_and_concurrent_duplicates_without_removing_positive_limits(cfg):
    alice,headers,identity=guest(cfg)
    with db.connect(cfg) as conn:
        db.set_setting(conn,'max_proposals',0)
        db.set_setting(conn,'max_total_proposals',1)  # Retired historical setting must not cap collection.
    assert alice.get('/api/session').json()['settings']['max_proposals']==0
    for number in range(12):
        manual(alice,headers,f'Proposition illimitée {number}')
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=list(pool.map(lambda _:alice.post('/api/proposals',json={'kind':'manual','title':'Même ajout concurrent'},headers=headers),range(4)))
    assert all(response.status_code==200 for response in responses)
    proposal_ids={response.json().get('item',response.json().get('duplicate'))['id'] for response in responses}
    assert len(proposal_ids)==1
    mine=alice.get('/api/proposals?mine=true').json()['items']
    assert len(mine)==13 and all(item['mine'] for item in mine)
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM proposals WHERE guest_id=?',(identity['id'],)).fetchone()[0]==13
        db.set_setting(conn,'max_proposals',2)
    assert alice.post('/api/proposals',json={'kind':'manual','title':'Limite positive restaurée'},headers=headers).status_code==429


def test_collection_and_public_list_continue_beyond_twenty_thousand_proposals(cfg):
    alice,headers,identity=guest(cfg)
    with db.connect(cfg) as conn:
        db.set_setting(conn,'max_proposals',0)
        db.set_setting(conn,'max_total_proposals',1)
        conn.executemany("INSERT INTO sources(id,title,artist,status,created_at) VALUES(?,?,'','pending',?)",
                         ((number,f'Catalogue historique {number}',db.now()) for number in range(1,20001)))
        conn.executemany("INSERT INTO proposals(source_id,guest_id,state,created_at) VALUES(?,?,'pending',?)",
                         ((number,identity['id'],db.now()) for number in range(1,20001)))
    added=manual(alice,headers,'Encore une musique après vingt mille')['item']
    assert added['mine'] is True
    public=alice.get('/api/proposals').json()['items']
    assert len(public)==20001
    assert added['id'] in {item['id'] for item in public}
    assert all(item['mine'] for item in public)
    assert alice.post(f"/api/proposals/{added['id']}/vote",json={'active':True},headers=headers).json()['votes']==1


def test_profile_photo_is_normalized_public_once_and_owner_session_isolated(cfg):
    alice,headers,identity=guest(cfg,'Même pseudo')
    bob,bob_headers,bob_identity=guest(cfg,'Même pseudo')
    proposal=manual(alice,headers,'Photo de mon profil')['item']
    assert proposal['avatar_id']==identity['id'] and proposal['avatar_version']==''
    response=alice.post('/api/profile',json={'avatar':avatar_uri(size=(64,64),metadata=True)},headers=headers)
    assert response.status_code==200,response.text
    profile=response.json()['guest']
    assert profile['id']==identity['id'] and profile['avatar_id']==identity['id']
    assert len(profile['avatar_version'])==32 and 'avatar_data' not in profile
    public=TestClient(create_guest_app(cfg))
    photos=public.get('/api/avatars',params={'ids':','.join((identity['id'],bob_identity['id']))}).json()['items']
    indexed={photo['id']:photo for photo in photos}
    stored=indexed[identity['id']]
    assert set(stored)=={'id','version','avatar_data'}
    raw=base64.b64decode(stored['avatar_data'].split(',',1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        assert image.format=='PNG' and image.size==(128,128) and image.mode=='RGBA'
        assert 'Location' not in image.info and not image.getexif()
    assert stored['version']==hashlib.md5(raw,usedforsecurity=False).hexdigest()
    assert indexed[bob_identity['id']]['avatar_data']==''
    listed=public.get('/api/proposals').json()['items'][0]
    assert listed['avatar_id']==identity['id'] and listed['avatar_version']==stored['version']
    assert 'avatar_data' not in listed and 'guest_id' not in listed
    assert alice.get('/api/session').json()['guest']['avatar_version']==stored['version']
    assert bob.post('/api/profile',json={'avatar':avatar_uri(color='blue'),'id':identity['id']},headers=bob_headers).status_code==422
    assert bob.post('/api/profile',json={'avatar':avatar_uri(color='blue')},headers=bob_headers).status_code==200
    assert public.get('/api/avatars',params={'ids':identity['id']}).json()['items']==[stored]


def test_profile_remove_requires_identity_csrf_and_preserves_music_votes_history(cfg):
    alice,headers,identity=guest(cfg)
    bob,bob_headers,_=guest(cfg,'Bob')
    proposal=manual(alice,headers)['item']
    assert bob.post(f"/api/proposals/{proposal['id']}/vote",json={'active':True},headers=bob_headers).status_code==200
    assert alice.post('/api/profile',json={'avatar':avatar_uri()},headers=headers).status_code==200
    with db.connect(cfg) as conn:
        before=tuple(dict(conn.execute(f'SELECT * FROM {table}').fetchone()) for table in ('proposals','sources','votes'))
    assert alice.post('/api/profile',json={'avatar':''}).status_code==403
    anonymous=TestClient(create_guest_app(cfg))
    anonymous_headers={'X-CSRF-Token':anonymous.get('/api/session').json()['csrf_token']}
    assert anonymous.post('/api/profile',json={'avatar':''},headers=anonymous_headers).status_code==401
    removed=alice.post('/api/profile',json={'avatar':''},headers=headers)
    assert removed.status_code==200 and removed.json()['guest']['avatar_version']==''
    assert alice.get('/api/avatars',params={'ids':identity['id']}).json()['items']==[{'id':identity['id'],'version':'','avatar_data':''}]
    with db.connect(cfg) as conn:
        after=tuple(dict(conn.execute(f'SELECT * FROM {table}').fetchone()) for table in ('proposals','sources','votes'))
        assert after==before


@pytest.mark.parametrize('invalid_kind',['svg','jpeg','noncanonical','oversized','dimensions','truncated','animation'])
def test_profile_invalid_images_never_replace_existing_photo(cfg,invalid_kind):
    alice,headers,identity=guest(cfg)
    assert alice.post('/api/profile',json={'avatar':avatar_uri()},headers=headers).status_code==200
    before=alice.get('/api/avatars',params={'ids':identity['id']}).json()
    if invalid_kind=='svg':
        invalid='data:image/png;base64,'+base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>').decode('ascii')
    elif invalid_kind=='jpeg': invalid=avatar_uri(format='JPEG')
    elif invalid_kind=='noncanonical': invalid=avatar_uri()+'\n'
    elif invalid_kind=='oversized': invalid='data:image/png;base64,'+'A'*200000
    elif invalid_kind=='dimensions': invalid=avatar_uri(size=(129,128))
    elif invalid_kind=='animation':
        output=io.BytesIO()
        Image.new('RGBA',(128,128),'red').save(output,format='PNG',save_all=True,append_images=[Image.new('RGBA',(128,128),'blue')],duration=100,loop=0)
        invalid='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
    else: invalid='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\n').decode('ascii')
    response=alice.post('/api/profile',json={'avatar':invalid},headers=headers)
    assert response.status_code in (400,422),response.text
    assert alice.get('/api/avatars',params={'ids':identity['id']}).json()==before


def test_avatar_batch_is_bounded_and_reads_imported_cloud_profile_without_identity_change(cfg):
    alice,headers,identity=guest(cfg)
    imported='cloud:testing-project:22222222-2222-4222-8222-222222222222'
    photo=avatar_uri()
    raw=base64.b64decode(photo.split(',',1)[1]); version=hashlib.md5(raw,usedforsecurity=False).hexdigest()
    with db.connect(cfg) as conn:
        conn.execute('INSERT INTO guests(id,nickname,created_at,avatar_data,avatar_version) VALUES(?,?,?,?,?)',(imported,'Cloud',db.now(),photo,version))
    assert alice.get('/api/avatars',params={'ids':imported}).json()['items']==[{'id':imported,'version':version,'avatar_data':photo}]
    assert alice.get('/api/session').json()['guest']['id']==identity['id']
    assert alice.get('/api/avatars',params={'ids':','.join([identity['id']]*60)}).status_code==200
    assert alice.get('/api/avatars',params={'ids':','.join([identity['id']]*61)}).status_code==400
    for invalid in ('not-a-uuid','../../private',identity['id']+',','cloud:bad/project:'+identity['id']):
        assert alice.get('/api/avatars',params={'ids':invalid}).status_code==400


def test_known_youtube_duration_fills_unknown_source_without_overwriting_title_or_verified_duration(cfg):
    alice,headers,_=guest(cfg)
    bob,bob_headers,_=guest(cfg,'Bob')
    with db.connect(cfg) as conn: db.set_setting(conn,'offline_mode',True)
    payload={'kind':'youtube','url':'https://youtu.be/BaW_jenozKc','title':'Titre choisi'}
    first=alice.post('/api/proposals',json=payload,headers=headers).json()['item']
    assert first['duration'] is None
    known=bob.post('/api/proposals',json={**payload,'title':'Ne pas remplacer','duration':123.5,'channel':'Chaîne connue'},headers=bob_headers)
    assert known.status_code==200,known.text
    duplicate=known.json()['duplicate']
    assert duplicate['id']==first['id'] and duplicate['duration']==123.5
    assert duplicate['title']=='Titre choisi' and duplicate['channel']=='Chaîne connue'
    repeated=alice.post('/api/proposals',json={**payload,'duration':456,'channel':'Ne pas remplacer'},headers=headers)
    assert repeated.json()['duplicate']['duration']==123.5 and repeated.json()['duplicate']['channel']=='Chaîne connue'
    for duration in (-1,0,'123',True,'NaN'):
        assert alice.post('/api/proposals',json={**payload,'duration':duration},headers=headers).status_code==422


@pytest.mark.parametrize('kind',['youtube','playlist'])
def test_two_guests_racing_same_canonical_source_create_only_one_active_proposal(cfg,monkeypatch,kind):
    import playlist25.guest as guest_module
    alice,alice_headers,_=guest(cfg,'Même pseudo')
    bob,bob_headers,_=guest(cfg,'Même pseudo')
    with db.connect(cfg) as conn: db.set_setting(conn,'offline_mode',True)
    barrier=threading.Barrier(2)
    seen=threading.local()
    original=guest_module._same_source
    def synchronize_first_read(items,source_id):
        # Force both requests to observe no source before either takes its writer lock.
        if source_id is None and not getattr(seen,'used',False):
            seen.used=True
            barrier.wait(timeout=5)
        return original(items,source_id)
    monkeypatch.setattr(guest_module,'_same_source',synchronize_first_read)
    links=(['https://youtu.be/BaW_jenozKc?t=30','https://www.youtube.com/watch?v=BaW_jenozKc&feature=share']
           if kind=='youtube' else ['https://music.youtube.com/playlist?list=PL1234567890abc&index=2','http://m.youtube.com/playlist?list=PL1234567890abc&feature=share'])
    calls=[(alice,alice_headers,links[0]),(bob,bob_headers,links[1])]
    def propose(call):
        client,headers,url=call
        return client.post('/api/proposals',json={'kind':'youtube','url':url},headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(propose,calls))
    assert all(response.status_code==200 for response in responses),[response.text for response in responses]
    bodies=[response.json() for response in responses]
    assert sum('item' in body for body in bodies)==1
    assert sum('duplicate' in body for body in bodies)==1
    created=next(body['item'] for body in bodies if 'item' in body)
    duplicate=next(body['duplicate'] for body in bodies if 'duplicate' in body)
    assert duplicate['id']==created['id'] and duplicate['source_id']==created['source_id']
    assert created['mine'] is True and duplicate['mine'] is False
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM proposals WHERE withdrawn_at IS NULL').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0


@pytest.mark.parametrize('status',['queued','downloading','available'])
def test_duplicate_of_prepared_source_preserves_queue_files_and_withdrawal_allows_readd(cfg,status):
    from playlist25 import queue
    alice,alice_headers,_=guest(cfg,'Même pseudo')
    bob,bob_headers,_=guest(cfg,'Même pseudo')
    with db.connect(cfg) as conn: db.set_setting(conn,'offline_mode',True)
    payload={'kind':'youtube','url':'https://youtu.be/BaW_jenozKc'}
    created=alice.post('/api/proposals',json=payload,headers=alice_headers).json()['item']
    assert alice.post(f"/api/proposals/{created['id']}/vote",json={'active':True},headers=alice_headers).status_code==200
    audio=None
    with db.connect(cfg) as conn:
        conn.execute("UPDATE proposals SET state='approved' WHERE id=?",(created['id'],))
        assert queue.enqueue_sources(conn,cfg,[created['source_id']])['queued']==[created['source_id']]
        job_id=conn.execute('SELECT id FROM jobs WHERE source_id=?',(created['source_id'],)).fetchone()[0]
        if status=='available':
            audio=cfg.audio_dir/'already-prepared.wav'
            with wave.open(str(audio),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(b'\x00\x00'*8000)
            conn.execute('UPDATE sources SET file_path=?,duration=1 WHERE id=?',(audio.name,created['source_id']))
        queue.set_job_state(conn,job_id,status,progress=100 if status=='available' else 0)
        before_job=dict(conn.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone())
        before_events=[dict(row) for row in conn.execute('SELECT * FROM job_events')]
        before_audio=audio.read_bytes() if audio else None
    response=bob.post('/api/proposals',json={**payload,'url':'https://www.youtube.com/watch?v=BaW_jenozKc&feature=share'},headers=bob_headers)
    assert response.status_code==200,response.text
    duplicate=response.json()['duplicate']
    assert duplicate['id']==created['id'] and duplicate['status']==status and duplicate['mine'] is False
    assert duplicate['votes']==1 and 'file_path' not in duplicate
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM proposals').fetchone()[0]==1
    assert alice.post(f"/api/proposals/{created['id']}/withdraw",json={},headers=alice_headers).status_code==200
    replacement=bob.post('/api/proposals',json=payload,headers=bob_headers)
    assert replacement.status_code==200,replacement.text
    new=replacement.json()['item']
    assert new['id']!=created['id'] and new['source_id']==created['source_id'] and new['status']==status
    assert new['mine'] is True and new['votes']==0
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM proposals').fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
        assert dict(conn.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone())==before_job
        assert [dict(row) for row in conn.execute('SELECT * FROM job_events')]==before_events
        assert conn.execute('SELECT COUNT(*) FROM votes WHERE proposal_id=?',(created['id'],)).fetchone()[0]==1
    if audio: assert audio.read_bytes()==before_audio


def test_withdrawal_audit_marker_excludes_stale_visible_state_from_listing_and_duplicates(cfg):
    alice,headers,_=guest(cfg)
    with db.connect(cfg) as conn: db.set_setting(conn,'offline_mode',True)
    payload={'kind':'youtube','url':'https://youtu.be/BaW_jenozKc'}
    old=alice.post('/api/proposals',json=payload,headers=headers).json()['item']
    assert alice.post(f"/api/proposals/{old['id']}/withdraw",json={},headers=headers).status_code==200
    with db.connect(cfg) as conn:
        withdrawn_at=conn.execute('SELECT withdrawn_at FROM proposals WHERE id=?',(old['id'],)).fetchone()[0]
        conn.execute("UPDATE proposals SET state='approved' WHERE id=?",(old['id'],))
    assert alice.get('/api/proposals').json()['items']==[]
    readded=alice.post('/api/proposals',json=payload,headers=headers)
    assert readded.status_code==200 and 'item' in readded.json(),readded.text
    assert readded.json()['item']['id']!=old['id'] and readded.json()['item']['source_id']==old['source_id']
    with db.connect(cfg) as conn:
        assert conn.execute('SELECT withdrawn_at FROM proposals WHERE id=?',(old['id'],)).fetchone()[0]==withdrawn_at


def test_same_title_different_youtube_recordings_are_not_exact_duplicates(cfg):
    alice,headers,_=guest(cfg)
    with db.connect(cfg) as conn: db.set_setting(conn,'offline_mode',True)
    first=alice.post('/api/proposals',json={'kind':'youtube','url':'https://youtu.be/raceVer_001','title':'Même titre'},headers=headers).json()['item']
    other=alice.post('/api/proposals',json={'kind':'youtube','url':'https://youtu.be/raceVer_002','title':'Même titre'},headers=headers)
    assert other.status_code==200 and 'duplicate' not in other.json(),other.text
    assert other.json()['item']['source_id']!=first['source_id']


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
