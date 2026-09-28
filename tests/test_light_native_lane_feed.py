"""Actual root file publication with fault injection and the real Permit parser."""
import hashlib
import json
import os
from types import SimpleNamespace

import pytest
from ops import light_native_lane_feed as feed
from test_oracle_autopilot_light_native_adapter import rig
from test_oracle_autopilot_light_native_pilot import permit_for


@pytest.fixture
def prepared(tmp_path,monkeypatch,rig):
    install = feed.install
    request = rig[0]
    request['assignment'].update(task_id='12345678-1234-4234-8234-123456789044',role='AUTOPILOT')
    permit,_ = permit_for(request,[110,10])
    value = permit.value
    cloud = b'{"profile":"light"}'
    value['environment_evidence_sha256'] = feed.lane.digest(cloud)
    raw = feed.lane.encoded(value)
    original = feed.lane.Permit
    monkeypatch.setattr(feed.lane,'Permit',lambda *a:original(*a,clock=lambda:110,monotonic=lambda:10))
    monkeypatch.setattr(install,'RETAINED_SOURCE',value['source'])
    monkeypatch.setattr(feed.release,'CLOUD_ENVIRONMENT_ID',value['environment_id'])
    for name in ('CONTROL','STATE','LEDGER'):
        path=tmp_path/name;path.mkdir(mode=0o700);monkeypatch.setattr(install,name,path)
    (install.CONTROL/'jobs').mkdir(mode=0o750)
    (install.CONTROL/'admission').write_bytes(b'HOLD\n');(install.CONTROL/'admission').chmod(0o640)
    (install.STATE/'pilot.lock').touch(mode=0o600)
    root=tmp_path/'releases';source=root/value['source'];source.mkdir(parents=True)
    (source/'environment.json').write_bytes(cloud);(source/'environment.json').chmod(0o600)
    monkeypatch.setattr(feed.release,'ROOT',root)
    before={'ActiveState':'active','SubState':'running','MainPID':'123','NRestarts':'0','InvocationID':'b'*32}
    record=dict(source=value['source'],invocation=before['InvocationID'],stop_rehearsal=True,
                unit_sha256=hashlib.sha256(install.render(value['source'])).hexdigest())
    (install.LEDGER/'installed.json').write_text(json.dumps(record));(install.LEDGER/'installed.json').chmod(0o600)
    user=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
    monkeypatch.setattr(feed.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    monkeypatch.setattr(feed.pwd,'getpwnam',lambda name:user)
    monkeypatch.setattr(install,'root_parent',lambda path:None)
    monkeypatch.setattr(install,'verify_running',lambda *a:before.copy())
    monkeypatch.setattr(install,'show',lambda *a:before.copy())
    monkeypatch.setattr(install.hold,'service_hold_identity',lambda:'legacy')
    monkeypatch.setattr(feed.release,'validate',lambda *a:None)
    monkeypatch.setattr(feed.release.staging,'verify_release',lambda *a:None)
    events=[]
    monkeypatch.setattr(feed.release.staging,'require_current_main',lambda source:events.append('main'))
    def observe(*a,**kw):events.append('db-read');return value['owner_preflight']
    monkeypatch.setattr(feed.preflight,'observe',observe)
    dispatch=value['dispatch']
    pr=dict(number=dispatch['target_pr'],state='open',merged=False,
        head=dict(sha=dispatch['expected_head_sha'],ref=dispatch['branch'],repo=dict(full_name='olegmed1-art/bridge-video-free')),
        base=dict(repo=dict(full_name='olegmed1-art/bridge-video-free')))
    package=feed.lane.encoded(dict(version=1,source=value['source'],runtime=dict(sha256='a'*64)))
    args=[object(),package,hashlib.sha256(package).hexdigest(),raw,feed.lane.digest(raw),'e'*40,lambda number:pr]
    plan=feed.lane.encoded(dict(version=1,source=value['source'],repository='olegmed1-art/bridge-video-free',
        target_pr=dispatch['target_pr'],expected_head_sha=dispatch['expected_head_sha'],
        branch=dispatch['branch'],work_key='lane-first-review',objective='Review the accepted target.',
        priority=10,task_spec_json=dispatch['assignment']['task_spec_json']))
    receipt=feed.lane.encoded(dict(plan_sha256=feed.lane.digest(plan),dispatch_id=dispatch['dispatch_id'],
        assignment=dispatch['assignment'],dispatch=dispatch,task_id=value['owner_preflight']['task_id'],
        work_item_id=value['owner_preflight']['work_item_id'],goal_json_sha256=value['owner_preflight']['goal_json_sha256']))
    args.extend([plan,feed.lane.digest(plan),receipt,feed.lane.digest(receipt)])
    return args,value,pr,events


@pytest.mark.skipif(os.geteuid()!=0,reason='real root metadata')
def test_publish_is_held_atomic_single_link_and_never_replayed(prepared):
    args,value,_,events=prepared
    result=feed.publish_first(*args)
    assert result['state']=='STAGED_HOLD' and result['task_started'] is False
    control=feed.install.CONTROL
    cursor=control/'current.json'
    row=feed.lane.entry(cursor.read_bytes(),value['source'])
    assert row['sequence']==0 and row['previous_terminal_sha256'] is None
    assert cursor.stat().st_nlink==1 and cursor.stat().st_mode & 0o777==0o640
    assert (control/'jobs'/row['dispatch_id']/'permit.json').read_bytes()==args[3]
    assert (control/'admission').read_bytes()==b'HOLD\n'
    assert events==['main','db-read','main','db-read']
    with pytest.raises(RuntimeError):feed.publish_first(*args)
    assert cursor.read_bytes()==feed.lane.encoded(row)


@pytest.mark.skipif(os.geteuid()!=0,reason='real root metadata')
@pytest.mark.parametrize('fault',['digest','head','db','state','hold','main','after_permit','cursor_exists'])
def test_faults_preserve_hold_and_never_publish_wrong_cursor(prepared,monkeypatch,fault):
    args,value,pr,_=prepared
    install=feed.install
    if fault=='digest':args[4]='0'*64
    elif fault=='head':pr['head']['sha']='0'*40
    elif fault=='db':monkeypatch.setattr(feed.preflight,'observe',lambda *a,**k:{})
    elif fault=='state':(install.STATE/'quarantine.json').write_text('{}')
    elif fault=='hold':(install.CONTROL/'admission').write_bytes(b'RUN\n')
    elif fault=='main':
        def fail(*a):raise RuntimeError('changed')
        monkeypatch.setattr(feed.release.staging,'require_current_main',fail)
    elif fault=='after_permit':
        old=install.write_new
        def write(path,*a):
            old(path,*a)
            if path.name=='permit.json':raise RuntimeError('lost acknowledgement')
        monkeypatch.setattr(install,'write_new',write)
    else:(install.CONTROL/'current.json').symlink_to(install.CONTROL/'admission')
    with pytest.raises((RuntimeError,OSError)):feed.publish_first(*args)
    assert (install.CONTROL/'admission').read_bytes()==(b'RUN\n' if fault=='hold' else b'HOLD\n')
    if fault=='cursor_exists':assert (install.CONTROL/'current.json').is_symlink()
    else:assert not (install.CONTROL/'current.json').exists()
    if fault=='after_permit':
        job=install.CONTROL/'jobs'/value['dispatch']['dispatch_id']
        assert (job/'permit.json').read_bytes()==args[3]
        assert (install.LEDGER/'first-feed-intent.json').exists()
        with pytest.raises(RuntimeError):feed.publish_first(*args)


@pytest.mark.skipif(os.geteuid()!=0,reason='real root metadata')
@pytest.mark.parametrize('fault',['gid','nlink'])
def test_final_readback_requires_consumer_metadata(prepared,monkeypatch,fault):
    install=feed.install
    fsync=feed.release.staging.fsync_directory
    def corrupt(path):
        fsync(path)
        if path==install.CONTROL:
            cursor=path/'current.json'
            if fault=='gid':
                # Some test containers map only gid 0; inject the observed gid.
                real_stat=type(cursor).lstat
                def changed(item):
                    row=real_stat(item)
                    if item==cursor:
                        return SimpleNamespace(st_mode=row.st_mode,st_uid=row.st_uid,
                            st_gid=row.st_gid+1,st_nlink=row.st_nlink)
                    return row
                monkeypatch.setattr(type(cursor),'lstat',changed)
            else:os.link(cursor,path/'unexpected-link')
    monkeypatch.setattr(feed.release.staging,'fsync_directory',corrupt)
    with pytest.raises(RuntimeError,match='READBACK'):feed.publish_first(*prepared[0])
    assert (install.CONTROL/'admission').read_bytes()==b'HOLD\n'
    assert (install.LEDGER/'first-feed-intent.json').exists()


@pytest.mark.skipif(os.geteuid()!=0,reason='real root metadata')
@pytest.mark.parametrize('field',['plan_sha256','dispatch_id','task_id','work_item_id','goal_json_sha256'])
def test_unrelated_accepted_intake_is_not_a_permit(prepared,field):
    args=prepared[0]
    receipt=feed.lane.parse(args[9])
    receipt[field]='0'*64
    args[9]=feed.lane.encoded(receipt)
    args[10]=feed.lane.digest(args[9])
    with pytest.raises(RuntimeError,match='PLAN_BINDING'):feed.publish_first(*args)
    assert not (feed.install.CONTROL/'current.json').exists()
    assert not (feed.install.LEDGER/'first-feed-intent.json').exists()
