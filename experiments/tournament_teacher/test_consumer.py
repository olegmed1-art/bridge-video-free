from copy import deepcopy
import itertools
import json
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.tournament_teacher.consumer import MODE, PROFILE, decide


def hand(shape):
    return [s + r for s, n in zip("SHDC", shape) for r in "23456789TJQKA"[:n]]


def request(shape=(3, 1, 4, 5), call="3H", points=None, auction=None):
    auction = ["1NT", "PASS"] if auction is None else auction
    value = dict(mode=MODE, system_profile=PROFILE, task="assess_call",
                 dealer="N", actor="S" if len(auction) == 2 else "N",
                 auction=auction, hand=hand(shape), proposed_call=call)
    if points is not None:
        value["school_points"] = {"value": points, "basis": "SYNTHETIC_SCHOOL_POINTS"}
    return value


def run(value):
    return decide(value, enabled=True)


def test_before_after_real_entrypoint_and_provenance():
    from bridge_school_api.l1_canonical_runtime_v4 import evaluate
    value = request()
    before = evaluate("RULE-TOUR-1NT-RSP-3H", {"S": 3, "H": 1, "D": 4, "C": 5})
    assert (before.status, before.action) == ("BLOCK", "UNKNOWN_RULE_ID")
    assert decide(value)["reason"] == "TEST_MODE_DISABLED"
    after = run(value)
    assert after["status"] == "SUPPORTED" and after["action"] is None
    check = after["checks"][0]
    assert check["decision_id"] == "TDEC-20261003-002"
    assert check["fact_id"] == "FACT-TOUR-1NT-FIRST-RESPONSES"
    assert check["source_id"] == "SRC-0096"
    assert check["original_excerpt"] == "3♥/♠ = ФГ, «5431», краткость, от 5/4 в минорах"
    assert "синглет ♥" in check["explanation"] and check["forcing_status"] == "FG"
    assert check["sync_status"] == "DRIVE_CANON_ONLY"
    assert not after["formal_db_activation_asserted"]
    assert not after["production_ready"] and not after["fallback_performed"]
    # Re-enter the unchanged L1 engine after running the experiment.
    assert evaluate("RULE-TOUR-1NT-RSP-3H", {}).action == "UNKNOWN_RULE_ID"


@pytest.mark.parametrize("shape,call", [((3,1,4,5),"3H"), ((3,1,5,4),"3H"),
                                      ((1,3,4,5),"3S"), ((1,3,5,4),"3S")])
def test_singleton_mapping_both_minor_orientations(shape, call):
    assert run(request(shape, call))["status"] == "SUPPORTED"
    other = "3S" if call == "3H" else "3H"
    assert run(request(shape, other))["status"] == "CONTRADICTED"


@pytest.mark.parametrize("points,expected", [(7,"CONTRADICTED"),(8,"SUPPORTED"),
                                            (9,"SUPPORTED"),(10,"CONTRADICTED")])
def test_direct_invite_boundaries_without_invented_shape_restrictions(points, expected):
    assert run(request((4,4,3,2), "2NT", points))["status"] == expected


@pytest.mark.parametrize("shape,points,expected", [
    ((4,3,3,3),7,"CONTRADICTED"), ((4,3,3,3),8,"SUPPORTED"),
    ((3,4,3,3),9,"SUPPORTED"), ((4,4,3,2),8,"SUPPORTED"),
    ((3,3,4,3),8,"CONTRADICTED"), ((5,3,3,2),8,"ABSTAIN"),
    ((5,4,2,2),8,"ABSTAIN"), ((4,3,3,3),None,"ABSTAIN"),
    ((4,3,3,3),41,"SUPPORTED"),
])
def test_stayman_exact_four_and_no_five_card_major_inference(shape, points, expected):
    assert run(request(shape,"2C",points))["status"] == expected


@pytest.mark.parametrize("major,shape", [("H",(3,5,3,2)),("S",(5,3,3,2))])
@pytest.mark.parametrize("points", [14,15,16,17,18,None])
def test_explicit_major_rebid_choice_boundaries(major, shape, points):
    value = request(shape,"2NT",points,["1"+major,"PASS","1NT","PASS"])
    value.pop("proposed_call")
    value["task"] = "recommend"
    result = run(value)
    supported = points in (15,16,17)
    assert result["status"] == ("RECOMMEND" if supported else "ABSTAIN")
    assert result["action"] == ("2NT" if supported else None)
    check = result["checks"][0]
    assert check["decision_id"] == "TDEC-20261002-008"
    assert check["forcing_status"] is None
    assert check["rule_status"] == "BLOCKED_SOURCE_CONFLICT"
    assert not result["formal_db_activation_asserted"]


def test_overlap_and_single_match_never_create_selection_policy():
    for value in [request((4,3,3,3),points=8), request((3,1,4,5),points=10)]:
        value["task"] = "recommend"
        value.pop("proposed_call")
        result = run(value)
        assert result["status"] == "ABSTAIN" and result["action"] is None
        assert result["reason"] == "NO_CONFIRMED_SELECTION_POLICY"
    overlap = run({**request((4,3,3,3),points=8), "proposed_call":"2C"})
    assert overlap["status"] == "SUPPORTED"


