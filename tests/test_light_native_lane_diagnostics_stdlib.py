"""Dependency-free unit checks; no host, database, network or provider calls.

The production AST is executed with explicit boundary doubles. Root metadata,
authenticated live delivery and the full pytest suite still require normal CI.
"""
import ast
import base64
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT.parent / 'baseline'
CODE = 'PILOT_INTAKE_ASSIGNMENT_DRIFT'


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def parse(raw):
    value = json.loads(raw)
    require(type(value) is dict and encoded(value) == raw, 'LANE_EXEC_RECORD')
    return value


def owner_double(root=None):
    def read(path, accepted=None, limit=262144):
        raw = path.read_bytes()
        require(len(raw) <= limit and (accepted is None or hashlib.sha256(raw).hexdigest() == accepted),
                'LANE_OWNER_RECORD_NOT_ACCEPTED')
        return raw
    def retain(path, raw):
        with path.open('xb') as stream:
            stream.write(raw)
    return SimpleNamespace(require=require, encoded=encoded, parse=parse,
        sha=lambda raw: hashlib.sha256(raw).hexdigest(), ROOT=root,
        read=read, retain=retain, install=SimpleNamespace(root_parent=lambda p: None,
        fresh_directory=lambda p, mode: p.mkdir(mode=mode)))


def load_source(path, **bindings):
    tree = ast.parse(path.read_text())
    # Only remove project imports; keep the real production definitions and
    # stdlib imports. No production dependency or external boundary is loaded.
    tree.body = [node for node in tree.body if not (isinstance(node, ast.ImportFrom)
                 and node.module and node.module.split('.')[0] in ('ops', 'database'))]
    module = SimpleNamespace()
    module.__dict__.update(bindings)
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


