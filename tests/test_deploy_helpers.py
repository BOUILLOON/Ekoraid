"""Deployment composition/control-flow checks: no credentials or real network."""
import importlib.util
from pathlib import Path
import re
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def deploy():
    spec=importlib.util.spec_from_file_location('deploy_helpers',ROOT/'cloud/deploy_supabase.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def test_compose_checked_in_migrations_is_single_ordered_transaction(deploy):
    combined=deploy.compose_schema_sql()
    assert len(re.findall(r'^begin;\s*$',combined,re.M|re.I))==1
    assert len(re.findall(r'^commit;\s*$',combined,re.M|re.I))==1
    assert combined.startswith('begin;') and combined.rstrip().endswith('commit;')
    assert combined.index('-- Migration : supabase.sql')<combined.index('-- Migration : categories.sql')
    assert combined.index("'category_ids',p.category_ids")>combined.index('create or replace function public.playlist25_public_item')
    assert combined.rfind('create or replace function public.playlist25_list')>combined.index('-- Migration : categories.sql')
    assert 'drop table' not in combined.lower()
    assert 'truncate' not in combined.lower()
    assert 'playlist25_approve_organizer(' in combined and "values ('info@" not in combined
    assert combined.rstrip().endswith("notify pgrst, 'reload schema';\ncommit;")

def test_only_outer_transaction_lines_are_removed(deploy,tmp_path):
    script=tmp_path/'fixture.sql'
    original="-- setup\n\nBEGIN;\ncreate function demo() returns void language plpgsql as $$\nbegin\n perform 1;\nend $$;\nCOMMIT;\n-- end\n"
    script.write_text(original,encoding='utf-8')
    body=deploy._migration_body(script)
    assert 'begin\n perform 1;' in body
    assert 'BEGIN;' not in body and 'COMMIT;' not in body
    assert '-- setup' in body and '-- end' in body
    assert script.read_text(encoding='utf-8')==original

@pytest.mark.parametrize('script',["select 1;\ncommit;\n","begin;\nselect 1;\n","begin; -- changed wrapper\nselect 1;\ncommit;\n",''])
def test_changed_wrapper_fails_before_any_network(deploy,tmp_path,monkeypatch,script):
    (tmp_path/'supabase.sql').write_text(script,encoding='utf-8')
    (tmp_path/'categories.sql').write_text('begin;\nselect 2;\ncommit;\n',encoding='utf-8')
    monkeypatch.setattr(deploy,'HERE',tmp_path)
    monkeypatch.setattr(deploy,'management_request',lambda *_args,**_kwargs:pytest.fail('Network called before validated composition'))
    with pytest.raises(deploy.DeployError,match='instructions externes'):deploy.install_schema('fixture')

@pytest.mark.parametrize('response',[
    [],None,[{'functions':21,'distinct_functions':20}], [{'functions':22,'distinct_functions':21}],
    [{'functions':21}], [{'functions':'21malformed','distinct_functions':'21'}],
])
def test_missing_or_overloaded_rpc_cannot_report_verified(deploy,monkeypatch,capsys,response):
    monkeypatch.setattr(deploy,'management_request',lambda _r,_m,_p,body:response if body['read_only'] else [])
    with pytest.raises(deploy.DeployError,match='fonctions attendues'):deploy.install_schema('fixture')
    assert 'vérifiés' not in capsys.readouterr().out

def test_management_count_strings_are_accepted_without_logging_rows(deploy,monkeypatch,capsys):
    probes=[]
    monkeypatch.setattr(deploy,'management_request',lambda _r,_m,_p,body:[{'functions':str(len(deploy.EXPECTED_FUNCTIONS)),
        'distinct_functions':str(len(deploy.EXPECTED_FUNCTIONS))}] if body['read_only'] else [])
    monkeypatch.setattr(deploy,'wait_for_schema_cache',lambda ref:probes.append(ref))
    deploy.install_schema('fixture-ref')
    assert probes==['fixture-ref'] and 'vérifiés' in capsys.readouterr().out

@pytest.mark.parametrize('flag,expected',[('--functions-only',['edge']),('--sql-only',['schema'])])
def test_deployment_filters_never_mutate_the_other_surface(deploy,monkeypatch,flag,expected):
    calls=[]
    monkeypatch.setattr(sys,'argv',['deploy_supabase.py',flag])
    monkeypatch.setattr(deploy,'project_ref',lambda:'fixture-ref')
    monkeypatch.setattr(deploy,'install_schema',lambda _ref:calls.append('schema'))
    monkeypatch.setattr(deploy,'deploy_search',lambda _ref,_cli:calls.append('edge'))
    assert deploy.main()==0 and calls==expected

def test_schema_failure_prevents_edge_deployment(deploy,monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['deploy_supabase.py'])
    monkeypatch.setattr(deploy,'project_ref',lambda:'fixture-ref')
    def fail(_ref):raise deploy.DeployError('fixture schema failed')
    monkeypatch.setattr(deploy,'install_schema',fail)
    monkeypatch.setattr(deploy,'deploy_search',lambda *_args:pytest.fail('Edge deployed after schema failure'))
    assert deploy.main()==1
    assert capsys.readouterr().err.strip()=='fixture schema failed'

def public_config(deploy,tmp_path,monkeypatch,key='sb_publishable_fixture_only'):
    import json
    (tmp_path/'public-config.json').write_text(json.dumps({'publicKey':key}),encoding='utf-8')
    monkeypatch.setattr(deploy,'HERE',tmp_path)

def test_postgrest_cache_404_retries_only_anonymous_read_rpc(deploy,tmp_path,monkeypatch):
    from urllib.error import HTTPError
    public_config(deploy,tmp_path,monkeypatch)
    calls=[];delays=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*_args):pass
        def read(self,limit):
            assert limit==16*1024
            return b'{"authenticated":false,"organizer":null}'
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            assert timeout==5 and request.data==b'{}'
            assert request.full_url.endswith('/rest/v1/rpc/playlist25_organizer_session')
            assert request.get_header('Authorization') is None
            assert request.get_header('Apikey')=='sb_publishable_fixture_only'
            if len(calls)<3:raise HTTPError(request.full_url,404,'cache not ready',{},None)
            return Response()
    monkeypatch.setattr(deploy,'build_opener',lambda *_args:Opener())
    monkeypatch.setattr(deploy.time,'sleep',lambda seconds:delays.append(seconds))
    deploy.wait_for_schema_cache('x'*20)
    assert len(calls)==3 and delays==[.25,.25]

