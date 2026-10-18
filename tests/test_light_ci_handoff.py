"""Offline file transport plus the real supervisor through explicit host ports."""
from contextlib import ExitStack
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from ops import light_ci_handoff as handoff
from ops import light_ci_once as ci
from ops import light_ci_once_admin as admin
if os.name == 'nt':
    # The production attestor remains POSIX-only; no identity check is executed.
    with patch.dict(sys.modules, {'pwd': SimpleNamespace()}):
        from ops import oracle_light_active_hold_attest as hold
else:
    from ops import oracle_light_active_hold_attest as hold
if __package__:
    from .test_light_ci_once import plan, row, Database, Runtime, fixture_root, runtime_manifest
else:
    from test_light_ci_once import plan, row, Database, Runtime, fixture_root, runtime_manifest


class HostOS:
    """Windows-local file writes; POSIX identity/directory-fsync are test ports."""
    name = 'posix'
    O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
    O_DIRECTORY = getattr(os, 'O_DIRECTORY', 0)
    directory_fd = 999999

    def __getattr__(self, name):
        return getattr(os, name)

    def geteuid(self):
        return 0

    def uname(self):
        return SimpleNamespace(nodename='autopilot-lite-vnic')

    def open(self, path, flags, *args):
        if Path(path).is_dir():
            return self.directory_fd
        return os.open(path, flags, *args)

    def fsync(self, fd):
        if fd != self.directory_fd:
            os.fsync(fd)

    def close(self, fd):
        if fd != self.directory_fd:
            os.close(fd)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        root = fixture_root()
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='handoff-', dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.assertEqual(self.directory.resolve().parent, root)
        self.p = plan()
        self.p['task_key'] = 'light-ci-audit-local-handoff-fixture-' + uuid.uuid4().hex
        self.p['unit'] = 'school-autopilot-light-ci-' + ci.sha(self.p['task_key'].encode())[:16] + '.service'
        self.task_id = str(uuid.uuid4())
        self.registration = {'version': 1, 'plan_sha256': ci.sha(ci.canonical(self.p)),
            'task_id': self.task_id, 'task_key': self.p['task_key'], 'goal': self.p['goal'],
            'status': 'READY', 'attempts': 0, 'lease_epoch': 0, 'max_attempts': 1,
            'cost_cap_microusd': 0, 'observed_at': ci.utc().isoformat(),
            'source': 'CI_ONCE_OWNER_REGISTRATION_V1'}
        self.input = self.directory/'owner-registration.json'
        self.input.write_bytes(ci.canonical(self.registration) + b'\n')
        self.package = self.directory/'package'
        self.prepared = admin.prepare(self.p, self.package, runtime_manifest())
        self.bundle = self.directory/'handoff'

    def prepare(self):
        return handoff.prepare_handoff(self.package, self.prepared['package_sha256'],
                    self.prepared['plan_sha256'], self.input, self.bundle)

    def local_load(self, digest, live=True):
        with patch.object(ci, 'read_root', side_effect=handoff.read_local):
            return handoff.load_handoff(self.package, self.bundle, digest,
                                       live=live, reader=handoff.read_local)

    def rewrite_manifest(self, change):
        path = self.bundle/handoff.MANIFEST
        value = ci.strict_json(path.read_bytes())
        change(value)
        raw = ci.canonical(value) + b'\n'
        path.write_bytes(raw)
        return ci.sha(raw)

    def host(self, outcome='success'):
        """Exercise actual launch guards/latches with no process or DB connector."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.host_root = self.directory/'host'
        self.host_root.mkdir(exist_ok=True)
        route = self.directory/'route'
        route.mkdir(exist_ok=True)
        (route/'route.lock').touch(exist_ok=True)
        private = self.host_root/'control'/ci.sha(self.p['task_key'].encode())
        self.job = self.host_root/'jobs'/ci.sha(self.p['task_key'].encode())
        private.mkdir(parents=True, exist_ok=True)
        self.job.mkdir(parents=True, exist_ok=True)
        identity = hold.ServiceHoldIdentity('autopilot-lite-vnic', 41, 'b'*32,
                                           str(ci.INSTALLED), 'fixture-not-a-credential')
        from dataclasses import asdict
        self.proof = private/'preflight.json'
        self.proof.write_bytes(ci.canonical({'plan_sha256': self.prepared['plan_sha256'],
            'package_sha256': self.prepared['package_sha256'], 'hold_identity': asdict(identity),
            'observed_at': ci.utc().isoformat()}) + b'\n')
        self.private = private
        self.db = Database(row(self.p, self.task_id))
        self.runtime = Runtime(self.p, self.task_id, self.db)
        self.unit_calls = 0

        def systemd_run(argv, **kwargs):
            self.unit_calls += 1
            if outcome == 'unknown_before_execution':
                raise TimeoutError('SECRET_SENTINEL missing systemd acknowledgement')
            journal = ci.Journal(self.job)
            journal.write('run-unit.json', {'unit': self.p['unit'], 'invocation_id': 'a'*32,
                                          'control_group': '/system.slice/'+self.p['unit']})
            ci.run_one(self.p, self.task_id, self.runtime, journal)
            if outcome == 'unknown_after_completion':
                raise TimeoutError('SECRET_SENTINEL completion happened, acknowledgement lost')
            return SimpleNamespace(returncode=0)

        def existing_directory(path, mode=0o755):
            self.assertTrue(Path(path).is_dir())

        stack.enter_context(patch.dict(sys.modules, {
            'fcntl': SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None),
            'pwd': SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=0, pw_gid=0))}))
        stack.enter_context(patch.object(admin, 'os', HostOS()))
        stack.enter_context(patch.object(handoff, 'require_owner'))
        stack.enter_context(patch.object(handoff, 'read_private_root', side_effect=handoff.read_local))
        stack.enter_context(patch.object(ci, 'read_root', side_effect=handoff.read_local))
        stack.enter_context(patch.object(ci, 'ROOT', self.host_root))
        stack.enter_context(patch.object(hold, 'ROUTE', route))
        stack.enter_context(patch.object(hold, 'service_hold_identity', return_value=identity))
        stack.enter_context(patch.object(admin, 'root_directory', side_effect=existing_directory))
        stack.enter_context(patch.object(admin, 'current_main', return_value=self.p['source_main']))
        stack.enter_context(patch.object(admin, 'checked_service_record',
                                        side_effect=lambda path, uid: ci.strict_json(Path(path).read_bytes())))
        stack.enter_context(patch.object(admin, 'exited_unit', return_value={
            'unit': self.p['unit'], 'invocation_id': 'a'*32, 'cgroup_empty': True, 'main_pid': 0}))
        stack.enter_context(patch.object(admin.subprocess, 'run', side_effect=systemd_run))
        return private

    def test_file_handoff_binds_every_value_and_includes_versioned_source(self):
        prepared = self.prepare()
        manifest, control, registration = self.local_load(prepared['handoff_sha256'])
        self.assertEqual(registration, self.registration)
        self.assertEqual(manifest['task_id'], self.task_id)
        self.assertEqual(control['plan'], self.p)
        self.assertIn('ops/light_ci_handoff.py', ci.strict_json(
            (self.package/'source-manifest.json').read_bytes())['files'])
        self.assertEqual(prepared['database_calls'], 0)
        self.assertEqual(prepared['units_started'], 0)

    def test_completed_or_partial_output_is_never_overwritten_or_resumed(self):
        self.prepare()
        original = (self.bundle/handoff.REGISTRATION).read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((self.bundle/handoff.REGISTRATION).read_bytes(), original)
        other = self.directory/'partial'
        other.mkdir()
        (other/handoff.REGISTRATION).write_bytes(b'{partial')
        with self.assertRaises(FileExistsError):
            handoff.prepare_handoff(self.package, self.prepared['package_sha256'],
                                    self.prepared['plan_sha256'], self.input, other)
        self.assertEqual((other/handoff.REGISTRATION).read_bytes(), b'{partial')

    def test_fsync_failure_is_not_success_and_preserves_partial_bundle(self):
        with patch.object(admin.os, 'fsync', side_effect=OSError('SECRET_SENTINEL disk failed')):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertTrue((self.bundle/handoff.REGISTRATION).exists())
        self.assertFalse((self.bundle/handoff.MANIFEST).exists())
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_two_concurrent_preparers_publish_only_one_complete_receipt(self):
        barrier = threading.Barrier(2)
        successes, errors = [], []

        def run():
            barrier.wait()
            try:
                successes.append(self.prepare())
            except FileExistsError:
                errors.append('EXISTING_DIRECTORY')

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual((len(successes), errors), (1, ['EXISTING_DIRECTORY']))
        self.assertEqual(self.local_load(successes[0]['handoff_sha256'])[2], self.registration)

    def test_private_owner_receipt_refuses_public_mode_and_multiple_links(self):
        for mode, links in ((0o100644, 1), (0o100600, 2)):
            info = SimpleNamespace(st_uid=0, st_mode=mode, st_nlink=links)
            with patch.object(ci, 'read_root', return_value=b'{}'), \
                 patch.object(Path, 'lstat', return_value=info):
                with self.assertRaisesRegex(ci.Blocked, 'HANDOFF_PRIVATE_METADATA'):
                    handoff.read_private_root(Path('/receipt'))

    def test_duplicate_owner_wrapper_types_and_large_input_rejected_before_output(self):
        bad = [b'{"version":1,"version":1}', b'{"registration":{}}', b'[]', b'{partial',
               ci.canonical({**self.registration, 'attempts': False}),
               ci.canonical({**self.registration, 'status': 'DONE'}),
               ci.canonical({**self.registration, 'attempts': 1}),
               ci.canonical({**self.registration, 'lease_epoch': 1}),
               ci.canonical({**self.registration, 'task_id': 'not-a-uuid'}), b' '*(handoff.LIMIT+1)]
        for raw in bad:
            with self.subTest(raw_type=type(raw).__name__, size=len(raw)):
                self.input.write_bytes(raw)
                with self.assertRaises((ci.Blocked, ValueError, TypeError)):
                    self.prepare()
                self.assertFalse(self.bundle.exists())

    def test_external_manifest_digest_is_required_and_cannot_self_accept(self):
        prepared = self.prepare()
        for digest in (None, '', 'bad', '0'*64):
            with self.subTest(digest=digest):
                with self.assertRaises(ci.Blocked):
                    self.local_load(digest)
        self.assertEqual(self.local_load(prepared['handoff_sha256'])[2], self.registration)

    def test_manifest_schema_canonical_bytes_and_registration_hash_cannot_drift(self):
        prepared = self.prepare()
        original = (self.bundle/handoff.MANIFEST).read_bytes()
        for change in (lambda m: m.update(extra=True), lambda m: m.update(version=True),
                       lambda m: m.update(task_id=str(uuid.uuid4())),
                       lambda m: m.update(package_sha256='0'*64),
                       lambda m: m.update(plan_sha256='0'*64),
                       lambda m: m.update(registration_sha256='0'*64)):
            (self.bundle/handoff.MANIFEST).write_bytes(original)
            digest = self.rewrite_manifest(change)
            with self.assertRaises(ci.Blocked):
                self.local_load(digest)
        (self.bundle/handoff.MANIFEST).write_bytes(b' '+original)
        with self.assertRaisesRegex(ci.Blocked, 'HANDOFF_CANONICAL'):
            self.local_load(ci.sha(b' '+original))
        (self.bundle/handoff.MANIFEST).write_bytes(original)
        (self.bundle/handoff.REGISTRATION).write_bytes(b'{partial')
        with self.assertRaisesRegex(ci.Blocked, 'HANDOFF_REGISTRATION_DIGEST'):
            self.local_load(prepared['handoff_sha256'])

    def test_actual_supervisor_and_runner_launch_once_and_keep_terminal(self):
        prepared = self.prepare()
        private = self.host()
        result = handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual(result['status'], 'RUNTIME_DONE_OWNER_ACCEPTANCE_REQUIRED')
        self.assertTrue((private/'runtime-result.json').exists())
        with self.assertRaises(FileExistsError):
            handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual((self.unit_calls, self.db.claims, self.runtime.completions), (1, 1, 1))
        snapshot = {p.name: p.read_bytes() for p in private.iterdir()}
        with patch.object(admin, 'supervise', side_effect=AssertionError('inspection must never launch')):
            observed = handoff.inspect_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual(observed['status'], 'RUNTIME_RESULT_RETAINED_OWNER_ACCEPTANCE_REQUIRED')
        self.assertFalse(observed['launch_permitted_by_this_report'])
        self.assertEqual(snapshot, {p.name: p.read_bytes() for p in private.iterdir()})

    def test_preexisting_partial_launch_intent_has_no_unit_or_claim(self):
        prepared = self.prepare()
        private = self.host()
        (private/'launch-intent.json').write_bytes(b'{partial')
        with self.assertRaises(FileExistsError):
            handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual((self.unit_calls, self.db.claims), (0, 0))
        observed = handoff.inspect_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual(observed['records']['launch-intent.json'], 'UNKNOWN')
        self.assertEqual(observed['status'], 'INSPECT_ONLY_NO_REPLAY')

    def test_lost_launch_ack_before_execution_latches_and_preserves_evidence(self):
        prepared = self.prepare()
        private = self.host('unknown_before_execution')
        with self.assertRaises(TimeoutError):
            handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        with self.assertRaises(FileExistsError):
            handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual(self.unit_calls, 1)
        self.assertTrue((private/'launch-intent.json').exists())
        self.assertEqual(handoff.inspect_handoff(self.package, self.bundle,
            prepared['handoff_sha256'])['status'], 'INSPECT_ONLY_NO_REPLAY')

    def test_lost_unit_ack_after_real_completion_does_not_replay_completed_task(self):
        prepared = self.prepare()
        private = self.host('unknown_after_completion')
        with self.assertRaises(TimeoutError):
            handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertTrue(self.runtime.terminal)
        self.assertTrue((self.job/'terminal.json').exists())
        self.assertFalse((private/'runtime-result.json').exists())
        with self.assertRaises(FileExistsError):
            handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual((self.unit_calls, self.db.claims, self.runtime.completions), (1, 1, 1))

    def test_result_write_failure_preserves_run_evidence_and_never_becomes_pass(self):
        prepared = self.prepare()
        private = self.host()
        writer = admin.write_new

        def fail_result(path, value, mode=0o600):
            if Path(path).name == 'runtime-result.json':
                with Path(path).open('xb') as stream:
                    stream.write(b'{partial')
                raise OSError('SECRET_SENTINEL disk failed')
            return writer(path, value, mode)

        with patch.object(admin, 'write_new', side_effect=fail_result):
            with self.assertRaises(OSError):
                handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertTrue((self.job/'terminal.json').exists())
        observed = handoff.inspect_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual(observed['records']['runtime-result.json'], 'UNKNOWN')
        self.assertEqual(observed['status'], 'INSPECT_ONLY_NO_REPLAY')
        self.assertEqual(self.db.claims, 1)

    def test_expired_plan_blocks_launch_but_historical_inspection_cannot_execute(self):
        prepared = self.prepare()
        self.host()
        future = ci.timestamp(self.p['expires_at']) + dt.timedelta(seconds=1)
        with patch.object(ci, 'utc', return_value=future):
            with self.assertRaisesRegex(ci.Blocked, 'PLAN_EXPIRED_OR_TOO_SHORT'):
                handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
            observed = handoff.inspect_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual(observed['status'], 'INSPECT_ONLY_NO_REPLAY')
        self.assertEqual((self.unit_calls, self.db.claims), (0, 0))

    def test_stale_preflight_is_rejected_before_intent_or_launch(self):
        prepared = self.prepare()
        private = self.host()
        value = ci.strict_json(self.proof.read_bytes())
        value['observed_at'] = (ci.utc()-dt.timedelta(seconds=601)).isoformat()
        self.proof.write_bytes(ci.canonical(value)+b'\n')
        with self.assertRaisesRegex(ci.Blocked, 'PREFLIGHT_STALE_OR_DRIFT'):
            handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertFalse((private/'launch-intent.json').exists())
        self.assertEqual((self.unit_calls, self.db.claims), (0, 0))

    def test_malformed_retained_intent_is_inspect_only_even_with_terminal(self):
        prepared = self.prepare()
        private = self.host()
        handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        (private/'launch-intent.json').write_bytes(b'[]\n')
        observed = handoff.inspect_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual(observed['status'], 'INSPECT_ONLY_NO_REPLAY')
        self.assertFalse(observed['launch_permitted_by_this_report'])

    def test_retained_intent_with_less_than_180_seconds_left_is_inspect_only(self):
        prepared = self.prepare()
        private = self.host()
        handoff.launch_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        late = ci.timestamp(self.p['expires_at']) - dt.timedelta(seconds=179)
        (private/'launch-intent.json').write_bytes(ci.canonical(
            {'plan_sha256': self.prepared['plan_sha256'], 'at': late.isoformat()})+b'\n')
        observed = handoff.inspect_handoff(self.package, self.bundle, prepared['handoff_sha256'])
        self.assertEqual(observed['status'], 'INSPECT_ONLY_NO_REPLAY')

    def test_cli_inspect_unknown_is_exit_three_and_retained_result_is_exit_zero(self):
        for status, expected in (('INSPECT_ONLY_NO_REPLAY', 3),
                                 ('RUNTIME_RESULT_RETAINED_OWNER_ACCEPTANCE_REQUIRED', 0)):
            output = io.StringIO()
            with patch.object(handoff, 'inspect_handoff', return_value={'status': status}), \
                 patch('sys.stdout', output):
                code = handoff.main(['inspect', '--package', str(self.package), '--handoff', str(self.bundle),
                                     '--accept-handoff-sha256', 'a'*64])
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(output.getvalue())['status'], status)

    def test_root_boundary_does_not_gain_an_alternative_non_posix_path(self):
        with patch.object(handoff, 'os', SimpleNamespace(name='nt')):
            with self.assertRaisesRegex(ci.Blocked, 'EXISTING_ROOT_ADMIN_REQUIRED'):
                handoff.require_owner()

    def test_cli_unknown_never_prints_private_exception_or_returns_success(self):
        output = io.StringIO()
        with patch.object(handoff, 'prepare_handoff', side_effect=OSError('SECRET_SENTINEL DSN')):
            with patch('sys.stdout', output):
                code = handoff.main(['prepare', '--package', str(self.package), '--handoff', str(self.bundle),
                    '--accept-package-sha256', self.prepared['package_sha256'],
                    '--accept-plan-sha256', self.prepared['plan_sha256'], '--registration-json', str(self.input)])
        self.assertEqual(code, 2)
        self.assertNotIn('SECRET_SENTINEL', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['status'], 'NOT_ACCEPTED_INSPECT_ONLY')


class ClosedCycleRegression(unittest.TestCase):
    def test_already_accepted_cycle_is_compatible_evidence_only(self):
        if not os.environ.get('LIGHT_CI_REGRESSION_FIXTURES'):
            self.skipTest('Historical owner evidence is local-only; no production archive in CI')
        fixtures = Path(os.environ['LIGHT_CI_REGRESSION_FIXTURES'])
        db = ci.strict_json((fixtures/'FINAL_NEON_INSPECT_V1.json').read_bytes())
        before = ci.strict_json((fixtures/'OWNER_BEFORE_SNAPSHOT_V1.json').read_bytes())
        snapshot = ci.strict_json((fixtures/'BOUND_PLAN_PRIMARY_V1.json').read_bytes())
        old_plan = snapshot['files'][snapshot['generation']+'/BOUND_PLAN.json']['value']
        owner = ci.strict_json((fixtures/'ACTUAL_OWNER_ROOT_FINAL_RESULT_V1.json').read_bytes())['result']
        accepted = ci.accept_owner(old_plan, owner['task_id'], before, db['unchanged_snapshot'], db['acceptance'])
        self.assertEqual(accepted['content_sha256'], owner['content_sha256'])
        self.assertEqual(owner['status'], 'ROOT_FINAL_READONLY_PASS')
        self.assertTrue(owner['persistent_hold_same_as_preflight'])
        self.assertTrue(owner['exact_run_and_probe_exit_and_cgroup_empty'])
        # No prepare/registration/runner invocation for the closed key or old plan.


if __name__ == '__main__':
    unittest.main()
