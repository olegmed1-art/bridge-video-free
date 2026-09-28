"""Offline safety/compatibility tests; never connect to Oracle, GitHub or Neon."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import types
from contextlib import nullcontext
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'ops/incident/light_after_probe_20260928.py'
spec = importlib.util.spec_from_file_location('incident_probe', SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Connection:
    def __init__(self, observed='on'):
        self.calls = []
        self.observed = observed
        self.closed = False
        self.read_only = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def execute(self, sql):
        self.calls.append((self.read_only, sql))
        return self

    def fetchone(self):
        return (self.observed,)


class ProbeTests(unittest.TestCase):
    def test_readonly_before_any_original_query_and_closed(self):
        conn = Connection()
        connect = Mock(return_value=conn)
        with probe.readonly_connection(connect, {'password': 'private'}) as returned:
            self.assertIs(returned, conn)
            self.assertEqual(conn.calls, [(True, 'SET default_transaction_read_only=on'),
                                         (True, 'SHOW default_transaction_read_only'),
                                         (True, 'SHOW transaction_read_only')])
        self.assertTrue(conn.closed)
        connect.assert_called_once_with(password='private', autocommit=True)

    def test_non_readonly_session_refuses_and_closes(self):
        conn = Connection('off')
        with self.assertRaisesRegex(RuntimeError, '^PROBE_READ_ONLY$'):
            with probe.readonly_connection(lambda **_: conn, {}):
                self.fail('unsafe connection returned')
        self.assertTrue(conn.closed)

    def test_error_output_does_not_echo_secrets(self):
        for exc in [RuntimeError('token=private DSN=private'),
                    RuntimeError('DATABASE_NOT_DRAINED', 'private'),
                    ValueError('private'), KeyError('private')]:
            self.assertEqual(probe.code(exc), 'UNCLASSIFIED')
        self.assertEqual(probe.code(RuntimeError('DATABASE_NOT_DRAINED')), 'DATABASE_NOT_DRAINED')

    def test_changed_bundle_refused_before_decoder_execution(self):
        with self.assertRaisesRegex(RuntimeError, '^PROBE_SOURCE$'):
            with probe.source_runtime(b'{"files": {"evil": "private"}}'):
                self.fail('entered unverified source')

    def test_scope_is_incident_specific(self):
        self.assertEqual(probe.SOURCE, '8bbc1d61010ef70c3fca02b5151ac86fce02a144')
        self.assertEqual(probe.FAILED_RUN['run_id'], 36388443245)
        self.assertEqual(probe.REQUEST, '01112f4599bd21c72e29dc5f890d9d538d39fa7976e17c26b674b258a40f043a')

    def test_real_original_bundle_loads_without_modified_source(self):
        # Bundle contains the historical FILES set, not this new helper.
        from ops import native_maintenance_bundle as bundle
        payload = bundle.build(ROOT, probe.SOURCE)
        self.assertEqual(bundle.digest(payload), probe.BUNDLE)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.json'
            path.write_bytes(payload)
            code = '''
import importlib.util, pathlib, sys, types
spec=importlib.util.spec_from_file_location('probe',sys.argv[1])
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
raw=pathlib.Path(sys.argv[2]).read_bytes()
with p.source_runtime(raw) as root:
 from ops import native_maintenance_stage_request as request
 from ops import native_maintenance_stage_inspect as inspection
 p.source_origins(root)
 assert pathlib.Path(request.__file__).is_relative_to(root)
 assert 'light_after_probe_20260928.py' not in str(list(root.rglob('*')))
 print('EXACT_SOURCE_PASS')
'''
            result = subprocess.run([sys.executable, '-I', '-B', '-S', '-c', code,
                                     str(SCRIPT), str(path)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'EXACT_SOURCE_PASS')
            polluted = code.replace('with p.source_runtime(raw) as root:',
                "sys.modules['ops']=types.ModuleType('ops')\nwith p.source_runtime(raw) as root:")
            result = subprocess.run([sys.executable, '-I', '-B', '-S', '-c', polluted,
                                     str(SCRIPT), str(path)], capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('PROBE_ISOLATION', result.stderr)


    def test_live_check_sequence_is_fail_closed(self):
        # Stubs represent external observations, not a claim of live success.
        # Failures must stop before subsequent checks or snapshot collection.
        names = ('database.native_cli_permission_engine',
                 'ops.native_maintenance_coordination', 'ops.oracle_light_active_hold_attest',
                 'ops.native_maintenance_owner_attest', 'ops.native_maintenance_stage_request',
                 'ops.native_maintenance_stage_inspect', 'ops.native_maintenance_run_guard',
                 'ops.native_maintenance_workflow_api', 'ops.native_maintenance_workflow_pause',
                 'ops.native_permission_hold_guard')
        for failing, expected_phase in [('workflow', 'workflow_drain'),
                ('prior', 'prior_host_drain'), ('backend', 'owner_backend_drain'),
                ('snapshot', 'after_snapshot'), (None, 'continuity')]:
            with self.subTest(failing=failing):
                modules = {n: types.ModuleType(n) for n in ('ops', 'database', *names)}
                for n in names:
                    setattr(modules[n.split('.')[0]], n.split('.')[1], modules[n])
                events = []
                def gate(name):
                    events.append(name)
                    if failing == name:
                        raise RuntimeError('synthetic')
                target = {'neon': {}}
                packet = {'stage': 'restore', 'expected_outcome': 'AFTER',
                    'scope': {'target': target, 'hold': {}}, 'plan': {},
                    'prior_units': [{'supervisor': {'unit': 'from-accepted-request'}}]}
                engine = modules[names[0]]
                engine.NeonBinding = lambda **_: None
                engine.Target = lambda **_: target
                def snapshot(*_):
                    gate('snapshot')
                    return {'checked': True}
                engine.snapshot = snapshot
                engine.dormant = lambda _: None
                engine.digest = lambda _: probe.AFTER
                coord = modules[names[1]]
                def observer(name):
                    return lambda *a, **k: types.SimpleNamespace(assert_drained=lambda: gate(name))
                coord.WorkflowDrain = observer('workflow')
                coord.PriorSupervisors = observer('prior')
                coord.OwnedConnections = observer('backend')
                hold = modules[names[2]]
                hold.HoldIdentity = lambda **_: 'private-hold'
                hold.attest = lambda: 'private-hold'
                modules[names[3]].parameters = lambda _: {}
                modules[names[4]].read_request = lambda _: b'accepted-request'
                modules[names[4]].AcceptedRequest = lambda *a: object()
                modules[names[5]].inspection_packet = lambda *a: packet
                modules[names[5]].failed_run = lambda *a: None
                modules[names[6]].PersistentAPI = lambda _: nullcontext(object())
                modules[names[7]].Transport = lambda *a, **k: object()
                modules[names[7]].source_matches = lambda *a: None
                modules[names[8]].digest = lambda _: probe.SCOPE
                modules[names[9]].EXPECTED_TARGET = target
                conn = Connection()
                conn.transaction = lambda: nullcontext()
                with patch.dict(sys.modules, modules), patch.object(probe, 'source_origins'), \
                        patch.object(probe, 'observed_journals', return_value=nullcontext()):
                    if failing:
                        with self.assertRaisesRegex(RuntimeError, '^synthetic$'):
                            probe.observe(lambda **_: conn, 'secret', 'token', ROOT)
                    else:
                        probe.observe(lambda **_: conn, 'secret', 'token', ROOT)
                self.assertEqual(probe.PHASE, expected_phase)
                sequence = ['workflow', 'prior', 'backend', 'snapshot']
                self.assertEqual(events, sequence if failing is None else sequence[:sequence.index(failing)+1])

    def test_recorded_runs_require_exact_identity(self):
        origin = dict(run_id=1, attempt=1, job_id=10)
        execute = dict(run_id=2, attempt=1, job_id=20)
        scope = {'origin_run': origin}
        priors = [{'run': origin}, {'run': execute}]
        journal = types.SimpleNamespace(records=[{'event': dict(kind='BOUND', scope=scope)},
            {'event': dict(kind='PREPARED', run=origin)},
            {'event': dict(kind='SESSION_RESULT', run=execute)}])
        probe.recorded_hosts(journal, scope, priors)
        journal.records[-1]['event']['run'] = {**execute, 'job_id': 21}
        with self.assertRaisesRegex(RuntimeError, '^RUNTIME_RECORDED_HOST_OMITTED$'):
            probe.recorded_hosts(journal, scope, priors)
        journal.records[0]['event']['scope'] = {'changed': True}
        with self.assertRaisesRegex(RuntimeError, '^RUNTIME_BOUND_JOURNAL$'):
            probe.recorded_hosts(journal, scope, priors)

    def test_journal_observation_never_creates_or_changes_files(self):
        from ops import native_maintenance_store as storage
        from ops import native_maintenance_snapshot as snapshot
        from ops import native_maintenance_stage_inspect as inspector
        from ops.native_maintenance_workflow_pause import Journal, encoded
        origin = dict(run_id=1, attempt=1, job_id=10)
        other = dict(run_id=2, attempt=1, job_id=20)
        scope = {'origin_run': origin}
        packet = dict(stage='restore', scope=scope, prior_units=[{'run': origin}, {'run': other}])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'store'; root.mkdir(mode=0o700)
            base = root / probe.SCOPE; base.mkdir(mode=0o700)
            for name in ('operation', 'pause', 'units'):
                (base / name).mkdir(mode=0o700)
            for path, raw in ((root / 'VERSION', storage.VERSION), (root / 'lock', b''),
                              (base / 'manifest.json', b'manifest')):
                path.write_bytes(raw); path.chmod(0o600)
            for row in packet['prior_units']:
                path = base / 'units' / (str(row['run']['run_id']) + '-1.json')
                path.write_bytes(encoded(row)); path.chmod(0o600)
            with Journal(base / 'operation') as op:
                op.append(dict(kind='BOUND', scope=scope))
                op.append(dict(kind='SESSION_RESULT', run=other))
            with Journal(base / 'pause'):
                pass
            def inventory():
                return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            # Local fixture is not a production trusted mount. Retain real Journal
            # opening/flocking/validation, substitute only storage trust and pair parsing.
            with patch.object(storage, 'PARENT', Path(tmp)), patch.object(storage, 'NAME', 'store'), \
                    patch.object(storage, 'trusted_parent'), patch.object(storage, 'persistent_mount'), \
                    patch.object(storage, 'private_directory', side_effect=lambda p: p.lstat()), \
                    patch.object(storage, 'open_file', side_effect=lambda fd,n,f: os.open(n,f|os.O_NOFOLLOW,dir_fd=fd)), \
                    patch.object(inspector, 'private_read', side_effect=lambda p,n: p.read_bytes()), \
                    patch.object(probe, 'MANIFEST', probe.digest(b'manifest')), \
                    patch.object(probe, 'PAIR', probe.digest(b'pair')), \
                    patch.object(snapshot, 'capture_locked', return_value=b'pair'):
                before = inventory()
                with probe.observed_journals(packet):
                    self.assertEqual(inventory(), before)
                self.assertEqual(inventory(), before)
                (base / 'pause' / 'lock').unlink()
                before = inventory()
                with self.assertRaises(FileNotFoundError):
                    with probe.observed_journals(packet):
                        self.fail('missing lock accepted')
                self.assertEqual(inventory(), before)
                self.assertFalse((base / 'pause' / 'lock').exists())
                # First journal lock is released even when second journal fails.
                with Journal(base / 'operation', create_lock=False):
                    pass

    def test_cli_requires_isolation_before_any_live_access(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                                text=True, timeout=10)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report['code'], 'PROBE_ISOLATION')
        self.assertFalse(report['production_mutations'])
        self.assertFalse(report['resume_authorized'])
        self.assertFalse(report['historical_cause_proven'])
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
