"""Durable ACK/reentry proof, including an actual fresh Python image via exec."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from oracle_autopilot import light_native_restart as restart
from oracle_autopilot.light_native_pilot import Claim
from test_oracle_autopilot_light_native_adapter import rig
from test_oracle_autopilot_light_native_pilot import make_session


def begin(rig, tmp_path, monkeypatch):
    monkeypatch.setenv('INVOCATION_ID', 'e'*32)
    session, claim, conn, reader, clock = make_session(rig, tmp_path)
    provider = restart.RestartingProvider(session.provider, session.permit, claim)
    session.provider = provider
    session.reserve()
    provider.attach(session)
    return session, claim, provider, clock


def reenter(rig, tmp_path, monkeypatch, *, clock=None):
    monkeypatch.setattr(restart, 'IMAGE', 'f'*64)
    session, claim, conn, reader, clock = make_session(rig, tmp_path, clock)
    try:
        provider = restart.RestartingProvider(session.provider, session.permit, claim)
        session.provider = provider
        session.reserve()
        provider.attach(session)
    except BaseException:
        claim.close()
        raise
    return session, claim, provider


def test_ack_then_fresh_image_uses_same_task_and_original_deadline(rig, tmp_path, monkeypatch):
    session, claim, provider, clock = begin(rig, tmp_path, monkeypatch)
    with pytest.raises(restart.RestartImage): session.step()
    assert rig[1]['row']['state'] == 'SUBMITTED'
    assert rig[1]['creates'] == 1 and rig[1]['finishes'] == 0
    assert not any(name == 'collect' for name, _ in rig[1]['calls'])
    deadline = session.permit.deadline
    claim.close()
    session, claim, provider = reenter(rig, tmp_path, monkeypatch, clock=[111, 20])
    try:
        assert session.permit.deadline == deadline
        assert session.step()['state'] == 'DONE'
        evidence = restart.proof(claim, session.permit.raw, session.request, 'task_e_adapter')
        assert evidence['kind'] == 'CONTROLLED_IMAGE_RESTART'
        assert rig[1]['creates'] == rig[1]['finishes'] == 1
    finally:
        claim.close()
    with pytest.raises(RuntimeError, match='ALREADY_RESUMED'):
        reenter(rig, tmp_path, monkeypatch)


@pytest.mark.parametrize('fault', ['missing_start', 'missing_intent', 'changed_pid', 'changed_unit',
    'changed_request', 'changed_provider', 'missing_provider', 'unknown_provider', 'missing_ack',
    'expired_monotonic', 'rollback_wall', 'changed_permit', 'unsafe_file', 'same_image'])
def test_reentry_conflicts_never_collect_or_resubmit(rig, tmp_path, monkeypatch, fault):
    session, claim, provider, clock = begin(rig, tmp_path, monkeypatch)
    initial_image = restart.IMAGE
    with pytest.raises(restart.RestartImage): session.step()
    claim.close()
    start = tmp_path/restart.NAMES[0]
    intent = tmp_path/restart.NAMES[1]
    if fault == 'missing_start': start.unlink()
    if fault == 'missing_intent': intent.unlink()
    if fault in ('changed_pid', 'changed_unit', 'changed_permit'):
        value = json.loads(start.read_text())
        key, v = {'changed_pid':('pid',1), 'changed_unit':('invocation_id','a'*32),
                  'changed_permit':('permit_sha256','a'*64)}[fault]
        value[key] = v
        start.write_text(restart.bridge.canonical(value))
    if fault == 'changed_request':
        value=json.loads(intent.read_text());value['request_sha256']='a'*64
        intent.write_text(restart.bridge.canonical(value))
    if fault == 'changed_provider': rig[1]['creation']['provider_task_id'] = 'task_e_other'
    if fault == 'missing_provider': rig[1]['creation'] = None
    if fault == 'unknown_provider': rig[1]['creation']['state'] = 'SUBMISSION_UNKNOWN'
    if fault == 'missing_ack': rig[1]['row']['state'] = 'RESERVED'
    if fault == 'unsafe_file': intent.chmod(0o644)
    clock = [110, 101] if fault == 'expired_monotonic' else [109, 11] if fault == 'rollback_wall' else [111, 11]
    if fault == 'same_image':
        # reenter() picks f*64; persist exactly that image as the initial one.
        monkeypatch.setattr(restart, 'IMAGE', initial_image)
        session, claim, *_ = make_session(rig, tmp_path, clock)
        try:
            with pytest.raises(RuntimeError, match='IMAGE_IDENTITY'):
                restart.RestartingProvider(session.provider, session.permit, claim)
        finally: claim.close()
    else:
        with pytest.raises((RuntimeError, OSError)):
            reenter(rig, tmp_path, monkeypatch, clock=clock)
    assert rig[1]['creates'] == 1 and rig[1]['finishes'] == 0
    assert not any(name == 'collect' for name, _ in rig[1]['calls'])


@pytest.mark.parametrize('name', restart.NAMES)
def test_lost_recovery_record_after_resume_is_not_recreated(rig, tmp_path, monkeypatch, name):
    session, claim, *_ = begin(rig, tmp_path, monkeypatch)
    with pytest.raises(restart.RestartImage): session.step()
    claim.close()
    session, claim, provider = reenter(rig, tmp_path, monkeypatch)
    try:
        (tmp_path/name).unlink()
        with pytest.raises(RuntimeError): session.step()
        assert not (tmp_path/name).exists()
        assert rig[1]['finishes'] == 0 and rig[1]['creates'] == 1
    finally: claim.close()


def test_actual_exec_rebuilds_session_with_same_pid_and_one_creation(tmp_path):
    """No live DB/Cloud: real exec loses all Python objects; durable fake ports persist."""
    script = tmp_path/'child.py'
    root = Path(__file__).resolve().parents[1]
    script.write_text('''import json,os,sys
from pathlib import Path
sys.path[:0]=[sys.argv[2],str(Path(sys.argv[2])/'tests')]
from test_oracle_autopilot_light_native_adapter import rig
from test_oracle_autopilot_light_native_pilot import make_session
from oracle_autopilot import light_native_restart as restart
root=Path(sys.argv[1]);saved=root/'ports.json';claims=root/'claims'
claims.mkdir(mode=0o700,exist_ok=True)
os.environ['INVOCATION_ID']='e'*32
ports=rig.__wrapped__()
if saved.exists():ports[1].update(json.loads(saved.read_text()))
session,claim,*_=make_session(ports,claims)
provider=restart.RestartingProvider(session.provider,session.permit,claim)
session.provider=provider
session.reserve();provider.attach(session)
try:
    result=session.step()
except restart.RestartImage:
    saved.write_text(json.dumps(ports[1]));claim.close()
    os.execv(sys.executable,[sys.executable,__file__,str(root),sys.argv[2]])
    raise AssertionError('exec returned')
else:
    evidence=restart.proof(claim,session.permit.raw,session.request,result['provider_task_id'])
    assert result['state']=='DONE' and ports[1]['creates']==ports[1]['finishes']==1
    assert evidence['pid']==os.getpid()
    assert restart.read(claim,restart.NAMES[0])['image_id']!=restart.IMAGE
    print(json.dumps(evidence))
finally:claim.close()
''')
    result = subprocess.run([sys.executable, str(script), str(tmp_path), str(root)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence['provider_task_id'] == 'task_e_adapter'
    assert evidence['invocation_id'] == 'e'*32


def test_readonly_terminal_proof_never_recreates_missing_lock(tmp_path):
    tmp_path.chmod(0o700)
    with pytest.raises(FileNotFoundError): Claim(tmp_path, create_lock=False)
    assert not (tmp_path/'pilot.lock').exists()
    with Claim(tmp_path): pass
    with Claim(tmp_path, create_lock=False) as claim: claim.check()
