"""Real filesystem locks and Session/NativeQueue/adapter/delivery composition."""
from copy import deepcopy
from contextlib import contextmanager

import pytest

from oracle_autopilot import light_native_lane as lane
from oracle_autopilot.light_native_pilot import Session
from test_oracle_autopilot_light_native_adapter import rig
from test_oracle_autopilot_light_native_pilot import Connection, permit_for

SOURCE = 'c' * 40


class Feed:
    allowed = True
    raw = b''
    selected = None
    reads = 0
    accepted = None

    def acceptance(self, value):
        if self.accepted is None:
            raise FileNotFoundError('owner acceptance missing')
        return self.accepted

    def admitted(self):
        return self.allowed

    def current(self):
        self.reads += 1
        return self.raw

    def permit(self, value):
        return self.selected


def select(feed, request, sequence=0, previous=None):
    clock = [110, 10]
    permit, reader = permit_for(request, clock)
    feed.selected = permit
    feed.raw = lane.encoded(dict(version=1, source=SOURCE, sequence=sequence,
        dispatch_id=request['dispatch_id'], permit_sha256=lane.digest(permit.raw),
        previous_terminal_sha256=previous))
    return clock, reader


def bind(rig):
    request, state, make = rig
    request['assignment'].update(task_id='12345678-1234-4234-8234-123456789044', role='auditor')
    state['row']['request']['assignment'].update(request['assignment'])
    return request, state, make


@contextmanager
def consumer(tmp_path, rig, feed=None, hook=None):
    request, state, make = bind(rig)
    feed = feed or Feed()
    if not feed.raw:
        select(feed, request)
    def execute(permit, claim, admitted):
        adapter = make()
        conn = Connection(adapter, state)
        session = Session(permit, claim, conn, adapter._read_pr, adapter._provider, admission=admitted)
        request = session.reserve()
        if hook:
            hook(session)
        try:
            return request, session.step()
        finally:
            conn.closed = True
    tmp_path.chmod(0o700)
    with lane.Journal(tmp_path) as journal:
        yield lane.Lane(SOURCE, journal, feed, execute), feed, state


def test_two_distinct_jobs_need_exact_owner_acceptance_and_keep_claims(rig, tmp_path):
    with consumer(tmp_path, rig) as (worker, feed, state):
        first = worker.tick()
        first_request = deepcopy(state['row']['request'])
        assert first['state'] == 'AWAITING_OWNER'
        assert worker.tick() == first
        assert state['creates'] == state['finishes'] == 1
        request = rig[0]
        request['dispatch_id'] = request['assignment']['dispatch_id'] = '12345678-1234-4234-8234-123456789099'
        request['reservation_id'] = '12345678-1234-4234-8234-123456789098'
        request['assignment']['task_id'] = '12345678-1234-4234-8234-123456789097'
        state.update(row=dict(state='RESERVED', request=deepcopy(request), provider_task_id=None, terminal=None),
                     intent=False, creation=None)
        feed.accepted = lane.encoded(lane.acceptance_record(
            (tmp_path/'00000000-intent.json').read_bytes(),
            (tmp_path/'00000000-terminal.json').read_bytes()))
        select(feed, request, 1, first['terminal_sha256'])
        second = worker.tick()
        assert second['terminal_sha256'] != first['terminal_sha256']
        assert state['creates'] == state['finishes'] == 2
        claims = sorted(tmp_path.glob('claim-*'))
        assert len(claims) == 2
        assert lane.parse((claims[0]/'request.json').read_bytes()) == first_request
        assert len(worker.journal.history(SOURCE)) == 2


@pytest.mark.parametrize('fault', ['wrong_digest', 'skip', 'old_dispatch'])
def test_owner_cursor_cannot_skip_or_reuse(rig, tmp_path, fault):
    with consumer(tmp_path, rig) as (worker, feed, state):
        result = worker.tick()
        request = deepcopy(rig[0])
        if fault != 'old_dispatch':
            request['dispatch_id'] = request['assignment']['dispatch_id'] = '12345678-1234-4234-8234-123456789099'
        select(feed, request, 2 if fault == 'skip' else 1,
               '0'*64 if fault == 'wrong_digest' else result['terminal_sha256'])
        with pytest.raises(RuntimeError):
            worker.tick()
        assert state['creates'] == 1
        assert worker.journal.read('quarantine.json') is not None


def test_idle_hold_opens_no_feed_or_effects(rig, tmp_path):
    with consumer(tmp_path, rig) as (worker, feed, state):
        feed.allowed = False
        assert worker.tick() == {'state': 'HOLD'}
        assert feed.reads == state['creates'] == 0
        assert state['calls'] == []
        assert not list(tmp_path.glob('*-intent.json'))


@pytest.mark.parametrize('fault', ['hold', 'cursor', 'expiry', 'unknown', 'connection'])
def test_failure_latches_across_process_restart_without_next_submission(rig, tmp_path, fault):
    def hook(session):
        if fault == 'hold':
            feed.allowed = False
        elif fault == 'cursor':
            feed.raw = b'{}'
        elif fault == 'expiry':
            session.permit.deadline = -1
        elif fault == 'unknown':
            session.provider.submit = lambda *a, **kw: {'state': 'UNKNOWN'}
        else:
            session.conn.closed = True
    with consumer(tmp_path, rig, hook=hook) as (worker, feed, state):
        with pytest.raises(RuntimeError):
            worker.tick()
        before = len(state['calls'])
        feed.allowed = True
        select(feed, rig[0])
        with pytest.raises(RuntimeError, match='QUARANTINED'):
            worker.tick()
    with consumer(tmp_path, rig, feed=feed) as (worker, _, state):
        with pytest.raises(RuntimeError, match='QUARANTINED'):
            worker.tick()
        assert len(state['calls']) == before


