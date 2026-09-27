"""Controller contracts use disposable files and explicit fake host effects."""
import base64
from dataclasses import asdict
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ops import light_native_service_controller as target
from ops import oracle_light_active_hold_attest as hold


SOURCE = 'a' * 40


def accepted_request():
    helpers = {name:('' if name in target.release.MARKERS else
                     (Path(__file__).resolve().parents[1] / name).read_text())
               for name in target.release.HELPERS}
    runtime = {'revision':SOURCE,
               'files':{**dict.fromkeys(target.release.EXTRA, ''),
                        **{name:helpers[name] for name in target.PACKAGE_HELPERS},
                        'oracle_autopilot/light_native_loader.py':'pass\n'}}
    runtime['sha256'] = target.digest(target.canonical(runtime))
    package = target.canonical({'version':1,'source':SOURCE,'runtime':runtime,
                                'helpers':helpers})
    permit = target.canonical({'fixture':'private','issued_at':int(time.time())})
    value = {'version':1,'source':SOURCE,'package_sha256':target.digest(package),
             'permit_b64':base64.b64encode(permit).decode(),
             'permit_sha256':target.digest(permit),'agreement':{},
             'accepted_agreement_sha256':'b'*64,'scope':{},
             'duration_seconds':300,'baseline_sha256':'c'*64,
             'dispatch_id':'11111111-1111-1111-1111-111111111111'}
    raw = target.canonical(value)
    return raw, target.digest(raw), package


def prior():
    return hold.HoldIdentity('autopilot-lite-vnic',123,'c'*32,
        str(target.plan.LIGHT/'releases'/('d'*40)),'e'*64)


def test_request_binds_exact_canonical_bytes_and_helper_source():
    raw, accepted, package = accepted_request()
    assert target.Request(raw, accepted, package).value['source'] == SOURCE
    with pytest.raises(RuntimeError, match='NOT_ACCEPTED'):
        target.Request(raw, 'f'*64, package)
    with pytest.raises(RuntimeError, match='NONCANONICAL'):
        target.Request(raw + b' ', target.digest(raw + b' '), package)
    edited = json.loads(package)
    edited['helpers']['ops/light_native_service_switch.py'] += '# changed\n'
    altered_package = target.canonical(edited)
    edited_request = json.loads(raw)
    edited_request['package_sha256'] = target.digest(altered_package)
    altered_raw = target.canonical(edited_request)
    with pytest.raises(RuntimeError, match='HELPER_CHANGED'):
        target.Request(altered_raw, target.digest(altered_raw), altered_package)


def test_request_refuses_ambiguous_json_and_noncanonical_permit():
    raw, accepted, package = accepted_request()
    with pytest.raises(RuntimeError, match='DUPLICATE_KEY'):
        target.strict_json(b'{"version":1,"version":1}', 100)
    value = json.loads(raw)
    value['permit_b64'] += '='
    edited = target.canonical(value)
    with pytest.raises(Exception):
        target.Request(edited, target.digest(edited), package)
    with pytest.raises(RuntimeError, match='NONFINITE'):
        target.strict_json(b'{"value":NaN}', 100)


@pytest.mark.skipif(target.os.geteuid() != 0, reason='root metadata test')
def test_environment_receipt_binds_exact_readonly_command(tmp_path,monkeypatch):
    monkeypatch.setattr(target.release,'ROOT',tmp_path)
    directory=tmp_path/SOURCE;directory.mkdir()
    receipt={'version':1,'source':SOURCE,
             'environment_id':target.release.CLOUD_ENVIRONMENT_ID,
             'service_user':'school-autopilot',
             'command':'cloud list --env '+target.release.CLOUD_ENVIRONMENT_ID+
                       ' --limit 1 --json','output_sha256':'a'*64}
    leaf=directory/'environment.json'
    leaf.write_bytes(target.canonical(receipt));leaf.chmod(0o600)
    request=SimpleNamespace(value={'source':SOURCE},permit_value={
        'environment_id':target.release.CLOUD_ENVIRONMENT_ID,
        'environment_evidence_sha256':target.digest(leaf.read_bytes())})
    assert target.environment_record(request)==receipt
    receipt['command']='cloud list'
    leaf.write_bytes(target.canonical(receipt))
    request.permit_value['environment_evidence_sha256']=target.digest(leaf.read_bytes())
    with pytest.raises(RuntimeError,match='ENVIRONMENT_NOT_ACCEPTED'):
        target.environment_record(request)


