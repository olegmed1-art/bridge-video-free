"""Private runtime tampering, credential transport, and refusal ordering."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from ops import native_maintenance_owner_host as host
from ops import native_maintenance_owner_host_runner as runner

CREDENTIAL = ('postgresql://neondb_owner:synthetic-secret@' + 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
              + '/neondb?sslmode=require&channel_binding=require')


class RunnerTests(unittest.TestCase):
    def test_host_and_runner_import_without_ambient_driver(self):
        root = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([runner.sys.executable, '-I', '-B', '-S', '-c',
            'import sys; sys.path.insert(0,sys.argv[1]); '
            'from ops import native_maintenance_owner_host,native_maintenance_owner_host_runner; '
            "assert not any(x.startswith('psycopg') for x in sys.modules)", root], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_wrong_actor_before_credential_or_ssh(self):
        with patch.object(runner.sys, 'argv', ['runner', 'key', 'known', 'wheels']), \
                patch.dict(os.environ, {'GITHUB_TRIGGERING_ACTOR': 'other'}), \
                patch.object(runner, 'source_check') as source, \
                patch.object(runner.subprocess, 'run') as ssh, self.assertRaises(runner.bundle.BundleError):
            runner.main()
        source.assert_not_called()
        ssh.assert_not_called()

    def test_source_drift_blocks_credential_transport(self):
        with patch.object(runner.sys, 'argv', ['runner', 'key', 'known', 'wheels']), \
                patch.dict(os.environ, {'GITHUB_TRIGGERING_ACTOR': 'olegmed1-art',
                                       'NATIVE_OWNER_DATABASE_URL': CREDENTIAL, 'EXPECTED_MAIN': 'a'*40}), \
                patch.object(runner, 'source_check', side_effect=[None, RuntimeError('drift')]), \
                patch.object(runner.bundle, 'build', return_value=b'source'), \
                patch.object(runner.driver, 'build', return_value=b'wheels'), \
                patch.object(runner, 'bootstrap', return_value='code'), \
                patch.object(runner.subprocess, 'run') as ssh, self.assertRaisesRegex(RuntimeError, 'drift'):
            runner.main()
        ssh.assert_not_called()

    def test_credentials_only_in_stdin_and_host_errors_not_printed(self):
        def failed_ssh(command, **kwargs):
            self.assertNotIn('synthetic-secret', repr(command))
            self.assertNotIn('synthetic-secret', repr(kwargs['env']))
            self.assertNotIn('NATIVE_OWNER_DATABASE_URL', os.environ)
            self.assertEqual(json.loads(kwargs['input'])['credential'], CREDENTIAL)
            return type('Result', (), dict(returncode=2, stdout=b'synthetic-secret', stderr=b'synthetic-secret'))()
        output = io.StringIO()
        with patch.object(runner.sys, 'argv', ['runner', 'key', 'known', 'wheels']), \
                patch.dict(os.environ, {'GITHUB_TRIGGERING_ACTOR': 'olegmed1-art',
                                       'NATIVE_OWNER_DATABASE_URL': CREDENTIAL, 'EXPECTED_MAIN': 'a'*40}), \
                patch.object(runner, 'source_check'), \
                patch.object(runner.bundle, 'build', return_value=b'source'), \
                patch.object(runner.driver, 'build', return_value=b'wheels'), \
                patch.object(runner, 'bootstrap', return_value='code'), \
                patch.object(runner.subprocess, 'run', side_effect=failed_ssh), \
                contextlib.redirect_stdout(output), self.assertRaises(runner.bundle.BundleError) as exc:
            runner.main()
        self.assertNotIn('synthetic-secret', str(exc.exception) + output.getvalue())

    def test_generated_bootstrap_compiles_and_contains_no_credential(self):
        root = Path(__file__).resolve().parents[1]
        with patch.object(runner.bundle, 'git', side_effect=lambda repo, cmd, ref:
                          (root / ref.split(':', 1)[1]).read_bytes()):
            code = runner.bootstrap(root, 'a'*40, 'b'*64, 'c'*64, '123-1')
        compile(code, '<outer>', 'exec')
        call = next(n for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == 'managed')
        child = ast.literal_eval(call.args[0])
        compile(child, '<child>', 'exec')
        self.assertIn('main(wheels', child)
        self.assertNotIn('synthetic-secret', code)
        self.assertLess(len(code.encode()), 98304)


@unittest.skipUnless(os.getuid() == 0, 'root-only isolated filesystem fixture')
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.parent = Path(tmp.name)
        self.identity = dict(python='fixture', binary_sha256='a'*64, architecture='fixture', wheels_digest='b'*64)
        self.files = {'psycopg/__init__.py': b'# fixture\n', 'typing_extensions.py': b'# fixture\n'}
        self.root = self.parent / host.driver.NAME / host.driver.sha(host.driver.encoded(self.identity))
        self.root.mkdir(parents=True, mode=0o700)
        self.root.parent.chmod(0o700)
        (self.root / 'site').mkdir(mode=0o700)
        (self.root / 'site/psycopg').mkdir(mode=0o700)
        for name, data in self.files.items():
            host.driver.write_private(self.root / 'site' / name, data)
        host.driver.write_private(self.root / 'READY.json', host.driver.encoded(self.identity))
        host.driver.write_private(self.root.parent / 'lock', b'')
        for p in (patch.object(host.driver, 'PARENT', self.parent),
                  patch.object(host.driver, 'python_identity', return_value=self.identity),
                  patch.object(host.driver, 'decode', return_value=self.files),
                  patch.object(host.driver, 'trusted_parent')):
            p.start()
            self.addCleanup(p.stop)

    def test_existing_tree_and_tampering(self):
        with host.verified_runtime(b'fixture') as (root, identity):
            self.assertEqual(root, self.root)
            self.assertEqual(identity, self.root.name)
        path = self.root / 'site/typing_extensions.py'
        path.write_bytes(b'changed')
        with self.assertRaises(Exception):
            with host.verified_runtime(b'fixture'):
                self.fail('must reject before import')
        self.assertEqual(path.read_bytes(), b'changed')

    def test_missing_lock_is_not_created(self):
        (self.root.parent / 'lock').unlink()
        with self.assertRaises(FileNotFoundError):
            with host.verified_runtime(b'fixture'):
                self.fail('must refuse')
        self.assertFalse((self.root.parent / 'lock').exists())

    def test_locked_runtime_refuses(self):
        fd = os.open(self.root.parent / 'lock', os.O_RDWR)
        try:
            host.fcntl.flock(fd, host.fcntl.LOCK_EX | host.fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                with host.verified_runtime(b'fixture'):
                    self.fail('must not run concurrently')
        finally:
            os.close(fd)

    def test_tampering_during_use_refuses_final_success(self):
        with self.assertRaises(Exception):
            with host.verified_runtime(b'fixture'):
                (self.root / 'site/typing_extensions.py').chmod(0o644)


class SecretFailureTests(unittest.TestCase):
    def test_host_failure_is_fixed_and_redacted(self):
        output = io.StringIO()
        with patch.object(host, 'observe', side_effect=RuntimeError(CREDENTIAL)), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            host.main(b'', CREDENTIAL)
        self.assertEqual(json.loads(output.getvalue()),
                         dict(audit='NATIVE_OWNER_HOST_READ_ONLY_REFUSED', production_mutations=False))

    def test_wrong_credential_refuses_before_hold_or_runtime(self):
        with patch.object(host.os, 'getuid', return_value=0), \
                patch.object(host.os, 'uname', return_value=type('U', (), dict(nodename='autopilot-lite-vnic'))()), \
                patch.object(host, 'attest') as attest, \
                patch.object(host, 'verified_runtime') as runtime, self.assertRaises(ValueError):
            host.observe(b'', 'postgresql://bad-host/bad-db')
        attest.assert_not_called()
        runtime.assert_not_called()


if __name__ == '__main__':
    unittest.main()
