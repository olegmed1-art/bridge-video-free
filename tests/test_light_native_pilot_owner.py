"""Owner effects use accepted evidence; public CI output excludes private bytes."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from database.light_native_pilot_intake import dispatch_body
from ops import light_native_pilot_owner as owner


def test_public_projection_excludes_permit_request_and_credentials():
    raw=owner.release.encoded(dict(dispatch_id='one',task_id='two',success=False,
        permit_b64='PRIVATE',request={'token':'SECRET'},assignment={'private':'text'},
        credential='password',agreement={'evidence':'owner evidence'}))
    result=owner.record_summary(raw)
    assert result==dict(record_sha256=owner.control.digest(raw),dispatch_id='one',
                       task_id='two',success=False)
    assert all(s not in json.dumps(result) for s in ('PRIVATE','SECRET','password','owner evidence'))


def target_pr():
    return dict(number=123,state='open',merged=False,head=dict(sha='b'*40,
        ref='fix/pilot',repo=dict(full_name=owner.REPOSITORY)),
        base=dict(repo=dict(full_name=owner.REPOSITORY)))


@pytest.mark.parametrize('change',[{'state':'closed'},{'merged':True},{'number':124},
    {'head':dict(sha='c'*40,ref='fix/pilot',repo=dict(full_name=owner.REPOSITORY))}])
def test_target_drift_refuses(change):
    pr=target_pr();pr.update(change)
    plan=SimpleNamespace(value=dict(target_pr=123,expected_head_sha='b'*40,branch='fix/pilot'))
    api=SimpleNamespace(get=lambda _:pr)
    with pytest.raises(RuntimeError,match='TARGET_CHANGED'):owner.observed_target(api,plan)


def discovery_fixture():
    dispatch=dict(dispatch_id='33333333-3333-4333-8333-333333333333',role='AUTOPILOT',
        target_pr=123,expected_head_sha='b'*40,mode='READ_ONLY',task_fingerprint='c'*64,
        dispatch_epoch=1,claim_epoch=1)
    receipt=dict(dispatch_id=dispatch['dispatch_id'],dispatch=dispatch)
    body=dispatch_body(dispatch)
    pr=target_pr();pr.update(number=456,draft=True,body=body,
        title='[Autopilot dispatch] AUTOPILOT '+dispatch['dispatch_id'],
        html_url='https://github.com/'+owner.REPOSITORY+'/pull/456',
        user=dict(login='bridge-school-oracle-autopilot[bot]',id=322994314,type='Bot'))
    pr['head'].update(sha='d'*40,ref='autopilot/dispatch/'+dispatch['dispatch_id'])
    file=dict(type='file',path='docs/evidence/autopilot/role-dispatch-'+dispatch['dispatch_id']+'.md',
              encoding='base64',content=base64.b64encode(body.encode()).decode())
    published=dict(dispatch_pull_request=456,dispatch_commit_sha='d'*40)
    return receipt,pr,file,published


@pytest.mark.parametrize('drift',['body','file','head','draft','repository'])
def test_publication_requires_fresh_matching_pr_and_file(drift):
    receipt,pr,file,published=discovery_fixture()
    if drift=='body':pr['body']+='modified'
    if drift=='file':file['content']=base64.b64encode(b'forged').decode()
    if drift=='head':pr['head']['sha']='e'*40
    if drift=='draft':pr['draft']=False
    if drift=='repository':pr['head']['repo']['full_name']='other/repo'
    api=SimpleNamespace(get=lambda path:pr if path.startswith('/pulls/') else file)
    with pytest.raises(RuntimeError,match='PILOT_OWNER_DISCOVERY'):
        owner.discovery(api,published,receipt)


def test_matching_publication_is_observed_from_two_primary_sources():
    receipt,pr,file,published=discovery_fixture()
    api=SimpleNamespace(get=Mock(side_effect=[pr,file]))
    result=owner.discovery(api,published,receipt)
    assert result['number']==456 and result['dispatch_id']==receipt['dispatch_id']
    assert api.get.call_count==2
    assert api.get.call_args_list[1].args[0].endswith('?ref='+'d'*40)


def test_payload_hash_refuses_before_driver_or_run_check(monkeypatch):
    monkeypatch.setattr(owner.os,'geteuid',lambda:0)
    monkeypatch.setattr(owner.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    driver=Mock();monkeypatch.setattr(owner,'loaded_runtime',driver)
    run=SimpleNamespace(assert_running=Mock())
    with pytest.raises(RuntimeError,match='PAYLOAD_NOT_ACCEPTED'):
        owner.step(b'','secret','token',b'{}',b'{}','0'*64,run)
    driver.assert_not_called();run.assert_running.assert_not_called()


def test_unaccepted_record_cannot_be_used(monkeypatch):
    monkeypatch.setattr(owner.control,'read',lambda _:b'{"changed":true}')
    with pytest.raises(RuntimeError,match='RECORD_NOT_ACCEPTED'):
        owner.accepted_record('permit.json','a'*64)
