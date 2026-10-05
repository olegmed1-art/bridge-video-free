"""Synthetic-only contracts for separately scoped initialized runtime observation."""
import ast
import contextlib
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import types
import unittest
from unittest.mock import MagicMock, patch

from ops import native_maintenance_owner_attest as owner
from ops import native_maintenance_owner_host as host
from ops import native_maintenance_owner_host_runner as runner
from ops import oracle_light_active_hold_attest as hold

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CREDENTIAL = 'fixture://synthetic-secret'
SYNTHETIC_BASELINE = dict(version=1, scope='initialized-runtime',
                          expected_receipts=3, incident_target_pr=42,
                          review_nonce='a' * 64)


class InitializedDatabaseTests(unittest.TestCase):
    def fixture(self, counts=None, read_only='on'):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        observed = []
        def execute(sql, params=()):
            observed.append((sql, params, conn.read_only))
            row = (read_only,) if sql.startswith('SELECT current_setting') else (
                owner.initialized_counts(SYNTHETIC_BASELINE) if counts is None else counts)
            return types.SimpleNamespace(fetchone=lambda: row)
        conn.execute.side_effect = execute
        connect = MagicMock(return_value=conn)
        return connect, conn, observed

    def run_observation(self, counts=None, identity_failure=None, read_only='on'):
        connect, conn, observed = self.fixture(counts, read_only)
        with ExitStack() as stack:
            stack.enter_context(patch.object(owner, 'parameters',
                return_value=dict(host='database.example', password='synthetic-secret')))
            identity = stack.enter_context(patch.object(owner.engine, 'identity'))
            if identity_failure:
                identity.side_effect = owner.engine.Refused('SYNTHETIC_TARGET_REFUSED')
            guards = {name: stack.enter_context(patch.object(owner.engine, name))
                      for name in ('snapshot', 'dormant', 'privileges', 'expected_after',
                                   'prepare', 'change')}
            report = owner.observe_initialized(connect, SYNTHETIC_CREDENTIAL, SYNTHETIC_BASELINE)
            identity.assert_called_once()
            for guard in guards.values():
                guard.assert_not_called()
        return report, conn, observed

    def test_retained_terminal_receipts_aggregate_readonly_without_authority(self):
        report, conn, observed = self.run_observation()
        self.assertEqual(report, owner.initialized_report())
        self.assertIs(report['retained_terminal_baseline_matched'], True)
        self.assertEqual(len(observed), 5)
        self.assertTrue(all(read_only is True for _, _, read_only in observed))
        self.assertEqual(observed[0][0],
            'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        self.assertEqual(observed[-1][0], owner.INITIALIZED_SQL)
        self.assertEqual(len(observed[-1][1]), 4)
        self.assertEqual(observed[-1][1][1:], (42, 42, 42))
        conn.commit.assert_not_called()
        for name in ('snapshot_approved', 'native_initial_install_qualified',
                     'native_execution_authorized', 'historical_provenance_verified',
                     'incident_reconciled', 'replay_authorized',
                     'live_admission', 'production_mutations'):
            self.assertIs(report[name], False)
        self.assertNotIn('synthetic-secret', json.dumps(report))

    def test_every_count_guard_refuses_and_type_or_shape_cannot_bypass(self):
        baseline = owner.initialized_counts(SYNTHETIC_BASELINE)
        bad_rows = [tuple(v + 1 if i == pos else v for i, v in enumerate(baseline))
                    for pos in range(len(baseline))]
        bad_rows += [baseline[:-1], list(baseline), (True,) + baseline[1:],
                     (7.0,) + baseline[1:], None]
        # None needs the fixture's default convention replaced explicitly.
        for row in bad_rows:
            connect, conn, _ = self.fixture()
            if row is None:
                conn.execute.side_effect = lambda sql, params=(): types.SimpleNamespace(
                    fetchone=lambda: ('on',) if sql.startswith('SELECT current_setting') else None)
            else:
                connect, conn, _ = self.fixture(row)
            with self.subTest(kind=type(row).__name__), patch.object(owner, 'parameters',
                    return_value={}), patch.object(owner.engine, 'identity'), self.assertRaises(owner.engine.Refused):
                owner.observe_initialized(connect, SYNTHETIC_CREDENTIAL, SYNTHETIC_BASELINE)
            conn.commit.assert_not_called()

    def test_bad_identity_and_readwrite_session_fail_closed(self):
        with self.assertRaises(owner.engine.Refused):
            self.run_observation(identity_failure=True)
        with self.assertRaises(owner.engine.Refused):
            self.run_observation(read_only='off')

    def test_original_initial_install_still_refuses_retained_receipts(self):
        state = dict(config=[dict(enabled=False)], receipts=3, nonterminal_tasks=0)
        with self.assertRaisesRegex(owner.engine.Refused, 'RECEIPTS_PRESENT'):
            owner.engine.dormant(state)
        connect, conn, _ = self.fixture()
        with patch.object(owner, 'parameters', return_value={}), \
                patch.object(owner.engine, 'snapshot', return_value=state), \
                patch.object(owner.engine, 'privileges') as privileges, \
                patch.object(owner.engine, 'prepare') as prepare, \
                patch.object(owner.engine, 'change') as change, self.assertRaisesRegex(
                    owner.engine.Refused, 'RECEIPTS_PRESENT'):
            owner.observe(connect, SYNTHETIC_CREDENTIAL)
        privileges.assert_not_called()
        prepare.assert_not_called()
        change.assert_not_called()


    def test_private_baseline_missing_malformed_or_unbounded_before_connection(self):
        bad = [None, {}, dict(SYNTHETIC_BASELINE, version=True),
               dict(SYNTHETIC_BASELINE, scope='other'),
               dict(SYNTHETIC_BASELINE, expected_receipts=0),
               dict(SYNTHETIC_BASELINE, expected_receipts=33),
               dict(SYNTHETIC_BASELINE, expected_receipts=True),
               dict(SYNTHETIC_BASELINE, incident_target_pr=0),
               dict(SYNTHETIC_BASELINE, incident_target_pr=1000001),
               dict(SYNTHETIC_BASELINE, incident_target_pr='42'),
               dict(SYNTHETIC_BASELINE, review_nonce='not-a-blinding-value'),
               dict(SYNTHETIC_BASELINE, unexpected='synthetic')]
        for baseline in bad:
            connect = MagicMock()
            with self.subTest(kind=type(baseline).__name__), self.assertRaises(owner.engine.Refused):
                owner.observe_initialized(connect, SYNTHETIC_CREDENTIAL, baseline)
            connect.assert_not_called()

    def test_sql_is_aggregate_only_without_payload_or_native_rpcs(self):
        sql = owner.INITIALIZED_SQL
        for denied in ('r.request', 'to_jsonb(', 'jsonb_agg(', "->>'summary'",
                       'native_cli_finish(', 'native_cli_reserve(', 'FOR UPDATE',
                       'DELETE ', 'UPDATE ', 'INSERT ', 'GRANT ', 'REVOKE '):
            self.assertNotIn(denied, sql)
        self.assertIn("jsonb_typeof(r.terminal->'summary')='string'", sql)
        self.assertIn("IS DISTINCT FROM 'TERMINAL'", sql)
        self.assertIn('relrowsecurity', sql)
        self.assertEqual(sql.count('%s'), 4)
        self.assertNotRegex(sql, r'target_pr\s*=\s*[0-9]')
        self.assertRegex('target_pr=42', r'target_pr\s*=\s*[0-9]')


class InitializedHostTests(unittest.TestCase):
    def setUp(self):
        self.identity = hold.HoldIdentity(
            hostname='machine.example', pid=123, invocation_id='a' * 32,
            release='b' * 40, fingerprint='c' * 64)
        # Derive the already public host predicate from the subject, never add a
        # production hostname or private observation to fixture source.
        import inspect
        tree = ast.parse(inspect.getsource(host.observe_initialized))
        check = next(n for n in ast.walk(tree) if isinstance(n, ast.Compare)
                     and isinstance(n.left, ast.Attribute) and n.left.attr == 'nodename')
        self.subject_hostname = ast.literal_eval(check.comparators[0])

    def fixture(self, after=None, missing_runtime=False):
        self.events = []
        def baseline_reader(pin):
            self.assertEqual(pin, 'd' * 64)
            self.events.append('baseline')
            return dict(SYNTHETIC_BASELINE)
        def attest():
            self.events.append('hold')
            return self.identity if self.events.count('hold') == 1 else (
                self.identity if after is None else after)
        @contextlib.contextmanager
        def runtime(wheels):
            self.events.append('runtime')
            if missing_runtime:
                raise RuntimeError('SYNTHETIC_RUNTIME_MISSING')
            yield types.SimpleNamespace(connect=MagicMock()), 'a' * 64
            self.events.append('runtime_verified')
        stack = ExitStack()
        stack.enter_context(patch.object(host.os, 'getuid', return_value=0))
        stack.enter_context(patch.object(host.os, 'uname',
            return_value=types.SimpleNamespace(nodename=self.subject_hostname)))
        stack.enter_context(patch.object(host, 'connection_parameters'))
        stack.enter_context(patch.object(host, 'attest', side_effect=attest))
        stack.enter_context(patch.object(host, 'read_initialized_baseline', side_effect=baseline_reader))
        stack.enter_context(patch.object(host, 'loaded_runtime', side_effect=runtime))
        stack.enter_context(patch.object(owner, 'observe_initialized',
            return_value=owner.initialized_report()))
        return stack

    def test_existing_runtime_and_typed_full_hold_bracket_before_report(self):
        with self.fixture():
            report = host.observe_initialized(b'synthetic-wheels', SYNTHETIC_CREDENTIAL, 'd' * 64)
        self.assertEqual(self.events, ['baseline', 'hold', 'runtime', 'runtime_verified', 'hold', 'baseline'])
        runner.validate_initialized_report(report)
        with self.assertRaises(runner.bundle.BundleError):
            runner.validate_report(report)
        self.assertIs(report['incident_reconciled'], False)
        self.assertIs(report['live_admission'], False)
        self.assertNotIn('machine.example', json.dumps(report))

    def test_missing_runtime_and_hold_drift_refuse(self):
        with self.fixture(missing_runtime=True), self.assertRaises(RuntimeError):
            host.observe_initialized(b'synthetic-wheels', SYNTHETIC_CREDENTIAL, 'd' * 64)
        changed = hold.HoldIdentity('other.example', 124, 'd' * 32, 'e' * 40, 'f' * 64)
        with self.fixture(after=changed), self.assertRaises(hold.Blocked):
            host.observe_initialized(b'synthetic-wheels', SYNTHETIC_CREDENTIAL, 'd' * 64)

    def test_service_only_identity_is_not_full_hold(self):
        with self.fixture(), patch.object(host, 'attest',
                return_value=hold.ServiceHoldIdentity('machine.example', 123,
                    'a' * 32, 'b' * 40, 'c' * 64)), self.assertRaises(hold.Blocked):
            host.observe_initialized(b'synthetic-wheels', SYNTHETIC_CREDENTIAL, 'd' * 64)
        self.assertNotIn('runtime', self.events)

    def test_wrong_credential_refuses_before_hold_or_runtime(self):
        with self.fixture(), patch.object(host, 'connection_parameters',
                side_effect=ValueError('SYNTHETIC_CREDENTIAL_REFUSED')), self.assertRaises(ValueError):
            host.observe_initialized(b'synthetic-wheels', SYNTHETIC_CREDENTIAL, 'd' * 64)
        self.assertEqual(self.events, [])


    def test_private_baseline_change_refuses_report(self):
        changed = dict(SYNTHETIC_BASELINE, incident_target_pr=43)
        with self.fixture(), patch.object(host, 'read_initialized_baseline',
                side_effect=[dict(SYNTHETIC_BASELINE), changed]), self.assertRaises(hold.Blocked):
            host.observe_initialized(b'synthetic-wheels', SYNTHETIC_CREDENTIAL, 'd' * 64)

    def test_private_baseline_reader_pin_missing_input_and_hash_mismatch(self):
        # Actual private-file policy helpers are reused; simulate only the file
        # transport here. This test never reads or creates a production input.
        raw = host.driver.encoded(SYNTHETIC_BASELINE)
        directory = types.SimpleNamespace(st_dev=1, st_ino=2)
        file_info = types.SimpleNamespace(st_dev=1, st_ino=3, st_size=len(raw),
                                          st_mtime_ns=1, st_ctime_ns=1)
        with patch.object(host.driver, 'trusted_parent'), \
                patch.object(host.driver, 'private_directory', return_value=directory), \
                patch.object(host.os, 'open', return_value=10), \
                patch.object(host.driver, 'open_file', return_value=11), \
                patch.object(host.os, 'fstat', side_effect=lambda fd: directory if fd == 10 else file_info), \
                patch.object(host.os, 'read', return_value=raw), \
                patch.object(host.os, 'close') as close:
            self.assertEqual(host.read_initialized_baseline(host.driver.sha(raw)), SYNTHETIC_BASELINE)
            with self.assertRaises(hold.Blocked):
                host.read_initialized_baseline('f' * 64)
            self.assertEqual(close.call_count, 4)
        with patch.object(host.driver, 'trusted_parent') as parent, self.assertRaises(hold.Blocked):
            host.read_initialized_baseline('')
        parent.assert_not_called()
        with patch.object(host.driver, 'trusted_parent'), \
                patch.object(host.driver, 'private_directory', side_effect=FileNotFoundError), \
                self.assertRaises(FileNotFoundError):
            host.read_initialized_baseline('d' * 64)

    def test_fixed_failure_never_prints_exception_or_credential(self):
        output = io.StringIO()
        with patch.object(host, 'observe_initialized',
                side_effect=RuntimeError(SYNTHETIC_CREDENTIAL)), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exc:
            host.initialized_main(b'synthetic-wheels', SYNTHETIC_CREDENTIAL, 'd' * 64)
        self.assertEqual(exc.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue()), dict(
            audit='NATIVE_OWNER_HOST_INITIALIZED_READ_ONLY_REFUSED',
            production_mutations=False))


