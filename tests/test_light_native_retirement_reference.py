"""Public references reconstruct independently accepted private bytes at root."""
import base64
from copy import deepcopy
from datetime import datetime,timezone
import pytest
from ops import light_native_retirement as r
from ops import light_native_retirement_live as live
from ops.native_maintenance_agreement import COVERAGE
from test_light_native_retirement import retained,request_bytes


@pytest.fixture
def reference(retained):
    t=retained
    scope=dict(version=1,operation='CREATE_FAILED_PREPARE_RETIREMENT_PROPOSAL',source='a'*40,
        controller_sha256=r.sha(t.request.controller),runtime_sha256=r.sha(t.request.runtime),
        record_sha256=r.sha(t.raw),historical_journal_sha256=t.pins)
    stamp=lambda n:datetime.fromtimestamp(n,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    agreement=dict(version=1,owner='olegmed1-art',coverage=COVERAGE,evidence=live.PUBLIC_EVIDENCE,
        operation_digest=r.sha(r.encoded(scope)),not_before=stamp(t.request.now-1),
        expires_at=stamp(t.request.now+900))
    request=dict(version=1,action='retire-prepare',source='a'*40,
        accepted_controller_sha256=scope['controller_sha256'],accepted_runtime_sha256=scope['runtime_sha256'],
        record_base64=base64.b64encode(t.raw).decode(),record_sha256=r.sha(t.raw),
        historical_journal_sha256=t.pins,agreement=agreement,accepted_agreement_sha256=r.sha(r.encoded(agreement)))
    private=r.encoded(request)
    public={k:request[k] for k in ('version','source','accepted_controller_sha256',
        'accepted_runtime_sha256','record_sha256','agreement','accepted_agreement_sha256')}
    public.update(action='retire-prepare-reference',policy_sha256=t.ref['policy_sha256'],index=0,
        journal_map_sha256=r.sha(r.encoded(t.pins)),private_request_sha256=r.sha(private))
    t.public=public;t.private=private
    return t


def resolve(t,value=None):
    raw=r.encoded(t.public if value is None else value)
    return live.resolve_reference(raw,r.sha(raw),t.request.controller,t.request.runtime,t.request.guard)


def test_exact_private_reconstruction_is_readonly(reference,capsys):
    t=reference;before={p.relative_to(t.root).as_posix():p.read_bytes() for p in t.root.rglob('*') if p.is_file()}
    assert resolve(t)==(t.private,r.sha(t.private))
    after={p.relative_to(t.root).as_posix():p.read_bytes() for p in t.root.rglob('*') if p.is_file()}
    assert after==before and not (t.root/t.entry/r.NAME).exists()
    assert capsys.readouterr()==('','')
    public=r.encoded(t.public)
    assert t.record['failed_work_key'].encode() not in public
    assert base64.b64encode(t.raw) not in public and b'historical_journal_sha256' not in public


@pytest.mark.parametrize('field',['record_sha256','journal_map_sha256','private_request_sha256',
    'accepted_controller_sha256','accepted_runtime_sha256','accepted_agreement_sha256','policy_sha256'])
def test_independent_pin_mismatch_never_creates(reference,field):
    value=deepcopy(reference.public);value[field]='0'*64
    with pytest.raises((RuntimeError,OSError)):resolve(reference,value)
    assert not (reference.root/reference.entry/r.NAME).exists()


@pytest.mark.parametrize('name',sorted(r.JOURNAL_KEYS))
def test_changed_private_journal_rejected(reference,name):
    (reference.root/reference.paths[name]).write_bytes(b'PRIVATE_SYNTHETIC_CHANGED_JOURNAL')
    with pytest.raises((RuntimeError,ValueError)):resolve(reference)
    assert not (reference.root/reference.entry/r.NAME).exists()


@pytest.mark.parametrize('fault',['expired','future','extended','operation','self-minted'])
def test_no_fresh_or_replacement_authority_minted(reference,fault):
    value=deepcopy(reference.public);a=value['agreement']
    if fault=='expired':a.update(not_before='2000-01-01T00:00:00Z',expires_at='2000-01-01T00:10:00Z')
    if fault=='future':a.update(not_before='2099-01-01T00:00:00Z',expires_at='2099-01-01T00:10:00Z')
    if fault=='extended':a['expires_at']='2099-01-01T00:00:00Z'
    if fault=='operation':a['operation_digest']='0'*64
    if fault=='self-minted':a['evidence']='PRIVATE_SYNTHETIC_UNAPPROVED_AUTHORITY'
    # Even a newly computed public hash cannot replace accepted private bytes.
    value['accepted_agreement_sha256']=r.sha(r.encoded(a))
    if fault!='self-minted':
        # Even independently accepted bytes must obey the original finite scope.
        private=r.parse(reference.private)
        private.update(agreement=a,accepted_agreement_sha256=value['accepted_agreement_sha256'])
        value['private_request_sha256']=r.sha(r.encoded(private))
    with pytest.raises(RuntimeError):resolve(reference,value)
    assert not (reference.root/reference.entry/r.NAME).exists()


@pytest.mark.parametrize('fault',['index','bool','action','extra','evidence','duplicate','noncanonical'])
def test_public_schema_rejects_private_or_ambiguous_inputs(reference,fault):
    value=deepcopy(reference.public)
    if fault=='index':value['index']=8
    if fault=='bool':value['index']=False
    if fault=='action':value['action']='retire-prepare'
    if fault=='extra':value['record_base64']=base64.b64encode(reference.raw).decode()
    if fault=='evidence':value['agreement']['evidence']='PRIVATE_SYNTHETIC_TEXT'
    raw=r.encoded(value)
    if fault=='duplicate':raw=raw[:-1]+b',"version":1}'
    if fault=='noncanonical':raw+=b'\n'
    with pytest.raises(RuntimeError):live.public_reference(raw)
