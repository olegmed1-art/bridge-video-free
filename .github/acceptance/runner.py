"""Run the bounded reviewed source tests offline; not production acceptance."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path('/work/candidate')
SOURCE_SHA = '0098446b9a79f9fdc73986312ad6fc6265b0b199'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def command(args, *, expected_marker=None, private=False):
    p = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=240)
    output = p.stdout + p.stderr
    if not private:
        print(output, flush=True)
    require(p.returncode == 0, 'LINUX_EXECUTION_FAILURE: ' + args[0])
    if expected_marker:
        require(expected_marker in output, 'COUNT_OR_MARKER_MISMATCH')
    return output


def pytest_group(label, files, expected):
    report = '/tmp/' + label + '.xml'
    command([sys.executable, '-B', '-m', 'pytest', '-q', '-ra', '-p', 'no:cacheprovider',
             '--noconftest', '-c', '/dev/null', '--rootdir=/work/candidate',
             '--junitxml=' + report, *files])
    cases = ET.parse(report).getroot().findall('.//testcase')
    require(len(cases) == expected, label + ': count mismatch')
    require(all(not any(c.find(t) is not None for t in ('failure', 'error', 'skipped'))
                for c in cases), label + ': failure/error/skip')
    print(f'{label}: {expected} PASS zero skipped', flush=True)


def preflight(lane):
    require(sys.platform == 'linux' and sys.version_info[:2] == (3, 12), 'PLATFORM_INCOMPATIBLE')
    require(Path.cwd() == ROOT, 'Wrong cwd')
    require(set(os.listdir('/sys/class/net')) == {'lo'}, 'NETWORK_ISOLATION_MISSING')
    require(os.environ.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD') == '1', 'PLUGIN_ISOLATION_MISSING')
    require('PYTEST_ADDOPTS' not in os.environ and 'PYTEST_PLUGINS' not in os.environ, 'PYTEST_OVERRIDES')
    uid = 1000 if lane == 'nonroot' else 0
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == uid, 'REAL_IDENTITY_REQUIRED')
    manifest = json.loads(Path('/control/source-manifest.json').read_text())
    require(manifest['source_sha'] == SOURCE_SHA and len(manifest['files']) == 2624, 'SOURCE_BINDING')
    actual = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file()}
    require(actual == {f['path'] for f in manifest['files']}, 'SOURCE_FILE_SET')
    for f in manifest['files']:
        p = ROOT / f['path']
        require(not p.is_symlink(), 'SOURCE_SYMLINK')
        require(hashlib.sha256(p.read_bytes()).hexdigest() == f['sha256'], 'SOURCE_HASH: ' + f['path'])
    for row in Path('/control/requirements.txt').read_text().splitlines():
        package, version = row.split('==')
        require(importlib.metadata.version(package) == version, 'DEPENDENCY_MISMATCH: ' + package)
    print('PREFLIGHT PASS exact source ' + SOURCE_SHA + ' lane=' + lane, flush=True)


lane = sys.argv[1]
require(lane in ('nonroot', 'light', 'layout'), 'Unknown lane')
preflight(lane)
if lane == 'nonroot':
    # Build a new local-only Git index; never import host .git/config/credentials.
    command(['git', 'init', '-q'])
    manifest = json.loads(Path('/control/source-manifest.json').read_text())
    Path('/tmp/tracked-paths').write_text('\n'.join(f['path'] for f in manifest['files']) + '\n')
    command(['git', '-c', 'core.autocrlf=false', 'add', '--force', '--pathspec-from-file=/tmp/tracked-paths'])
    paths = command(['git', 'ls-files', '-z'], private=True).rstrip('\0').split('\0')
    require(set(paths) == {f['path'] for f in manifest['files']}, 'SCANNER_INDEX_INCOMPLETE')
    command(['bash', 'ops/check_no_committed_secrets.sh'], expected_marker='SECRET_GATE_PASS', private=True)
    print('LOCAL_SOURCE_SECRET_SCAN_PASS (not required GitHub check status)', flush=True)
    pytest_group('uv-regression', [
        'tests/test_universal_video_contract.py',
        'tests/test_universal_video_runtime_safety.py',
        'tests/test_universal_video_server_intake.py'], 43)
    pytest_group('spool-regression', [
        'tests/test_universal_video_spool_security.py',
        'tests/test_universal_video_spool_repair_helper.py',
        'tests/test_universal_video_spool_repair_contract.py'], 18)
    output = command([sys.executable, '-B', 'tools/run_uv_lifecycle_tests.py'])
    require(re.search(r'Ran 28 tests in ', output) is not None and
            re.search(r'^OK\s*$', output, re.M) is not None and
            'skipped=' not in output, 'LIFECYCLE_COUNT_OR_SKIP')
    print('uv-lifecycle: 28 PASS zero skipped', flush=True)
elif lane == 'light':
    command([sys.executable, '-B', '.github/scripts/light_root_gate.py'],
            expected_marker='SOURCE TEST GATE PASS: 100 unique cases (86 + 14)')
else:
    command([sys.executable, '-B', 'tools/run_uv_installer_layout_tests.py'],
            expected_marker='UV_INSTALLER_LAYOUT_PASS: 4 tests, real root -> UID/GID 65534, zero skipped')
print('LANE_PASS ' + lane, flush=True)