def test_terminal_restart_is_read_only_and_never_creates_again(rig, tmp_path):
    with consumer(tmp_path, rig) as (worker, feed, state):
        result = worker.tick()
        calls = len(state['calls'])
    with consumer(tmp_path, rig, feed=feed) as (worker, _, state):
        assert worker.tick() == result
        assert len(state['calls']) == calls
        assert state['creates'] == 1


def test_process_death_before_local_terminal_requires_owner_recovery(rig, tmp_path):
    # Simulate process death after database terminal, before local terminal write:
    # keep the fsynced intent, claim, provider journal and native DB state.
    with consumer(tmp_path, rig) as (worker, feed, state):
        original = worker.journal.retain
        def crash(name, raw):
            if name.endswith('-terminal.json'):
                raise SystemExit('process died')
            original(name, raw)
        worker.journal.retain = crash
        # Use _tick to model SIGKILL, which cannot run exception cleanup.
        with pytest.raises(SystemExit):
            worker._tick()
        assert state['row']['state'] == 'TERMINAL'
    with consumer(tmp_path, rig, feed=feed) as (worker, _, state):
        with pytest.raises(RuntimeError, match='INTERRUPTED'):
            worker.tick()
        assert state['creates'] == state['finishes'] == 1


@pytest.mark.parametrize('fault', ['partial_intent', 'missing_intent', 'bad_terminal', 'symlink'])
def test_corrupt_durable_records_fail_closed(rig, tmp_path, fault):
    with consumer(tmp_path, rig) as (worker, feed, state):
        worker.tick()
        path = tmp_path/'00000000-intent.json'
        if fault == 'partial_intent':
            path.write_bytes(b'{')
        elif fault == 'missing_intent':
            path.unlink()
        elif fault == 'bad_terminal':
            (tmp_path/'00000000-terminal.json').write_bytes(b'{}')
        else:
            data = path.read_bytes()
            path.unlink()
            target = tmp_path.parent/'external'
            target.write_bytes(data)
            target.chmod(0o600)
            path.symlink_to(target)
        with pytest.raises((RuntimeError, ValueError, OSError)):
            worker.tick()
        assert state['creates'] == 1


def test_global_lock_excludes_second_consumer(rig, tmp_path):
    with consumer(tmp_path, rig):
        with pytest.raises(BlockingIOError):
            lane.Journal(tmp_path)


def test_root_feed_requires_environment_and_live_admission(monkeypatch):
    reads = []
    def root_read(path, limit):
        reads.append(path)
        return b'RUN\n'
    monkeypatch.setattr(lane, 'root_bytes', root_read)
    feed = lane.OwnerFeed(SOURCE)
    monkeypatch.delenv('AUTOPILOT_ADMISSION_MODE', raising=False)
    assert feed.admitted() is False and not reads
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE', 'NATIVE')
    assert feed.admitted() is True
    monkeypatch.setattr(lane, 'root_bytes', lambda *a: b'HOLD\n')
    assert feed.admitted() is False


def test_rejected_terminal_cannot_advance_without_owner(rig, tmp_path):
    def block(session):
        original = session.provider.collect
        def collect(*a, **kw):
            result = original(*a, **kw)
            result['report']['status'] = 'BLOCKED'
            result['report_sha256'] = lane.bridge.digest(lane.bridge.canonical(result['report']))
            return result
        session.provider.collect = collect
    with consumer(tmp_path, rig, hook=block) as (worker, feed, state):
        result = worker.tick()
        assert result['state'] == 'AWAITING_OWNER'
        assert worker.tick() == result
        assert state['row']['terminal']['status'] == 'BLOCKED'
        assert state['creates'] == 1


def test_orphan_claim_is_not_ignored(rig, tmp_path):
    with consumer(tmp_path, rig) as (worker, feed, state):
        (tmp_path/'claim-12345678-1234-4234-8234-123456789000').mkdir(mode=0o700)
        with pytest.raises(RuntimeError, match='ORPHAN'):
            worker.tick()
        assert state['creates'] == 0


def test_cursor_digest_without_separate_owner_acceptance_cannot_advance(rig, tmp_path):
    with consumer(tmp_path, rig) as (worker, feed, state):
        first = worker.tick()
        request = deepcopy(rig[0])
        request['dispatch_id'] = request['assignment']['dispatch_id'] = '12345678-1234-4234-8234-123456789099'
        select(feed, request, 1, first['terminal_sha256'])
        with pytest.raises(FileNotFoundError):
            worker.tick()
        assert state['creates'] == 1


@pytest.mark.parametrize('after_submit', [False, True])
def test_killed_during_work_never_resumes_or_selects_next(rig, tmp_path, after_submit):
    def killed(session):
        if after_submit:
            original = session.provider.submit
            def submit(*a, **kw):
                original(*a, **kw)
                raise SystemExit('killed after creation')
            session.provider.submit = submit
        else:
            raise SystemExit('killed after reservation')
    with consumer(tmp_path, rig, hook=killed) as (worker, feed, state):
        with pytest.raises(SystemExit):
            worker._tick()  # SIGKILL bypasses the durable exception latch.
        calls = len(state['calls'])
    with consumer(tmp_path, rig, feed=feed) as (worker, _, state):
        with pytest.raises(RuntimeError, match='INTERRUPTED'):
            worker.tick()
        assert len(state['calls']) == calls
        assert state['creates'] == int(after_submit)
