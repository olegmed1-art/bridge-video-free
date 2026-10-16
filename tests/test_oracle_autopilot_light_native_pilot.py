from copy import deepcopy
import fcntl
import os

import pytest

from oracle_autopilot import codex_cli_bridge as bridge
from oracle_autopilot.light_native_pilot import Claim, Permit, Session
from ops.native_permission_hold_guard import EXPECTED_TARGET
from test_oracle_autopilot_light_native_adapter import rig


def permit_for(request, clock):
    value = dict(version=1, source='c'*40,
        dispatch={k: deepcopy(v) for k, v in request.items() if k != 'reservation_id'},
        environment_id='a'*32, environment_evidence_sha256='d'*64, issued_at=100, expires_at=200)
    value['owner_preflight'] = dict(target=EXPECTED_TARGET,
        dispatch_sha256=bridge.digest(bridge.canonical(value['dispatch'])),
        task_id=request['assignment']['task_id'], role_id=request['assignment']['role'],
        work_item_id='12345678-1234-4234-8234-123456789055', goal_json_sha256='e'*64,
        successor_task_key=None, can_repair=False,
        publication=dict(status='PUBLISHED', version=3, comment_id=123),
        competing_native_receipts=0, agreement_sha256='f'*64, issued_at=100, expires_at=200)
    raw = bridge.canonical(value).encode()
    reader = {'raw': raw}
    permit = Permit(raw, bridge.digest(raw.decode()), lambda: reader['raw'], 'c'*40,
                    clock=lambda: clock[0], monotonic=lambda: clock[1])
    return permit, reader


class Connection:
    autocommit = True
    closed = False

    def __init__(self, adapter, state):
        self.adapter, self.state = adapter, state
        self.held = False
        self.pid = 101

    def execute(self, sql, params=()):
        if 'pg_try_advisory_lock' in sql:
            self.held = True
            self.row = (self.pid, True)
        elif sql.startswith('SELECT EXISTS'):
            self.row = (self.held,)
        elif 'pg_locks' in sql:
            self.row = (self.pid, self.held)
        elif 'native_cli_reserve' in sql:
            self.row = (deepcopy(self.state['row']),)
        else:
            self.row = (self.adapter._rpc(sql, params)['payload'],)
        return self

    def fetchone(self):
        return self.row


def make_session(rig, tmp_path, clock=None):
    request, state, make = rig
    request['assignment'].update(task_id='12345678-1234-4234-8234-123456789044', role='auditor')
    state['row']['request']['assignment'].update(request['assignment'])
    clock = clock or [110, 10]
    permit, reader = permit_for(request, clock)
    os.chmod(tmp_path, 0o700)
    claim = Claim(tmp_path)
    adapter = make()
    conn = Connection(adapter, state)
    session = Session(permit, claim, conn, adapter._read_pr, adapter._provider, admission=lambda: True)
    return session, claim, conn, reader, clock


def test_complete_then_restart_retains_same_task_and_terminal(rig, tmp_path):
    session, claim, conn, _, _ = make_session(rig, tmp_path)
    try:
        request = session.reserve()
        result = session.step()
        assert result['state'] == 'DONE'
    finally:
        claim.close()
    second, claim, *_ = make_session(rig, tmp_path)
    try:
        assert second.reserve() == request
        assert second.step() == result
        assert rig[1]['creates'] == rig[1]['finishes'] == 1
    finally:
        claim.close()


def test_same_connection_cannot_reenter_session_lock(rig, tmp_path):
    session, claim, conn, *_ = make_session(rig, tmp_path)
    try:
        with pytest.raises(RuntimeError, match='ALREADY_BOUND'):
            Session(session.permit, claim, conn, session.read_pr, session.provider, admission=lambda: True)
    finally:
        claim.close()


def test_claim_rejects_another_dispatch_after_process_restart(rig, tmp_path):
    session, claim, *_ = make_session(rig, tmp_path)
    claim.close()
    changed = deepcopy(rig[0])
    changed['dispatch_id'] = changed['assignment']['dispatch_id'] = '12345678-1234-4234-8234-123456789099'
    permit, _ = permit_for(changed, [110, 10])
    with Claim(tmp_path) as claim:
        with pytest.raises(RuntimeError, match='PILOT_RECORD_CONFLICT'):
            claim.retain('permit.json', permit.raw)
    assert rig[1]['creates'] == 0


@pytest.mark.parametrize('fault', ['database_lock', 'database_pid', 'file_lock', 'permit', 'wall_expiry', 'monotonic_expiry'])
def test_lost_authority_latches_without_creating(rig, tmp_path, fault):
    session, claim, conn, reader, clock = make_session(rig, tmp_path)
    try:
        session.reserve()
        if fault == 'database_lock': conn.held = False
        if fault == 'database_pid': conn.pid += 1
        if fault == 'file_lock': fcntl.flock(claim.lock, fcntl.LOCK_UN)
        if fault == 'permit': reader['raw'] = b'{}'
        if fault == 'wall_expiry': clock[0] = 201
        if fault == 'monotonic_expiry': clock[1] = 101
        with pytest.raises(RuntimeError): session.step()
        assert rig[1]['creates'] == 0
        conn.held, conn.pid, reader['raw'], clock[:] = True, 101, session.permit.raw, [110, 10]
        with pytest.raises(RuntimeError, match='ALREADY_FAILED'): session.step()
    finally:
        claim.close()