@pytest.mark.parametrize("changes", [
    {"mode":"production"}, {"system_profile":"SCHOOL_L1_DB_V1"},
    {"auction":["1NT"]}, {"auction":["1NT","X"]},
    {"auction":["PASS","PASS","1NT","PASS"]}, {"auction":[]},
    {"actor":"N"}, {"dealer":None}, {"hand":[]},
    {"hand":["SA"]*13}, {"hand":[None]*13}, {"hand":[{}]*13},
    {"partner_hand":["SA"]}, {"HCP":8}, {"priority":1000},
    {"school_points":{"value":8,"basis":"HCP"}},
    {"school_points":{"value":True,"basis":"SYNTHETIC_SCHOOL_POINTS"}},
    {"school_points":{"value":"8","basis":"SYNTHETIC_SCHOOL_POINTS"}},
    {"school_points":{"value":8.5,"basis":"SYNTHETIC_SCHOOL_POINTS"}},
    {"school_points":{"value":-1,"basis":"SYNTHETIC_SCHOOL_POINTS"}},
    {"school_points":8}, {"task":"production"}, {"proposed_call":"PASS"},
    {"auction":["2NT","PASS","4C","PASS"]},
    {"auction":["1C","PASS"],"proposed_call":"2H"},
    {"auction":["2S","PASS"],"proposed_call":"3D"},
    {"auction":["1D","PASS","1NT","PASS"],"proposed_call":"PASS"},
])
def test_invalid_hidden_unsupported_and_unresolved_cases_abstain(changes):
    result = run({**request(), **changes})
    assert result["status"] == "ABSTAIN" and result["action"] is None


def test_points_never_derived_from_honors_and_input_unchanged():
    value = request((4,3,3,3), "2C")
    value["hand"] = ["SA","SK","SQ","SJ","HA","HK","HQ","DA","DK","DQ","CA","CK","CQ"]
    saved = deepcopy(value)
    assert run(value)["status"] == "ABSTAIN"
    assert value == saved
    result = run(request())
    result["checks"][0]["decision_id"] = "corrupt"
    assert run(request())["checks"][0]["decision_id"] == "TDEC-20261003-002"


def test_cli_is_real_decision_path_and_disabled_by_default():
    command = [sys.executable, "-m", "experiments.tournament_teacher"]
    for flags, status in [([],"ABSTAIN"), (["--test-only"],"SUPPORTED")]:
        process = subprocess.run(command+flags, input=json.dumps(request()),
                                 text=True, encoding="utf-8", capture_output=True, check=True)
        assert json.loads(process.stdout)["status"] == status


@pytest.mark.parametrize("raw", ["{", "null", "[]", "["*2000+"0"+"]"*2000])
def test_cli_malformed_json_abstains(raw):
    process = subprocess.run([sys.executable,"-m","experiments.tournament_teacher","--test-only"],
                             input=raw, text=True, capture_output=True, check=True)
    assert json.loads(process.stdout)["status"] == "ABSTAIN"


def test_existing_production_profile_gate_stays_closed(monkeypatch):
    from fastapi import HTTPException
    from bridge_school_api import knowledge
    monkeypatch.setattr(knowledge, "connect", lambda: pytest.fail("No DB access allowed"))
    with pytest.raises(HTTPException) as exc:
        knowledge.l1_runtime_catalog(system_profile=PROFILE, rule_id=None, limit=200, offset=0)
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "RUNTIME_PROFILE_GAP"


def test_independent_finite_model_all_560_shapes():
    """I2 bounded oracle: explicit tuple language, not consumer predicates.

    Enumerate every 13-card suit-length partition. Two singleton mappings must
    accept exactly four tuples total; major rebids exactly six tuples per point
    value. This verifies finite shape semantics, not domain completeness.
    """
    accepted_singletons = {"3H":{(3,1,4,5),(3,1,5,4)},
                           "3S":{(1,3,4,5),(1,3,5,4)}}
    accepted_rebids = {
        "H":{(2,5,3,3),(3,5,2,3),(3,5,3,2)},
        "S":{(5,2,3,3),(5,3,2,3),(5,3,3,2)},
    }
    count = 0
    for s,h,d in itertools.product(range(14), repeat=3):
        c = 13-s-h-d
        if c < 0:
            continue
        shape = (s,h,d,c)
        count += 1
        for call, language in accepted_singletons.items():
            assert (run(request(shape,call))["status"] == "SUPPORTED") == (shape in language)
        for major, language in accepted_rebids.items():
            value = request(shape,"2NT",16,["1"+major,"PASS","1NT","PASS"])
            value.pop("proposed_call")
            value["task"] = "recommend"
            assert (run(value)["action"] == "2NT") == (shape in language)
    assert count == 560
