"""Validate an explicit methodology decision and write a non-mutating receipt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evolutionary_course.methodology_queue import (
    MethodologyQueueError,
    build_candidate_review_request,
    record_candidate_review_decision,
)


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise MethodologyQueueError(f"{label} must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MethodologyQueueError(f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise MethodologyQueueError(f"{label} must be an object")
    return value


def build_receipt(
    candidate: dict[str, Any], catalog: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    decision_input = request.get("decision_input")
    expected_fields = {
        "decision", "reviewer_id", "reviewer_authority", "reviewed_at", "rationale"
    }
    if not isinstance(decision_input, dict) or set(decision_input) != expected_fields:
        raise MethodologyQueueError("decision input fields mismatch")
    unsigned = dict(request)
    unsigned["decision_input"] = {field: None for field in (
        "decision", "reviewer_id", "reviewer_authority", "reviewed_at", "rationale"
    )}
    expected = build_candidate_review_request(candidate, catalog=catalog)
    if unsigned != expected:
        raise MethodologyQueueError("review request binding mismatch")
    return record_candidate_review_decision(
        candidate,
        catalog=catalog,
        decision=decision_input["decision"],
        reviewer_id=decision_input["reviewer_id"],
        reviewer_authority=decision_input["reviewer_authority"],
        reviewed_at=decision_input["reviewed_at"],
        rationale=decision_input["rationale"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("output already exists")
    try:
        receipt = build_receipt(
            _read_object(args.candidate, "candidate"),
            _read_object(args.catalog, "catalog"),
            _read_object(args.request, "request"),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except (MethodologyQueueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "status": "DECISION_RECORDED",
        "decision": receipt["decision"],
        "catalog_mutated": receipt["catalog_mutated"],
        "follow_up_required": receipt["follow_up_required"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
