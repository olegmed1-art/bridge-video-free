"""Exercise the real tmpfiles engine through two simulated boot cycles."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class RuntimeDirectoryRule(unittest.TestCase):
    def test_recreates_runtime_with_expected_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'run').mkdir()
            config = root / 'rule.conf'
            script = (Path(__file__).resolve().parents[1] / 'ops/issue_881_runtime_directory_repair.sh').read_text()
            rule = next(line for line in script.splitlines() if line.startswith('readonly RULE=')).split("'", 2)[1]
            rule = rule.replace('universal-video universal-video', f'{os.getuid()} {os.getgid()}')
            config.write_text(rule + '\n')
            for _ in range(2):
                subprocess.run(['systemd-tmpfiles', '--root=' + directory, '--create', str(config)], check=True)
                runtime = root / 'run/bridge-school'
                st = runtime.stat()
                self.assertEqual(st.st_mode & 0o777, 0o750)
                self.assertEqual((st.st_uid, st.st_gid), (os.getuid(), os.getgid()))
                runtime.rmdir()


if __name__ == '__main__':
    unittest.main()
