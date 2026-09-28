"""Offline safety/compatibility tests; never connect to Oracle, GitHub or Neon."""
import importlib.util
import json
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
                with patch.dict(sys.modules, modules), patch.object(probe, 'source_origins'):
                    if failing:
                        with self.assertRaisesRegex(RuntimeError, '^synthetic$'):
                            probe.observe(lambda **_: conn, 'secret', 'token', ROOT)
                    else:
                        probe.observe(lambda **_: conn, 'secret', 'token', ROOT)
                self.assertEqual(probe.PHASE, expected_phase)
                sequence = ['workflow', 'prior', 'backend', 'snapshot']
                self.assertEqual(events, sequence if failing is None else sequence[:sequence.index(failing)+1])

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
