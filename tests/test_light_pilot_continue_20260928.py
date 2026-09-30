"""Focused local checks for the fixed same-task ledger continuation."""
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from ops.incident import light_pilot_continue_20260928 as continuation


class Ledger:
    def __init__(self, root):
        self.plan = type('Plan', (), {'ROOT': root, 'SUPERVISOR_UNIT': 'test-supervisor',
            'PILOT_UNIT': 'test-pilot'})()
        self.CLAIM = root.parent / 'absent-claim'

    @staticmethod
    def digest(raw):
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def directory(path, uid, gid, mode):
        row = path.lstat()
        assert stat.S_ISDIR(row.st_mode)
        assert row.st_uid == uid and row.st_gid == gid
        assert stat.S_IMODE(row.st_mode) == mode

    @staticmethod
    def read(path, limit):
        raw = path.read_bytes()
        assert len(raw) <= limit
        return raw

    @staticmethod
    def strict_json(raw, limit):
        assert len(raw) <= limit
        return json.loads(raw)


class Guard:
    def __init__(self, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at

    def assert_running(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError('AUTHORITY_REVOKED')


class Agreement:
    def assert_held(self, scope):
        assert scope == continuation.SCOPE


def make_old(tmp_path, monkeypatch):
    root = tmp_path / 'ledger'
    root.mkdir(mode=0o700)
    intake = root / 'intake'
    intake.mkdir(mode=0o700)
    baseline = b'{"version":1}'
    (root / 'baseline.json').write_bytes(baseline)
    values = {name: name.encode() for name in continuation.FILES}
    for name, raw in values.items():
        (intake / name).write_bytes(raw)
    for name, value in {'BASELINE': baseline, 'PLAN': values['plan.json'],
            'INTAKE': values['intake.json'], 'DISCOVERY': values['discovery.json'],
            'PUBLICATION': values['publication.json'], 'PERMIT': values['permit.json']}.items():
        monkeypatch.setattr(continuation, name, hashlib.sha256(value).hexdigest())
    return Ledger(root), baseline, values


def test_old_ledger_requires_exact_inventory_and_pinned_receipts(tmp_path, monkeypatch):
    ledger, baseline, values = make_old(tmp_path, monkeypatch)
    assert continuation._read_old(ledger) == (baseline, values, ledger.plan.ROOT.stat().st_ino)
    (ledger.plan.ROOT / 'intake' / 'permit.json').write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='^CONTINUE_OLD_DIGEST$'):
        continuation._read_old(ledger)
    (ledger.plan.ROOT / 'intake' / 'permit.json').write_bytes(values['permit.json'])
    (ledger.plan.ROOT / 'intake' / 'unexpected.json').write_bytes(b'x')
    with pytest.raises(RuntimeError, match='^CONTINUE_OLD_INVENTORY$'):
        continuation._read_old(ledger)


def test_missing_old_root_does_not_create_ledger(tmp_path):
    ledger = Ledger(tmp_path / 'ledger')
    with pytest.raises(FileNotFoundError):
        continuation._read_old(ledger)
    assert not ledger.plan.ROOT.exists()


def switch_setup(tmp_path, monkeypatch):
    from ops import light_native_service_switch as switch
    from ops import oracle_light_active_hold_attest as hold

    ledger, _, values = make_old(tmp_path, monkeypatch)
    prior = hold.HoldIdentity('test-host', 123, 'test-invocation', '/test/release', 'test-hash')
    old_baseline = json.dumps({'prior': prior.__dict__, 'protected': {},
        'protected_sha256': 'accepted'}, sort_keys=True).encode()
    (ledger.plan.ROOT / 'baseline.json').write_bytes(old_baseline)
    monkeypatch.setattr(continuation, 'BASELINE', ledger.digest(old_baseline))
    monkeypatch.setattr(hold, 'service_hold_identity', lambda: prior)
    monkeypatch.setattr(switch, 'unchanged_files', lambda protected, observed, digest:
        (protected == {} and observed == prior and digest == 'accepted') or
        (_ for _ in ()).throw(AssertionError('protected drift')))
    monkeypatch.setattr(switch, 'unit_absent', lambda unit: None)
    monkeypatch.setattr(switch, 'CONTROL', tmp_path / 'absent-control')
    stage = tmp_path / 'staged'
    stage.mkdir(mode=0o700)
    (stage / 'intake').mkdir(mode=0o700)
    baseline_raw = b'new-baseline'
    sidecar_raw = b'new-continuation'
    (stage / 'baseline.json').write_bytes(baseline_raw)
    (stage / 'continuation.json').write_bytes(sidecar_raw)
    for name in continuation.COPIED:
        (stage / 'intake' / name).write_bytes(values[name])
    archive = tmp_path / 'archive'
    old_inode = ledger.plan.ROOT.stat().st_ino
    return ledger, stage, archive, old_inode, old_baseline, values, baseline_raw, sidecar_raw


def test_switch_preserves_old_inode_and_installs_copies(tmp_path, monkeypatch):
    args = switch_setup(tmp_path, monkeypatch)
    ledger, stage, archive, old_inode, old_baseline, values, baseline_raw, sidecar_raw = args
    stage_inode = stage.stat().st_ino
    guard = Guard()
    assert continuation._switch(ledger, stage, archive, old_inode, old_baseline, values,
        baseline_raw, sidecar_raw, guard, Agreement()) == stage_inode
    assert archive.stat().st_ino == old_inode
    assert archive.joinpath('baseline.json').read_bytes() == old_baseline
    assert archive.joinpath('intake/permit.json').read_bytes() == values['permit.json']
    assert ledger.plan.ROOT.stat().st_ino == stage_inode
    assert ledger.plan.ROOT.joinpath('continuation.json').read_bytes() == sidecar_raw
    assert not ledger.plan.ROOT.joinpath('intake/permit.json').exists()
    assert guard.calls == 3


def test_revocation_between_renames_preserves_archive_and_stage(tmp_path, monkeypatch):
    args = switch_setup(tmp_path, monkeypatch)
    ledger, stage, archive, old_inode, old_baseline, values, baseline_raw, sidecar_raw = args
    guard = Guard(fail_at=2)
    with pytest.raises(RuntimeError, match='^AUTHORITY_REVOKED$'):
        continuation._switch(ledger, stage, archive, old_inode, old_baseline, values,
            baseline_raw, sidecar_raw, guard, Agreement())
    assert not ledger.plan.ROOT.exists()
    assert archive.stat().st_ino == old_inode
    assert archive.joinpath('baseline.json').read_bytes() == old_baseline
    assert archive.joinpath('intake/permit.json').read_bytes() == values['permit.json']
    assert stage.joinpath('baseline.json').read_bytes() == baseline_raw
    assert stage.joinpath('continuation.json').read_bytes() == sidecar_raw
    assert stage.joinpath('intake/intake.json').read_bytes() == values['intake.json']
    assert not stage.joinpath('intake/permit.json').exists()
