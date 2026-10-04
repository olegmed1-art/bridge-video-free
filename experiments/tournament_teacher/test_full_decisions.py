import json
from pathlib import Path

import pytest

from .consumer import decide
from .scenarios import CASES, make_request, scenarios


@pytest.mark.parametrize("case", CASES, ids=lambda c:c[0])
def test_all_confirmed_rule_scenarios_through_teacher(case):
    for category, request, expected in scenarios(case):
        result = decide(request, enabled=True)
        assert result["status"] == expected, (case[0],category,result)
        assert result["action"] is None  # assessments never masquerade as unique choices
        assert not result["formal_db_activation_asserted"]
        if result["checks"]:
            check = result["checks"][0]
            assert check["rule_id"] == "RULE-TOUR-"+case[0]
            assert check["decision_id"] == "TDEC-202610"+case[1]
            assert check["forcing_status"] == case[6]
            assert check["source_id"] == "SRC-0096"
            assert check["original_excerpt"] and check["explanation"]
            assert check["sync_status"] == "DRIVE_CANON_ONLY"


def get(suffix):
    return next(c for c in CASES if c[0] == suffix)


def run(suffix,shape=None,points="DEFAULT"):
    return decide(make_request(get(suffix),shape=shape,points=points),enabled=True)


@pytest.mark.parametrize("suffix,shape,status", [
    ("1D-1NT-REBID-2H",(1,4,6,2),"SUPPORTED"),
    ("1D-1NT-REBID-2H",(1,5,5,2),"CONTRADICTED"),
    ("1D-1NT-REBID-2H",(3,4,4,2),"CONTRADICTED"),
    ("1D-1NT-REBID-2S",(4,1,6,2),"SUPPORTED"),
    ("1D-1NT-REBID-2S",(5,1,5,2),"CONTRADICTED"),
    ("1D-1NT-REBID-2S",(4,3,4,2),"CONTRADICTED"),
    ("1H-1NT-REBID-2S",(4,6,1,2),"SUPPORTED"),
    ("1H-1NT-REBID-2S",(5,5,1,2),"CONTRADICTED"),
    ("1H-1NT-REBID-2S",(4,4,3,2),"CONTRADICTED"),
    ("1C-1H-REBID-2C",(2,2,3,6),"SUPPORTED"),
    ("1C-1H-REBID-2C",(4,1,2,6),"CONTRADICTED"),
    ("1C-1H-REBID-2C",(1,4,2,6),"CONTRADICTED"),
    ("1C-1H-REBID-2C",(1,2,5,5),"CONTRADICTED"),
    # No unsupported H/S restriction is added to the 3C row.
    ("1C-1H-REBID-3C",(4,1,2,6),"SUPPORTED"),
    ("1C-1H-REBID-1S",(5,1,2,5),"CONTRADICTED"),
    ("1D-1H-1S-RSP-3C",(1,4,2,6),"SUPPORTED"),
    ("1D-1H-1S-RSP-3C",(2,4,3,4),"CONTRADICTED"),
    ("1D-1H-1NT-RSP-3C",(1,4,2,6),"SUPPORTED"),
    ("1D-1H-1NT-RSP-3C",(2,4,3,4),"CONTRADICTED"),
    ("2S-RSP-3C",(2,2,3,6),"SUPPORTED"),
    ("2S-RSP-3D",(2,2,6,3),"SUPPORTED"),
])
def test_exact_lengths_conjunctions_and_preserved_alternatives(suffix,shape,status):
    assert run(suffix,shape)["status"] == status


@pytest.mark.parametrize("suffix", ["OPEN-1NT","OPEN-2NT"])
@pytest.mark.parametrize("shape,status", [
    ((3,3,5,2),"SUPPORTED"), ((3,3,2,5),"SUPPORTED"),
    ((4,2,2,5),"SUPPORTED"), ((2,4,5,2),"SUPPORTED"),
    ((3,2,6,2),"SUPPORTED"), ((2,2,3,6),"SUPPORTED"),
    ((3,5,3,2),"CONTRADICTED"), ((5,3,3,2),"CONTRADICTED"),
    ((4,3,3,3),"ABSTAIN"), ((4,4,3,2),"ABSTAIN"),
    ((5,4,2,2),"ABSTAIN"), ((6,3,2,2),"ABSTAIN"),
])
def test_nt_partial_shape_list_is_not_completed_or_made_exhaustive(suffix,shape,status):
    result = run(suffix,shape)
    assert result["status"] == status
    if status == "ABSTAIN":
        assert result["abstain_reasons"] == ["NT_SHAPE_NOT_ESTABLISHED_NONEXHAUSTIVE_SOURCE"]


@pytest.mark.parametrize("major", ["H","S"])
def test_confirmed_opening_choice_and_unresolved_high_range_priority(major):
    request = make_request(get("OPEN-1"+major))
    request["task"] = "recommend"
    request.pop("proposed_call")
    result = decide(request,enabled=True)
    assert (result["status"],result["action"]) == ("RECOMMEND","1"+major)
    request["school_points"]["value"] = 21
    result = decide(request,enabled=True)
    assert result["status"] == "ABSTAIN" and result["action"] is None
    assert "OPENING_PRIORITY_UNSPECIFIED_OUTSIDE_15_17_5M332" in result["abstain_reasons"]


def test_pass_correction_is_draft_condition_not_sufficient_choice():
    request = make_request(get("1D-1NT-REBID-PASS"))
    result = decide(request,enabled=True)
    assert result["checks"][0]["rule_status"] == "DRAFT"
    request["proposed_call"] = "1NT"
    assert decide(request,enabled=True)["reason"] == "UNSUPPORTED_CALL"
    request.pop("proposed_call")
    request["task"] = "recommend"
    result = decide(request,enabled=True)
    assert result["status"] == "ABSTAIN"
    assert "PASS_SHAPE_AND_PRIORITY_UNSPECIFIED" in result["abstain_reasons"]


@pytest.mark.parametrize("minor", ["C","D"])
def test_partial_decision_011_requires_followup_and_weak_context(minor):
    request = make_request(get("2S-RSP-3"+minor))
    result = decide(request,enabled=True)
    check = result["checks"][0]
    assert check["decision_ids"] == ["TDEC-20261002-011","TDEC-20261003-001"]
    assert "→ ценности" in check["original_excerpt"]
    assert "ценности" not in check["meaning"] and "ценности" not in check["explanation"]
    assert check["rule_status"] == ("DRAFT" if minor == "D" else "ACTIVE")
    del request["opening_meaning"]
    result = decide(request,enabled=True)
    assert result["status"] == "ABSTAIN"
    assert result["abstain_reasons"] == ["WEAK_2S_CONTEXT_REQUIRED"]


def test_decision_mapping_is_complete_and_each_rule_has_behavioral_cases():
    rows = json.loads(Path(__file__).with_name("decision_mapping.json").read_text(encoding="utf-8"))
    expected = {f"TDEC-20261002-{n:03}" for n in range(1,12)} | {f"TDEC-20261003-{n:03}" for n in range(1,4)}
    assert {r["decision_id"] for r in rows} == expected
    assert len(rows) == 14
    tested = {"RULE-TOUR-"+c[0] for c in CASES}
    for row in rows:
        assert set(row["rule_ids"]) <= tested
        assert row["coverage"] == ["positive","negative","boundary","hidden_information"]
        assert row["boundary_or_limitation"]
