"""Synthetic assembly contracts; no host/OCI production call from tests."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops import native_maintenance_checkpoint_host_probe as host
from ops import native_maintenance_checkpoint_duplex_probe as runner


class AssemblyTests(unittest.TestCase):
    def test_scope_is_unique_to_exact_source_run_and_attempt(self):
        original = host.identity('a' * 40, 1, 1)
        for args in [('b' * 40, 1, 1), ('a' * 40, 2, 1), ('a' * 40, 1, 2)]:
            value = host.identity(*args)
            self.assertNotEqual(value[2:], original[2:])
        for args in [('bad', 1, 1), ('a' * 40, 0, 1), ('a' * 40, True, 1)]:
            with self.assertRaises(Exception):
                host.identity(*args)

    def test_fixed_bootstrap_compiles_and_preserves_interactive_stdin(self):
        root = Path(__file__).resolve().parents[1]
        def read_git(repo, verb, path):
            return (root / path.split(':', 1)[1]).read_bytes()
        with patch.object(runner.bundle, 'git', side_effect=read_git):
            text = runner.bootstrap(root, 'a' * 40, 'b' * 64, 1, 1)
        compile(text, '<duplex-bootstrap>', 'exec')
        self.assertIn('read_exact', text)
        self.assertNotIn('sys.stdin.buffer.read(', text)
        self.assertNotIn('OCI_KEY', text)

    def test_terminal_result_never_authorizes_other_scope_or_expired_report(self):
        source = 'a' * 40
        _, _, scope, binding = host.identity(source, 1, 1)
        value = dict(kind='SYNTHETIC_DUPLEX_COMPLETE', binding=binding, scope=scope, source=source,
                     head_digest='f' * 64, sequence=21, elapsed_ms=1000, hold_unchanged=True)
        runner.validate_complete(value, source, binding, scope, 21)
        for key, replacement in [('source', 'b' * 40), ('scope', 'b' * 64), ('binding', 'b' * 64),
                                  ('sequence', 20), ('elapsed_ms', 60000), ('hold_unchanged', False),
                                  ('extra', 'private')]:
            with self.subTest(key=key), self.assertRaises(Exception):
                runner.validate_complete({**value, key: replacement}, source, binding, scope, 21)

    def test_failure_has_no_private_exception_or_traceback(self):
        output = io.StringIO()
        with patch.object(runner, 'main', side_effect=RuntimeError('private key material')), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            runner.entrypoint()
        self.assertNotIn('private', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['audit'], 'SYNTHETIC_DUPLEX_CHECKPOINT_REFUSED')


@unittest.skipUnless(os.getuid() == 0, 'root-owned host storage fixture')
class HostStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / 'store'
        self.root.mkdir(mode=0o700)
        for name, data in [('VERSION', host.storage.VERSION), ('lock', b'')]:
            path = self.root / name
            path.write_bytes(data)
            path.chmod(0o600)
        for p in (patch.object(host.storage, 'PARENT', self.parent),
                  patch.object(host.storage, 'NAME', 'store'),
                  patch.object(host.storage, 'trusted_parent'),
                  patch.object(host.storage, 'persistent_mount', return_value='ext4')):
            p.start()
            self.addCleanup(p.stop)

    def test_retained_private_journals_and_no_reuse(self):
        with host.journals('a' * 64) as (operation, pause, restored):
            operation.append(dict(kind='SYNTHETIC_ONLY'))
            pause.append(dict(kind='SYNTHETIC_ONLY'))
            self.assertEqual(restored.stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.root / ('a' * 64) / 'operation/000000.json').exists())
        with self.assertRaises(FileExistsError):
            with host.journals('a' * 64):
                self.fail('never overwrite evidence')

    def test_absent_prepared_lock_and_wrong_version_refuse_before_new_scope(self):
        (self.root / 'lock').unlink()
        with self.assertRaises(FileNotFoundError):
            with host.journals('a' * 64):
                self.fail('not prepared')
        self.assertFalse((self.root / ('a' * 64)).exists())
        (self.root / 'lock').touch(mode=0o600)
        (self.root / 'VERSION').write_bytes(b'wrong')
        with self.assertRaises(Exception):
            with host.journals('a' * 64):
                self.fail('wrong version')
        self.assertFalse((self.root / ('a' * 64)).exists())


if __name__ == '__main__':
    unittest.main()
