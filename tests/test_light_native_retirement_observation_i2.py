"""Independent real-root read-only lifecycle tests with synthetic external ports.

Snapshot, locks, reconstruction, Agreement, local journal validation and bounded
fork/cleanup are real. Deployed runtime and aggregate live observer are explicit
ports here; the separate observer suite covers actual SQL/terminal verification.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from ops import light_native_bounded as bounded
from ops import light_native_lane_controller as owner
from ops import light_native_lane_run_guard as run_guard
from ops import light_native_retirement as r
from ops import light_native_retirement_live as live
from ops import native_maintenance_owner_attest as attest
from ops import native_maintenance_owner_host as host
from test_light_native_lane_issuer import request_bytes as base_request_bytes
from test_light_native_retirement import retained, store
from test_light_native_retirement_reference import reference
from test_light_native_retirement_observation import observation


PRIVATE = 'SYNTHETIC_PRIVATE_OBSERVATION_FAILURE_7091'


@pytest.fixture
def request_bytes(base_request_bytes):
    # Canonical synthetic deployed-package port, installed before retained()
    # constructs and pins all original journals and independent authority.
    t = base_request_bytes
    t.runtime = r.encoded(dict(source=owner.install.RETAINED_SOURCE,
                              runtime={'sha256': '9' * 64}))
    t.policy['accepted_runtime_sha256'] = r.sha(t.runtime)
    t.raw = r.encoded(t.policy)
    t.accepted = r.sha(t.raw)
    return t


def target(t):
    return t.root / t.entry / r.NAME


def install_proposal(t, state):
    if state == 'exact':
        store(t.root, t.entry + '/' + r.NAME, t.raw)
    elif state in ('partial', 'conflict'):
        store(t.root, t.entry + '/' + r.NAME, b'{')
    elif state == 'same-size':
        store(t.root, t.entry + '/' + r.NAME, b'x' * len(t.raw))


def fingerprint(root):
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        row = path.lstat()
        key = path.relative_to(root).as_posix()
        meta = r.metadata(row)
        if path.is_file() and not path.is_symlink():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
            try:
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    digest = hashlib.sha256(stream.read()).hexdigest()
            finally:
                os.close(fd)
            result[key] = (meta, row.st_atime_ns, digest)
        else:
            result[key] = (meta,)
    return result


@pytest.mark.parametrize('state', ['absent', 'exact', 'partial', 'same-size'])
def test_real_snapshot_proposal_status_without_content_export_or_writes(observation, state):
    t = observation
    install_proposal(t, state)
    before = fingerprint(t.root)
    with r.Snapshot(t.root, 'unused') as view:
        result = live.proposal_observation(view, t.public)
        assert live.proposal_observation(view, t.public) == result
        view.check()
    assert result['proposal_state'] == {'absent': 'ABSENT', 'exact': 'EXACT',
                                       'partial': 'CONFLICT', 'same-size': 'CONFLICT'}[state]
    assert result['record_matches'] is (state == 'exact')
    assert result['content_shape_valid'] is (state == 'exact')
    assert set(result) == {'proposal_state', 'metadata', 'record_matches', 'content_shape_valid'}
    assert fingerprint(t.root) == before


@pytest.mark.parametrize('fault', ['symlink', 'mode', 'uid', 'hardlink', 'oversize'])
def test_real_proposal_metadata_failure_is_not_absence(observation, fault):
    t = observation
    install_proposal(t, 'exact')
    path = target(t)
    if fault == 'symlink':
        path.unlink()
        path.symlink_to(t.root / t.entry / 'intent.json')
    elif fault == 'mode':
        path.chmod(0o644)
    elif fault == 'uid':
        os.chown(path, 12345, 0)
    elif fault == 'hardlink':
        os.link(path, t.root / 'synthetic-second-link')
    else:
        path.write_bytes(b'x' * 4097)
    with r.Snapshot(t.root, 'unused') as view:
        with pytest.raises((RuntimeError, OSError)):
            live.proposal_observation(view, t.public)


@pytest.mark.parametrize('fault', ['appearance', 'disappearance', 'replacement'])
def test_proposal_identity_or_presence_drift_refuses(observation, fault):
    t = observation
    if fault != 'appearance':
        install_proposal(t, 'exact')
    with r.Snapshot(t.root, 'unused') as view:
        live.proposal_observation(view, t.public)
        if fault == 'appearance':
            install_proposal(t, 'exact')
        elif fault == 'disappearance':
            target(t).unlink()
        else:
            fd = os.open(target(t), os.O_RDONLY | os.O_NOATIME)
            try:
                target(t).unlink()
                install_proposal(t, 'exact')
                assert os.fstat(fd).st_ino != target(t).stat().st_ino
            finally:
                os.close(fd)
        with pytest.raises((RuntimeError, OSError)):
            live.proposal_observation(view, t.public)


def test_real_shared_observation_locks_exclude_exclusive_writer_without_mutation(observation):
    t = observation
    before = fingerprint(t.root)
    with live.observation_locks(t.root):
        for directory in ('issuers', 'cycles'):
            fd = os.open(t.root / directory / 'cycle.lock', os.O_RDONLY | os.O_NOATIME)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
    assert fingerprint(t.root) == before


@pytest.mark.parametrize('fault', ['missing', 'symlink', 'mode', 'exclusive'])
def test_readonly_locks_never_create_or_repair(observation, fault):
    t = observation
    path = t.root / 'cycles' / 'cycle.lock'
    fd = None
    if fault == 'missing':
        path.unlink()
    elif fault == 'symlink':
        path.unlink()
        path.symlink_to(t.root / 'issuers' / 'cycle.lock')
    elif fault == 'mode':
        path.chmod(0o644)
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NOATIME)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises((RuntimeError, OSError)):
            with live.observation_locks(t.root):
                pytest.fail('invalid lock admitted')
        if fault == 'missing':
            assert not path.exists()
    finally:
        if fd is not None:
            os.close(fd)
    # Failure must also release the earlier issuer lock.
    fd = os.open(t.root / 'issuers' / 'cycle.lock', os.O_RDONLY | os.O_NOATIME)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


@pytest.fixture
def inspection_ports(observation, monkeypatch):
    t = observation
    assert os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0
    machine = os.uname()
    monkeypatch.setattr(live.os, 'uname', lambda: type(machine)(
        (machine.sysname, 'autopilot-lite-vnic', machine.release, machine.version, machine.machine)))
    ports = SimpleNamespace(fault=None)

    def forbidden(*args, **kwargs):
        raise AssertionError('PROHIBITED_STATE_WRITER')
    for module, name in ((live, 'write'), (r, 'write_proposal'), (owner, 'phase'),
                         (owner, 'retain'), (owner, 'bootstrap_helpers')):
        monkeypatch.setattr(module, name, forbidden)

    def runtime_validate(manifest, source, pin):
        assert manifest == {'sha256': '9' * 64} and pin == '9' * 64
        assert source == owner.install.RETAINED_SOURCE
        if ports.fault == 'runtime':
            raise RuntimeError(PRIVATE)
    monkeypatch.setattr(owner.release, 'validate', runtime_validate)
    monkeypatch.setattr(owner.release.staging, 'verify_release', lambda *args: None)
    monkeypatch.setattr(attest, 'parameters', lambda credential: {'synthetic': credential})

    class Connection:
        read_only = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql):
            assert self.read_only and sql == 'SELECT 1'
            return (1,)
    def connect(**kwargs):
        assert kwargs == {'synthetic': 'synthetic-credential', 'autocommit': True}
        if ports.fault == 'database':
            raise RuntimeError(PRIVATE)
        return Connection()
    @contextmanager
    def loaded(wheels):
        assert wheels == b'synthetic-wheel-port'
        yield SimpleNamespace(connect=connect), None
    monkeypatch.setattr(host, 'loaded_runtime', loaded)

    def checked_observer(view, proposal, conn, guard, *, pins, observation=False):
        assert observation is True and conn.read_only is True
        # Reuse REAL local journal validator inside the checked external port.
        live.local(view, proposal, pins, observation=True)
        assert conn.execute('SELECT 1') == (1,)
        if ports.fault == 'observer':
            raise RuntimeError(PRIVATE)
        if ports.fault == 'drift':
            target(t).write_bytes(b'SYNTHETIC_UNCOOPERATIVE_WRITER')
        if ports.fault == 'crash':
            os._exit(23)
        if ports.fault == 'timeout':
            time.sleep(1)
        return {'synthetic_checked_observer': True}
    monkeypatch.setattr(live, 'observer', checked_observer)
    def inspect():
        raw = r.encoded(t.public)
        return live.inspect_retirement(b'synthetic-wheel-port', 'synthetic-credential',
            t.request.controller, t.request.runtime, raw, r.sha(raw), t.request.guard)
    ports.inspect = inspect
    ports.data = t
    return ports


@pytest.mark.parametrize('state', ['absent', 'exact', 'conflict'])
def test_actual_bounded_inspection_is_readonly_and_never_calls_writer(inspection_ports, state, capsys):
    t = inspection_ports.data
    install_proposal(t, state)
    before = fingerprint(t.root)
    result = inspection_ports.inspect()
    assert result['state'] == 'OBSERVED' and result['phase'] == 'DONE'
    assert result['proposal_state'] == {'absent': 'ABSENT', 'exact': 'EXACT', 'conflict': 'CONFLICT'}[state]
    assert result['proposal_observation_final'] is True
    assert result['journal_pins_verified'] and result['hold_db_provider_verified']
    assert all(result[k] is False for k in ('incident_closed', 'execution_acknowledged', 'new_task_authorized'))
    assert fingerprint(t.root) == before
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('fault,phase', [('runtime', 'RUNTIME'), ('database', 'DATABASE_CONNECT'),
    ('observer', 'HOST_DB_PROVIDER'), ('authority', 'AUTHORITY'), ('pin', 'RECONSTRUCTION')])
def test_guard_refusal_has_fixed_phase_and_no_final_or_private_claim(inspection_ports, fault, phase, capsys):
    ports = inspection_ports
    t = ports.data
    install_proposal(t, 'exact')
    ports.fault = fault
    if fault == 'authority':
        t.public['accepted_controller_sha256'] = '0' * 64
    elif fault == 'pin':
        t.public['journal_map_sha256'] = '0' * 64
    before = fingerprint(t.root)
    result = ports.inspect()
    assert result['state'] == 'OBSERVATION_REFUSED' and result['phase'] == phase
    assert not result['proposal_observation_final']
    assert not result['journal_pins_verified'] and not result['hold_db_provider_verified']
    assert PRIVATE not in str(result) and 'PROHIBITED_STATE_WRITER' not in str(result)
    assert fingerprint(t.root) == before and capsys.readouterr() == ('', '')


def test_drift_after_early_proposal_readback_is_not_final_observation(inspection_ports):
    ports = inspection_ports
    install_proposal(ports.data, 'exact')
    ports.fault = 'drift'
    result = ports.inspect()
    assert result['state'] == 'OBSERVATION_REFUSED'
    assert not result['proposal_observation_final'] and not result['hold_db_provider_verified']
    assert target(ports.data).read_bytes() == b'SYNTHETIC_UNCOOPERATIVE_WRITER'
    assert 'UNCOOPERATIVE' not in str(result)


@pytest.mark.parametrize('fault', ['timeout', 'crash'])
def test_actual_supervisor_failure_stays_unknown_without_retry(inspection_ports, monkeypatch, fault):
    ports = inspection_ports
    ports.fault = fault
    original = bounded.run
    monkeypatch.setattr(bounded, 'run', lambda callback: original(callback, seconds=0.2))
    before = fingerprint(ports.data.root)
    with pytest.raises(RuntimeError, match='^LANE_RETIREMENT_UNKNOWN$'):
        ports.inspect()
    assert fingerprint(ports.data.root) == before


@pytest.mark.parametrize('readonly', [False, True])
def test_workflow_profiles_cannot_be_swapped(readonly):
    workflow = run_guard.OBSERVATION_WORKFLOW if readonly else run_guard.WORKFLOW
    env = dict(GITHUB_SHA='a' * 40, GITHUB_REPOSITORY=run_guard.REPOSITORY,
        GITHUB_REF='refs/heads/main', GITHUB_EVENT_NAME='workflow_dispatch',
        GITHUB_WORKFLOW_REF=run_guard.REPOSITORY + '/' + workflow + '@refs/heads/main',
        GITHUB_WORKFLOW_SHA='a' * 40, GITHUB_JOB='step', GITHUB_ACTOR=run_guard.OWNER,
        GITHUB_TRIGGERING_ACTOR=run_guard.OWNER, GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1')
    assert run_guard.local_context(env, read_only=readonly) == ('a' * 40, 123, 1)
    with pytest.raises(RuntimeError):
        run_guard.local_context(env, read_only=not readonly)
    assert run_guard.LaneObservationBinding.workflow != run_guard.LaneRunBinding.workflow


def test_observe_workflow_and_fixed_wrapper_have_no_private_dispatch_interpolation():
    root = Path(__file__).resolve().parents[1]
    raw = (root / run_guard.OBSERVATION_WORKFLOW).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == run_guard.OBSERVATION_WORKFLOW_SHA256
    steps = False
    for line in raw.decode().splitlines():
        if line.startswith('    steps:'): steps = True
        elif line.startswith('  ') and not line.startswith('   ') and line.strip(): steps = False
        if steps: assert 'inputs.' not in line and 'inputs[' not in line
    wrapper = (root / 'ops/light_native_retirement_observe_runner.py').read_text()
    assert 'main(read_only=True)' in wrapper
    assert 'sys.argv' not in wrapper and 'os.environ' not in wrapper
