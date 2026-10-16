"""Predicate preservation and distinct local-only authority regression tests."""
import ast
from copy import deepcopy
import hashlib
from pathlib import Path
import pytest
from ops import light_native_retirement as r
from ops import light_native_retirement_live as live
from test_light_native_retirement_observation import observation
from test_light_native_retirement_reference import reference
from test_light_native_retirement import retained,request_bytes

@pytest.fixture
def diagnostic_reference(observation):
    t=observation;p=r.parse(t.private);p['action']='diagnose-local'
    scope=dict(version=1,operation='READ_ONLY_DIAGNOSE_LOCAL_RETIREMENT_JOURNALS',source=p['source'],
        controller_sha256=p['accepted_controller_sha256'],runtime_sha256=p['accepted_runtime_sha256'],
        record_sha256=p['record_sha256'],historical_journal_sha256=p['historical_journal_sha256'])
    p['agreement'].update(evidence='OWNER_ACCEPTED_LOCAL_JOURNAL_DIAGNOSTIC',operation_digest=r.sha(r.encoded(scope)))
    p['accepted_agreement_sha256']=r.sha(r.encoded(p['agreement']));t.private=r.encoded(p)
    t.public.update(action='diagnose-local-reference',agreement=p['agreement'],
        accepted_agreement_sha256=p['accepted_agreement_sha256'],private_request_sha256=r.sha(t.private))
    return t

def test_exact_local_authority_reconstruction(diagnostic_reference):
    t=diagnostic_reference;raw=r.encoded(t.public)
    assert live.read_only_reference(raw)==t.public
    assert live.resolve_reference(raw,r.sha(raw),t.request.controller,t.request.runtime,t.request.guard,
        read_only=True,local_only=True)==(t.private,r.sha(t.private))

@pytest.mark.parametrize('kind',['observe','create'])
def test_local_diagnostic_cannot_reuse_other_authority(diagnostic_reference,kind):
    t=diagnostic_reference;p=r.parse(t.private)
    p['action']='observe-retirement' if kind=='observe' else 'retire-prepare'
    raw=r.encoded(p)
    with pytest.raises(RuntimeError):live.write_request(raw,r.sha(raw),t.request.controller,t.request.runtime,
        t.request.guard,read_only=True,local_only=True)

def test_original_local_predicates_and_order_are_byte_independent_ast_identical():
    node=next(x for x in ast.parse(Path(live.__file__).read_bytes()).body if isinstance(x,ast.FunctionDef) and x.name=='local')
    class Strip(ast.NodeTransformer):
        def visit_If(self,x):
            if isinstance(x.test,ast.Compare) and isinstance(x.test.left,ast.Name) and x.test.left.id=='diagnostic':return None
            return self.generic_visit(x)
        def visit_Assign(self,x):
            if any(isinstance(y,ast.Name) and y.id=='_parse' for y in x.targets):return None
            return self.generic_visit(x)
        def visit_Call(self,x):
            if isinstance(x.func,ast.Name) and x.func.id=='_local_condition':return self.visit(x.args[2].body)
            x=self.generic_visit(x)
            if isinstance(x.func,ast.Name) and x.func.id=='_parse':x.func=ast.Attribute(value=ast.Name(id='r',ctx=ast.Load()),attr='parse',ctx=ast.Load())
            return x
    node=Strip().visit(node);node.args.kwonlyargs.pop();node.args.kw_defaults.pop()
    assert hashlib.sha256(ast.dump(node).encode()).hexdigest()=='7c589eaed44cd3f02563c28b4533bd4b594aef9502b8a02a08b4e7edf21c91ea'

@pytest.mark.parametrize('field',['checkpoint','reason','role','helper_index','trace','record_sha256'])
def test_egress_rejects_private_or_unbound_fields(field):
    d=live.LocalDiagnostic();v=d.result(RuntimeError('PRIVATE_SENTINEL'));v['record_sha256']='a'*64
    v[field]='PRIVATE_SENTINEL'
    with pytest.raises((RuntimeError,TypeError)):live.public_local_result(v,'a'*64)

def test_unknown_exception_is_never_serialized():
    d=live.LocalDiagnostic();d.at('L001');v=d.result(RuntimeError('PRIVATE_SENTINEL'))
    v['record_sha256']='a'*64
    assert live.public_local_result(v,'a'*64)['reason']=='UNCLASSIFIED'
    assert b'PRIVATE_SENTINEL' not in r.encoded(v) and len(r.encoded(v))<4096

@pytest.mark.parametrize('flag',['proposal_observation_final','hold_db_provider_verified','incident_closed',
    'execution_acknowledged','new_task_authorized','local_checks_final'])
def test_refusal_never_claims_finality_or_controls(flag):
    v=live.LocalDiagnostic().result(RuntimeError());v['record_sha256']='a'*64;v[flag]=True
    with pytest.raises(RuntimeError):live.public_local_result(v,'a'*64)

def test_typed_refusal_keeps_existing_runtimeerror_contract():
    with pytest.raises(RuntimeError,match='^LANE_RETIREMENT_METADATA$') as caught:r.need(False,'LANE_RETIREMENT_METADATA')
    assert isinstance(caught.value,r.Refusal) and live.LocalDiagnostic.reason(caught.value)=='METADATA'