def test_cache_probe_is_bounded_and_no_readiness_claim_on_failure(deploy,tmp_path,monkeypatch):
    from urllib.error import HTTPError
    public_config(deploy,tmp_path,monkeypatch)
    calls=[]
    class Opener:
        def open(self,request,**_options):
            calls.append(request)
            raise HTTPError(request.full_url,404,'private response must not be logged',{},None)
    monkeypatch.setattr(deploy,'build_opener',lambda *_args:Opener())
    monkeypatch.setattr(deploy.time,'sleep',lambda _seconds:None)
    with pytest.raises(deploy.DeployError,match='pas encore confirmée') as error:deploy.wait_for_schema_cache('x'*20)
    assert len(calls)==6 and 'private response' not in str(error.value)

@pytest.mark.parametrize('key',['sb_secret_never_send',None,'malformed.jwt',
    'e30.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.signature'])
def test_public_cache_probe_refuses_privileged_or_invalid_keys(deploy,tmp_path,monkeypatch,key):
    public_config(deploy,tmp_path,monkeypatch,key)
    monkeypatch.setattr(deploy,'build_opener',lambda *_args:pytest.fail('Key validation must precede any network'))
    with pytest.raises(deploy.DeployError,match='publishable ou anon'):deploy.wait_for_schema_cache('x'*20)
