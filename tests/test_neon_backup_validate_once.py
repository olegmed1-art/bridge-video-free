"""Offline only: mock subprocess/network, synthetic file bytes and credentials."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from ops import neon_backup_validate_once as v


class ValidationTests(unittest.TestCase):
    def workflow(self):
        return Path('.github/workflows/native-registry-credential-probe.yml').read_text()

    def test_no_publication_and_dispatch_bounds(self):
        text = self.workflow()
        for required in ('timeout-minutes: 20', 'github.run_attempt == 1',
                         'inputs.expected_review_sha == github.sha', v.TOKEN,
                         'deadline_epoch:', 'if: always()', 'persist-credentials: false'):
            self.assertIn(required, text)
        for forbidden in ('upload-artifact', 'download-artifact', 'issues: write', 'gh api',
                          'schedule:', 'push:', 'pull_request:', 'secrets.NEON_DATABASE_URL'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(text.count('runs-on:'), 1)
        self.assertEqual(text.count('secrets.'), 2)

    def test_size_gate_and_hash_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'file'
            path.write_bytes(b'synthetic')
            expected = v.hash_file(path)
            path.write_bytes(b'corrupted')
            self.assertNotEqual(v.hash_file(path), expected)
            with patch.object(v, 'MAX_BYTES', 2), self.assertRaises(v.Refused):
                v.hash_file(path)
            path.write_bytes(b'')
            with self.assertRaises(v.Refused):
                v.hash_file(path)

    def test_counts_are_role_independent_and_identical_on_both_sides(self):
        self.assertNotIn('information_schema.tables', v.STATS)
        self.assertIn("c.relkind IN ('r','p','v','m','f')", v.STATS)
        self.assertIn("nspname !~ '^pg_'", v.STATS)
        self.assertTrue(v.VERIFY.startswith(v.STATS))

    def test_child_isolation_caps_and_no_secret_argv(self):
        runner = v.Runner(Path('/private/synthetic'), '123', time.monotonic()+100)
        pg = {'PGPASSWORD': 'synthetic-password', 'PGHOST': v.source.HOST}
        with patch.object(os, 'getuid', return_value=1001, create=True), \
             patch.object(os, 'getgid', return_value=1001, create=True), \
             patch.object(runner, 'run') as run:
            runner.source_client(pg, ['pg_dump'], dump=True)
            cmd = run.call_args.args[0]
            self.assertIn('fsize=209715200:209715200', cmd)
            self.assertNotIn('synthetic-password', str(cmd))
            self.assertNotIn('BACKUP_PASSPHRASE', run.call_args.kwargs['env'])
            self.assertEqual(run.call_args.kwargs['seconds'], 360)
            runner.crypt('synthetic-passphrase')
            self.assertNotIn('synthetic-passphrase', str(run.call_args.args))
            self.assertNotIn('PGPASSWORD', run.call_args.kwargs['env'])
            self.assertNotIn('DATABASE_URL', run.call_args.kwargs['env'])
        with tempfile.TemporaryDirectory() as directory:
            runner.root = Path(directory)
            (runner.root / 'restored').write_bytes(b'synthetic')
            with patch.object(runner, 'run', side_effect=['', '', '', '1|2|3|4\nt']) as run:
                runner.restore('1|2|3|4')
            cmd = run.call_args_list[0].args[0]
            self.assertIn('--network=none', cmd)
            self.assertIn('/var/lib/postgresql:rw,size=1073741824', cmd)
            self.assertNotIn('--mount', cmd)
            self.assertNotIn('-p', cmd)
            self.assertNotIn('BACKUP_PASSPHRASE', str(run.call_args_list))
            self.assertNotIn('PGPASSWORD', str(run.call_args_list))

    def test_deadline_caps_every_subprocess(self):
        runner = v.Runner(Path('/synthetic'), '123', 101)
        with patch.object(time, 'monotonic', return_value=100), \
             patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([],0,b'')) as run:
            runner.run(['synthetic'], seconds=360)
            self.assertEqual(run.call_args.kwargs['timeout'], 1)
            self.assertEqual(run.call_args.kwargs['env'], v.minimal_env())
        with patch.object(time, 'monotonic', return_value=101), \
             patch.object(subprocess, 'run') as run, self.assertRaises(v.Refused):
            runner.run(['synthetic'])
        run.assert_not_called()

    def test_cleanup_verifies_daemon_absence_even_after_rm_error(self):
        for remains in (b'', b'container-id'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'private'; root.mkdir()
                (root/'dump').write_bytes(b'synthetic')
                runner = v.Runner(root, '123', 0)
                results = [subprocess.CompletedProcess([],1), subprocess.CompletedProcess([],0,remains)] * 2
                with patch.object(subprocess, 'run', side_effect=results) as run:
                    self.assertEqual(runner.cleanup(), not remains)
                self.assertFalse(root.exists())
                self.assertEqual(run.call_count, 4)

    def simulate(self, directory, fail=None, overrides=None):
        env = dict(VALIDATION_OPERATION=v.TOKEN, VALIDATION_STARTED='1000',
                   VALIDATION_DEADLINE='2200', RUNNER_TEMP=directory, GITHUB_RUN_ID='123',
                   DATABASE_URL='synthetic-uri', BACKUP_PASSPHRASE='synthetic-passphrase-24-chars')
        env.update(overrides or {})
        def stage(name):
            if fail == name:
                raise RuntimeError('PRIVATE_CANARY_DSNPASSWORD_SQLDATA')
        def preflight(pg, *, gates, run_client):
            stage('preflight'); gates.update({k:'PASS' for k in gates})
        def client(self, pg, args, *, sql=None, dump=False):
            stage('dump' if dump else 'source_stats')
            if dump:
                (self.root/'dump').write_bytes(b'synthetic')
                return ''
            return '1|2|3|4'
        def crypt(self, phrase, decrypt=False):
            stage('hash_decrypt' if decrypt else 'encrypt')
            (self.root/('restored' if decrypt else 'encrypted')).write_bytes(b'synthetic')
        def cleanup(self):
            import shutil
            shutil.rmtree(self.root)
            return fail != 'cleanup'
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            stack.enter_context(patch.object(time, 'time', return_value=1001))
            stack.enter_context(patch.object(v.context_guard, 'context', return_value='a'*40))
            stack.enter_context(patch.object(v.context_guard, 'check_main'))
            stack.enter_context(patch.object(v.source, 'parameters', return_value={'PGPASSWORD':'synthetic'}))
            stack.enter_context(patch.object(v.source, 'preflight', side_effect=preflight))
            stack.enter_context(patch.object(v.Runner, 'run', side_effect=lambda *a,**k: stage('tools')))
            stack.enter_context(patch.object(v.Runner, 'source_client', client))
            stack.enter_context(patch.object(v.Runner, 'crypt', crypt))
            stack.enter_context(patch.object(v.Runner, 'restore', side_effect=lambda *a: stage('restore')))
            stack.enter_context(patch.object(v.Runner, 'cleanup', cleanup))
            stack.enter_context(patch.object(signal, 'signal'))
            stack.enter_context(patch.object(signal, 'SIGALRM', 14, create=True))
            stack.enter_context(patch.object(signal, 'ITIMER_REAL', 0, create=True))
            stack.enter_context(patch.object(signal, 'setitimer', create=True))
            return v.execute()

    def test_roundtrip_mock_success_and_all_phase_failures_sanitized(self):
        for failure in (None, 'tools', 'preflight', 'source_stats', 'dump', 'encrypt',
                        'hash_decrypt', 'restore', 'cleanup'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                row = self.simulate(directory, failure)
                self.assertEqual(row['status'], 'PASS' if failure is None else 'FAIL')
                self.assertEqual(row['cleanup'], failure != 'cleanup')
                self.assertNotIn('PRIVATE', json.dumps(row))
                self.assertFalse(row['upload']); self.assertFalse(row['durable_backup'])
                self.assertFalse(list(Path(directory).iterdir()))

    def test_expired_or_extended_deadline_and_wrong_operation_fail_before_tools(self):
        for values in ({'VALIDATION_DEADLINE':'1000'}, {'VALIDATION_DEADLINE':'2201'},
                       {'VALIDATION_STARTED':'1002'}, {'VALIDATION_OPERATION':'wrong'}):
            with tempfile.TemporaryDirectory() as directory:
                row = self.simulate(directory, overrides=values)
                self.assertEqual(row['status'], 'FAIL')
                self.assertEqual(row['phase'], 'context')
                self.assertFalse(row['dump'])
                self.assertFalse(list(Path(directory).iterdir()))


if __name__ == '__main__':
    unittest.main()
