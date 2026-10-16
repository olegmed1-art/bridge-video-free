"""CI-only PG session and executor rehearsals from verified source."""
import os
from pathlib import Path
import subprocess
import sys

from ops import native_maintenance_bundle as bundle

DSN = 'postgresql://postgres:postgres@localhost:5432/bridge_school_ci'


def main():
    bundle.check(os.getuid() == 0 and os.environ.get('ADMIN_DATABASE_URL') == DSN,
                 'DISPOSABLE_ROOT_CI_REQUIRED')
    # Checkout SHA is supplied by GitHub's event environment, not payload metadata.
    source = os.environ.get('GITHUB_SHA')
    repo = Path(__file__).resolve().parents[2]
    payload = bundle.build(repo, source)
    # CI self-build digest checks transport consistency only, not external approval.
    expected_digest = bundle.digest(payload)
    with bundle.extracted(payload, source, expected_digest) as root:
        # CI-only dependency path; never use a runner-user site directory in a
        # production root process holding an owner credential.
        dependency_paths = [p for p in sys.path if 'site-packages' in p or 'dist-packages' in p]
        env = {'PATH': '/usr/bin:/bin', 'ADMIN_DATABASE_URL': DSN,
               'PYTHONDONTWRITEBYTECODE': '1',
               'PYTHONPATH': os.pathsep.join([str(root), *dependency_paths])}
        command = '''
import importlib
from pathlib import Path
import sys
root = Path.cwd().resolve()
for path in sys.argv[1:]:
    module = importlib.import_module(path[:-3].replace('/', '.'))
    if Path(module.__file__).resolve() != root / path:
        raise RuntimeError('BUNDLE_IMPORT_ESCAPED')
from database.fixtures.native_maintenance_session_rehearsal import main
main()
print('NATIVE_MAINTENANCE_EXTRACTED_SOURCE_PASS')
from database.fixtures.native_maintenance_executor_rehearsal import main as executor_main
executor_main()
for path in sys.argv[1:]:
    module = importlib.import_module(path[:-3].replace('/', '.'))
    if Path(module.__file__).resolve() != root / path:
        raise RuntimeError('BUNDLE_IMPORT_ESCAPED')
print('NATIVE_MAINTENANCE_EXTRACTED_EXECUTOR_PASS')
'''
        subprocess.run([sys.executable, '-B', '-c', command, *bundle.FILES],
                       cwd=root, env=env, check=True, timeout=180)
    print('NATIVE_MAINTENANCE_SOURCE_BUNDLE_PASS')


if __name__ == '__main__':
    main()
