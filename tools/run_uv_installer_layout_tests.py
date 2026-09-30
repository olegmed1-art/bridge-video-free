"""Only for the explicitly authorized ephemeral Linux root layout runner."""
import os
from pathlib import Path
import sys
import unittest

root = Path(__file__).resolve().parents[1]
if not (sys.platform == 'linux' and os.getuid() == os.geteuid() == 0
        and os.environ.get('UV_LAYOUT_ISOLATED') == '1'):
    raise SystemExit('Refusing: isolated Linux root runner required')
if set(os.listdir('/sys/class/net')) != {'lo'}:
    raise SystemExit('Refusing: offline network namespace required')
sys.path.insert(0, str(root))
suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern='test_uv_installer_attempts_layout.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful() or result.skipped or result.testsRun != 4:
    raise SystemExit(1)
print('UV_INSTALLER_LAYOUT_PASS: 4 tests, real root -> UID/GID 65534, zero skipped')