def test_connection_loss_after_create_recovers_original_id_on_restart(rig, tmp_path):
    session, claim, conn, *_ = make_session(rig, tmp_path)
    session.reserve()
    original = session.provider.submit
    def submit(*args, **kwargs):
        result = original(*args, **kwargs)
        conn.closed = True
        return result
    session.provider.submit = submit
    try:
        with pytest.raises(RuntimeError): session.step()
        assert rig[1]['creates'] == 1 and rig[1]['row']['state'] == 'RESERVED'
    finally:
        claim.close()
    session, claim, *_ = make_session(rig, tmp_path)
    try:
        session.reserve()
        assert session.step()['state'] == 'DONE'
        assert rig[1]['creates'] == 1
    finally:
        claim.close()


def test_two_local_controllers_cannot_share_claim(rig, tmp_path):
    session, claim, *_ = make_session(rig, tmp_path)
    try:
        with pytest.raises(BlockingIOError): Claim(tmp_path)
    finally:
        claim.close()


def test_partial_request_record_is_quarantined(rig, tmp_path):
    session, claim, *_ = make_session(rig, tmp_path)
    path = tmp_path / 'request.json'
    path.write_bytes(b'{')
    path.chmod(0o600)
    try:
        with pytest.raises(RuntimeError, match='PILOT_RECORD_CONFLICT'): session.reserve()
        assert rig[1]['creates'] == 0 and path.read_bytes() == b'{'
    finally:
        claim.close()


@pytest.mark.parametrize('name', ['permit.json', 'request.json'])
@pytest.mark.parametrize('fault', ['missing', 'changed'])
def test_retained_record_loss_latches_and_never_recreates(rig, tmp_path, name, fault):
    session, claim, *_ = make_session(rig, tmp_path)
    try:
        session.reserve()
        path = tmp_path / name
        original = path.read_bytes()
        if fault == 'missing': path.unlink()
        else: path.write_bytes(b'{}')
        with pytest.raises((RuntimeError, FileNotFoundError)): session.step()
        assert rig[1]['creates'] == 0
        assert path.exists() is (fault != 'missing')
        path.write_bytes(original)
        path.chmod(0o600)
        with pytest.raises(RuntimeError, match='ALREADY_FAILED'): session.step()
    finally:
        claim.close()


def test_pr_drift_latches_even_after_pr_recovers(rig, tmp_path):
    session, claim, *_ = make_session(rig, tmp_path)
    original = session.read_pr
    def closed(number):
        pr = original(number)
        pr['state'] = 'closed'
        return pr
    try:
        session.read_pr = closed
        with pytest.raises(RuntimeError, match='PILOT_PR_CHANGED'): session.reserve()
        session.read_pr = original
        with pytest.raises(RuntimeError, match='ALREADY_FAILED'): session.reserve()
        assert rig[1]['creates'] == 0
    finally:
        claim.close()


def test_live_hold_latches_all_effects(rig, tmp_path):
    session, claim, *_ = make_session(rig, tmp_path)
    try:
        session.reserve()
        session.admission = lambda: False
        with pytest.raises(RuntimeError, match='PILOT_NOT_ADMITTED'): session.step()
        session.admission = lambda: True
        with pytest.raises(RuntimeError, match='ALREADY_FAILED'): session.step()
        assert rig[1]['creates'] == 0
    finally:
        claim.close()


def test_exact_resume_does_not_reauthorize_moved_pr(rig, tmp_path):
    session, claim, *_ = make_session(rig, tmp_path)
    request = session.reserve()
    claim.close()
    session, claim, *_ = make_session(rig, tmp_path)
    try:
        session.read_pr = lambda _: pytest.fail('resume must not reauthorize PR')
        assert session.reserve() == request
        assert rig[1]['creates'] == 0
    finally:
        claim.close()


@pytest.mark.parametrize('key,value', [('successor_task_key', 'next-task'), ('can_repair', True),
    ('competing_native_receipts', 1), ('expires_at', 201), ('goal_json_sha256', 'invalid')])
def test_owner_preflight_scope_must_match_window(rig, tmp_path, key, value):
    session, claim, *_ = make_session(rig, tmp_path)
    try:
        changed = deepcopy(session.permit.value)
        changed['owner_preflight'][key] = value
        raw = bridge.canonical(changed).encode()
        with pytest.raises(RuntimeError, match='PILOT_OWNER_PREFLIGHT_INVALID'):
            Permit(raw, bridge.digest(raw.decode()), lambda: raw, 'c'*40,
                   clock=lambda: 110, monotonic=lambda: 10)
    finally:
        claim.close()
