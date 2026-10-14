"""Real PostgreSQL additive categories/Auth checks; disposable, offline Docker only.

Run .venv/bin/python cloud/test_categories_sql.py. auth.uid() is a test JWT-context
stub; verified organizer identities are always read from the real auth.users table.
No real account, email, Supabase data, music or external connection is used.
"""
from __future__ import annotations
import json
import importlib.util
from pathlib import Path
import subprocess
import time
from uuid import uuid4

HERE = Path(__file__).resolve().parent
TOKEN_A, TOKEN_B = 'a'*64, 'b'*64
OWNER = '10000000-0000-4000-8000-000000000001'
ATTACKER = '20000000-0000-4000-8000-000000000002'
UNCONFIRMED = '30000000-0000-4000-8000-000000000003'

def literal(value):
    if value is None: return 'NULL'
    if isinstance(value, bool): return 'true' if value else 'false'
    if isinstance(value, int): return str(value)
    if isinstance(value, (dict, list)): return "'"+json.dumps(value,ensure_ascii=False).replace("'","''")+"'::jsonb"
    return "'"+value.replace("'","''")+"'"

def main():
    name='playlist25-categories-test-'+uuid4().hex[:12]
    subprocess.run(['docker','run','--rm','-d','--network','none','--memory','256m','--cpus','1','--name',name,
                    '-e','POSTGRES_PASSWORD=disposable-categories-test','postgres:16-alpine'],check=True,stdout=subprocess.DEVNULL)
    checks=[]
    def sql(statement,role=None,uid=None,email=None,success=True):
        context=''
        if uid: context="set request.jwt.claims="+literal(json.dumps({'sub':uid,'email':email or 'forged@example.invalid'}))+";\n"
        if role: context+='set role '+role+';\n'
        result=subprocess.run(['docker','exec','-i',name,'psql','-X','-qAt','-U','postgres','-v','ON_ERROR_STOP=1'],input=context+statement,text=True,capture_output=True)
        if success and result.returncode: raise AssertionError(result.stderr)
        if not success: assert result.returncode!=0,'Unauthorized operation unexpectedly succeeded'
        return result.stdout.strip()
    def rpc(function,*args,role='anon',uid=None,email=None):
        return json.loads(sql('select public.'+function+'('+','.join(literal(x) for x in args)+');',role,uid,email))
    def checked(message): checks.append(message); print('PASS:',message,flush=True)
    try:
        for _ in range(60):
            logs=subprocess.run(['docker','logs',name],text=True,capture_output=True)
            ready=subprocess.run(['docker','exec',name,'pg_isready','-U','postgres'],capture_output=True)
            if 'PostgreSQL init process complete' in logs.stdout and ready.returncode==0: break
            time.sleep(.2)
        else: raise AssertionError('PostgreSQL did not start')
        sql("""create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
          create schema auth;
          create table auth.users(id uuid primary key,email text,email_confirmed_at timestamptz,deleted_at timestamptz,banned_until timestamptz);
          create function auth.uid() returns uuid language sql stable as $$
            select (nullif(current_setting('request.jwt.claims',true),'')::jsonb->>'sub')::uuid $$;""")
        base=(HERE/'supabase.sql').read_text(encoding='utf-8')
        extension=(HERE/'categories.sql').read_text(encoding='utf-8')
        sql(base)
        a=rpc('playlist25_identity',TOKEN_A,'Même pseudo',None)['id']
        b=rpc('playlist25_identity',TOKEN_B,'Même pseudo',None)['id']
        p1=rpc('playlist25_propose',TOKEN_A,'manual','Première proposition','',None)['item']['id']
        p2=rpc('playlist25_propose',TOKEN_B,'manual','Autre proposition','',None)['item']['id']
        rpc('playlist25_vote',TOKEN_B,p1,True)
        counts=sql("select (select count(*) from public.playlist25_guests),(select count(*) from public.playlist25_sources),(select count(*) from public.playlist25_proposals),(select count(*) from public.playlist25_votes);")
        sql(extension);sql(extension)
        assert sql("select (select count(*) from public.playlist25_guests),(select count(*) from public.playlist25_sources),(select count(*) from public.playlist25_proposals),(select count(*) from public.playlist25_votes);")==counts
        assert rpc('playlist25_categories_list')=={'categories':[]}
        assert rpc('playlist25_list',TOKEN_A)['items'][0]['category_ids']==[]
        checked('Additive migration twice preserves existing IDs, guests, sources, proposals and votes; no seeded categories')
        assert rpc('playlist25_approve_organizer','owner@example.invalid',True,role='service_role')=={'approved':True}
        assert rpc('playlist25_organizer_session')=={'authenticated':False,'organizer':None}
        assert rpc('playlist25_organizer_session',role='authenticated',uid=OWNER,email='owner@example.invalid')['authenticated'] is False
        sql(f"insert into auth.users values('{OWNER}','OWNER@EXAMPLE.INVALID',now(),null,null),('{ATTACKER}','outsider@example.invalid',now(),null,null),('{UNCONFIRMED}','owner@example.invalid',null,null,null);")
        assert rpc('playlist25_organizer_session',role='authenticated',uid=ATTACKER,email='owner@example.invalid')['authenticated'] is False
        assert rpc('playlist25_organizer_session',role='authenticated',uid=UNCONFIRMED,email='owner@example.invalid')['authenticated'] is False
        organizer=rpc('playlist25_organizer_session',role='authenticated',uid=OWNER,email='outsider@example.invalid')
        assert organizer['authenticated'] and organizer['organizer']['id']==OWNER
        checked('JWT email claims cannot spoof organizer; auth.users identity, confirmed email and private whitelist required')
        for role in ('anon','authenticated'):
            for statement in ["select * from public.playlist25_organizer_emails;","select * from public.playlist25_categories;",
                              "select public.playlist25_organizer_id();","select public.playlist25_approve_organizer('x@example.invalid',true);",
                              "select public.playlist25_admin_categories('[]','[]');"]:
                sql(statement,role=role,uid=OWNER if role=='authenticated' else None,success=False)
        sql("grant select on public.playlist25_categories,public.playlist25_organizer_emails to anon,authenticated;")
        for role in ('anon','authenticated'):
            assert sql('select count(*) from public.playlist25_organizer_emails;',role=role,uid=OWNER if role=='authenticated' else None)=='0'
        sql("revoke select on public.playlist25_categories,public.playlist25_organizer_emails from anon,authenticated;")
        sql("select public.playlist25_organizer_category('{}');",role='anon',success=False)
        assert rpc('playlist25_organizer_category',{'name':'Interdit'},role='authenticated',uid=ATTACKER)['status']==403
        assert rpc('playlist25_category_suggestions',role='authenticated',uid=UNCONFIRMED)['status']==403
        suggestions=rpc('playlist25_category_suggestions',role='authenticated',uid=OWNER)['items']
        assert len(suggestions)==5 and rpc('playlist25_categories_list')=={'categories':[]}
        checked('RLS and EXECUTE denial protect organizer whitelist/private APIs; suggestions remain private and create no rows')
        c1=rpc('playlist25_organizer_category',{'name':'Accueil','sort_order':2},role='authenticated',uid=OWNER)['item']
        c2=rpc('playlist25_organizer_category',{'name':'Danse'},role='authenticated',uid=OWNER)['item']
        assert len(c1['id'])==32 and c1['active'] and c2['sort_order']==3
        assert rpc('playlist25_organizer_category',{'name':' accueil '},role='authenticated',uid=OWNER)['status']==409
        assert rpc('playlist25_organizer_category',{'id':c1['id'],'name':'Bienvenue','sort_order':0},role='authenticated',uid=OWNER)['item']['name']=='Bienvenue'
        assert rpc('playlist25_proposal_categories',TOKEN_A,p1,[c1['id'],c2['id']])['item']['category_ids']==[c1['id'],c2['id']]
        assert rpc('playlist25_proposal_categories',TOKEN_B,p1,[])['status']==404
        assert rpc('playlist25_proposal_categories','c'*64,p1,[])['status']==401
        assert rpc('playlist25_organizer_proposal_categories',p2,[c2['id']],role='authenticated',uid=OWNER)['item']['category_ids']==[c2['id']]
        own_admin=rpc('playlist25_organizer_proposal_categories',p1,[c1['id'],c2['id']],TOKEN_A,role='authenticated',uid=OWNER)['item']
        assert own_admin['mine'] is True and own_admin['voted'] is False
        peer_admin=rpc('playlist25_organizer_proposal_categories',p1,[c1['id'],c2['id']],TOKEN_B,role='authenticated',uid=OWNER)['item']
        assert peer_admin['mine'] is False and peer_admin['voted'] is True
        for bad in [None,{},[c1['id'],c1['id']],['f'*32],[1],['invalid']]:
            assert rpc('playlist25_proposal_categories',TOKEN_A,p1,bad)['status']==422
        assert rpc('playlist25_vote',TOKEN_B,p1,False)['votes']==0
        assert rpc('playlist25_vote',TOKEN_B,p1,True)['votes']==1
        checked('Organizer creates/renames/reorders and assigns other proposals; same-nickname foreign owner denied; invalid IDs never alter votes')
        archived=rpc('playlist25_organizer_category',{'id':c1['id'],'active':False},role='authenticated',uid=OWNER)
        assert not archived['item']['active']
        assert any(not row['active'] for row in rpc('playlist25_categories_list')['categories'])
        assert rpc('playlist25_proposal_categories',TOKEN_A,p1,[c1['id'],c2['id']])['item']['category_ids']==[c1['id'],c2['id']]
        assert rpc('playlist25_proposal_categories',TOKEN_B,p2,[c1['id']])['status']==422
        assert rpc('playlist25_organizer_proposal_categories',p2,[c1['id']],role='authenticated',uid=OWNER)['status']==422
        assert rpc('playlist25_proposal_categories',TOKEN_A,p1,[c2['id']])['item']['category_ids']==[c2['id']]
        assert rpc('playlist25_proposal_categories',TOKEN_A,p1,[c1['id']])['status']==422
        checked('Archive preserves catalogue/history, may be kept only when previously assigned, and cannot be newly assigned')
        new_id='9'*32
        catalogue=[{'id':new_id,'name':'Souvenirs personnels','sort_order':7,'active':True}]
        before=rpc('playlist25_snapshot',role='service_role')
        assert rpc('playlist25_admin_categories',catalogue,[{'id':999999,'category_ids':[new_id]}],role='service_role')['status']==404
        assert not any(row['id']==new_id for row in rpc('playlist25_categories_list')['categories'])
        applied=rpc('playlist25_admin_categories',catalogue,[{'id':p1,'category_ids':[new_id,c2['id']]}],role='service_role')
        assert applied=={'updated_categories':1,'updated_assignments':1}
        assert rpc('playlist25_admin_categories',None,[],role='service_role')['status']==422
        assert rpc('playlist25_admin_categories',[],[{'id':p1,'category_ids':[new_id]},{'id':p1,'category_ids':[]}],role='service_role')['status']==422
        for bad in [{'name':'','sort_order':0,'active':True},{'name':'x'*81},{'name':'a\nprivate'},
                    {'name':'invalid','sort_order':True},{'name':'invalid','sort_order':-1}, {'name':'invalid','sort_order':2147483648},
                    {'name':'invalid','active':None},{'name':'invalid','unexpected':'field'}]:
            assert rpc('playlist25_organizer_category',bad,role='authenticated',uid=OWNER)['status']==422
        checked('Explicit PC catalogue/assignment publication is atomic and validates whole batches before mutation')
        rpc('playlist25_approve_organizer','owner@example.invalid',False,role='service_role')
        assert rpc('playlist25_organizer_category',{'name':'Cannot create'},role='authenticated',uid=OWNER)['status']==403
        rpc('playlist25_approve_organizer','owner@example.invalid',True,role='service_role')
        sql(f"update auth.users set banned_until=now()+interval '1 hour' where id='{OWNER}';")
        assert not rpc('playlist25_organizer_session',role='authenticated',uid=OWNER)['authenticated']
        sql(f"update auth.users set banned_until=null,deleted_at=now() where id='{OWNER}';")
        assert not rpc('playlist25_organizer_session',role='authenticated',uid=OWNER)['authenticated']
        sql(f"update auth.users set deleted_at=null where id='{OWNER}';")
        snapshot=rpc('playlist25_snapshot',role='service_role')
        assert snapshot['schema_version']==1 and len(snapshot['categories'])==3
        assert 'token_hash' not in json.dumps(snapshot) and 'organizer_emails' not in json.dumps(snapshot)
        assert len(snapshot['votes'])==1 and {row['id']:row['guest_id'] for row in snapshot['proposals']}=={p1:a,p2:b}
        picks={row['id']:row['category_ids'] for row in snapshot['proposals']}
        spec=importlib.util.spec_from_file_location('deployment',HERE/'deploy_supabase.py')
        deployment=importlib.util.module_from_spec(spec);spec.loader.exec_module(deployment)
        sql(deployment.compose_schema_sql());sql(extension)
        after=rpc('playlist25_snapshot',role='service_role')
        assert {row['id']:row['category_ids'] for row in after['proposals']}==picks and len(after['votes'])==1
        assert rpc('playlist25_organizer_session',role='authenticated',uid=OWNER)['authenticated']
        assert rpc('playlist25_list',TOKEN_B)['categories']
        checked('Whitelist revocation/banned/deleted identities apply immediately; full base+extension redeploy preserves votes, owners and category history')
        print(f'PASS: {len(checks)} real PostgreSQL categories/Auth/security groups.',flush=True)
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

if __name__=='__main__': main()
