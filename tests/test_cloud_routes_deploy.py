import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from playlist25 import db
from playlist25.app import create_app
from playlist25.config import Settings


def test_cloud_routes_private_and_missing_key_preserves_data(tmp_path):
    cfg=Settings(data_dir=tmp_path/'data',admin_password='test-only-password',session_secret='test-only-signing-secret')
    app=create_app('admin',cfg); client=TestClient(app)
    assert client.get('/api/cloud/status').status_code==401
    assert client.post('/api/cloud/sync').status_code==401
    assert client.get('/api/qr?scope=local').status_code==401
    token=client.get('/api/session').json()['csrf_token']
    result=client.post('/api/login',json={'password':cfg.admin_password},headers={'X-CSRF-Token':token})
    headers={'X-CSRF-Token':result.json()['csrf_token']}
    status=client.get('/api/cloud/status').json()
    assert status['configured'] is False and 'supabase_service_role_key' not in status
    assert client.post('/api/cloud/sync').status_code==403
    with db.connect(cfg) as conn: db.upsert_source(conn,{'title':'À préserver'})
    result=client.post('/api/cloud/sync',headers=headers)
    assert result.status_code==503
    with db.connect(cfg) as conn: assert conn.execute('SELECT title FROM sources').fetchone()[0]=='À préserver'
    assert client.get('/api/qr?scope=local').headers['content-type']=='image/png'
    client.patch('/api/settings',json={'offline_mode':True},headers=headers)
    assert client.post('/api/cloud/publish',headers=headers).status_code==503
    assert TestClient(create_app('guest',cfg)).get('/api/cloud/status').status_code==404


def deployment_module():
    path=Path(__file__).resolve().parents[1]/'cloud'/'deploy_supabase.py'
    spec=importlib.util.spec_from_file_location('deployment',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_deploy_checks_secure_binding_before_network(monkeypatch):
    import pytest
    module=deployment_module()
    monkeypatch.delenv('SUPABASE_ACCESS_TOKEN',raising=False)
    with pytest.raises(module.DeployError,match='absent'):
        module.management_request('test-ref','POST','/database/query',{'query':'SELECT 1'})


def test_migration_verifies_expected_functions(monkeypatch,capsys):
    module=deployment_module(); calls=[]
    def request(ref,method,suffix,body=None):
        calls.append((method,suffix,body))
        return [{'functions':6}] if body.get('read_only') else []
    monkeypatch.setattr(module,'management_request',request)
    module.install_schema('test-ref')
    assert len(calls)==2 and calls[0][2]['read_only'] is False
    assert 'BEGIN' in calls[0][2]['query'].upper()
    assert 'playlist25_withdraw' in calls[1][2]['query']
    assert "n.nspname='public'" in calls[1][2]['query']
    assert 'vérifiés' in capsys.readouterr().out


def test_deployment_refuses_schema_missing_withdrawal_rpc(monkeypatch):
    import pytest
    module=deployment_module()
    monkeypatch.setattr(module,'management_request',lambda ref,method,suffix,body=None: [{'functions':5}] if body.get('read_only') else [])
    with pytest.raises(module.DeployError,match='fonctions attendues'):
        module.install_schema('test-ref')


def test_management_error_does_not_dump_private_response(monkeypatch):
    import pytest
    from urllib.error import HTTPError
    module=deployment_module()
    monkeypatch.setenv('SUPABASE_ACCESS_TOKEN','test-only-access-token')
    class Failure:
        def open(self,*args,**kwargs): raise HTTPError('https://api.supabase.com',403,'private response text',{},None)
    monkeypatch.setattr(module,'build_opener',lambda *a:Failure())
    with pytest.raises(module.DeployError) as error: module.management_request('test','GET','/functions')
    assert 'test-only-access-token' not in str(error.value) and 'private response text' not in str(error.value)