def child_envelope(code, exc):
    """Execute the actual generated bootstrap handler, with its real constants."""
    tree = ast.parse(code)
    bindings = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            exec(compile(ast.Module(body=[node], type_ignores=[]), '<constants>', 'exec'), bindings)
    original = next(node for node in tree.body if isinstance(node, ast.Try))
    body = ast.Try(body=[ast.Raise(exc=ast.Name(id='error', ctx=ast.Load()), cause=None)],
                   handlers=original.handlers, orelse=[], finalbody=[])
    fragment = ast.fix_missing_locations(ast.Module(body=[body], type_ignores=[]))
    bindings.update(json=json, error=exc)
    output = io.StringIO()
    with patch('sys.stdout', output):
        try:
            exec(compile(fragment, '<actual-child-handler>', 'exec'), bindings)
        except SystemExit as exit_value:
            require(exit_value.code == 2, 'TEST_CHILD_EXIT')
    return output.getvalue().encode()


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.owner = owner_double()
        self.cycle = load_source(ROOT/'ops/light_native_lane_cycle.py', owner=self.owner)
        self.guard = SimpleNamespace(source='a'*40, run_id=1, attempt=1)

    def invoke(self, cycle=None, runner=None):
        cycle = cycle or self.cycle
        payload = encoded(dict(action='prepare'))
        with patch.object(cycle.subprocess, 'run', runner):
            return cycle.isolated(b'wheels', 'private-password', 'private-token', b'controller',
                b'runtime', payload, self.owner.sha(payload), self.guard, outer='b'*64, mode='phase')

    def run_child(self, exc, cycle=None):
        def runner(argv, **kwargs):
            self.assertEqual(argv[:5], ['/usr/bin/python3', '-I', '-S', '-B', '-c'])
            self.assertNotIn('private-password', argv[-1])
            self.assertNotIn('private-token', argv[-1])
            output = child_envelope(argv[-1], exc)
            self.assertNotIn(b'private-', output)
            return SimpleNamespace(returncode=2, stdout=output, stderr=b'private-stderr')
        return self.invoke(cycle=cycle, runner=runner)

    @unittest.skipUnless((BASELINE/'ops/light_native_lane_cycle.py').is_file(),
                         'Historical baseline is supplied only in the local review bundle')
    def test_original_bootstrap_erased_the_known_cause(self):
        original = load_source(BASELINE/'ops/light_native_lane_cycle.py', owner=self.owner)
        with self.assertRaisesRegex(RuntimeError, '^LANE_CYCLE_CHILD_REFUSED$'):
            self.run_child(RuntimeError(CODE), original)

    def test_every_allowlisted_code_survives_actual_child_handler_and_parent(self):
        self.assertEqual(len(self.cycle.FAILURE_REASONS), len(set(self.cycle.FAILURE_REASONS)))
        for reason in self.cycle.FAILURE_REASONS:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(RuntimeError, '^'+reason+'$'):
                    self.run_child(RuntimeError(reason))

    def test_secret_text_and_nonexact_exception_types_are_unclassified(self):
        class Hostile(RuntimeError):
            def __str__(self):
                raise AssertionError('Exception formatting must never be invoked')
        class HostileText(str):
            def __str__(self):
                raise AssertionError('Text conversion must never be invoked')
        errors = (RuntimeError('private-password'), RuntimeError(CODE+' private-token'),
                  RuntimeError(CODE, 'private-token'), ValueError(CODE), Hostile(CODE),
                  RuntimeError(HostileText(CODE)), KeyboardInterrupt('private-password'))
        for exc in errors:
            with self.subTest(kind=type(exc).__name__, args_count=len(exc.args)):
                self.assertEqual(self.cycle.failure_reason(exc), 'UNCLASSIFIED')
                with self.assertRaisesRegex(RuntimeError, '^UNCLASSIFIED$'):
                    self.run_child(exc)

    def test_real_agreement_refusals_are_explicitly_unclassified(self):
        pause = load_source(ROOT/'ops/native_maintenance_workflow_pause.py')
        agreement = load_source(ROOT/'ops/native_maintenance_agreement.py',
                                require=pause.require, digest=pause.digest)
        self.assertIs(agreement.require, pause.require)
        expired = agreement.Agreement.__new__(agreement.Agreement)
        expired.failed = True
        with self.assertRaises(pause.Refused) as raised:
            expired.assert_held('scope')
        self.assertEqual(raised.exception.args, ('COORDINATION_AGREEMENT_ALREADY_FAILED',))
        with self.assertRaisesRegex(RuntimeError, '^UNCLASSIFIED$'):
            self.run_child(raised.exception)
        for reason in ('COORDINATION_AGREEMENT_NOT_CURRENT', 'COORDINATION_AGREEMENT_EXPIRED',
                       'COORDINATION_AGREEMENT_ALREADY_FAILED', 'COORDINATION_AGREEMENT_CHANGED'):
            with self.subTest(reason=reason):
                self.assertNotIn(reason, self.cycle.FAILURE_REASONS)
                with self.assertRaises(pause.Refused) as raised:
                    agreement.require(False, reason)
                self.assertEqual(self.cycle.failure_reason(raised.exception), 'UNCLASSIFIED')
                with self.assertRaisesRegex(RuntimeError, '^UNCLASSIFIED$'):
                    self.run_child(raised.exception)

    def test_malformed_or_untrusted_child_output_fails_closed(self):
        good = dict(audit='LIGHT_LANE_CYCLE_CHILD_REFUSED', reason=CODE)
        cases = [(2, b'not-json private-password'), (2, b'x'*1025),
                 (2, encoded(dict(good, private='private-password'))),
                 (2, encoded(dict(good, reason='private-password'))),
                 (2, encoded(dict(good, reason=[CODE]))),
                 (2, encoded(dict(good, audit='OTHER'))),
                 (2, json.dumps(good).encode()), (1, encoded(good)),
                 (-9, encoded(good)), (2, b'{"audit":"LIGHT_LANE_CYCLE_CHILD_REFUSED"}')]
        for status, output in cases:
            with self.subTest(status=status, length=len(output)):
                runner = lambda *a, **kw: SimpleNamespace(returncode=status, stdout=output,
                                                          stderr=b'private-token')
                with self.assertRaisesRegex(RuntimeError, '^LANE_CYCLE_CHILD_REFUSED$'):
                    self.invoke(runner=runner)

    def test_timeout_remains_unknown(self):
        def timeout(*a, **kw):
            raise subprocess.TimeoutExpired('private-command', 300)
        with self.assertRaisesRegex(RuntimeError, '^LANE_CYCLE_CHILD_UNKNOWN$'):
            self.invoke(runner=timeout)

    def test_success_output_is_unchanged(self):
        result = dict(audit='LIGHT_LANE_OWNER', phase='prepare')
        runner = lambda *a, **kw: SimpleNamespace(returncode=0, stdout=encoded(result)+b'\n')
        self.assertEqual(self.invoke(runner=runner), result)

    def journal_failure(self, reason, containment_fails=False, baseline=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            owner = owner_double(root)
            source = BASELINE if baseline else ROOT
            cycle = load_source(source/'ops/light_native_lane_cycle.py', owner=owner)
            cycle.os = SimpleNamespace(geteuid=lambda:0,
                uname=lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
            cycle.exclusive = lambda *a, **kw: nullcontext()
            prepare = dict(accepted_plan_sha256='c'*64)
            cycle.validated = lambda *a: prepare
            calls = []
            scope = root/prepare['accepted_plan_sha256']
            def isolated(*args, mode, **kwargs):
                if mode == 'validate':
                    return dict(audit='LIGHT_LANE_CYCLE_VALIDATED')
                action = parse(args[5])['action']
                calls.append((mode, action))
                if action == 'prepare':
                    scope.mkdir()
                    for name in ('before.json', 'baseline.json'):
                        owner.retain(scope/name, b'{}')
                    raise RuntimeError(reason)
                if containment_fails:
                    raise RuntimeError('private-containment-error')
                if mode == 'containment_check':
                    return dict(audit='LIGHT_LANE_CYCLE_CONTAINMENT_CHECKED')
                result = dict(audit='LIGHT_LANE_OWNER', phase='contain',
                    plan_sha256=prepare['accepted_plan_sha256'], state='INTAKE_ROLLED_BACK',
                    queue_retry_authorized=False)
                owner.retain(scope/'contained.json', encoded(result))
                return result
            cycle.isolated = isolated
            guard = SimpleNamespace(assert_running=lambda:None)
            invoke = lambda:cycle.run(b'wheels','private-password','private-token',
                                      b'controller',b'runtime',b'{}','digest',guard)
            with self.assertRaisesRegex(RuntimeError, '^LANE_CYCLE_RECONCILIATION_REQUIRED$'):
                invoke()
            journal = cycle.location(prepare)
            before = {p.name:p.read_bytes() for p in journal.iterdir()}
            with self.assertRaisesRegex(RuntimeError, '^LANE_CYCLE_REPLAY$'):
                invoke()
            self.assertEqual(before, {p.name:p.read_bytes() for p in journal.iterdir()})
            self.assertFalse((journal/'complete.json').exists())
            self.assertEqual([x for x in calls if x[1]=='prepare'], [('phase','prepare')])
            self.assertNotIn(b'private-', b''.join(before.values()))
            return before

    @unittest.skipUnless((BASELINE/'ops/light_native_lane_cycle.py').is_file(),
                         'Historical baseline is supplied only in the local review bundle')
    def test_original_cycle_did_not_retain_the_cause(self):
        records = self.journal_failure(CODE, baseline=True)
        self.assertNotIn('refusal.json', records)
        self.assertNotIn(CODE.encode(), b''.join(records.values()))

    def test_cycle_keeps_first_refusal_and_never_replays(self):
        for containment_fails in (False, True):
            records = self.journal_failure(CODE, containment_fails=containment_fails)
            self.assertEqual(parse(records['refusal.json']), dict(phase='prepare',reason=CODE))
            self.assertEqual(parse(records['incident.json']), dict(phase='prepare',
                containment='RECONCILIATION_REQUIRED' if containment_fails else 'INTAKE_ROLLED_BACK',
                state='RECONCILIATION_REQUIRED'))

    def test_cycle_redacts_unknown_errors(self):
        records = self.journal_failure('private-password')
        self.assertEqual(parse(records['refusal.json'])['reason'], 'UNCLASSIFIED')

    def observe(self, refusal=None, damage=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            owner = owner_double(root)
            cycle = SimpleNamespace(STEPS=self.cycle.STEPS, FAILURE_REASONS=self.cycle.FAILURE_REASONS,
                                    location=lambda prepare:root/'cycle')
            issuer = load_source(ROOT/'ops/light_native_lane_issuer.py', owner=owner, cycle=cycle)
            expected = dict(prepare=dict(accepted_plan_sha256='c'*64))
            issuer.progress = lambda *a, **kw:(0, {})
            issuer.derive = lambda *a:expected
            journal = root/'cycle'; journal.mkdir()
            owner.retain(journal/'intent.json', encoded(expected))
            owner.retain(journal/'incident.json', encoded(dict(phase='prepare',
                containment='INTAKE_ROLLED_BACK', state='RECONCILIATION_REQUIRED')))
            if refusal is not None:
                owner.retain(journal/'refusal.json', encoded(refusal))
            if damage == 'no_incident':
                (journal/'incident.json').unlink()
            if damage == 'symlink':
                (journal/'refusal.json').unlink()
                (journal/'refusal.json').symlink_to(journal/'intent.json')
            for item in journal.iterdir():
                if not item.is_symlink(): item.chmod(0o600)
            before = {p.name:p.read_bytes() for p in journal.iterdir()}
            actual_lstat = Path.lstat
            def metadata(path):
                original = actual_lstat(path)
                mode = original.st_mode
                if damage == 'mode' and path.name == 'refusal.json': mode = stat.S_IFREG|0o644
                return SimpleNamespace(st_mode=mode,
                    st_uid=1000 if damage == 'owner' and path.name == 'refusal.json' else 0,
                    st_nlink=2 if damage == 'hardlink' and path.name == 'refusal.json' else original.st_nlink)
            with patch.object(Path, 'lstat', metadata):
                result = issuer.incomplete_diagnostic(root, {}, 'd'*64, 0,
                                                      dict(start=1, cycle=expected))
            self.assertEqual(before, {p.name:p.read_bytes() for p in journal.iterdir()})
            return result

    def test_legacy_incident_has_no_invented_reason(self):
        self.assertNotIn('refusal', self.observe())

    def test_observation_reads_only_the_valid_bound_enum(self):
        refusal = dict(phase='prepare', reason=CODE)
        self.assertEqual(self.observe(refusal)['refusal'], refusal)

    def test_observation_rejects_untrusted_refusal_records(self):
        valid = dict(phase='prepare', reason=CODE)
        for refusal in (dict(valid, secret='private-password'), dict(valid, reason='private-password'),
                        dict(valid, phase='publish'), dict(valid, reason=[CODE])):
            with self.assertRaises(RuntimeError): self.observe(refusal)
        for damage in ('no_incident','symlink','mode','owner','hardlink'):
            with self.subTest(damage=damage):
                with self.assertRaises(RuntimeError): self.observe(valid, damage)

    @unittest.skipUnless((BASELINE/'ops/light_native_lane_cycle.py').is_file(),
                         'Historical baseline is supplied only in the local review bundle')
    def test_authority_replay_and_monitor_gates_are_ast_identical(self):
        for file, changed in [('light_native_lane_cycle.py', {'failure_reason','isolated','run'}),
                              ('light_native_lane_issuer.py', {'incomplete_diagnostic'})]:
            def definitions(tree):
                return {node.name:ast.dump(node, include_attributes=False) for node in ast.parse(tree).body
                        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name not in changed}
            self.assertEqual(definitions((ROOT/'ops'/file).read_text()),
                             definitions((BASELINE/'ops'/file).read_text()))


if __name__ == '__main__':
    unittest.main(verbosity=2)