@pytest.mark.skipif(target.os.geteuid() != 0, reason='root metadata test')
def test_full_baseline_precedes_request_and_is_bound_to_same_agreement(tmp_path,monkeypatch):
    root = tmp_path / 'ledger'
    identity = prior()
    protected = {'fixed':'hash'}
    calls = []
    class Window:
        def __init__(self, record, accepted, scope):
            calls.append('agreement')
        def assert_held(self, scope_digest):
            assert scope_digest == target.digest(target.canonical({'operation':'pilot'}))
    monkeypatch.setattr(target.plan,'ROOT',root)
    monkeypatch.setattr(target.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(target,'verified_package',lambda *a:{'runtime':{}})
    monkeypatch.setattr(target,'Agreement',Window)
    monkeypatch.setattr(target.release.staging,'require_current_main',lambda *_:None)
    monkeypatch.setattr(target,'stage_observation',lambda *a:identity)
    monkeypatch.setattr(target.hold,'attest',lambda:calls.append('full HOLD') or identity)
    monkeypatch.setattr(target.switch,'attest_hardening',lambda:calls.append('hardening'))
    monkeypatch.setattr(target.switch,'unit_absent',lambda *_:None)
    monkeypatch.setattr(target.switch,'protect_snapshot',lambda *_:protected)
    result=target.prepare_baseline(SOURCE,'b'*64,b'package',{},'c'*64,{'operation':'pilot'})
    assert result['baseline_sha256'] == target.digest((root/'baseline.json').read_bytes())
    assert calls.count('full HOLD') == 2
    assert calls.index('full HOLD') < calls.index('hardening')


@pytest.mark.skipif(target.os.geteuid() != 0, reason='root metadata test')
def test_request_bind_uses_service_only_identity_after_task_creation(tmp_path,monkeypatch):
    identity=prior()
    root=tmp_path/'ledger'; root.mkdir(mode=0o700)
    control=tmp_path/'control'
    runtime=tmp_path/'runtime'; runtime.mkdir(mode=0o700)
    claim=runtime/'native-single-pilot'
    protected={'fixed':'hash'}
    digest=target.digest(target.canonical(protected))
    baseline={'version':1,'source':SOURCE,'package_sha256':'b'*64,
              'agreement_sha256':'c'*64,'scope_sha256':target.digest(target.canonical({})),
              'prior':asdict(identity),'protected':protected,'protected_sha256':digest,
              'observed_at':100}
    (root/'baseline.json').write_bytes(target.canonical(baseline))
    (root/'baseline.json').chmod(0o600)
    fake=SimpleNamespace(value={'source':SOURCE,'package_sha256':'b'*64,
        'baseline_sha256':target.digest(target.canonical(baseline)),
        'accepted_agreement_sha256':'c'*64,'scope':{},
        'permit_sha256':'d'*64},
        permit_issued_at=101,package={'runtime':{}},raw=b'request',
        permit=b'permit',agreement=lambda:None)
    monkeypatch.setattr(target.plan,'ROOT',root)
    monkeypatch.setattr(target.switch,'CONTROL',control)
    monkeypatch.setattr(target,'CLAIM',claim)
    monkeypatch.setattr(target,'Request',lambda *a:fake)
    monkeypatch.setattr(target.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(target.pwd,'getpwnam',lambda *_:SimpleNamespace(pw_uid=0,pw_gid=0))
    monkeypatch.setattr(target.release.staging,'require_current_main',lambda *_:None)
    monkeypatch.setattr(target.hold,'attest',Mock(side_effect=AssertionError('queue0 no longer true')))
    monkeypatch.setattr(target.hold,'service_hold_identity',
                        lambda:hold.ServiceHoldIdentity(**asdict(identity)))
    monkeypatch.setattr(target.switch,'unchanged_files',lambda *a:None)
    monkeypatch.setattr(target,'stage_observation',lambda *a:identity)
    monkeypatch.setattr(target,'environment_record',lambda *_:None)
    monkeypatch.setattr(target.switch,'attest_hardening',lambda:None)
    monkeypatch.setattr(target.switch,'unit_absent',lambda *_:None)
    monkeypatch.setattr(target,'permit_probe',lambda *_:None)
    result=target.prepare(b'request','a'*64,b'package')
    assert result['audit']=='LIGHT_NATIVE_PILOT_PREPARED_HOLD'
    assert (control/'admission').read_bytes()==b'HOLD\n'
    assert claim.is_dir()
    target.hold.attest.assert_not_called()


@pytest.mark.skipif(target.os.geteuid() != 0, reason='root metadata test')
def test_private_ledger_files_are_create_only_and_read_back(tmp_path):
    directory = tmp_path / 'ledger'
    target.new_directory(directory, 0, 0, 0o700)
    leaf = directory / 'request.json'
    target.retained(leaf, b'private')
    assert target.read(leaf) == b'private'
    with pytest.raises(FileExistsError):
        target.retained(leaf, b'other')
    assert leaf.read_bytes() == b'private'


def test_supervisor_bootstrap_is_fixed_and_suppresses_exceptions():
    data = target.supervisor_source(SOURCE)
    compile(data, 'supervisor.py', 'exec')
    assert b'from ops.light_native_service_controller import main' in data
    assert b'raise SystemExit(2) from None' in data
    with pytest.raises(RuntimeError):
        target.supervisor_source('../escape')


def test_launch_persists_intent_before_start_and_never_retries(tmp_path, monkeypatch):
    request = SimpleNamespace(value={'source':SOURCE,'duration_seconds':300},
                              agreement=Mock())
    prior_value = prior()
    scope = tmp_path / 'scope'
    scope.mkdir()
    events = []
    monkeypatch.setattr(target, 'ledger', lambda *_:(request,prior_value,{},'f'*64,scope))
    monkeypatch.setattr(target.os, 'uname', lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(target.switch, 'unchanged_files', lambda *a:events.append('protected'))
    monkeypatch.setattr(target.hold, 'service_hold_identity',
                        lambda:hold.ServiceHoldIdentity(**asdict(prior_value)))
    monkeypatch.setattr(target.switch, 'unit_absent', lambda unit:events.append('absent'))
    monkeypatch.setattr(target.release.staging, 'require_current_main', lambda *_:events.append('main'))
    monkeypatch.setattr(target, 'environment_record', lambda *_:events.append('environment'))
    monkeypatch.setattr(target, 'permit_probe', lambda *_:events.append('permit'))
    monkeypatch.setattr(target, 'retained', lambda path, raw:events.append('intent'))
    monkeypatch.setattr(target.plan, 'supervisor_command', lambda *a:['systemd-run'])
    monkeypatch.setattr(target.switch, 'command', lambda *a:events.append('systemd'))
    result = target.launch('a'*64)
    assert result['terminal_verified'] is False
    assert events.index('intent') < events.index('systemd')
    assert events.count('systemd') == 1


def test_restore_denies_before_ledger_and_never_checks_expired_agreement(monkeypatch):
    events = []
    monkeypatch.setattr(target.os, 'uname', lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(target.switch, 'deny_admission', lambda:events.append('deny'))
    def bad_ledger(_):
        events.append('ledger')
        raise RuntimeError('corrupt')
    monkeypatch.setattr(target, 'ledger', bad_ledger)
    with pytest.raises(RuntimeError, match='corrupt'):
        target.restore('a'*64)
    assert events == ['deny','ledger']


def test_repeated_restore_with_receipt_never_reopens_backend(monkeypatch,tmp_path):
    raw, accepted, package = accepted_request()
    request = target.Request(raw, accepted, package)
    identity = prior()
    scope = tmp_path
    monkeypatch.setattr(target.os, 'uname', lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(target, 'ledger', lambda *_:(request,identity,{},'f'*64,scope))
    monkeypatch.setattr(target.switch, 'deny_admission', lambda:None)
    monkeypatch.setattr(target, 'restored_receipt', lambda *a:{'version':1})
    monkeypatch.setattr(target.switch, 'restore', Mock(side_effect=AssertionError('second restore')))
    result = target.restore(accepted)
    assert result['outcome'] == 'RECONCILE'
    target.switch.restore.assert_not_called()


def test_run_writes_one_shot_latch_before_any_host_effect(monkeypatch,tmp_path):
    request = SimpleNamespace(value={'source':SOURCE,'duration_seconds':300,
                              'scope':{},'dispatch_id':'dispatch'},agreement=lambda:object())
    prior_value = prior()
    monkeypatch.setattr(target.os, 'uname', lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(target, 'ledger', lambda *_:(request,prior_value,{},'f'*64,tmp_path))
    expected = target.canonical({'request_sha256':'a'*64,'source':SOURCE,
                                  'unit':target.plan.SUPERVISOR_UNIT})
    monkeypatch.setattr(target, 'read', lambda *a:expected)
    events = []
    monkeypatch.setattr(target, 'retained', lambda *a:events.append('latch'))
    monkeypatch.setattr(target.switch, 'run_once', lambda **k:events.append('host') or {'state':'QUARANTINED'})
    result = target.run('a'*64)
    assert events == ['latch','host'] and result['terminal_verified'] is False
