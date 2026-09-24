"""A rollout process must never process a notification or reconnect as work."""
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from oracle_autopilot import worker


@pytest.mark.parametrize('value',['','hold','HOLD ','true','DISABLED','active'])
def test_unknown_admission_never_connects(value,monkeypatch):
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE',value)
    connect=Mock(side_effect=AssertionError('must not connect'))
    monkeypatch.setattr(worker,'_connect',connect)
    with pytest.raises(RuntimeError,match='ADMISSION_MODE_INVALID'):
        worker.run_forever(None)
    connect.assert_not_called()


def test_default_keeps_existing_active_contract(monkeypatch):
    monkeypatch.delenv('AUTOPILOT_ADMISSION_MODE',raising=False)
    assert worker.admission_mode() == 'ACTIVE'
    worker.require_active_admission()


def test_hold_blocks_direct_rpc_and_every_drain(monkeypatch):
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE','HOLD')
    for name in ['drain_ready','drain_project_work','drain_role_dispatch_outbox','_connect']:
        monkeypatch.setattr(worker,name,Mock(side_effect=AssertionError(name)))
    with pytest.raises(RuntimeError,match='ADMISSION_HELD'):
        worker.drain_cycle(None)
    with pytest.raises(RuntimeError,match='ADMISSION_HELD'):
        worker._rpc_one(None,'SELECT mutating_rpc()',())


def test_notify_and_reconnect_do_not_activate(monkeypatch):
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE','HOLD')
    config=worker.WorkerConfig('dsn-not-printed','oracle-autopilot-light-1')
    listener=Mock()
    listener.execute.return_value.fetchone.return_value=('on',)
    calls=[]
    @contextmanager
    def connect(dsn,**kwargs):
        calls.append(kwargs)
        if len(calls)==1:
            raise worker.psycopg.OperationalError('private connection detail')
        yield listener
    monkeypatch.setattr(worker.psycopg,'connect',connect)
    monkeypatch.setattr(worker.time,'sleep',lambda _:None)
    # Two wakes are processed without visiting any queue lane, then clean exit.
    wakes=Mock(side_effect=[None,None,KeyboardInterrupt])
    monkeypatch.setattr(worker,'wait_for_wakeup',wakes)
    monkeypatch.setattr(worker,'drain_cycle',Mock(side_effect=AssertionError('must not drain')))
    worker.run_forever(config)
    assert len(calls)==2 and wakes.call_count==3
    assert all('-c default_transaction_read_only=on' in c['options'] for c in calls)
    assert [c.args[0] for c in listener.execute.call_args_list]==[
        'SHOW default_transaction_read_only','LISTEN autopilot_ready']


def test_hold_rejects_nonreadonly_connection(monkeypatch):
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE','HOLD')
    conn=Mock()
    conn.execute.return_value.fetchone.return_value=('off',)
    @contextmanager
    def connect(*a,**kw):
        yield conn
    monkeypatch.setattr(worker.psycopg,'connect',connect)
    with pytest.raises(RuntimeError,match='HOLD_READONLY_REQUIRED'):
        worker.run_forever(worker.WorkerConfig('dsn','test'))
    assert conn.execute.call_count==1
