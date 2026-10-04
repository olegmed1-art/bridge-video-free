"""Independent cold writer boundary and disposable Linux/root persistence tests.

Actual write, bounded supervisor, issuer/cycle locks, Snapshot, record validation,
write_proposal, attest/permission engine and binary driver imports execute. Only
authority/source/hostname, verified-loader provisioning, DB connection and the
aggregate live observer are synthetic ports. No production path or credential is
used. Driver dependencies become visible only inside the synthetic loader.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from ops import light_native_lane_controller as owner


pytestmark = pytest.mark.skipif(
    sys.platform != 'linux' or getattr(os, 'geteuid', lambda: -1)() != 0,
    reason='Requires isolated Linux/root disposable fixtures',
)

CHILD = r'''
import contextlib, fcntl, importlib, importlib.util, json, os, pathlib, sys
from types import SimpleNamespace

packed, driver_json, mode = sys.argv[1:]
packed = pathlib.Path(packed)
driver_roots = json.loads(driver_json)
assert set(driver_roots) == {'psycopg', 'psycopg_binary', 'typing_extensions'}
sites = list(dict.fromkeys(driver_roots.values()))
assert sys.flags.isolated == sys.flags.no_site == 1
assert all(site not in sys.path for site in sites)
assert all(name not in sys.modules and importlib.util.find_spec(name) is None
           for name in driver_roots)
sys.path.insert(0, str(packed))

def no_external(event, args):
    if event in ('socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system'):
        raise AssertionError('EXTERNAL_IO_FORBIDDEN')
sys.addaudithook(no_external)

if mode == 'cold-counterexample':
    try:
        from ops.native_maintenance_owner_attest import parameters
    except ModuleNotFoundError as exc:
        assert exc.name == 'psycopg'
        print('COLD_WRITER_ATTEST_REQUIRES_DRIVER')
    else:
        raise AssertionError('COLD_IMPORT_UNEXPECTEDLY_SUCCEEDED')
    raise SystemExit(0)

from ops import light_native_retirement_live as live
from ops import light_native_retirement as r
from ops import light_native_lane_controller as owner
from ops import native_maintenance_owner_host as host
from ops import oracle_autopilot_source_preflight as preflight

assert 'ops.native_maintenance_owner_attest' not in sys.modules
assert 'database.native_cli_permission_engine' not in sys.modules
assert os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0
root = packed / 'disposable-owner'
root.mkdir(mode=0o700)
owner.ROOT = root
event_path = packed / 'events.txt'  # Test oracle outside the Snapshot root.

def event(name):
    with event_path.open('a', encoding='ascii') as stream:
        stream.write(name + '\n')

proposal = dict(version=1, kind='FAILED_PREPARE_RETIREMENT_PROPOSAL',
    requested_disposition='RETIRE_FAILED_PREPARE_AND_CANCEL_OLD_POLICY_REMAINDER',
    policy_sha256='a'*64, index=0, plan_sha256='b'*64,
    repository='synthetic/repository', failed_work_key='synthetic-work', failed_target_pr=1,
    predecessor=dict(sequence=2, plan_sha256='c'*64, terminal_sha256='d'*64),
    incident_sha256='e'*64, historical_controller_source='f'*40,
    historical_controller_sha256='1'*64, retained_runtime_source=owner.install.RETAINED_SOURCE,
    retained_runtime_sha256='2'*64, original_failure='UNKNOWN',
    interpretation='EXPLICIT_NEW_POLICY_HASH_ALLOWLIST_AND_FRESH_GUARDS_REQUIRED',
    **{key: False for key in r.FALSE_FLAGS})
payload = r.encoded(proposal)
pin = r.sha(payload)
assert r.record(payload, pin) == proposal
entry = r.entry(proposal)
for rel in ('issuers', 'cycles', 'issuers/' + proposal['policy_sha256'], entry):
    (root / rel).mkdir(mode=0o700)
for rel in ('issuers/cycle.lock', 'cycles/cycle.lock', entry + '/intent.json'):
    (root / rel).write_bytes(b'{}')
    (root / rel).chmod(0o600)
target = root / entry / r.NAME
initial = None
if mode in ('existing-exact', 'existing-conflict'):
    initial = payload if mode == 'existing-exact' else b'{"synthetic":"conflict"}'
    target.write_bytes(initial)
    target.chmod(0o600)
originals = {str(p.relative_to(root)): (p.read_bytes(), r.metadata(p.stat()))
             for p in root.rglob('*') if p.is_file()}
entered = False
observer_calls = 0

class Connection:
    read_only = False
    def __enter__(self):
        event('connection-enter')
        return self
    def __exit__(self, *args):
        event('connection-exit')

def connect(**kwargs):
    assert entered and kwargs == dict(password='synthetic-not-a-credential', autocommit=True)
    event('connect')
    if mode == 'connect-failure':
        raise RuntimeError('SYNTHETIC_PRIVATE_CONNECT_FAILURE')
    return Connection()

@contextlib.contextmanager
def loader(wheels):
    global entered
    assert wheels == b'synthetic-loader-input'
    event('loader-enter')
    # Both actual exclusive locks must already be held before driver loading.
    for rel in ('issuers/cycle.lock', 'cycles/cycle.lock'):
        fd = os.open(root / rel, os.O_RDONLY)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise AssertionError('MISSING_REAL_WRITER_LOCK')
        finally:
            os.close(fd)
    assert 'ops.native_maintenance_owner_attest' not in sys.modules
    assert not any(name.startswith('psycopg') for name in sys.modules)
    if mode == 'loader-failure':
        raise RuntimeError('SYNTHETIC_PRIVATE_LOADER_FAILURE')
    entered = True
    sys.path[:0] = sites
    try:
        psycopg = importlib.import_module('psycopg')
        assert psycopg.__version__ == '3.3.4' and psycopg.pq.__impl__ == 'binary'
        for name, location in driver_roots.items():
            module = importlib.import_module(name)
            assert pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path(location).resolve())
        assert sys.modules['psycopg_binary'].__version__ == '3.3.4'
        psycopg.connect = connect
        if mode == 'attest-import-failure':
            class RefuseAttest:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == 'ops.native_maintenance_owner_attest':
                        event('attest-import')
                        raise ModuleNotFoundError('SYNTHETIC_PRIVATE_ATTEST_FAILURE')
            sys.meta_path.insert(0, RefuseAttest())
        yield psycopg, 'synthetic-runtime-id'
    finally:
        for site in sites:
            sys.path.remove(site)
        entered = False
        event('loader-exit')

runtime = r.encoded(dict(source=owner.install.RETAINED_SOURCE, runtime={'sha256':'3'*64}))
raw = r.encoded({'action':'retire-prepare'})
agreement = SimpleNamespace(scope='synthetic-only', assert_held=lambda scope: event('agreement'))
live.os = SimpleNamespace(geteuid=os.geteuid,
                          uname=lambda: SimpleNamespace(nodename='autopilot-lite-vnic'))
live.write_request = lambda *args: (
    dict(source='4'*40, record_sha256=pin, historical_journal_sha256={}), payload, agreement)
owner.release.validate = lambda *args: None
owner.release.staging.verify_release = lambda *args: None
owner.release.staging.require_current_main = lambda *args: event('source')
owner.execution.plan.source_path = lambda *args: packed / 'synthetic-unread-release'
host.loaded_runtime = loader
preflight.connection_parameters = lambda *args: {'password':'synthetic-not-a-credential'}

def observer(view, value, conn, guard, **kwargs):
    global observer_calls
    assert entered and conn.read_only is True and value == proposal
    from database import native_cli_permission_engine as engine
    from ops import native_maintenance_owner_attest as attest
    import psycopg
    assert engine.sql is psycopg.sql and attest.engine is engine
    assert pathlib.Path(engine.__file__).resolve().is_relative_to(packed.resolve())
    assert pathlib.Path(attest.__file__).resolve().is_relative_to(packed.resolve())
    assert view.names(entry, allow_output=True) == {'intent.json'}
    assert view.read(entry + '/intent.json') == b'{}'
    observer_calls += 1
    event('observer')
    if mode == 'post-observer-failure' and observer_calls == 3:
        assert target.read_bytes() == payload
        raise RuntimeError('SYNTHETIC_PRIVATE_AFTER_CREATE_FAILURE')
    return {'synthetic-aggregate': True}
live.observer = observer

class FaultOS:
    # Inject only the writer's syscalls; bounded's pipe writer stays real.
    def __getattr__(self, name):
        return getattr(os, name)
    def write(self, fd, data):
        if mode == 'partial-write-failure':
            event('partial-write')
            os.write(fd, data[:17])
            raise OSError('SYNTHETIC_PRIVATE_SHORT_WRITE')
        return os.write(fd, data)
    def fsync(self, fd):
        if mode == 'fsync-failure':
            event('fsync-failure')
            raise OSError('SYNTHETIC_PRIVATE_FSYNC')
        return os.fsync(fd)
r.os = FaultOS()

# Actual supervisor executes the actual writer once. Its child failure becomes
# UNKNOWN without serializing private exception text or implicitly retrying.
result = None
error = None
try:
    result = live.write(b'synthetic-loader-input', 'synthetic-credential-port',
                        b'synthetic-controller-port', runtime, raw, r.sha(raw),
                        SimpleNamespace(assert_current=lambda: None))
except RuntimeError as exc:
    error = str(exc)
events = event_path.read_text().splitlines() if event_path.exists() else []
assert events.count('loader-enter') == 1, ('COLD_WRITER_IMPORT_BEFORE_DRIVER', events, error)
assert 'SYNTHETIC_PRIVATE' not in str(result) + str(error)
assert not entered and all(site not in sys.path for site in sites)
assert all(name not in sys.modules for name in driver_roots)
assert (root / entry).is_dir()
for rel, (data, metadata) in originals.items():
    path = root / rel
    assert path.read_bytes() == data and r.metadata(path.stat()) == metadata, rel
# Returning from the real supervisor must release its owned children's locks.
for rel in ('issuers/cycle.lock', 'cycles/cycle.lock'):
    fd = os.open(root / rel, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)

if mode in ('success', 'existing-exact'):
    assert error is None, (error, events)
    assert result['state'] == ('PROPOSAL_RETAINED_UNACCEPTED' if mode == 'success'
                                else 'PROPOSAL_PRESENT_UNACCEPTED'), result
    assert result['record_sha256'] == pin
    assert not any(result[key] for key in ('incident_closed','execution_acknowledged','new_task_authorized'))
    assert target.read_bytes() == payload and target.stat().st_mode & 0o777 == 0o600
    assert events.count('observer') == (3 if mode == 'success' else 2)
else:
    assert result is None and error == 'LANE_RETIREMENT_UNKNOWN', (result, error, events)
    if mode == 'existing-conflict':
        assert target.read_bytes() == initial and 'observer' not in events
    elif mode == 'partial-write-failure':
        assert target.read_bytes() == payload[:17] and events.count('partial-write') == 1
    elif mode in ('fsync-failure', 'post-observer-failure'):
        assert target.read_bytes() == payload
    else:
        assert not target.exists() and 'observer' not in events
if mode not in ('loader-failure',):
    assert events.count('loader-exit') == 1
assert events.count('connect') == (0 if mode in ('loader-failure','attest-import-failure') else 1)
expected_files = set(originals)
if target.exists():
    expected_files.add(entry + '/' + r.NAME)
assert {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()} == expected_files
print('ISOLATED_WRITER_BOUNDARY_PASS', mode)
'''


@pytest.mark.parametrize('mode', [
    'cold-counterexample', 'success', 'loader-failure', 'attest-import-failure',
    'connect-failure', 'existing-exact', 'existing-conflict', 'partial-write-failure',
    'fsync-failure', 'post-observer-failure',
])
def test_isolated_packed_writer_import_boundary(tmp_path, mode):
    repository = Path(__file__).resolve().parents[1]
    names = tuple(dict.fromkeys((*owner.release.HELPERS, *owner.EXTRA)))
    assert len(names) == 35
    with tempfile.TemporaryDirectory(prefix='packed-writer-', dir=tmp_path) as directory:
        packed = Path(directory)
        for name in names:
            path = packed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'' if name.endswith('/__init__.py') else (repository / name).read_bytes())
        driver_roots = {}
        for name in ('psycopg', 'psycopg_binary', 'typing_extensions'):
            spec = importlib.util.find_spec(name)
            assert spec is not None and spec.origin
            location = Path(spec.origin).resolve()
            driver_roots[name] = str(location.parent.parent if spec.submodule_search_locations is not None else location.parent)
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', '-c', CHILD, str(packed), json.dumps(driver_roots), mode],
            capture_output=True, text=True, timeout=65,
            env={'PATH':'/usr/bin:/bin', 'PSYCOPG_IMPL':'binary'},
        )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = ('COLD_WRITER_ATTEST_REQUIRES_DRIVER' if mode == 'cold-counterexample'
                else 'ISOLATED_WRITER_BOUNDARY_PASS ' + mode)
    assert result.stdout.strip() == expected