class InitializedRunnerTests(unittest.TestCase):
    def bootstrap_child(self, **options):
        with patch.object(runner.bundle, 'git',
                side_effect=lambda repo, cmd, ref: (ROOT / ref.split(':', 1)[1]).read_bytes()):
            code = runner.bootstrap(ROOT, 'a' * 40, 'b' * 64, 'c' * 64,
                                    '123-1', **options)
        compile(code, '<synthetic-outer>', 'exec')
        call = next(n for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == 'managed')
        child = ast.literal_eval(call.args[0])
        compile(child, '<synthetic-child>', 'exec')
        return code, child

    def test_default_and_candidate_modes_stay_separate_from_initialized(self):
        _, old = self.bootstrap_child()
        _, candidate = self.bootstrap_child(candidate=True)
        code, initialized = self.bootstrap_child(scope='initialized-runtime', baseline_sha='d' * 64)
        self.assertIn(' import main\n', old)
        self.assertIn('candidate_main as main', candidate)
        self.assertIn('initialized_main as main', initialized)
        self.assertNotIn(SYNTHETIC_CREDENTIAL, code)
        with self.assertRaises(runner.bundle.BundleError):
            self.bootstrap_child(candidate=True, scope='initialized-runtime')

    def test_invalid_scope_before_source_or_credential_transport(self):
        with patch.object(runner.sys, 'argv', ['runner', 'key', 'known', 'wheels']), \
                patch.dict(os.environ, dict(GITHUB_TRIGGERING_ACTOR='olegmed1-art',
                                            OWNER_PROBE_SCOPE='unsupported')), \
                patch.object(runner, 'source_check') as source, \
                patch.object(runner.subprocess, 'run') as ssh, self.assertRaises(runner.bundle.BundleError):
            runner.main()
        source.assert_not_called()
        ssh.assert_not_called()


    def test_missing_private_baseline_pin_refuses_before_source_or_credentials(self):
        for scope, pin in [('initialized-runtime', ''), ('initialized-runtime', 'invalid'),
                           ('initial-install', 'd' * 64)]:
            with self.subTest(scope=scope), \
                    patch.object(runner.sys, 'argv', ['runner', 'key', 'known', 'wheels']), \
                    patch.dict(os.environ, dict(GITHUB_TRIGGERING_ACTOR='olegmed1-art',
                        OWNER_PROBE_SCOPE=scope, OWNER_INITIALIZED_BASELINE_SHA256=pin)), \
                    patch.object(runner, 'source_check') as source, \
                    patch.object(runner.subprocess, 'run') as ssh, self.assertRaises(runner.bundle.BundleError):
                runner.main()
            source.assert_not_called()
            ssh.assert_not_called()

    def test_initialized_report_validator_is_exact_and_non_authorizing(self):
        report = dict(owner.initialized_report(),
            audit='NATIVE_OWNER_HOST_INITIALIZED_READ_ONLY_PASS',
            runtime_id='a' * 64, hold_unchanged=True, elapsed_ms=1)
        runner.validate_initialized_report(report)
        for key, value in [('retained_terminal_baseline_matched', False), ('elapsed_ms', 100000),
                           ('elapsed_ms', True), ('runtime_id', 'not-a-digest'),
                           ('hold_unchanged', False), ('live_admission', True),
                           ('snapshot_approved', 0), ('extra', 'synthetic')]:
            with self.subTest(key=key), self.assertRaises(runner.bundle.BundleError):
                runner.validate_initialized_report(dict(report, **{key: value}))


    def test_initialized_validator_imports_without_ambient_driver(self):
        import subprocess
        report = dict(owner.initialized_report(),
            audit='NATIVE_OWNER_HOST_INITIALIZED_READ_ONLY_PASS',
            runtime_id='a' * 64, hold_unchanged=True, elapsed_ms=1)
        code = ('import sys,json; sys.path.insert(0,sys.argv[1]); '
                'from ops import native_maintenance_owner_host_runner as subject; '
                'subject.validate_initialized_report(json.loads(sys.argv[2])); '
                "assert not any(n.startswith('psycopg') for n in sys.modules)")
        result = subprocess.run([runner.sys.executable, '-I', '-B', '-S', '-c',
                                 code, str(ROOT), json.dumps(report)],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_actual_runner_main_selects_new_validator_and_stdin_only_transport(self):
        report = dict(owner.initialized_report(),
            audit='NATIVE_OWNER_HOST_INITIALIZED_READ_ONLY_PASS',
            runtime_id='a' * 64, hold_unchanged=True, elapsed_ms=1)
        calls = []
        def transport(command, **kwargs):
            self.assertNotIn(SYNTHETIC_CREDENTIAL, repr(command))
            self.assertNotIn(SYNTHETIC_CREDENTIAL, repr(kwargs['env']))
            self.assertEqual(json.loads(kwargs['input'])['credential'], SYNTHETIC_CREDENTIAL)
            self.assertEqual(kwargs['timeout'], 115)
            self.assertNotIn('NATIVE_OWNER_DATABASE_URL', os.environ)
            calls.append('ssh_fixture')
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(report).encode(),
                                         stderr=b'')
        output = io.StringIO()
        with patch.object(runner.sys, 'argv', ['runner', 'key', 'known', 'wheels']), \
                patch.dict(os.environ, dict(GITHUB_TRIGGERING_ACTOR='olegmed1-art',
                    OWNER_PROBE_SCOPE='initialized-runtime', EXPECTED_MAIN='a' * 40,
                    OWNER_INITIALIZED_BASELINE_SHA256='d' * 64,
                    GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1',
                    NATIVE_OWNER_DATABASE_URL=SYNTHETIC_CREDENTIAL)), \
                patch.object(runner, 'connection_parameters'), \
                patch.object(runner, 'source_check') as source, \
                patch.object(runner.bundle, 'build', return_value=b'synthetic-source'), \
                patch.object(runner.driver, 'build', return_value=b'synthetic-wheels'), \
                patch.object(runner, 'bootstrap', return_value='synthetic-code') as bootstrap, \
                patch.object(runner.subprocess, 'run', side_effect=transport), \
                contextlib.redirect_stdout(output):
            runner.main()
        self.assertEqual(calls, ['ssh_fixture'])
        self.assertEqual(source.call_count, 3)
        self.assertEqual(bootstrap.call_args.kwargs, dict(scope='initialized-runtime', baseline_sha='d' * 64))
        self.assertEqual(json.loads(output.getvalue()), dict(report, source_sha='a' * 40))
        self.assertNotIn(SYNTHETIC_CREDENTIAL, output.getvalue())

    def test_workflow_default_and_original_admission_guards_remain(self):
        text = (ROOT / '.github/workflows/native-maintenance-owner-host.yml').read_text()
        self.assertIn('default: initial-install', text)
        self.assertIn('          - initialized-runtime', text)
        self.assertIn('OWNER_PROBE_SCOPE: ${{ inputs.probe_scope }}', text)
        self.assertIn('inputs.expected_main_sha == github.sha', text)
        self.assertIn('github.triggering_actor == github.repository_owner', text)
        self.assertIn('environment: database-production', text)
        self.assertIn('contents: read', text)
        self.assertNotIn('NEON_DATABASE_URL', text)
        self.assertIn('OWNER_INITIALIZED_BASELINE_SHA256: ${{ inputs.initialized_baseline_sha256 }}', text)
        self.assertNotIn("inputs.probe_scope }}'", text)


if __name__ == '__main__':
    unittest.main()
