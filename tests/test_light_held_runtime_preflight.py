"""The installed release attestation must fail closed before service activation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import stat
from types import SimpleNamespace

import pytest

from ops import oracle_light_held_runtime_preflight as held


def service():
    return {
        'ActiveState': 'active', 'SubState': 'running', 'MainPID': '29894',
        'NRestarts': '0', 'InvocationID': 'a' * 32,
        'WorkingDirectory': str(held.RELEASE), 'User': 'school-autopilot',
        'Group': 'school-autopilot', 'DropInPaths': str(held.DROP),
        'FragmentPath': str(held.UNIT_PATH),
        'ExecStart': '{ path=' + held.PYTHON + ' ; argv[]=' + held.PYTHON +
                     ' -m oracle_autopilot.worker_v17 ; ignore_errors=no ; }',
        'EnvironmentFiles': str(held.ENV_PATH) + ' (ignore_errors=no)',
        'Environment': 'PYTHONDONTWRITEBYTECODE=1 AUTOPILOT_ADMISSION_MODE=HOLD',
        'NeedDaemonReload': 'yes',
    }


def test_pinned_git_source_matches_installed_bundle_digest():
    bundle = held.source_bundle()
    held.validate_bundle(bundle)
    assert bundle['revision'] == held.REVISION
    assert bundle['sha256'] == held.BUNDLE_SHA256
    program = subprocess.check_output([sys.executable, str(Path(held.__file__)), 'bundle'],
                                      text=True)
    compile(program, 'held-preflight-transport', 'exec')


@pytest.mark.parametrize('field,value', [
    ('WorkingDirectory', '/tmp/other'), ('User', 'root'), ('DropInPaths', ''),
    ('Environment', 'AUTOPILOT_ADMISSION_MODE=ACTIVE'),
    ('MainPID', '0'), ('ActiveState', 'failed'),
    ('ExecStart', '/bin/true'), ('EnvironmentFiles', '/tmp/secret'),
])
def test_service_drift_rejected(field, value):
    row = service()
    row[field] = value
    with pytest.raises(held.Blocked):
        held.validate_service(row)


def test_current_hold_service_shape_and_reload_signal_are_distinct():
    row = service()
    held.validate_service(row)
    row['NeedDaemonReload'] = 'no'
    held.validate_service(row)
    # A stale daemon state is explicitly reported but cannot turn HOLD to ACTIVE.


def test_source_digest_and_path_tampering_rejected():
    bundle = held.source_bundle()
    for tamper in (
        lambda b: b.update(revision='0' * 40),
        lambda b: b['files'].__setitem__('oracle_autopilot/worker.py', 'pass\n'),
        lambda b: b['files'].__setitem__('../../etc/passwd', 'pass\n'),
    ):
        altered = copy.deepcopy(bundle)
        tamper(altered)
        with pytest.raises(held.Blocked):
            held.validate_bundle(altered)


def test_release_inventory_and_content_drift_rejected(tmp_path, monkeypatch):
    bundle = held.source_bundle()
    release = tmp_path / 'release'
    release.mkdir(mode=0o755)
    monkeypatch.setattr(held, 'RELEASE', release)
    contents = {**bundle['files'], 'SOURCE_REVISION': held.REVISION + '\n',
                'RUNTIME_BUNDLE_SHA256': held.BUNDLE_SHA256 + '\n'}
    for name, text in contents.items():
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        target.write_text(text)
        target.chmod(0o444)
    # GitHub-hosted runners are non-root. Model root ownership of the installed
    # release while retaining real file modes, content and inventory behavior.
    original_lstat = Path.lstat

    def root_owned_lstat(path):
        info = original_lstat(path)
        if path == release or path in release.parents or release in path.parents:
            mode = info.st_mode & ~0o022 if path in release.parents else info.st_mode
            return SimpleNamespace(st_uid=0, st_mode=mode)
        return info

    monkeypatch.setattr(Path, 'lstat', root_owned_lstat)
    held.verify_release(bundle)
    target = release / 'oracle_autopilot/contract.py'
    target.chmod(0o644)
    with pytest.raises(held.Blocked, match='RELEASE_FILE_DRIFT'):
        held.verify_release(bundle)
    target.chmod(0o444)
    extra = release / 'unexpected.py'
    extra.write_text('pass\n')
    with pytest.raises(held.Blocked, match='RELEASE_INVENTORY_DRIFT'):
        held.verify_release(bundle)


def test_release_ancestor_must_be_root_owned_and_not_writable(tmp_path, monkeypatch):
    release = tmp_path / 'release'
    release.mkdir()
    monkeypatch.setattr(held, 'RELEASE', release)
    original_lstat = Path.lstat

    def unsafe_parent(path):
        info = original_lstat(path)
        if path == tmp_path:
            return SimpleNamespace(st_uid=1001, st_mode=info.st_mode | 0o022)
        return SimpleNamespace(st_uid=0, st_mode=info.st_mode)

    monkeypatch.setattr(Path, 'lstat', unsafe_parent)
    with pytest.raises(held.Blocked, match='RELEASE_PARENT'):
        held.verify_release(held.source_bundle())


def test_release_symlink_ancestor_rejected(tmp_path, monkeypatch):
    release = tmp_path / 'release'
    release.mkdir()
    monkeypatch.setattr(held, 'RELEASE', release)
    original_lstat = Path.lstat

    def symlink_parent(path):
        info = original_lstat(path)
        mode = stat.S_IFLNK | 0o777 if path == tmp_path else info.st_mode
        return SimpleNamespace(st_uid=0, st_mode=mode)

    monkeypatch.setattr(Path, 'lstat', symlink_parent)
    with pytest.raises(held.Blocked, match='RELEASE_PARENT'):
        held.verify_release(held.source_bundle())


@pytest.mark.parametrize('where,key', [
    ('live', 'PYTHONPATH'), ('live', 'PYTHONHOME'),
    ('expected', 'PYTHONPATH'), ('expected', 'PYTHONHOME'),
])
def test_python_import_override_rejected_before_probe(where, key):
    live = {'AUTOPILOT_WORKER_ID': 'oracle-autopilot-light-1',
            'AUTOPILOT_ADMISSION_MODE': 'HOLD'}
    expected = {'AUTOPILOT_WORKER_ID': 'oracle-autopilot-light-1'}
    (live if where == 'live' else expected)[key] = '/tmp/injected'
    with pytest.raises(held.Blocked, match='PYTHON_IMPORT_OVERRIDE'):
        held.validate_live_environment(live, expected)


def test_exact_fence_digest_and_child_exit_required_before_result():
    proof = {'status': 'PASS', 'worker_id': 'oracle-autopilot-light-1',
             'mailbox_pr': 1703, 'fence_sha256': held.FENCE_SHA256}
    held.validate_proof(proof, 0)
    for changed, exit_code in (({'fence_sha256': '0' * 64}, 0),
                               ({'mailbox_pr': 1150}, 0), ({}, 2)):
        with pytest.raises(held.Blocked, match='PREFLIGHT_NOT_PASSED'):
            held.validate_proof({**proof, **changed}, exit_code)


@pytest.mark.parametrize('raw', [b'KEY=x\nKEY=y\n', b'KEY=x y\n', b'BAD-KEY=x\n'])
def test_environment_parser_rejects_ambiguous_values(raw):
    with pytest.raises(held.Blocked):
        held.environment(raw)


def test_transport_and_workflow_keep_exact_readonly_scope():
    script = Path(held.__file__).read_text()
    flow = (Path(__file__).resolve().parents[1] /
            '.github/workflows/oracle-light-held-runtime-preflight.yml').read_text()
    assert "github.event_name == 'workflow_dispatch'" in flow
    assert "github.ref == 'refs/heads/main'" in flow
    assert 'group: oracle-light-backup-mutation' in flow
    assert 'sudo -n /usr/bin/python3 -' in flow
    assert 'contents: write' not in flow and 'actions: write' not in flow
    assert 'systemctl restart' not in flow and 'systemctl stop' not in flow
    assert 'default_transaction_read_only=on' in bundle_probe_source()
    assert "'AUTOPILOT_ADMISSION_MODE') == 'HOLD'" in script
    assert "Path(f'/proc/{pid}/cwd').resolve() == RELEASE" in script
    assert held.FENCE_SHA256 == '655fa30ce165663fb0de1b98bb3bba85237fefc6900a507e4a88edced9b0b11e'


def bundle_probe_source():
    return held.source_bundle()['files']['oracle_autopilot/light_runtime_probe.py']


def test_failure_record_never_prints_exception_text(capsys, monkeypatch):
    monkeypatch.setattr(held.os, 'geteuid', lambda: 1001)
    with pytest.raises(held.Blocked, match='HOST_IDENTITY'):
        held.run(held.source_bundle())
    # The remote entry point catches errors and emits only fixed codes; no env is logged.
    program = Path(held.__file__).read_text()
    assert "'guard': str(exc) if type(exc) is Blocked else 'SYSTEM_ERROR'" in program
