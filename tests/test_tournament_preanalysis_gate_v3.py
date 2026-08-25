from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bridge_school_api.tournament_preanalysis_gate_v3 import build_preanalysis_gate


FACTS = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")
RECEIVED_AT = "2026-08-21T11:05:01Z"
RECEIPT_COMMIT = "0158e506c022fd20051898a3161ab8b576d51f9b"
ALGORITHM_REVISION = "AIroW35dhYwaAOQ1dDxMCkYjVKitrJKTII3Zx0IS7RNHuQDBE8iXBw-l2Ux9qF42DTU1gUWmWDu3kn9XVb1ne2cTls8HTLvblELsTAZRRuY"


def _load():
    raw = FACTS.read_bytes()
    return raw, json.loads(raw.decode("utf-8"))


def _gate(source=None):
    raw, actual = _load()
    return build_preanalysis_gate(
        actual if source is None else source,
        normalized_facts_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_facts_size_bytes=len(raw),
        normalized_facts_received_at=RECEIVED_AT,
        normalized_facts_commit=RECEIPT_COMMIT,
        algorithm_revision_id=ALGORITHM_REVISION,
    )


def _mutate_row(source, board_number: int, **updates):
    columns = list(source["columns"])
    for index, raw in enumerate(source["rows"]):
        row = dict(zip(columns, raw.split("|"), strict=True))
        if int(row["board"]) == board_number:
            row.update({key: str(value) for key, value in updates.items()})
            source["rows"][index] = "|".join(row[column] for column in columns)
            return
    raise AssertionError(board_number)


def test_real_30041_is_ready_for_facts_only_not_full_causal_replay():
    gate = _gate()
    assert gate["schema"] == "tournament-preanalysis-gate-v1"
    assert gate["facts_only_analysis_ready"] is True
    assert gate["full_causal_replay_ready"] is False
    assert gate["hard_stop_conditions"] == []
    assert gate["gates"]["structure_pass"] is True
    assert gate["gates"]["duplicate_score_gate_pass"] is True
    assert gate["gates"]["played_scores_checked"] == 21
    assert gate["gates"]["opening_lead_gate_pass"] is True
    assert gate["evidence_availability"] == {
        "played_boards": 21,
        "actual_auction_boards": 0,
        "actual_full_play_boards": 0,
        "partial_play_boards": 21,
        "opening_leads_checked": 21,
        "opening_leads_legal": 21,
    }


def test_capabilities_preserve_evidence_boundaries():
    gate = _gate()
    assert "CONTRACT_LEVEL_DD_OPPORTUNITY" in gate["allowed_analyses"]
    assert "OPENING_LEAD_DD_WHERE_LEAD_IS_ACTUAL" in gate["allowed_analyses"]
    assert "DUPLICATE_SCORE_VALIDATION" in gate["allowed_analyses"]
    assert "AUCTION_ANALYSIS_ONLY_ON_BOARDS_WITH_ACTUAL_AUCTION" not in gate["allowed_analyses"]
    assert "CARD_BY_CARD_ANALYSIS_ONLY_ON_BOARDS_WITH_ACTUAL_FULL_PLAY" not in gate["allowed_analyses"]
    assert "AUTOMATIC_STUDENT_ERROR_ATTRIBUTION" in gate["blocked_attributions"]
    assert "BIDDING_DECISION_ATTRIBUTION_WITHOUT_ACTUAL_AUCTION" in gate["blocked_attributions"]
    assert "LATER_CARD_ATTRIBUTION_WITHOUT_FULL_PLAY_RECORD" in gate["blocked_attributions"]
    assert "ACTUAL_AUCTION_ABSENT_FOR_SOME_OR_ALL_PLAYED_BOARDS" in gate["limitations"]
    assert "FULL_PLAY_RECORD_ABSENT_FOR_SOME_OR_ALL_PLAYED_BOARDS" in gate["limitations"]


def test_illegal_opening_lead_is_hard_stop_for_facts_only_gate():
    _, source = _load()
    # Board 2 declarer S => opening leader W; AS is not in W hand.
    _mutate_row(source, 2, opening_lead="SA")
    gate = _gate(source)
    assert gate["facts_only_analysis_ready"] is False
    assert "STRUCTURAL_VALIDATION_FAILED" in gate["hard_stop_conditions"]
    assert gate["gates"]["opening_lead_gate_pass"] is False


def test_score_mismatch_is_hard_stop_not_explained_away():
    _, source = _load()
    _mutate_row(source, 2, pair_score="-590")
    gate = _gate(source)
    assert gate["facts_only_analysis_ready"] is False
    assert "DUPLICATE_SCORE_MISMATCH" in gate["hard_stop_conditions"]
    assert gate["gates"]["duplicate_score_gate_pass"] is False


def test_source_status_conflict_is_hard_stop():
    _, source = _load()
    _mutate_row(source, 2, status="mystery")
    gate = _gate(source)
    assert gate["facts_only_analysis_ready"] is False
    assert "SOURCE_CONFLICT" in gate["hard_stop_conditions"]
