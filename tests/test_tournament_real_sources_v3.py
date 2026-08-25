from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bridge_school_api.tournament_adapters_v3 import TournamentAdapterError
from bridge_school_api.tournament_analyzer_v3 import EvidenceKind
from bridge_school_api.tournament_real_sources_v3 import (
    PAIR_SAME_CONTRACT_REPEAT_KEY,
    findings_29912,
    findings_30041,
    normalize_30041_facts,
    pbn_hand_to_cards,
    validate_29912_report_contract,
    validate_30041_dds3_report,
)

ROOT = Path(__file__).resolve().parents[1]


def _policy_29912() -> dict:
    return {
        "engine": "DDS3",
        "fallback_used": False,
        "site_dd_used": False,
        "full_play_records_available": False,
        "auction_records_available": False,
    }


def _report_29912(boards_by_round: dict[int, list[dict]]) -> dict:
    return {
        "schema": "diana-29912-multi-session-dds3-v2",
        "policy": _policy_29912(),
        "aggregate": {
            "sessions": [1, 2, 4, 5, 6],
            "played_boards": 100,
            "decision_analyzable_boards": 99,
            "source_inconsistencies": [{"round": 1, "board": 5, "reason": "test"}],
        },
        "sessions": [{"round": r, "boards": boards_by_round.get(r, [])} for r in (1, 2, 4, 5, 6)],
    }


def _dds_board_30041(board: int, delta: float | None = None) -> dict:
    row = {
        "board": board,
        "dds3": {"engine": "DDS3", "fallback_used": False, "input_validated": True},
    }
    if delta is not None:
        row["same_contract_dd_comparison"] = {"target_pair_delta_vs_dd_tricks": delta}
    return row


def test_pbn_hand_converts_to_core_rank_suit_tokens() -> None:
    cards = pbn_hand_to_cards("9.AT2.JT2.Q98643")
    assert len(cards) == 13
    assert cards[0] == "9S"
    assert "AH" in cards
    assert "QC" in cards


def test_real_30041_extract_normalizes_all_24_boards_without_play_invention() -> None:
    source = json.loads((ROOT / "data/tournaments/tournament_30041_round2_diana_facts_v1.json").read_text(encoding="utf-8"))
    batch = normalize_30041_facts(source)
    assert batch.event_id == "30041"
    assert batch.session_id == "round-2"
    assert len(batch.deals) == 24
    assert all(d.play_record is None and d.auction is None for d in batch.deals)
    assert all(sum(len(v) for v in d.hands.values()) == 52 for d in batch.deals)


def test_30041_dds3_evidence_requires_exact_json_binding_and_no_fallback() -> None:
    source_hash = "a" * 64
    report = {
        "schema": "bridge-dds3-tournament-baseline-v1",
        "mode": "FACTS_ONLY_DDS3_BASELINE",
        "source_sha256": source_hash,
        "policy": {
            "engine": "DDS3",
            "fallback_used": False,
            "card_level_attribution_allowed": False,
            "student_skill_writes_allowed": False,
        },
        "summary": {"boards_total": 24, "played_contracts_compared": 21},
        "boards": [_dds_board_30041(n) for n in range(1, 25)],
    }
    validate_30041_dds3_report(report, source_json_sha256=source_hash)
    bad = copy.deepcopy(report)
    bad["policy"]["fallback_used"] = True
    with pytest.raises(TournamentAdapterError):
        validate_30041_dds3_report(bad, source_json_sha256=source_hash)
    with pytest.raises(TournamentAdapterError):
        validate_30041_dds3_report(report, source_json_sha256="b" * 64)


def test_29912_policy_fails_closed_on_site_dd_or_changed_inconsistency() -> None:
    report = _report_29912({})
    validate_29912_report_contract(report)
    bad = copy.deepcopy(report)
    bad["policy"]["site_dd_used"] = True
    with pytest.raises(TournamentAdapterError):
        validate_29912_report_contract(bad)
    bad = copy.deepcopy(report)
    bad["aggregate"]["source_inconsistencies"][0]["board"] = 6
    with pytest.raises(TournamentAdapterError):
        validate_29912_report_contract(bad)


def test_source_inconsistent_29912_board_cannot_create_a_finding() -> None:
    bad_board = {
        "board": 5,
        "source_consistency": {"ok": False},
        "same_contract": {"actual_minus_dd_declarer": -2, "actual_tricks": 6},
        "pair_direction": "NS",
        "declarer": "N",
        "diana_opening_leader": True,
        "diana_declarer": True,
        "opening_lead_dds3": {"regret": 2, "declarer_ceiling_after_recorded_lead": 9},
    }
    assert findings_29912(_report_29912({1: [bad_board]})) == ()


def test_common_pair_dd_repeat_key_is_shared_but_not_methodology() -> None:
    r30041 = {
        "boards": [_dds_board_30041(2, -1.0)],
    }
    f30041 = findings_30041(r30041)
    good_board = {
        "board": 7,
        "source_consistency": {"ok": True},
        "same_contract": {"actual_minus_dd_declarer": -1, "actual_tricks": 8},
        "pair_direction": "NS",
        "declarer": "N",
        "diana_opening_leader": False,
        "diana_declarer": False,
        "opening_lead_dds3": None,
    }
    f29912 = findings_29912(_report_29912({2: [good_board]}))
    assert f30041[0].repeat_key == PAIR_SAME_CONTRACT_REPEAT_KEY
    assert f29912[0].repeat_key == PAIR_SAME_CONTRACT_REPEAT_KEY
    kinds = {e.kind for f in (*f30041, *f29912) for e in f.evidence}
    assert kinds == {EvidenceKind.DDS_FACT}
    assert EvidenceKind.SYSTEM_RULE not in kinds
    assert EvidenceKind.MODEL_OPINION not in kinds
