"""Real derive/launch/supervisor/request/feed boundary, with synthetic host ports.

No sequence, launch, supervisor, request, feed, Permit, history, root metadata,
run_once, admission or restore implementation is replaced. Systemd/OS process,
GitHub authority and DB transports are explicit synthetic adapters; all durable
records and controller helper verification use actual fixture files.
"""
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from database.light_native_pilot_intake import Plan
from ops import light_native_lane_controller as owner
from ops import light_native_lane_execution as execution
from ops import light_native_lane_feed as feed
from ops import light_native_lane_issuer as issuer
from ops import light_native_retirement as retirement
from ops.native_maintenance_agreement import COVERAGE
from oracle_autopilot import light_native_lane as lane
from test_oracle_autopilot_light_native_adapter import rig
from test_oracle_autopilot_light_native_pilot import permit_for


pytestmark = pytest.mark.skipif(
    sys.platform != 'linux' or getattr(os, 'geteuid', lambda: -1)() != 0,
    reason='actual Linux root metadata; required by native CI')


@pytest.fixture
def sequence_host(tmp_path, monkeypatch, rig):
    install = execution.install
    events = []
    now = int(time.time())
    source = install.RETAINED_SOURCE
    controller_source = 'a' * 40
    root = tmp_path / 'owner'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(owner, 'ROOT', root)
    monkeypatch.setattr(execution, 'ROOT', tmp_path / 'execution')
    for key in ('CONTROL', 'STATE', 'LEDGER'):
        path = tmp_path / key
        path.mkdir(mode=0o700)
        monkeypatch.setattr(install, key, path)
    (install.CONTROL / 'jobs').mkdir(mode=0o750)
    install.write_new(install.CONTROL / 'admission', b'HOLD\n', 0o640)
    install.write_new(install.STATE / 'pilot.lock', b'', 0o600)
    unit = tmp_path / 'native.service'
    install.write_new(unit, install.render(source), 0o644)
    monkeypatch.setattr(install, 'UNIT_FILE', unit)
    user = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
    monkeypatch.setattr(execution.pwd, 'getpwnam', lambda name: user)
    actual_uname = os.uname()
    monkeypatch.setattr(os, 'uname', lambda: SimpleNamespace(
        nodename='autopilot-lite-vnic', machine=actual_uname.machine))

    # Verify a real, complete synthetic controller tree, not a no-op verifier.
    repo = Path(__file__).resolve().parents[1]
    helpers = {name: ('' if name.endswith('/__init__.py') else
                     (repo / name).read_text(encoding='utf-8'))
               for name in dict.fromkeys((*owner.release.HELPERS, *owner.EXTRA))}
    package = dict(version=1, kind='LIGHT_LANE_CONTROLLER',
                   source=controller_source, helpers=helpers)
    (root / 'controllers').mkdir(mode=0o700)
    owner.bootstrap_helpers(package)
    controller_sha = owner.sha(owner.encoded(package))
    original = dict(ActiveState='active', SubState='running', MainPID='555',
                    InvocationID='a' * 32, NRestarts='0')
    prior = install.hold.HoldIdentity('autopilot-lite-vnic', 123, 'b' * 32,
                                    str(execution.plan.source_path('b' * 40)), 'synthetic')
    state = dict(permanent=original.copy(), transient=False, digest=None,
                 fault=None, accepted_permit=None, error=None)
    guard = SimpleNamespace(run_id=17, attempt=1,
                            assert_running=lambda: events.append('authority'),
                            assert_current=lambda: events.append('authority'))
    monkeypatch.setattr(install.release.staging, 'require_current_main',
                        lambda value: events.append('main'))
    monkeypatch.setattr(install.hold, 'service_hold_identity', lambda: prior)
    monkeypatch.setattr(install, 'verify_unit', lambda value: events.append('unit'))
    monkeypatch.setattr(install, 'verify_hold_process', lambda *args: original.copy())
    monkeypatch.setattr(install, 'show', lambda unit, fields:
                        {key: state['permanent'][key] for key in fields})
    monkeypatch.setattr(execution.switch, 'unchanged_files',
                        lambda *args: events.append('protected'))
    monkeypatch.setattr(execution.switch, 'unit_absent',
                        lambda unit: events.append('transient-absent'))

    def permanent_stopped():
        assert state['permanent']['MainPID'] == '0'
    monkeypatch.setattr(install, 'stopped', permanent_stopped)
    monkeypatch.setattr(execution, 'owned', lambda *args: state['transient'])
    def transient_stopped():
        assert not state['transient']
    monkeypatch.setattr(execution, 'stopped', transient_stopped)
    process = dict(ActiveState='active', SubState='running', MainPID='777',
                   InvocationID='c' * 32, NRestarts='0')
    monkeypatch.setattr(execution, 'process', lambda value: process.copy())
    def journal(invocation):
        running = (install.CONTROL / 'admission').read_bytes() == b'RUN\n'
        return [owner.encoded(dict(audit='LIGHT_NATIVE_LANE',
                state='AWAITING_OWNER' if running else 'HOLD',
                **({'terminal_sha256': 'e' * 64} if running else {}))).decode()]
    monkeypatch.setattr(execution, 'journal', journal)

    def command(*args, **kwargs):
        events.append(args)
        if args[:2] == ('/usr/bin/systemctl', 'stop'):
            if args[2] == install.UNIT:
                state['permanent'].update(ActiveState='inactive', SubState='dead', MainPID='0')
            elif args[2] == execution.UNIT:
                state['transient'] = False
            else:
                raise AssertionError('unexpected stop')
        elif args[0] == '/usr/bin/systemd-run':
            state['transient'] = True
        else:
            raise AssertionError('unexpected system command')
    monkeypatch.setattr(execution.switch, 'command', command)
    def show(unit, fields):
        assert unit == execution.SUPERVISOR
        digest = state['digest']
        script = execution.ROOT / digest / 'supervisor.py'
        wrap = lambda mode: ('{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I -S -B '
                             + str(script) + ' ' + mode + ' ' + digest + ' ; ignore_errors=no ; }')
        return dict(MainPID=str(os.getpid()), ActiveState='active', User='root', Group='root',
                    Restart='no', NoNewPrivileges='yes', KillMode='control-group',
                    ExecStart=wrap('run'), ExecStopPost=wrap('restore'),
                    RuntimeMaxUSec=str(owner.FIRST_EXECUTION_SECONDS) + 's', TimeoutStopUSec='120s')
    monkeypatch.setattr(execution.switch, 'show', show)

    from ops import native_maintenance_owner_host as host
    from ops import native_maintenance_owner_attest as attest
    from ops import light_native_lane_run_guard as authority
    from ops import light_native_pilot_owner as pilot
    from oracle_autopilot import light_native_preflight as preflight
    class Connection:
        read_only = False
        def __enter__(self): return self
        def __exit__(self, *args): return False
    @contextmanager
    def loaded(wheels):
        assert wheels == b'synthetic-wheels'
        yield SimpleNamespace(connect=lambda **kwargs: Connection()), None
    monkeypatch.setattr(host, 'loaded_runtime', loaded)
    monkeypatch.setattr(attest, 'parameters', lambda credential: {})
    monkeypatch.setattr(authority, 'authenticated', lambda *args: guard)
    monkeypatch.setattr(pilot, 'observed_target', lambda *args: events.append('github-pr'))
    def observe(conn, dispatch, **kwargs):
        assert conn.read_only is True
        assert dispatch == state['accepted_permit']['dispatch']
        events.append('db-read')
        return state['accepted_permit']['owner_preflight']
    monkeypatch.setattr(preflight, 'observe', observe)

    # The only launch transport replacement models PID1 dispatch/ExecStopPost.
    # It calls the actual supervisor for both modes; protocol code is not mocked.
    def systemd_run(command, *, input, **kwargs):
        assert command[0] == '/usr/bin/systemd-run' and command[-2] == 'run'
        digest = command[-1]
        state['digest'] = digest
        path = execution.ROOT / digest / 'request.json'
        request = owner.parse(path.read_bytes())
        state['request'] = request
        if state['fault']:
            if state['fault'] == 'missing':
                request.pop('sequence', None); request['version'] = 1
            else:
                request['sequence'] = request.get('sequence', 0) + 1
            raw = owner.encoded(request)
            digest = owner.sha(raw)
            new = execution.ROOT / digest
            install.fresh_directory(new, 0o700)
            for name in ('supervisor.py', 'owner-context.json'):
                owner.retain(new / name, owner.read(path.parent / name))
            owner.retain(new / 'request.json', raw)
            state['digest'] = digest
        monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(input)))
        events.append('supervisor-run')
        try:
            owner.supervisor('run', digest)
        except RuntimeError as exc:
            state['error'] = str(exc)
            owner.supervisor('restore', digest)
            raise
        owner.supervisor('restore', digest)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(owner.subprocess, 'run', systemd_run)

    def prepare(policy_version, number):
        previous = None
        for seq in range(number + 1):
            request = deepcopy(rig[0])
            dispatch = f'12345678-1234-4234-8234-{seq + 100:012d}'
            request['dispatch_id'] = dispatch
            request['assignment'].update(dispatch_id=dispatch,
                task_id='12345678-1234-4234-8234-123456789044', role='AUTOPILOT')
            template, _ = permit_for(request, [110, 10])
            value = deepcopy(template.value)
            value.update(source=source, issued_at=now - 1, expires_at=now + 1800)
            value['owner_preflight'].update(issued_at=value['issued_at'], expires_at=value['expires_at'])
            raw = lane.encoded(value)
            cursor = lane.encoded(dict(version=1, source=source, sequence=seq,
                dispatch_id=dispatch, permit_sha256=lane.digest(raw), previous_terminal_sha256=previous))
            job = install.CONTROL / 'jobs' / dispatch
            job.mkdir(mode=0o750)
            install.write_new(job / 'permit.json', raw, 0o640)
            if seq == number:
                install.write_new(install.CONTROL / 'current.json', cursor, 0o640)
                install.write_new(install.LEDGER / f'{seq:08d}-feed-intent.json', cursor, 0o600)
                state['accepted_permit'] = value
                break
            terminal = lane.encoded(lane.terminal_record(cursor, request,
                dict(state='DONE', dispatch_id=dispatch, provider_task_id='task_e_synthetic',
                     terminal=dict(status='SUCCEEDED', provider_evidence_sha256='f' * 64))))
            install.write_new(install.STATE / f'{seq:08d}-intent.json', cursor, 0o600)
            install.write_new(install.STATE / f'{seq:08d}-terminal.json', terminal, 0o600)
            claim = install.STATE / ('claim-' + dispatch)
            claim.mkdir(mode=0o700)
            install.write_new(claim / 'request.json', lane.encoded(request), 0o600)
            install.write_new(claim / 'permit.json', raw, 0o600)
            install.write_new(job / 'accepted-terminal.json',
                              lane.encoded(lane.acceptance_record(cursor, terminal)), 0o640)
            previous = lane.digest(terminal)
        plan_raw = owner.encoded(dict(version=1, source=source,
            repository='olegmed1-art/bridge-video-free', target_pr=request['target_pr'],
            expected_head_sha=request['expected_head_sha'], branch=request['branch'],
            work_key='sequence-regression', objective='Synthetic read-only audit.', priority=0,
            task_spec_json=request['assignment']['task_spec_json']))
        plan = Plan(plan_raw, owner.sha(plan_raw))
        stamp = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        predecessor = dict(sequence=number - 1, plan_sha256='b' * 64,
                           terminal_sha256=previous)
        policy = dict(version=policy_version, action='issue', source=controller_source,
            not_before=stamp(now - 1), expires_at=stamp(now + 1800),
            accepted_controller_sha256=controller_sha, accepted_runtime_sha256='f' * 64,
            predecessor=predecessor,
            plans=[dict(plan_base64=base64.b64encode(plan_raw).decode(), accepted_plan_sha256=plan.digest)],
            authority=dict(owner='olegmed1-art', coverage=COVERAGE,
                           delegation='FINITE_ISSUER_AGREEMENTS', evidence='Synthetic fixture acceptance.'))
        if policy_version == 2:
            policy.update(retirements=[dict(policy_sha256='d' * 64, index=0, record_sha256='e' * 64)],
                          retirement_evidence={'d' * 64: dict.fromkeys(retirement.JOURNAL_KEYS, 'f' * 64)})
            retirement.references(policy['retirements'])
            retirement.journal_pins(policy['retirement_evidence']['d' * 64])
        assert issuer.policy_shape(policy)
        value = issuer.derive(policy, owner.sha(owner.encoded(policy)), 0, predecessor, now)['prepare']
        directory = root / plan.digest
        directory.mkdir(mode=0o700)
        owner.retain(directory / 'plan.json', plan_raw)
        owner.retain(directory / 'wheels.tar', b'synthetic-wheels')
        owner.retain(directory / 'permit.json', raw)
        assert len(feed.completed_history(source)) == number
        return value, plan, directory, dict(cursor_sha256=lane.digest(cursor), dispatch_id=dispatch)

    def launch(policy_version=2, number=3, fault=None):
        state['fault'] = fault
        value, plan, directory, result = prepare(policy_version, number)
        state['prepare'] = value
        return owner.launch(b'synthetic-wheels', 'synthetic-credential', 'synthetic-token',
                            plan, directory, value, prior,
                            dict(protected={}, protected_sha256='f' * 64), result, guard)
    return SimpleNamespace(launch=launch, state=state, events=events, install=install)


