import copy

import pytest

from bridge_school_api.tournament_committed_teacher_decisions_v3 import (
    CommittedTeacherDecisionError,
    sync_committed_board_decisions_into_portfolio_intake,
)


def _row(bundle, review, board, category):
    return {
        "source_bundle_id": bundle,
        "review_id": review,
        "event_id": "30041",
        "deal_id": f"30041:round-2:{board}",
        "category": category,
        "allowed_statuses": ["CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED", "NEEDS_CONTEXT"],
        "status": None,
        "decision_note": None,
        "decision_actor": None,
        "decision_reference": None,
        "explicit_teacher_decision": False,
    }


def _intake():
    rows = [
        _row("b1", "r14", 14, "contract_result"),
        _row("b1", "r19", 19, "contract_result"),
        _row("b1", "r2-contract", 2, "contract_result"),
        _row("b2", "r2-lead", 2, "opening_lead_dds3"),
    ]
    return {
        "schema": "tournament-teacher-review-portfolio-decision-intake-v1",
        "portfolio_id": "p" * 64,
        "source_bundle_ids": ["b1", "b2"],
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "required_decision_source": "EXPLICIT_TEACHER_DECISION",
        "decision_count": len(rows),
        "decisions": rows,
    }


def _record(board, decision, statement):
    return {
        "schema": "bridge-school-teacher-decision-v1",
        "event_id": "30041",
        "session_id": "round-2",
        "board_number": board,
        "decision": decision,
        "teacher_statement": statement,
        "scope": "board-level teacher adjudication",
        "student_error_attribution": None,
        "specific_card_or_cause_attribution": None,
        "methodology_mapping": None,
        "provenance": {
            "source": "interactive teacher review in ChatGPT",
            "recorded_date": "2026-08-25",
        },
    }


def test_syncs_unique_confirmed_error_and_needs_context_without_causal_inference():
    intake = _intake()
    original = copy.deepcopy(intake)
    records = [
        _record(19, "ERROR_CONFIRMED_BY_TEACHER", "ошибка"),
        _record(14, "NEEDS_CONTEXT", "нужно разобраться"),
    ]
    out = sync_committed_board_decisions_into_portfolio_intake(
        intake,
        records,
        decision_actor="teacher:interactive-chatgpt",
        decision_references=["data/board19.json", "data/board14.json"],
    )
    assert intake == original
    assert out["applied_count"] == 2
    assert out["blocked_count"] == 0
    by_review = {row["review_id"]: row for row in out["intake"]["decisions"]}
    assert by_review["r19"]["status"] == "CONFIRMED_TECHNICAL_RELEVANCE"
    assert by_review["r14"]["status"] == "NEEDS_CONTEXT"
    assert by_review["r19"]["explicit_teacher_decision"] is True
    assert by_review["r19"]["decision_actor"] == "teacher:interactive-chatgpt"
    assert "no specific technical cause" in by_review["r19"]["decision_note"]
    assert out["automatic_methodology_mapping_allowed"] is False
    assert out["automatic_student_error_attribution_allowed"] is False


def test_multisignal_board_fails_closed_without_review_binding():
    out = sync_committed_board_decisions_into_portfolio_intake(
        _intake(),
        [_record(2, "ERROR_CONFIRMED_BY_TEACHER", "ошибка")],
        decision_actor="teacher",
    )
    assert out["applied_count"] == 0
    assert out["blocked_count"] == 1
    assert out["blocked"][0]["reason"] == "AMBIGUOUS_MULTI_SIGNAL_BOARD_REQUIRES_EXACT_REVIEW_BINDING"
    assert out["blocked"][0]["matching_review_count"] == 2
    assert all(row["status"] is None for row in out["intake"]["decisions"])


def test_exact_review_binding_can_resolve_multisignal_board_without_touching_sibling_signal():
    record = _record(2, "NO_ERROR_CONFIRMED_BY_TEACHER", "не ошибка")
    record["review_id"] = "r2-lead"
    record["source_bundle_id"] = "b2"
    out = sync_committed_board_decisions_into_portfolio_intake(
        _intake(),
        [record],
        decision_actor="teacher",
    )
    assert out["applied_count"] == 1
    by_review = {row["review_id"]: row for row in out["intake"]["decisions"]}
    assert by_review["r2-lead"]["status"] == "DISMISSED"
    assert by_review["r2-contract"]["status"] is None


def test_rejects_untrusted_provenance_and_weakened_boundaries():
    record = _record(19, "ERROR_CONFIRMED_BY_TEACHER", "ошибка")
    record["provenance"]["source"] = "automatic model guess"
    with pytest.raises(CommittedTeacherDecisionError):
        sync_committed_board_decisions_into_portfolio_intake(
            _intake(), [record], decision_actor="teacher"
        )

    intake = _intake()
    intake["automatic_student_error_attribution_allowed"] = True
    with pytest.raises(CommittedTeacherDecisionError):
        sync_committed_board_decisions_into_portfolio_intake(
            intake, [_record(19, "ERROR_CONFIRMED_BY_TEACHER", "ошибка")], decision_actor="teacher"
        )
