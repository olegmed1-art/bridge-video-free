"""Cold-process import boundary, not a live-host/driver verification test.

Each case packs the actual 35 controller helpers into a disposable directory and
starts Python -I -S -B. Earlier observer guards and external ports are explicit
synthetic adapters. The actual inspect_retirement, attest module, permission
engine and installed psycopg binary import execute; no credential or live I/O
is used. The driver adapter exposes its site directory only upon context entry.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from ops import light_native_lane_controller as owner


CHILD = r'''
import base64, contextlib, hashlib, importlib, json, os, pathlib, sys, time
import importlib.util
from types import SimpleNamespace

root, site, mode = sys.argv[1:]
assert sys.flags.isolated == 1 and sys.flags.no_site == 1
assert not any(x == site for x in sys.path)
assert not any(x.startswith('psycopg') for x in sys.modules)
assert importlib.util.find_spec('psycopg') is None
sys.path.insert(0, root)

def no_external(event, args):
    if event in ('socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system'):
        raise AssertionError('EXTERNAL_IO_FORBIDDEN')
sys.addaudithook(no_external)

if mode == 'cold-counterexample':
    try:
        from ops.native_maintenance_owner_attest import parameters
    except ModuleNotFoundError as exc:
        assert exc.name == 'psycopg'
        print('COLD_ATTEST_REQUIRES_DRIVER')
    else:
        raise AssertionError('COLD_IMPORT_UNEXPECTEDLY_SUCCEEDED')
    raise SystemExit(0)

from ops import light_native_retirement_live as live
from ops import light_native_retirement as r
from ops import light_native_lane_controller as owner
from ops import native_maintenance_owner_host as host
from ops import oracle_autopilot_source_preflight as preflight
from ops import light_native_bounded as bounded

assert 'ops.native_maintenance_owner_attest' not in sys.modules
assert 'database.native_cli_permission_engine' not in sys.modules
assert importlib.util.find_spec('psycopg') is None
events = []
entered = False

@contextlib.contextmanager
def context(value):
    yield value

class View:
    def check(self):
        events.append('snapshot-check')

class Connection:
    read_only = False
    def __enter__(self):
        events.append('connection-enter')
        return self
    def __exit__(self, *args):
        events.append('connection-exit')

def connect(**kwargs):
    assert entered
    assert kwargs['autocommit'] is True
    assert kwargs['password'] == 'synthetic-not-a-credential'
    events.append('connect')
    if mode == 'connect-failure':
        raise RuntimeError('SYNTHETIC_PRIVATE_CONNECT_FAILURE')
    return Connection()

@contextlib.contextmanager
def loader(wheels):
    global entered
    assert wheels == b'synthetic-driver-port'
    events.append('loader-enter')
    assert 'ops.native_maintenance_owner_attest' not in sys.modules
    assert not any(x.startswith('psycopg') for x in sys.modules)
    if mode == 'loader-failure':
        raise RuntimeError('SYNTHETIC_PRIVATE_DRIVER_FAILURE')
    entered = True
    sys.path.insert(0, site)
    try:
        psycopg = importlib.import_module('psycopg')
        assert psycopg.__version__ == '3.3.4'
        assert psycopg.pq.__impl__ == 'binary'
        assert pathlib.Path(psycopg.__file__).resolve().is_relative_to(pathlib.Path(site).resolve())
        psycopg.connect = connect
        if mode == 'attest-import-failure':
            # A finder refuses the real attest import after driver entry; it
            # does not substitute a fake attest or psycopg module.
            class RefuseAttest:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == 'ops.native_maintenance_owner_attest':
                        assert entered
                        events.append('attest-import')
                        raise ModuleNotFoundError('SYNTHETIC_PRIVATE_ATTEST_FAILURE')
            sys.meta_path.insert(0, RefuseAttest())
        yield psycopg, 'synthetic-runtime-id'
    finally:
        sys.path.remove(site)
        entered = False
        events.append('loader-exit')

# These replace earlier protocol/host ports only. Their semantics and real
# filesystem behavior are covered by the separate Snapshot/observer suites.
raw = b'public-reference-port'
record_pin = 'a' * 64
runtime = r.encoded({'source': owner.install.RETAINED_SOURCE,
                     'runtime': {'sha256': 'b' * 64}})
now = time.time()
stamp = lambda t: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))
reference = dict(source='c' * 40, accepted_controller_sha256='d' * 64,
                 accepted_runtime_sha256=r.sha(runtime), record_sha256=record_pin,
                 agreement=dict(not_before=stamp(now-60), expires_at=stamp(now+600)))
live.os = SimpleNamespace(geteuid=lambda: 0,
                          uname=lambda: SimpleNamespace(nodename='autopilot-lite-vnic'))
live.public_reference = lambda value, **kwargs: reference
owner.validate_package = lambda *args: None
owner.release.staging.require_current_main = lambda *args: None
owner.release.validate = lambda *args: None
owner.release.staging.verify_release = lambda *args: None
owner.execution.plan.source_path = lambda *args: pathlib.Path('/synthetic-not-read')
live.observation_locks = lambda *args: context(None)
r.Snapshot = lambda *args: context(View())
live.proposal_observation = lambda *args: dict(proposal_state='ABSENT', metadata=None,
                                             record_matches=False, content_shape_valid=False)
live.resolve_reference = lambda *args, **kwargs: (b'private-request-port', 'e' * 64)
agreement = SimpleNamespace(scope='synthetic', assert_held=lambda *args: None)
live.write_request = lambda *args, **kwargs: (
    dict(record_sha256=record_pin, historical_journal_sha256={}), b'proposal-port', agreement)
r.record = lambda *args: {'synthetic': True}
live.local = lambda *args, **kwargs: events.append('local')
host.loaded_runtime = loader
preflight.connection_parameters = lambda *args: {'password': 'synthetic-not-a-credential'}
bounded.run = lambda operation: operation()  # No fork/root operation in this boundary test.

def observer(view, proposal, conn, guard, **kwargs):
    assert entered and conn.read_only is True
    from database import native_cli_permission_engine as engine
    from ops import native_maintenance_owner_attest as attest
    import psycopg
    assert engine.sql is psycopg.sql and attest.engine is engine
    assert pathlib.Path(engine.__file__).resolve().is_relative_to(pathlib.Path(root).resolve())
    assert pathlib.Path(attest.__file__).resolve().is_relative_to(pathlib.Path(root).resolve())
    events.append('observer')
    return {'synthetic': True}
live.observer = observer

def forbidden(*args, **kwargs):
    raise AssertionError('WRITE_OR_SERVICE_FORBIDDEN')
live.write = forbidden
r.retain = forbidden
owner.phase = forbidden

result = live.inspect_retirement(b'synthetic-driver-port', 'synthetic-input', b'controller-port',
                                 runtime, raw, r.sha(raw), SimpleNamespace(assert_current=lambda: None))
assert 'local' in events
assert 'loader-enter' in events, ('COLD_OBSERVER_IMPORT_BEFORE_DRIVER', events, result)
assert events.index('local') < events.index('loader-enter'), (events, result)
assert not entered
assert all('SYNTHETIC_PRIVATE' not in str(value) for value in result.values())
if mode == 'success':
    assert result['state'] == 'OBSERVED' and result['phase'] == 'DONE', result
    assert events.count('observer') == 2
    assert events.index('loader-enter') < events.index('connect') < events.index('loader-exit')
    assert result['hold_db_provider_verified'] is True  # Synthetic aggregate port only.
else:
    assert result['state'] == 'OBSERVATION_REFUSED', result
    assert result['phase'] == ('DATABASE_CONNECT' if mode == 'connect-failure' else 'RUNTIME'), result
    assert result['hold_db_provider_verified'] is False
    assert result['journal_pins_verified'] is False
    assert 'observer' not in events
    if mode != 'loader-failure':
        assert events.count('loader-exit') == 1
assert not any(result[k] for k in ('incident_closed', 'execution_acknowledged', 'new_task_authorized'))
print('ISOLATED_IMPORT_BOUNDARY_PASS', mode)
'''


@pytest.mark.parametrize('mode', ['cold-counterexample', 'success', 'loader-failure',
                                 'attest-import-failure', 'connect-failure'])
def test_isolated_packed_observer_import_boundary(tmp_path, mode):
    repository = Path(__file__).resolve().parents[1]
    names = tuple(dict.fromkeys((*owner.release.HELPERS, *owner.EXTRA)))
    assert len(names) == 35
    with tempfile.TemporaryDirectory(prefix='packed-helpers-', dir=tmp_path) as temporary:
        helpers = Path(temporary)
        for name in names:
            path = helpers / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'' if name.endswith('/__init__.py') else (repository / name).read_bytes())
        spec = importlib.util.find_spec('psycopg')
        assert spec is not None and spec.origin
        site = Path(spec.origin).resolve().parent.parent
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', CHILD,
                                 str(helpers), str(site), mode], capture_output=True,
                                text=True, timeout=45, env={'PATH': '/usr/bin:/bin', 'PSYCOPG_IMPL': 'binary'})
    assert result.returncode == 0, result.stdout + result.stderr
    expected = 'COLD_ATTEST_REQUIRES_DRIVER' if mode == 'cold-counterexample' else 'ISOLATED_IMPORT_BOUNDARY_PASS ' + mode
    assert result.stdout.strip() == expected
