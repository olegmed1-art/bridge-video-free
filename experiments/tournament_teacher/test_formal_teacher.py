"""New formal evaluator and existing-route boundary; SQL gates run in CI rehearsal."""
from copy import deepcopy
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from bridge_school_api import tournament_teacher as teacher, ai_teacher
from .formal_package import formal_package
from .first_init import position,envelope

SCHOOL=UUID("00000000-0000-4000-8000-000000000101")


def catalog(call):
    entry=next(r for r in formal_package()["rules"] if r["payload"]["source_rule"]["call"]==call)
    return dict(school_id=SCHOOL,scope_key=teacher.SCOPE,rule_key=entry["rule_key"],
        rule_id=UUID(int=201),knowledge_version_id=UUID(int=202),runtime_activation_id=UUID(int=203),
        compiled_payload=entry["payload"],**entry["payload"]["catalog"])


def test_compiler_matches_reviewed_runtime_pin():
    pkg=formal_package()
    assert pkg["create_activations"] is False and pkg["school_id"] is None
    for entry in pkg["rules"]:
        assert entry["payload_sha256"]==teacher.PAYLOAD_HASHES[entry["payload"]["source_rule"]["call"]]


@pytest.mark.parametrize("call",["3H","3S"])
def test_all_560_distributions_against_explicit_four_allowed_shapes(call):
    row=catalog(call)
    allowed=({(3,1,4,5),(3,1,5,4)} if call=="3H" else {(1,3,4,5),(1,3,5,4)})
    total=0
    for s in range(14):
        for h in range(14-s):
            for d in range(14-s-h):
                shape=(s,h,d,13-s-h-d)
                answer=teacher.evaluate(position(SCHOOL,shape),call,[row])
                assert answer["status"]==("SUPPORTED" if shape in allowed else "CONTRADICTED")
                assert answer["action"] is None and answer["point_threshold"] is None
                assert answer["assessment_scope"]=="shape_meaning_only"
                total+=1
    assert total==560


@pytest.mark.parametrize("field,value",[("seat","E"),("dealer",None),("hand_pbn","AAAA.2.234.23456"),
    ("hand_pbn",None),("auction_json",["1NT","X"]),("cards_played_json",["SA"]),
    ("dummy_pbn","234.2.2345.23456"),("input_status","INCOMPLETE"),
    ("system_us","BEN_DEFAULT"),("decision_type","PLAY"),("school_status","inactive")])
def test_context_gaps_abstain(field,value):
    pos=position(SCHOOL,(3,1,4,5),{field:value})
    assert teacher.evaluate(pos,"3H",[catalog("3H")])["status"]=="ABSTAIN"


@pytest.mark.parametrize("mutation",["none","duplicate","version","school","scope","payload","columns"])
def test_missing_or_mismatched_binding_cannot_activate(mutation):
    row=catalog("3H")
    rows=[row]
    if mutation=="none": rows=[]
    if mutation=="duplicate": rows=[row,deepcopy(row)]
    if mutation=="version": row["method_version"]="other"
    if mutation=="school": row["school_id"]=UUID(int=9)
    if mutation=="scope": row["scope_key"]="default"
    if mutation=="payload": row["compiled_payload"]["recommend_supported"]=True
    if mutation=="columns": row["hand_constraints"]={"singleton":"S"}
    result=teacher.evaluate(position(SCHOOL,(3,1,4,5)),"3H",rows)
    assert result["status"]=="ABSTAIN" and result["source_bindings"]==[]


@pytest.mark.parametrize("field,value",[("hand_constraints",{"shape_sorted":[True,3,4,5],"singleton":"H","minor_lengths":[4,5]}),
    ("action",{"call":"3H","assessment_only":1})])
def test_catalog_json_types_are_bound_exactly(field,value):
    row=catalog("3H")
    row[field]=value
    result=teacher.evaluate(position(SCHOOL,(3,1,4,5)),"3H",[row])
    assert result["status"]=="ABSTAIN" and result["reason"]=="CANON_COLUMN_BINDING_MISMATCH"


@pytest.mark.parametrize("change,expected",[
    ({"action":"3H"},422),({"canon_request":None},422),({"canon_request":{}},422),
    ({"teacher_version":"stale"},409),({"teacher_system":"BEN_DEFAULT"},409),
    ({"test_request":{}},422),({"canonRequest":{}},422),
])
def test_invalid_request_never_falls_into_legacy_writer(monkeypatch,change,expected):
    import app as entrypoint
    from bridge_school_api.main import require_api_token
    def forbidden(): raise AssertionError("Invalid request reached SQL")
    monkeypatch.setattr(ai_teacher,"connect",forbidden)
    monkeypatch.setattr(teacher,"connect",forbidden)
    monkeypatch.setenv("VERCEL_OIDC_TOKEN","")
    monkeypatch.setitem(entrypoint.app.dependency_overrides,require_api_token,lambda:None)
    with TestClient(entrypoint.app) as client:
        response=client.post('/v1/ai/positions/00000000-0000-4000-8000-000000000001/teacher-evidence',json=envelope("3H")|change)
        assert response.status_code==expected,response.text