@pytest.mark.parametrize('policy_version,number', [(2, 3), (2, 4), (1, 3)],
                         ids=['retirement-sequence-3', 'retirement-sequence-4', 'legacy-v2-sequence-3'])
def test_derived_prepare_reaches_real_supervisor_and_feed(sequence_host, policy_version, number):
    host = sequence_host
    result = host.launch(policy_version, number)
    assert host.state['prepare']['version'] == (3 if policy_version == 2 else 2)
    assert host.state['request']['version'] == 2 and host.state['request']['sequence'] == number
    assert result['state'] == 'STOPPED_HOLD'
    assert host.state['error'] is None and not host.state['transient']
    assert 'db-read' in host.events and 'supervisor-run' in host.events
    assert host.install.hold.read(host.install.CONTROL / 'admission', 0o640, 16) == b'HOLD\n'
    target = execution.ROOT / result['request_sha256']
    assert owner.parse(owner.read(target / 'outcome.json'))['state'] == 'READBACK_REQUIRED'
    assert owner.parse(owner.read(target / 'stopped-hold.json'))['state'] == 'STOPPED_HOLD'


@pytest.mark.parametrize('fault', ['missing', 'wrong'])
def test_actual_supervisor_refuses_missing_or_wrong_sequence_before_service_start(sequence_host, fault):
    host = sequence_host
    with pytest.raises(RuntimeError, match='LANE_EXEC_FEED_CHANGED'):
        host.launch(fault=fault)
    assert host.state['error'] == 'LANE_EXEC_FEED_CHANGED'
    assert not any(isinstance(x, tuple) for x in host.events)
    assert not host.state['transient']
    assert host.install.hold.read(host.install.CONTROL / 'admission', 0o640, 16) == b'HOLD\n'
    target = execution.ROOT / host.state['digest']
    assert not (target / 'launch-intent.json').exists()
    assert owner.parse(owner.read(target / 'stopped-hold.json'))['state'] == 'ORIGINAL_HOLD'
