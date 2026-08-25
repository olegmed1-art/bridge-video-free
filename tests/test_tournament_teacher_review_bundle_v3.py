import copy

import pytest

from bridge_school_api.tournament_teacher_decisions_v3 import (
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)
from bridge_school_api.tournament_teacher_review_bundle_v3 import (
    TeacherReviewBundleError,
    build_teacher_review_bundle,
    verify_teacher_review_bundle,
)


def _cards():
    return [rank + suit for suit in "CDHS" for rank in "23456789TJQKA"]


def _queue():
    item = {
        "event_id": "E1",
        "deal_id": "E1:S1:1",
        "category": "contract_result",
        "causal_link": "NOT_ESTABLISHED",
        "student_error_attribution_allowed": False,
        "teacher_review_required": True,
        "outcome_scale": "MP_PERCENTAGE",
        "observed_outcome": 22.5,
        "adverse_outcome_magnitude": 27.5,
        "technical_trick_loss": 1.0,
    }
    return {
        "schema": "tournament-teacher-review-queue-v1",
        "cross_event_numeric_ranking_allowed": False,
        "causal_error_attribution_allowed": False,
        "student_error_attribution_allowed": False,
        "lanes": [
            {
                "event_id": "E1",
                "ranking_scope": "WITHIN_EVENT_ONLY",
                "items": [item],
            }
        ],
    }


def _payloads():
    queue = _queue()
    ledger = serialize_teacher_decision_ledger(build_pending_teacher_decision_ledger(queue))
    decision = ledger["decisions"][0]
    cards = _cards()
    dossier = {
        "schema": "tournament-teacher-review-dossier-v1",
        "queue_sha256": ledger["queue_sha256"],
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "cross_event_numeric_ranking_allowed": False,
        "items": [
            {
                "review_id": decision["review_id"],
                "event_id": "E1",
                "deal_id": "E1:S1:1",
                "category": "contract_result",
                "status": "PENDING",
                "teacher_decision_required": True,
                "automatic_methodology_mapping_allowed": False,
                "automatic_student_error_attribution_allowed": False,
                "causal_link": "NOT_ESTABLISHED",
                "queue_context": {
                    "outcome_scale": "MP_PERCENTAGE",
                    "observed_outcome": 22.5,
                    "adverse_outcome_magnitude": 27.5,
                    "technical_trick_loss": 1.0,
                },
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
                    "source_provenance": {"source": "synthetic-contract-test"},
                },
                "technical_finding": {
                    "summary": "DDS opportunity differs from observed result.",
                    "trick_loss": 1.0,
                    "observability": "NOT_OBSERVABLE",
                    "evidence": [{"kind": "DDS_FACT", "message": "test", "confidence": 1.0}],
                },
                "methodology_mapping": None,
                "student_error_attribution": None,
            }
        ],
    }
    return queue, ledger, dossier


def test_builds_deterministic_portable_pending_bundle():
    queue, ledger, dossier = _payloads()
    first = build_teacher_review_bundle(queue, ledger, dossier)
    second = build_teacher_review_bundle(queue, ledger, dossier)
    assert first == second
    assert first["schema"] == "tournament-teacher-review-portable-bundle-v1"
    assert first["review_state"] == "PENDING_TEACHER_DECISION"
    assert first["item_count"] == 1
    assert first["event_counts"] == {"E1": 1}
    assert first["automatic_decisions_allowed"] is False
    assert first["automatic_methodology_mapping_allowed"] is False
    assert first["automatic_student_error_attribution_allowed"] is False
    assert first["cross_event_numeric_ranking_allowed"] is False
    verify_teacher_review_bundle(first)


def test_rejects_ledger_that_is_not_exact_pending_queue_binding():
    queue, ledger, dossier = _payloads()
    bad = copy.deepcopy(ledger)
    bad["decisions"][0]["status"] = "CONFIRMED_TECHNICAL_RELEVANCE"
    bad["decisions"][0]["teacher_decision_required"] = False
    with pytest.raises(TeacherReviewBundleError):
        build_teacher_review_bundle(queue, bad, dossier)


def test_rejects_pedagogical_attribution_in_pending_dossier():
    queue, ledger, dossier = _payloads()
    bad = copy.deepcopy(dossier)
    bad["items"][0]["methodology_mapping"] = "invented"
    with pytest.raises(TeacherReviewBundleError):
        build_teacher_review_bundle(queue, ledger, bad)


def test_verifier_detects_component_tampering():
    queue, ledger, dossier = _payloads()
    bundle = build_teacher_review_bundle(queue, ledger, dossier)
    bad = copy.deepcopy(bundle)
    bad["components"]["dossier"]["items"][0]["technical_finding"]["summary"] = "tampered"
    with pytest.raises(TeacherReviewBundleError):
        verify_teacher_review_bundle(bad)
