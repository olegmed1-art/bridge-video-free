#!/usr/bin/env python3
from __future__ import annotations

from tools.decision_readiness_shadow import audit_snapshot


def verified_evidence(evidence_id: str = "e-1") -> dict:
    return {
        "evidence_id": evidence_id,
        "quality_status": "verified",
        "confidence_class": "HIGH",
    }


def ready_decision() -> dict:
    return {
        "decision_id": "d-ready",
        "actor_person_id": "person-1",
        "student_id": "student-1",
        "deal_id": "deal-1",
        "evidence_ids": ["e-1"],
        "action_taken": {
            "status": "observed_choice",
            "text": "1♠",
            "actor_attribution_status": "verified",
        },
        "available_information": {},
    }


def current_like_decision() -> dict:
    return {
        "decision_id": "d-current",
        "actor_person_id": None,
        "student_id": None,
        "deal_id": None,
        "evidence_ids": ["e-2"],
        "action_taken": {
            "status": "observed_text",
            "text": "контра",
            "actor_attribution_status": "unavailable_without_speaker_labels",
        },
        "available_information": {"context_excerpt": "teacher explanation text"},
    }


def expect_ready_case() -> None:
    result = audit_snapshot({"decisions": [ready_decision()], "evidence": [verified_evidence()]})
    assert result["production_write"] is False
    assert result["formal_assessment_ready"] == 1
    assert result["student_transfer_ready"] == 1
    row = result["rows"][0]
    assert row["formal_assessment_state"] == "READY"
    assert row["student_transfer_state"] == "READY"
    assert row["correctness_label"] is None


def expect_current_like_case_blocks_without_guessing() -> None:
    evidence = {
        "evidence_id": "e-2",
        "quality_status": "accepted",
        "confidence_class": "UNKNOWN",
    }
    result = audit_snapshot({"decisions": [current_like_decision()], "evidence": [evidence]})
    assert result["formal_assessment_ready"] == 0
    assert result["student_transfer_ready"] == 0
    row = result["rows"][0]
    blockers = set(row["formal_assessment_blockers"])
    assert "not_observed_choice:observed_text" in blockers
    assert "no_high_confidence_verified_evidence" in blockers
    assert "actor_unresolved" in blockers
    assert "position_or_deal_unbound" in blockers
    assert row["correctness_label"] is None


def expect_missing_evidence_fails_closed() -> None:
    decision = ready_decision()
    result = audit_snapshot({"decisions": [decision], "evidence": []})
    row = result["rows"][0]
    assert row["formal_assessment_state"] == "BLOCKED"
    assert any(x.startswith("evidence_record_missing:") for x in row["formal_assessment_blockers"])
    assert "no_high_confidence_verified_evidence" in row["formal_assessment_blockers"]


def expect_quarantined_evidence_fails_closed() -> None:
    decision = ready_decision()
    evidence = verified_evidence()
    evidence["quality_status"] = "quarantined"
    result = audit_snapshot({"decisions": [decision], "evidence": [evidence]})
    row = result["rows"][0]
    assert row["formal_assessment_state"] == "BLOCKED"
    assert any(x.startswith("evidence_blocked:") for x in row["formal_assessment_blockers"])
    assert "no_high_confidence_verified_evidence" in row["formal_assessment_blockers"]


def expect_verified_position_binding_can_replace_deal_id() -> None:
    decision = ready_decision()
    decision["deal_id"] = None
    decision["available_information"] = {"position_binding_status": "verified"}
    result = audit_snapshot({"decisions": [decision], "evidence": [verified_evidence()]})
    assert result["formal_assessment_ready"] == 1


def main() -> None:
    expect_ready_case()
    expect_current_like_case_blocks_without_guessing()
    expect_missing_evidence_fails_closed()
    expect_quarantined_evidence_fails_closed()
    expect_verified_position_binding_can_replace_deal_id()
    print("DECISION_READINESS_SHADOW: PASS")


if __name__ == "__main__":
    main()
