"""Synthetic harness control-flow tests. No PostgreSQL driver, SQL or sockets."""
import importlib.util
import json
import os
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runner(monkeypatch):
    import socket
    forbidden=Mock(side_effect=AssertionError('external I/O forbidden'))
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)
    path=ROOT/'database/tests/fixed_subset_admission/run.py'
    spec=importlib.util.spec_from_file_location('admission_lifecycle_tests',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    yield module
    forbidden.assert_not_called()


class Connection:
    def __init__(self,pid):
        self.info=SimpleNamespace(backend_pid=pid)
        self.closed=False
        self.cancel_safe=Mock()
        self.close=Mock(side_effect=self.finish)

    def finish(self): self.closed=True


def harness(runner):
    h=runner.Harness.__new__(runner.Harness)
    h.connections={}; h.log=[]; h.connection_history=[]; h.variants=[]
    h.futures=[]; h.threads=[]; h.operation_threads=[]; h.relays=[]
    h.peak_connections=0; h.peak_backend_connections=0; h.closing=False
    h.case_id='synthetic'; h.config={}; h.credentials={}
    h.row=object()
    h.pg=SimpleNamespace(connect=Mock(side_effect=lambda **kw:Connection(len(h.connection_history)+100)))
    h.q=Mock(return_value=[])
    h.scalar=Mock(side_effect=lambda *args:len(h.connections))
    return h


def test_client_connection_cap_includes_temporary_cutover_and_relay(runner):
    h=harness(runner)
    for name in ('admin','approver','collector','admit1','admit2','worker','legacy','attacker'):
        h.connect(name,options={})
    with pytest.raises(AssertionError,match='connection cap'): h.connect('cutover',options={})
    assert h.pg.connect.call_count==8
    h.close_connection('admit2'); h.connect('cutover',options={})
    h.close_connection('admit1'); h.connect('admit1',options={'port':12345})
    assert len(h.connections)==h.peak_connections==h.peak_backend_connections==8
    assert {r['session'] for r in h.connection_history}>={'cutover','admit1'}


def test_lingering_backend_prevents_replacement_from_opening_ninth(runner,monkeypatch):
    h=harness(runner); h.connect('admin')
    h.scalar=Mock(return_value=8)
    times=iter((0,4)); monkeypatch.setattr(runner.time,'monotonic',lambda:next(times))
    with pytest.raises(AssertionError,match='backend connection cap'): h.connect('cutover',options={})
    assert h.pg.connect.call_count==1


def test_all_database_connects_are_owned_by_the_capped_method(runner):
    import ast
    tree=ast.parse(Path(runner.__file__).read_text())
    calls=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.FunctionDef):
            for call in ast.walk(node):
                if (isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute)
                    and call.func.attr=='connect' and isinstance(call.func.value,ast.Attribute)
                    and call.func.value.attr=='pg'): calls.append(node.name)
    assert calls==['connect']


def test_relay_is_registerable_before_any_socket_is_opened(runner):
    relay=runner.CommitAckDropRelay()
    assert relay.listener is relay.thread is None
    relay.close()


def test_cleanup_closes_every_connection_after_cancel_and_close_errors(runner):
    h=harness(runner)
    for name in ('admin','first','second'): h.connect(name,options={})
    first=h.connections['first']; second=h.connections['second']; admin=h.connections['admin']
    first.cancel_safe.side_effect=RuntimeError('cancel failed')
    first.close.side_effect=RuntimeError('close failed')
    result=h.close()
    assert result['status']=='FAIL'
    assert second.closed and admin.closed
    assert first.close.call_count==1
    assert result['remaining_connections']==['first']
    assert {r['stage'] for r in result['errors']}>={'cancel-first','close-first','resources'}


def test_successful_cleanup_records_backend_lock_and_thread_proof(runner):
    h=harness(runner)
    h.connect('admin'); h.connect('worker',options={})
    assert h.submit(lambda:'done').result(1)=='done'
    result=h.close()
    assert result['status']=='PASS'
    assert result['database_census']=={'other_backends':[],'advisory_locks':[],'prepared_transactions':[]}
    assert not result['remaining_connections'] and not result['alive_threads']
    assert not result['pending_futures']


def test_cleanup_without_admin_cannot_claim_backend_census(runner):
    h=harness(runner); h.connect('admin'); h.close_connection('admin')
    result=h.close()
    assert result['status']=='FAIL'
    assert any(e['stage']=='cleanup-census' for e in result['errors'])


def test_fixture_rollback_failure_preserves_the_original_exception(runner):
    h=harness(runner); h.sql=Mock()
    def query(session,statement,*params):
        if statement=='fixture failure':raise ValueError('primary fixture error')
        if statement=='ROLLBACK':raise OSError('rollback transport error')
    h.q=Mock(side_effect=query)
    with pytest.raises(ValueError,match='primary fixture error') as failure:
        h.fixture_change('job','fixture failure')
    assert 'rollback transport error' in failure.value.__notes__[0]
    assert h.log[-1]['fixture_rollback_error']=='rollback transport error'


