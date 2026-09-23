"""Disposable branch evidence must never fall back to the production DSN."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


PATH = Path(__file__).resolve().parents[1] / 'ops/oracle_light_0355_rehearsal.py'
spec = importlib.util.spec_from_file_location('light_rehearsal', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def identity(monkeypatch, tmp_path):
    retire, refence = tmp_path / 'retire.sql', tmp_path / 'refence.sql'
    retire.write_text('retire')
    refence.write_text('refence')
    monkeypatch.setenv('NEON_API_KEY', 'test-key')
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    monkeypatch.setattr(module.sys, 'argv', ['script', str(retire), str(refence)])
    return retire, refence


def test_default_parent_drift_rejects_before_branch_creation(monkeypatch, tmp_path):
    identity(monkeypatch, tmp_path)
    def get_only(method, path, token, body=None):
        assert method == 'GET' and path == '/branches'
        return {'branches':[{'id':'br-other','default':True}]}
    with patch.object(module, 'api', side_effect=get_only) as calls, \
         patch.object(module, 'sql') as sql:
        with pytest.raises(RuntimeError, match='PRODUCTION_PARENT_DRIFT'):
            module.main()
    assert calls.call_count == 1
    sql.assert_not_called()


def test_sql_failure_deletes_only_created_child(monkeypatch, tmp_path):
    identity(monkeypatch, tmp_path)
    requests = []
    def child_api(method, path, token, body=None):
        requests.append((method,path))
        if path == '/branches' and method == 'GET':
            return {'branches':[{'id':module.PRODUCTION,'default':True}]}
        if path == '/branches' and method == 'POST':
            return {'branch':{'id':'br-test-child','parent_id':module.PRODUCTION},
                    'endpoints':[{'type':'read_write','host':'ep-test.aws.neon.tech'}]}
        if path.startswith('/connection_uri?'):
            return {'uri':'postgresql://neondb_owner:test-password@ep-test.aws.neon.tech/neondb?sslmode=require'}
        if path == '/branches/br-test-child' and method == 'DELETE':
            return {}
        raise AssertionError((method,path))
    with patch.object(module, 'api', side_effect=child_api), \
         patch.object(module, 'sql', side_effect=['neondb_owner:neondb',RuntimeError('CHILD_SQL_FAILED')]):
        with pytest.raises(RuntimeError, match='CHILD_SQL_FAILED'):
            module.main()
    assert requests[-1] == ('DELETE','/branches/br-test-child')
    assert not any(path == '/branches/' + module.PRODUCTION for _,path in requests)


def test_psql_does_not_receive_neon_api_key(monkeypatch):
    monkeypatch.setenv('NEON_API_KEY', 'test-key')
    with patch.object(module.subprocess, 'run') as run:
        run.return_value.returncode = 0
        run.return_value.stdout = 'ok\n'
        assert module.sql('postgresql://child-only', 'SELECT 1') == 'ok'
    assert 'NEON_API_KEY' not in run.call_args.kwargs['env']
    assert run.call_args.kwargs['env']['PGDATABASE'] == 'postgresql://child-only'
