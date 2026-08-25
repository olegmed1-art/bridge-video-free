import json
from pathlib import Path

import pytest

from bridge_school_api.tournament_opening_lead_review_handoff_v3 import (
    OpeningLeadReviewHandoffError,
    build_opening_lead_review_handoff,
    findings_from_opening_lead_report,
)


FACTS = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")


def _source():
    return json.loads(FACTS.read_text(encoding="utf-8"))


def _candidate(*, deal_id="30041:round-2:2", regret=2.0):
    return {
        "deal_id": deal_id,
        "board_number": int(deal_id.rsplit(":", 1)[1]),
        "actual_lead": "S2",
        "optimal_leads": ["C2", "H2"],
        "best_tricks_for_side_to_lead": 7,
        "actual_lead_tricks_for_side_to_lead": 5,
        "regret_tricks": regret,
        "position_sha256": "a" * 64,
        "engine": "DDS3",
        "engine_version": "v3.0.0+cdd13cf5b700788ac8c1391501b42445b3129b45",
        "fallback_used": False,
        "target_pair_made_opening_lead": True,
        "causal_error_attribution": "NOT_ESTABLISHED",
        "student_error_attribution": None,
        "methodology_mapping": None,
        "teacher_review_required": True,
        "coverage_eligible": False,
    }


def _report(candidates=None):
    candidates = list(candidates or [_candidate()])
    return {
        "schema": "tournament-opening-lead-dds3-v1",
        "provider_native_key": "bridge.co.il:event:30041:round:2",
        "event_id": "30041",
        "session_id": "round-2",
        "engine": "DDS3",
        "fallback_used": False,
        "target_pair_positive_regret_candidates": len(candidates),
        "teacher_review_candidates": candidates,
    }


def test_real_source_handoff_stays_pending_and_coverage_ineligible():
    handoff = build_opening_lead_review_handoff(_source(), _report())
    assert handoff["schema"] == "tournament-opening-lead-review-handoff-v1"
    assert handoff["candidate_count"] == 1
    assert handoff["historical_review_batch_mutation_allowed"] is False

    queue = handoff["queue"]
    assert queue["schema"] == "tournament-teacher-review-queue-v1"
    assert len(queue["lanes"]) == 1
    item = queue["lanes"][0]["items"][0]
    assert item["deal_id"] == "30041:round-2:2"
    assert item["category"] == "opening_lead_dds3"
    assert item["technical_trick_loss"] == 2.0
    assert item["observed_outcome"] == 33.5
    assert item["adverse_outcome_magnitude"] == 16.5
    assert item["causal_link"] == "NOT_ESTABLISHED"
    assert item["student_error_attribution_allowed"] is False

    ledger = handoff["decision_ledger"]
    assert len(ledger["decisions"]) == 1
    assert ledger["decisions"][0]["status"] == "PENDING"
    assert ledger["automatic_decisions_allowed"] is False

    dossier = handoff["dossier"]
    assert len(dossier["items"]) == 1
    assert dossier["items"][0]["deal_facts"]["opening_lead"] == "S2"
    assert dossier["items"][0]["technical_finding"]["observability"] == "OBSERVABLE"
    assert dossier["items"][0]["methodology_mapping"] is None
    assert dossier["items"][0]["student_error_attribution"] is None

    inventory = handoff["episode_candidate_inventory"]
    assert inventory["technical_candidate_count"] == 1
    assert inventory["technical_candidates"][0]["category"] == "opening_lead_dds3"
    assert inventory["technical_candidates"][0]["impact_score"] is None
    assert inventory["technical_candidates"][0]["coverage_eligible"] is False
    assert inventory["coverage_episode_inputs"] == []
    assert inventory["v1_4_episode_inventory_complete"] is False


def test_finding_is_dds_fact_not_student_error():
    finding = findings_from_opening_lead_report(_report())[0]
    assert finding.category == "opening_lead_dds3"
    assert finding.trick_loss == 2.0
    assert finding.repeat_key == "DDS3_OPENING_LEAD_REGRET_V1"
    assert finding.evidence[0].kind.value == "DDS_FACT"
    assert finding.evidence[0].provenance["actual_lead"] == "S2"


def test_causal_or_pedagogical_attribution_fails_closed():
    candidate = _candidate()
    candidate["causal_error_attribution"] = "ESTABLISHED"
    with pytest.raises(OpeningLeadReviewHandoffError):
        build_opening_lead_review_handoff(_source(), _report([candidate]))

    candidate = _candidate()
    candidate["methodology_mapping"] = "invented"
    with pytest.raises(OpeningLeadReviewHandoffError):
        build_opening_lead_review_handoff(_source(), _report([candidate]))


def test_non_target_or_zero_regret_candidate_fails_closed():
    candidate = _candidate()
    candidate["target_pair_made_opening_lead"] = False
    with pytest.raises(OpeningLeadReviewHandoffError):
        build_opening_lead_review_handoff(_source(), _report([candidate]))

    candidate = _candidate(regret=0.0)
    with pytest.raises(OpeningLeadReviewHandoffError):
        build_opening_lead_review_handoff(_source(), _report([candidate]))
