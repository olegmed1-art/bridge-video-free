from dataclasses import asdict
from pathlib import Path
import hashlib
import os
import pytest
from ops.incident import light_fixed_stage_20260928 as target
from ops.incident import light_fixed_stage_runner_20260928 as runner
from ops import light_native_pilot_release as release

REPO=Path(__file__).resolve().parents[1]


def test_reviewed_dual_package_closure():
    old=release.package(REPO,runner.SOURCE)
    new=release.package(REPO,target.SOURCE)
    result=target.validate_packages(old,new)
    assert result['source']==target.SOURCE
    with pytest.raises(RuntimeError,match='FIXED_STAGE_PACKAGE'):
        target.validate_packages(old,new+b' ')


@pytest.mark.skipif(os.geteuid()!=0,reason='root-owned retention')
@pytest.mark.parametrize('fail_at',[None,1,2,3,'probe'])
def test_staging_guards_and_receipt(tmp_path,monkeypatch,fail_at):
    root=tmp_path/'receipts';root.mkdir(mode=0o700)
    monkeypatch.setattr(release,'ROOT',root)
    for name in ('BASE','DROP'):
        path=tmp_path/name;path.write_bytes(name.encode());path.chmod(0o644)
        monkeypatch.setattr(target.hold,name,path)
    identity=target.hold.ServiceHoldIdentity('autopilot-lite-vnic',123,'a'*32,
        str(target.control.plan.LIGHT/'releases'/('b'*40)),'c'*64)
    calls=[]
    def guard():
        calls.append('guard')
        if fail_at==calls.count('guard'):raise RuntimeError('guard expired')
        return identity
    monkeypatch.setattr(release.staging,'stage',lambda value:calls.append('stage') or tmp_path/'candidate')
    def probe(path):
        calls.append('probe')
        if fail_at=='probe':raise RuntimeError('probe failed')
    monkeypatch.setattr(release,'probe',probe)
    monkeypatch.setattr(release,'probe_environment',lambda path:{'verified':True})
    monkeypatch.setattr(release.staging,'verify_release',lambda *a:calls.append('verify'))
    package={'runtime':{'sha256':'d'*64}}
    if fail_at is not None:
        with pytest.raises(RuntimeError):target.stage_files(package,guard,{'queue_zero_reobserved':False})
        assert not (root/target.SOURCE/'staged.json').exists()
        if fail_at==1:assert list(root.iterdir())==[]
        if fail_at==2:assert 'stage' not in calls
    else:
        result=target.stage_files(package,guard,{'queue_zero_reobserved':False})
        assert calls==['guard','guard','stage','probe','verify','guard']
        assert result['queue_zero_reobserved'] is False
        assert result['service_restarted'] is False
        assert (root/target.SOURCE/'before.json').exists()
        assert (root/target.SOURCE/'owned-task-stage.json').exists()
        assert (root/target.SOURCE/'staged.json').exists()


def test_runner_bootstrap_format_and_isolation(monkeypatch):
    import base64
    payload=b'{}'
    values=dict(EXPECTED_MAIN=target.SOURCE,GITHUB_SHA=target.SOURCE,
        GITHUB_WORKFLOW_SHA=target.SOURCE,GITHUB_WORKFLOW_REF=runner.REPO+'/'+runner.WORKFLOW+'@refs/heads/'+runner.BRANCH,
        GITHUB_JOB='step',GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',GITHUB_REF='refs/heads/'+runner.BRANCH,
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',
        GITHUB_REPOSITORY=runner.REPO,PILOT_ACCEPTED_PACKAGE=runner.PACKAGE,
        PILOT_ACCEPTED_PAYLOAD=hashlib.sha256(payload).hexdigest(),NATIVE_OWNER_DATABASE_URL='test-placeholder',
        GH_TOKEN='test-placeholder',PILOT_PAYLOAD_BASE64=base64.b64encode(payload).decode())
    for key,value in values.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(runner,'connection_parameters',lambda *a:None)
    monkeypatch.setattr(runner.driver,'build',lambda path:b'test-wheels')
    original=runner.subprocess.check_output
    def check_output(args,**kw):
        if args[:2]==['git','show']:return (REPO/args[2].split(':',1)[1]).read_bytes()
        return original(args,**kw)
    monkeypatch.setattr(runner.subprocess,'check_output',check_output)
    program=runner.program('/unused')
    compile(program,'<fixed-stage-bootstrap>','exec')
    assert "main['object']['sha']=='"+target.SOURCE+"'" in program
    assert 'time.monotonic()+220' in program
