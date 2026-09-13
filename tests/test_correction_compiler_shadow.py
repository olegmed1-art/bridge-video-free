#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy

from tools.correction_compiler_shadow import compile_snapshot


def evidence(evidence_id: str, quality_status: str = "verified") -> dict:
    return {
        "evidence_id": evidence_id,
        "quality_status": quality_status,
        "confidence_class": "HIGH",
    }


def correction(correction_id: str = "c-1") -> dict:
    return {
        "correction_record_id": correction_id,
        "school_id": "school-1",
        "target_entity_id": "target-1",
        "target_entity_type": "decision",
        "correction_class": "analysis",
        "summary": "Verified technical correction",
        "details": {
            "target_component": "tournament_analysis",
            "test_reference": "fixture://board/1",
            "expected_contract": {"must_preserve_equal_optimal_moves": True},
        },
        "severity": "medium",
        "material": False,
        "regression_required": True,
        "protected_methodology": False,
        "teacher_approval_state": "not_required",
        "evidence_ids": ["e-1"],
        "status": "confirmed",
    }


def expect_single_candidate() -> None:
    snapshot = {"corrections": [correction()], "evidence": [evidence("e-1")]}
    result = compile_snapshot(snapshot)
    assert result["mode"] == "SHADOW"
    assert result["production_write"] is False
    assert result["candidate_count"] == 1
    assert result["skipped_count"] == 0
    candidate = result["candidates"][0]
    assert candidate["status"] == "candidate"
    assert candidate["provenance"]["shadow_only"] is True
    assert candidate["provenance"]["evidence_ids"] == ["e-1"]
    assert candidate["stable_key"].startswith("correction-v1:")


def expect_fail_closed_gates() -> None:
    base = correction()

    cases: list[tuple[dict, list[dict], str]] = []

    item = deepcopy(base)
    item["status"] = "observed"
    cases.append((item, [evidence("e-1")], "ineligible_status"))

    item = deepcopy(base)
    item["regression_required"] = False
    cases.append((item, [evidence("e-1")], "regression_not_required"))

    item = deepcopy(base)
    item["evidence_ids"] = []
    cases.append((item, [], "missing_evidence_ids"))

    item = deepcopy(base)
    cases.append((item, [], "missing_evidence_record"))

    item = deepcopy(base)
    cases.append((item, [evidence("e-1", "quarantined")], "blocked_evidence_quality"))

    item = deepcopy(base)
    item["correction_class"] = "methodology"
    item["protected_methodology"] = True
    item["teacher_approval_state"] = "pending"
    cases.append((item, [evidence("e-1")], "teacher_approval_required"))

    item = deepcopy(base)
    item["severity"] = "high"
    item["teacher_approval_state"] = "not_required"
    cases.append((item, [evidence("e-1")], "teacher_approval_required"))

    item = deepcopy(base)
    del item["details"]["expected_contract"]
    cases.append((item, [evidence("e-1")], "missing_expected_contract"))

    item = deepcopy(base)
    del item["details"]["target_component"]
    cases.append((item, [evidence("e-1")], "missing_target_component"))

    item = deepcopy(base)
    del item["details"]["test_reference"]
    cases.append((item, [evidence("e-1")], "missing_test_reference"))

    for item, evidence_rows, expected_reason in cases:
        result = compile_snapshot({"corrections": [item], "evidence": evidence_rows})
        assert result["candidate_count"] == 0, expected_reason
        assert result["skipped_count"] == 1, expected_reason
        assert result["skipped"][0]["reason"].startswith(expected_reason), result


def expect_determinism_and_deduplication() -> None:
    first = correction("c-1")
    second = correction("c-1")
    snapshot = {"corrections": [first, second], "evidence": [evidence("e-1")]}
    result1 = compile_snapshot(snapshot)
    result2 = compile_snapshot(deepcopy(snapshot))
    assert result1 == result2
    assert result1["candidate_count"] == 1
    assert result1["skipped_count"] == 1
    assert result1["skipped"][0]["reason"].startswith("deterministic_duplicate")


def expect_empty_production_snapshot_is_no_change() -> None:
    result = compile_snapshot({"corrections": [], "evidence": []})
    assert result["input_corrections"] == 0
    assert result["candidate_count"] == 0
    assert result["skipped_count"] == 0
    assert result["production_write"] is False


def main() -> None:
    expect_single_candidate()
    expect_fail_closed_gates()
    expect_determinism_and_deduplication()
    expect_empty_production_snapshot_is_no_change()
    print("CORRECTION_COMPILER_SHADOW: PASS")


if __name__ == "__main__":
    main()
