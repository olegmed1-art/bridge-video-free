import copy

import pytest

from bridge_school_api.tournament_teacher_decision_intake_v3 import (
    TeacherDecisionIntakeError,
    apply_teacher_decision_intake,
    build_teacher_decision_template,
)
from bridge_school_api.tournament_teacher_decisions_v3 import (
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)
from bridge_school_api.tournament_teacher_review_bundle_v3 import build_teacher_review_bundle


def _cards():
    return [rank + suit for suit in "CDHS" for rank in "23456789TJQKA"]


def _bundle():
    queue_items = []
    for event_id, deal_id in (("E1", "E1:S1:1"), ("E2", "E2:S1:1")):
        queue_items.append({
            "event_id": event_id,
            "deal_id": deal_id,
            "category": "contract_result",
            "causal_link": "NOT_ESTABLISHED",
            "student_error_attribution_allowed": False,
            "teacher_review_required": True,
            "outcome_scale": "TEST",
            "observed_outcome": 0,
            "adverse_outcome_magnitude": 1,
            "technical_trick_loss": 1.0,
        })
    queue = {
        "schema": "tournament-teacher-review-queue-v1",
        "cross_event_numeric_ranking_allowed": False,
        "causal_error_attribution_allowed": False,
        "student_error_attribution_allowed": False,
        "lanes": [
            {"event_id": "E1", "ranking_scope": "WITHIN_EVENT_ONLY", "items": [queue_items[0]]},
            {"event_id": "E2", "ranking_scope": "WITHIN_EVENT_ONLY", "items": [queue_items[1]]},
        ],
    }
    ledger = serialize_teacher_decision_ledger(build_pending_teacher_decision_ledger(queue))
    cards = _cards()
    dossier_items = []
    for decision in ledger["decisions"]:
        dossier_items.append({
            "review_id": decision["review_id"],
            "event_id": decision["event_id"],
            "deal_id": decision["deal_id"],
            "category": decision["category"],
            "status": "PENDING",
            "teacher_decision_required": True,
            "automatic_methodology_mapping_allowed": False,
            "automatic_student_error_attribution_allowed": False,
            "causal_link": "NOT_ESTABLISHED",
            "queue_context": {"outcome_scale": "TEST", "technical_trick_loss": 1.0},
            "deal_facts": {
                "dealer": "N",
                "vulnerability": "None",
                "hands": {
                    "N": cards[0:13], "E": cards[13:26], "S": cards[26:39], "W": cards[39:52]
                },
                "contract": "3NT",
                "declarer": "S",
                "opening_lead": "S2",
                "source_provenance": {"source": "test"},
            },
            "technical_finding": {
                "summary": "Technical finding",
                "trick_loss": 1.0,
                "observability": "NOT_OBSERVABLE",
                "repeat_key": "TECH_TEST",
                "evidence": [{"kind": "DDS_FACT", "message": "test", "confidence": 1.0}],
            },
            "methodology_mapping": None,
            "student_error_attribution": None,
        })
    dossier = {
        "schema": "tournament-teacher-review-dossier-v1",
        "queue_sha256": ledger["queue_sha256"],
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "cross_event_numeric_ranking_allowed": False,
        "items": dossier_items,
    }
    return build_teacher_review_bundle(queue, ledger, dossier)


def test_template_is_inert_and_preserves_exact_review_set():
    bundle = _bundle()
    template = build_teacher_decision_template(bundle)
    assert template["schema"] == "tournament-teacher-decision-intake-v1"
    assert template["bundle_id"] == bundle["bundle_id"]
    assert len(template["decisions"]) == 2
    assert all(row["status"] is None for row in template["decisions"])
    assert all(row["decision_actor"] is None for row in template["decisions"])
    assert all(row["explicit_teacher_decision"] is False for row in template["decisions"])
    assert template["automatic_decisions_allowed"] is False


def test_one_explicit_teacher_choice_updates_only_that_review():
    bundle = _bundle()
    intake = build_teacher_decision_template(bundle)
    intake["decisions"][0].update({
        "status": "CONFIRMED_TECHNICAL_RELEVANCE",
        "decision_actor": "teacher-1",
        "decision_note": "retain technical finding",
        "decision_reference": "review-session-1",
        "explicit_teacher_decision": True,
    })
    result = apply_teacher_decision_intake(bundle, intake)
    assert result["decided_count"] == 1
    assert result["pending_count"] == 1
    statuses = [row["status"] for row in result["ledger"]["decisions"]]
    assert statuses.count("CONFIRMED_TECHNICAL_RELEVANCE") == 1
    assert statuses.count("PENDING") == 1
    decided = next(row for row in result["ledger"]["decisions"] if row["status"] != "PENDING")
    assert decided["decision_provenance"]["decision_source"] == "EXPLICIT_TEACHER_DECISION"
    assert decided["decision_provenance"]["decision_actor"] == "teacher-1"
    assert decided["automatic_student_error_attribution_allowed"] is False


def test_rejects_decision_without_explicit_teacher_attestation():
    bundle = _bundle()
    intake = build_teacher_decision_template(bundle)
    intake["decisions"][0]["status"] = "DISMISSED"
    intake["decisions"][0]["decision_actor"] = "teacher-1"
    with pytest.raises(TeacherDecisionIntakeError):
        apply_teacher_decision_intake(bundle, intake)


def test_rejects_unknown_or_missing_review_row():
    bundle = _bundle()
    intake = build_teacher_decision_template(bundle)
    intake["decisions"].pop()
    with pytest.raises(TeacherDecisionIntakeError):
        apply_teacher_decision_intake(bundle, intake)


def test_rejects_identity_tampering():
    bundle = _bundle()
    intake = build_teacher_decision_template(bundle)
    bad = copy.deepcopy(intake)
    bad["decisions"][0]["deal_id"] = "forged"
    with pytest.raises(TeacherDecisionIntakeError):
        apply_teacher_decision_intake(bundle, bad)
