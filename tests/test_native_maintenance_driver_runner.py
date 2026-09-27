"""Transport refusal before any SSH mutation, plus actual bootstrap compilation."""
from pathlib import Path
import os
import unittest
from unittest.mock import patch

from ops import native_maintenance_driver_runner as runner


class RunnerTests(unittest.TestCase):
    def test_triggering_actor_refused_before_source_or_ssh(self):
        with patch.object(runner.sys, 'argv', ['runner', 'key', 'known', 'wheels']), \
                patch.dict(os.environ, {'GITHUB_TRIGGERING_ACTOR': 'other'}), \
                patch.object(runner, 'source_check') as source, \
                patch.object(runner.subprocess, 'run') as ssh, \
                self.assertRaisesRegex(runner.bundle.BundleError, 'DRIVER_ARGS'):
            runner.main()
        source.assert_not_called()
        ssh.assert_not_called()

    def test_source_change_prevents_ssh_after_packaging(self):
        with patch.object(runner.sys, 'argv', ['runner', 'key', 'known', 'wheels']), \
                patch.dict(os.environ, {'GITHUB_TRIGGERING_ACTOR': 'olegmed1-art', 'EXPECTED_MAIN': 'a'*40}), \
                patch.object(runner, 'source_check', side_effect=[None, runner.bundle.BundleError('MAIN_CHANGED')]), \
                patch.object(runner.bundle, 'build', return_value=b'source'), \
                patch.object(runner.driver, 'build', return_value=b'wheels'), \
                patch.object(runner, 'bootstrap', return_value='compiled'), \
                patch.object(runner.subprocess, 'run') as ssh, \
                self.assertRaisesRegex(runner.bundle.BundleError, 'MAIN_CHANGED'):
            runner.main()
        ssh.assert_not_called()

    def test_real_bootstrap_compiles_without_executing(self):
        root = Path(__file__).resolve().parents[1]
        def read(repo, command, reference):
            self.assertEqual(command, 'show')
            return (root / reference.split(':', 1)[1]).read_bytes()
        with patch.object(runner.bundle, 'git', side_effect=read):
            code = runner.bootstrap(root, 'a'*40, 'b'*64, 'c'*64, '123-1')
        compile(code, '<bootstrap>', 'exec')
        self.assertLess(len(code.encode()), 98304)
        # Compile the generated child code too, without invoking systemd or SSH.
        import ast
        tree = ast.parse(code)
        managed = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Attribute) and n.func.attr == 'managed')
        child = ast.literal_eval(managed.args[0])
        compile(child, '<child>', 'exec')
        self.assertIn('wire=sys.stdin.buffer.read(16777217)', child)
        self.assertIn("'c" + 'c'*63 + "'", child)
        self.assertNotIn('DATABASE_URL', code)


if __name__ == '__main__':
    unittest.main()