def test_cleanup_never_accepts_unfinished_future(runner,monkeypatch):
    h=harness(runner); h.connect('admin')
    pending=Future(); pending.set_running_or_notify_cancel(); h.futures.append(pending)
    monkeypatch.setattr(runner,'wait',lambda *a,**kw:None)
    result=h.close()
    assert result['status']=='FAIL' and result['pending_futures']==1
    pending.set_result(None)


def test_bounded_cleanup_timeout_is_explicit_and_thread_can_be_reaped(runner):
    h=harness(runner); release=threading.Event()
    try:
        with pytest.raises(AssertionError,match='timed out'):
            h.bounded(lambda:release.wait(1),.01,'synthetic-hang')
        assert h.operation_threads[0].daemon
    finally:
        release.set(); h.operation_threads[0].join(1)
    assert not h.operation_threads[0].is_alive()


def test_statement_limit_and_reset_refuse_active_work(runner):
    h=harness(runner)
    first=Future(); first.set_running_or_notify_cancel()
    second=Future(); second.set_running_or_notify_cancel(); h.futures=[first,second]
    with pytest.raises(AssertionError,match='concurrent statement cap'): h.submit(lambda:None)
    with pytest.raises(AssertionError,match='active test statements'): h.reset()
    h.q.assert_not_called()
    first.set_result(None); second.set_result(None)


def test_variant_preserves_primary_exception_and_records_failure(runner):
    h=harness(runner); h.snapshot=Mock(return_value={'rows':[]})
    with pytest.raises(ValueError,match='primary'):
        with h.variant('bad',unchanged=True): raise ValueError('primary')
    assert h.variants[0]['status']=='FAIL'
    assert h.variants[0]['error_type']=='ValueError'
    assert h.snapshot.call_count==1


def test_variant_snapshot_mismatch_cannot_pass(runner):
    h=harness(runner); h.snapshot=Mock(side_effect=[{'rows':[]},{'rows':[1]}])
    with pytest.raises(AssertionError,match='changed persisted state'):
        with h.variant('bad',unchanged=True): pass
    assert h.variants[0]['status']=='FAIL'
    assert h.variants[0]['before_sha256']!=h.variants[0]['after_sha256']


def test_run_case_keeps_original_error_when_capture_and_log_save_fail(runner,tmp_path,monkeypatch):
    h=harness(runner)
    h.failure_evidence=Mock(side_effect=RuntimeError('capture failed'))
    def fail(_): raise ValueError('original case error')
    monkeypatch.setattr(runner,'CASES',{'synthetic':fail})
    original_save=runner.save_json
    def save(path,value):
        if path.name.endswith('.sql.json'): raise OSError('log write failed')
        original_save(path,value)
    monkeypatch.setattr(runner,'save_json',save)
    record={'id':'synthetic'}; report={'status':'RUNNING','cases':[record]}
    assert not runner.run_case(h,record,tmp_path,report)
    assert report['error']=='original case error' and report['error_type']=='ValueError'
    assert record['failure_capture_error']['error']=='capture failed'
    assert report['secondary_errors'][0]['error']=='log write failed'


def test_missing_variant_cannot_turn_case_green(runner,tmp_path,monkeypatch):
    h=harness(runner); h.failure_evidence=Mock(return_value={'state':{}})
    monkeypatch.setattr(runner,'CASES',{'synthetic':lambda h:None})
    monkeypatch.setattr(runner,'REQUIRED_VARIANTS',{'synthetic':('must-run',)})
    record={'id':'synthetic'}; report={'status':'RUNNING','cases':[record]}
    assert not runner.run_case(h,record,tmp_path,report)
    assert record['status']=='FAIL' and 'variant evidence incomplete' in report['error']


def test_cleanup_failure_keeps_nonzero_exit_even_if_all_cases_pass(runner,tmp_path,monkeypatch):
    h=harness(runner); h.server={}; h.migration=ROOT/'database/migrations/0060_fixed_subset_admission.sql'
    h.start=Mock(); h.snapshot=Mock(return_value={})
    h.close=Mock(return_value={'status':'FAIL','errors':[{'stage':'census','error':'unknown backend'}]})
    monkeypatch.setattr(runner,'Harness',lambda *a:h)
    monkeypatch.setattr(runner,'REQUIRED_VARIANTS',{})
    monkeypatch.setattr(runner,'CASES',{key:lambda h:None for key in runner.CASE_IDS})
    for key in tuple(os.environ):
        if key.startswith('PG') or key=='DATABASE_URL':monkeypatch.delenv(key)
    code=runner.main(['--execute','--dsn','postgresql://postgres:fixedsubset-local-only@127.0.0.1:5432/fixedsubset_test',
                     '--evidence-dir',str(tmp_path)])
    report=json.loads((tmp_path/'results.json').read_text())
    assert code==1 and report['status']=='FAIL' and report['stage']=='cleanup'
    assert all(c['status']=='PASS' for c in report['cases'])


