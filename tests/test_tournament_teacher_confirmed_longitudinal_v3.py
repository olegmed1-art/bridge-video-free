import copy

import pytest

from bridge_school_api.tournament_teacher_confirmed_longitudinal_v3 import (
    TeacherConfirmedLongitudinalError,
    build_teacher_confirmed_longitudinal_report,
)
from bridge_school_api.tournament_teacher_decisions_v3 import (
    TeacherDecisionStatus,
    apply_explicit_teacher_decision,
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)
from bridge_school_api.tournament_teacher_review_bundle_v3 import build_teacher_review_bundle


def _cards():
    return [rank + suit for suit in "CDHS" for rank in "23456789TJQKA"]


def _queue():
    def item(event_id, deal_id):
        return {
            "event_id": event_id,
            "deal_id": deal_id,
            "category": "contract_result",
            "causal_link": "NOT_ESTABLISHED",
            "student_error_attribution_allowed": False,
            "teacher_review_required": True,
            "outcome_scale": "TECHNICAL_TEST",
            "observed_outcome": 0,
            "adverse_outcome_magnitude": 1,
            "technical_trick_loss": 2.0,
        }

    return {
        "schema": "tournament-teacher-review-queue-v1",
        "cross_event_numeric_ranking_allowed": False,
        "causal_error_attribution_allowed": False,
        "student_error_attribution_allowed": False,
        "lanes": [
            {"event_id": "E1", "ranking_scope": "WITHIN_EVENT_ONLY", "items": [item("E1", "E1:S1:1")]},
            {"event_id": "E2", "ranking_scope": "WITHIN_EVENT_ONLY", "items": [item("E2", "E2:S1:1")]},
        ],
    }


def _bundle_and_ledger_object():
    queue = _queue()
    ledger_obj = build_pending_teacher_decision_ledger(queue)
    ledger = serialize_teacher_decision_ledger(ledger_obj)
    cards = _cards()
    items = []
    for decision in ledger["decisions"]:
        items.append(
            {
                "review_id": decision["review_id"],
                "event_id": decision["event_id"],
                "deal_id": decision["deal_id"],
                "category": decision["category"],
                "status": "PENDING",
                "teacher_decision_required": True,
                "automatic_methodology_mapping_allowed": False,
                "automatic_student_error_attribution_allowed": False,
                "causal_link": "NOT_ESTABLISHED",
                "queue_context": {"outcome_scale": "TECHNICAL_TEST", "technical_trick_loss": 2.0},
                "deal_facts": {
                    "dealer": "N",
                    "vulnerability": "None",
                    "hands": {
                        "N": cards[0:13],
                        "E": cards[13:26],
                        "S": cards[26:39],
                        "W": cards[39:52],
                    },
                    "contract": "3NT",
                    "declarer": "S",
                    "opening_lead": "S2",
                    "source_provenance": {"source": "test"},
                },
                "technical_finding": {
                    "summary": "Technical DDS finding",
                    "trick_loss": 2.0,
                    "observability": "NOT_OBSERVABLE",
                    "repeat_key": "DDS3_PAIR_SAME_CONTRACT_DELTA_V1",
                    "evidence": [{"kind": "DDS_FACT", "message": "test", "confidence": 1.0}],
                },
                "methodology_mapping": None,
                "student_error_attribution": None,
            }
        )
    dossier = {
        "schema": "tournament-teacher-review-dossier-v1",
        "queue_sha256": ledger["queue_sha256"],
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "cross_event_numeric_ranking_allowed": False,
        "items": items,
    }
    return build_teacher_review_bundle(queue, ledger, dossier), ledger_obj


def _provenance(actor="teacher"):
    return {"decision_source": "EXPLICIT_TEACHER_DECISION", "decision_actor": actor}


def test_pending_real_review_state_cannot_enter_confirmed_longitudinal_view():
    bundle, _ = _bundle_and_ledger_object()
    report = build_teacher_confirmed_longitudinal_report(bundle)
    assert report["status_counts"]["PENDING"] == 2
    assert report["status_counts"]["CONFIRMED_TECHNICAL_RELEVANCE"] == 0
    assert report["confirmed_items"] == []
    assert report["clusters"] == []
    assert report["teacher_decision_gate_enforced"] is True
    assert report["automatic_student_error_attribution_allowed"] is False


def test_two_explicit_teacher_confirmations_create_only_technical_persistent_cluster():
    bundle, ledger = _bundle_and_ledger_object()
    ids = [decision.review_id for decision in ledger.decisions]
    for index, review_id in enumerate(ids):
        ledger = apply_explicit_teacher_decision(
            ledger,
            review_id=review_id,
            status=TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE,
            note=f"retain technical finding {index}",
            provenance=_provenance(),
        )
    report = build_teacher_confirmed_longitudinal_report(bundle, serialize_teacher_decision_ledger(ledger))
    assert report["status_counts"]["CONFIRMED_TECHNICAL_RELEVANCE"] == 2
    assert len(report["confirmed_items"]) == 2
    assert len(report["persistent_clusters"]) == 1
    cluster = report["persistent_clusters"][0]
    assert cluster["repeat_key"] == "DDS3_PAIR_SAME_CONTRACT_DELTA_V1"
    assert cluster["event_count"] == 2
    assert cluster["finding_count"] == 2
    assert cluster["technical_trick_loss_mass"] == 4.0
    assert cluster["causal_link"] == "NOT_ESTABLISHED"
    assert cluster["student_error_attribution"] is None
    assert cluster["methodology_mapping"] is None


def test_dismissed_item_does_not_enter_confirmed_cluster():
    bundle, ledger = _bundle_and_ledger_object()
    first, second = [decision.review_id for decision in ledger.decisions]
    ledger = apply_explicit_teacher_decision(
        ledger,
        review_id=first,
        status=TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE,
        provenance=_provenance(),
    )
    ledger = apply_explicit_teacher_decision(
        ledger,
        review_id=second,
        status=TeacherDecisionStatus.DISMISSED,
        provenance=_provenance(),
    )
    report = build_teacher_confirmed_longitudinal_report(bundle, serialize_teacher_decision_ledger(ledger))
    assert len(report["confirmed_items"]) == 1
    assert report["status_counts"]["DISMISSED"] == 1
    assert report["persistent_clusters"] == []


def test_rejects_forged_identity_in_decision_ledger():
    bundle, ledger = _bundle_and_ledger_object()
    payload = serialize_teacher_decision_ledger(ledger)
    bad = copy.deepcopy(payload)
    bad["decisions"][0]["deal_id"] = "forged"
    with pytest.raises(TeacherConfirmedLongitudinalError):
        build_teacher_confirmed_longitudinal_report(bundle, bad)
