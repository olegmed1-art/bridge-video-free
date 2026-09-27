"""Source acceptance and fault injection for dormant release staging."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from ops import light_native_pilot_release as target


def value():
    raw = dict(revision='a'*40, files={**dict.fromkeys(target.EXTRA, ''),
        'oracle_autopilot/light_native_loader.py': 'pass\n'})
    return {**raw, 'sha256': hashlib.sha256(target.encoded(raw)).hexdigest()}


def reseal(bundle):
    bundle['sha256'] = hashlib.sha256(target.encoded({k: bundle[k] for k in ('revision', 'files')})).hexdigest()
    return bundle


@pytest.mark.parametrize('path', ['../escape.py', '/escape.py', 'oracle_autopilot/../escape.py',
    'oracle_autopilot//escape.py', 'oracle_autopilot/./escape.py', 'oracle_autopilot/back\\slash.py',
    'ops/escape.py'])
def test_noncanonical_or_unapproved_path_refused(path):
    item = value()
    item['files'][path] = 'pass'
    reseal(item)
    with pytest.raises(RuntimeError, match='PILOT_RELEASE_PATH'):
        target.validate(item, item['revision'], item['sha256'])


def test_acceptance_is_external_and_all_required_files_present():
    item = value()
    target.validate(item, item['revision'], item['sha256'])
    with pytest.raises(RuntimeError, match='NOT_ACCEPTED'):
        target.validate(item, item['revision'], 'b'*64)
    item['files'].pop('database/native_cli_permission_engine.py')
    reseal(item)
    with pytest.raises(RuntimeError, match='INCOMPLETE'):
        target.validate(item, item['revision'], item['sha256'])


def committed_tree(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    git('init', '-q')
    root = Path(__file__).resolve().parents[1]
    for name in set(target.EXTRA + target.HELPERS) | {'oracle_autopilot/light_native_loader.py'}:
        if name in target.MARKERS:
            continue
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / name).read_bytes())
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
    return git('rev-parse', 'HEAD')


def test_package_ignores_dirty_worktree_and_acceptance_covers_helpers(tmp_path):
    revision = committed_tree(tmp_path)
    raw = target.package(tmp_path, revision)
    (tmp_path / 'ops/light_native_pilot_release.py').write_text('raise Exception("dirty")')
    assert target.package(tmp_path, revision) == raw
    accepted = hashlib.sha256(raw).hexdigest()
    compile(target.program(raw, revision, accepted), 'bootstrap', 'exec')
    with pytest.raises(RuntimeError, match='NOT_ACCEPTED'):
        target.program(raw + b' ', revision, accepted)
    item = json.loads(raw)
    item['helpers']['ops/light_native_pilot_release.py'] += '\n# changed'
    with pytest.raises(RuntimeError, match='NOT_ACCEPTED'):
        target.program(target.encoded(item), revision, accepted)


@pytest.mark.skipif(os.geteuid() != 0, reason='root metadata test')
@pytest.mark.parametrize('failure', [None, 'main', 'hold', 'probe'])
def test_stage_never_restarts_service_or_edits_config(tmp_path, monkeypatch, failure):
    root = tmp_path / 'records'
    base = tmp_path / 'base.unit'; base.write_bytes(b'original unit'); base.chmod(0o644)
    drop = tmp_path / 'hold.conf'; drop.write_bytes(b'original HOLD'); drop.chmod(0o644)
    monkeypatch.setattr(target, 'ROOT', root)
    monkeypatch.setattr(target.hold, 'BASE', base)
    monkeypatch.setattr(target.hold, 'DROP', drop)
    monkeypatch.setattr(target.os, 'uname', lambda: SimpleNamespace(nodename='autopilot-lite-vnic'))
    identity = target.hold.HoldIdentity('autopilot-lite-vnic', 123, 'a'*32, '/old', 'private-fingerprint')
    changed = target.hold.HoldIdentity('autopilot-lite-vnic', 456, 'b'*32, '/old', 'changed')
    attest = Mock(side_effect=[identity, changed] if failure == 'hold' else None, return_value=identity)
    monkeypatch.setattr(target.hold, 'attest', attest)
    main = Mock(side_effect=RuntimeError('main changed') if failure == 'main' else None)
    monkeypatch.setattr(target.staging, 'require_current_main', main)
    stage = Mock(return_value=tmp_path / 'release')
    monkeypatch.setattr(target.staging, 'stage', stage)
    monkeypatch.setattr(target.staging, 'verify_release', Mock())
    monkeypatch.setattr(target, 'probe', Mock(side_effect=RuntimeError('probe') if failure == 'probe' else None))
    # No service commands or hidden subprocess side effects are permitted by stage.
    monkeypatch.setattr(target.subprocess, 'run', Mock(side_effect=AssertionError('unexpected process')))
    item = value()
    if failure:
        with pytest.raises(RuntimeError): target.stage(item, item['revision'], item['sha256'])
        assert not (root / item['revision'] / 'staged.json').exists()
        if failure in ('main', 'hold'): stage.assert_not_called()
    else:
        result = target.stage(item, item['revision'], item['sha256'])
        assert result['hold_unchanged'] and not result['service_restarted']
        record = json.loads((root / item['revision'] / 'before.json').read_bytes())
        assert record['hold']['pid'] == 123
        assert 'private-fingerprint' not in json.dumps(result)
    assert base.read_bytes() == b'original unit' and drop.read_bytes() == b'original HOLD'


def test_workflow_is_manual_source_pinned_and_never_activates():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/light-native-pilot-stage.yml').read_text()
    assert '\n  push:' not in workflow and '\n  schedule:' not in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.expected_main_sha == github.sha" in workflow
    assert "&& 'oracle-light-backup-mutation' || format('light-pilot-stage-noop-{0}'" in workflow
    assert 'environment: database-production' in workflow
    assert 'accepted_package_sha256' in workflow
    assert 'github.actor == github.repository_owner' in workflow
    assert 'github.triggering_actor == github.repository_owner' in workflow
    assert 'sudo -n /usr/bin/python3 -I -S -B -' in workflow
    assert 'git/ref/heads/main --jq .object.sha' in workflow
    assert 'contents: write' not in workflow
