import pytest

from bridge_school_api.tournament_teacher_decisions_v3 import (
    TeacherDecisionError,
    TeacherDecisionStatus,
    apply_explicit_teacher_decision,
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)


def _queue():
    return {
        "schema": "tournament-teacher-review-queue-v1",
        "cross_event_numeric_ranking_allowed": False,
        "causal_error_attribution_allowed": False,
        "student_error_attribution_allowed": False,
        "lanes": [
            {
                "event_id": "30041",
                "outcome_scale": "MP_PERCENTAGE",
                "ranking_scope": "WITHIN_EVENT_ONLY",
                "items": [
                    {
                        "event_id": "30041",
                        "deal_id": "30041:round-2:19",
                        "category": "dds3_pair_same_contract_delta",
                        "technical_trick_loss": 2.0,
                        "outcome_scale": "MP_PERCENTAGE",
                        "observed_outcome": 6.0,
                        "adverse_outcome_magnitude": 44.0,
                        "causal_link": "NOT_ESTABLISHED",
                        "student_error_attribution_allowed": False,
                        "teacher_review_required": True,
                    }
                ],
            }
        ],
    }


def test_pending_ledger_is_deterministic_and_cannot_decide_automatically():
    a = build_pending_teacher_decision_ledger(_queue())
    b = build_pending_teacher_decision_ledger(_queue())
    assert a == b
    payload = serialize_teacher_decision_ledger(a)
    assert payload["schema"] == "tournament-teacher-decision-ledger-v1"
    assert payload["automatic_decisions_allowed"] is False
    assert payload["automatic_methodology_mapping_allowed"] is False
    assert payload["automatic_student_error_attribution_allowed"] is False
    assert payload["decisions"][0]["status"] == "PENDING"
    assert payload["decisions"][0]["teacher_decision_required"] is True


def test_explicit_teacher_decision_requires_provenance_and_is_immutable():
    ledger = build_pending_teacher_decision_ledger(_queue())
    review_id = ledger.decisions[0].review_id
    with pytest.raises(TeacherDecisionError, match="provenance"):
        apply_explicit_teacher_decision(
            ledger,
            review_id=review_id,
            status=TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE,
        )
    decided = apply_explicit_teacher_decision(
        ledger,
        review_id=review_id,
        status=TeacherDecisionStatus.NEEDS_CONTEXT,
        note="Нужна запись торговли.",
        provenance={"decision_source": "EXPLICIT_TEACHER_DECISION", "receipt": "test"},
    )
    item = decided.decisions[0]
    assert item.status is TeacherDecisionStatus.NEEDS_CONTEXT
    assert item.teacher_decision_required is False
    assert item.automatic_methodology_mapping_allowed is False
    assert item.automatic_student_error_attribution_allowed is False
    with pytest.raises(TeacherDecisionError, match="immutable"):
        apply_explicit_teacher_decision(
            decided,
            review_id=review_id,
            status=TeacherDecisionStatus.DISMISSED,
            provenance={"decision_source": "EXPLICIT_TEACHER_DECISION"},
        )


def test_queue_boundary_weakening_fails_closed():
    bad = _queue()
    bad["causal_error_attribution_allowed"] = True
    with pytest.raises(TeacherDecisionError, match="causal boundary"):
        build_pending_teacher_decision_ledger(bad)


def test_duplicate_queue_identity_fails_closed():
    bad = _queue()
    item = dict(bad["lanes"][0]["items"][0])
    bad["lanes"][0]["items"].append(item)
    with pytest.raises(TeacherDecisionError, match="duplicate"):
        build_pending_teacher_decision_ledger(bad)