def test_main_failure_cleanup_cannot_replace_original_case_error(runner,tmp_path,monkeypatch):
    h=harness(runner); h.server={}; h.migration=ROOT/'database/migrations/0060_fixed_subset_admission.sql'
    h.start=Mock(); h.failure_evidence=Mock(side_effect=RuntimeError('snapshot failed'))
    h.close=Mock(side_effect=RuntimeError('cleanup failed'))
    monkeypatch.setattr(runner,'Harness',lambda *a:h)
    for key in tuple(os.environ):
        if key.startswith('PG') or key=='DATABASE_URL':monkeypatch.delenv(key)
    def fail(_): raise ValueError('first error')
    monkeypatch.setitem(runner.CASES,runner.CASE_IDS[0],fail)
    code=runner.main(['--execute','--dsn','postgresql://postgres:fixedsubset-local-only@127.0.0.1:5432/fixedsubset_test',
                      '--evidence-dir',str(tmp_path)])
    report=json.loads((tmp_path/'results.json').read_text())
    assert code==1 and report['error']=='first error'
    assert report['error_type']=='ValueError'
    assert any(e['stage']=='cleanup' and e['error']=='cleanup failed' for e in report['secondary_errors'])
    assert report['cases'][0]['status']=='FAIL'
    assert [c['status'] for c in report['cases'][1:]]==['NOT_RUN']*28


def test_l02_bootstraps_without_an_inherited_admit2_slot(runner):
    h=SimpleNamespace(config={},migration=ROOT/'database/migrations/0060_fixed_subset_admission.sql')
    h.connections={}; opened=[]
    def reset(**kw):h.connections={'admin':object(),'legacy':object()}
    def connect(name,**kw):
        assert name not in h.connections and len(h.connections)<8
        h.connections[name]=object(); opened.append(name)
    def submit(call):
        result=Future(); result.set_result(call()); return result
    h.reset=reset; h.connect=connect
    h.close_connection=lambda name:h.connections.pop(name,None)
    h.q=Mock(); h.pid=lambda name:123
    h.legacy=Mock(); h.submit=submit; h.blocked=Mock()
    h.apply_managed=Mock(); h.fixture=lambda:{}; h.admit=lambda f:{}
    h.expect_error=Mock(); h.scalar=Mock(return_value=1); h.verify_binding=Mock()
    @contextmanager
    def variant(*args,**kwargs):yield
    h.variant=variant
    runner.l02(h)
    assert opened==['cutover','cutover']
    assert 'cutover' not in h.connections


@pytest.mark.parametrize('incorrect_selector',[False,True])
def test_w01_uses_locked_row_behavior_not_definition_alias(runner,incorrect_selector):
    class Fake:
        def __init__(self):self.locked=False; self.draft=None
        def reset(self,**kw):pass
        def legacy(self,**kw):return {'batch_id':'legacy'}
        def apply_managed(self):pass
        def fixture(self,numbers,**kw):return {'numbers':numbers,'request_key':str(numbers),'approval_id':'approval'}
        def admit(self,f,*args):return {'batch_id':str(f['numbers'][0])}
        def q(self,session,sql,*args):
            if 'AND is_canary FOR UPDATE' in sql:self.locked=True
            if session=='admin' and sql=='COMMIT':self.locked=False
        def scalar(self,session,sql,*args):
            if 'pg_get_functiondef' in sql:return 'body with any alias whatsoever'
            if 'to_jsonb' in sql:return {'job_id':'old'}
            return True
        def snapshot(self):return {}
        def arm(self,*args,**kw):pass
        def blocked(self,*args):pass
        def submit(self,call):
            future=Future()
            if getattr(call,'__name__',None)=='claim':future.set_result(call())
            else:self.draft=(future,call)
            return future
        def release(self,*args):self.draft[0].set_result(self.draft[1]())
        def claim(self,*args):
            if args:raise PermissionError('synthetic legacy denial')
            return [{'batch_id':'40' if incorrect_selector or not self.locked else '50',
                     'job_id':'eligible','lease_token':'token'}]
        def expect_error(self,call,**kwargs):
            # This fake exercises only the selector experiment, not SQL ACLs.
            try:call()
            except PermissionError:pass
        def output(self,c):return {}
        def verify_binding(self,*args):pass
        @contextmanager
        def variant(self,*args,**kw):yield
        log=[]
    h=Fake()
    if incorrect_selector:
        with pytest.raises(AssertionError,match='held/ineligible'):runner.w01(h)
    else:runner.w01(h)
