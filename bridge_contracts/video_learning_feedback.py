"""Fail-closed learning feedback for the video analyzer.

It creates versioned training examples and an improvement proposal only.  It
never trains, deploys, activates a model, or modifies Canon.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SCHEMA = "video-analyzer-learning-feedback-v1"
_KINDS = {"ASR", "SPEAKER", "CARD", "AUCTION", "EXTRACTION", "PEDAGOGY"}


class VideoLearningFeedbackError(ValueError):
    pass


def _text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise VideoLearningFeedbackError(f"{label} required")
    return result


def _sha(value: object) -> str:
    value = _text(value, "source_sha256").lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise VideoLearningFeedbackError("invalid source_sha256")
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_learning_feedback(master: Mapping[str, Any], quality: Mapping[str, Any]) -> dict[str, Any]:
    """Create immutable example candidates and a holdout-gated proposal."""
    source = master.get("source") if isinstance(master.get("source"), Mapping) else {}
    corrections = master.get("human_corrections") or []
    if not isinstance(corrections, list):
        raise VideoLearningFeedbackError("human_corrections must be a list")
    source_sha = None
    if corrections:
        source_sha = _sha(source.get("sha256") or source.get("source_sha256"))
    examples: list[dict[str, Any]] = []
    for raw in corrections:
        if not isinstance(raw, Mapping) or set(raw) != {
            "correction_id", "kind", "input_ref", "corrected_value", "reviewer_ref", "evidence_refs"
        }:
            raise VideoLearningFeedbackError("correction fields mismatch")
        kind = _text(raw.get("kind"), "correction kind").upper()
        if kind not in _KINDS:
            raise VideoLearningFeedbackError("unsupported correction kind")
        refs = raw.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise VideoLearningFeedbackError("correction evidence refs required")
        payload = {
            "training_example_id": f"train_{_digest([source_sha, raw['correction_id']])[:20]}",
            "source_sha256": source_sha,
            "kind": kind,
            "input_ref": _text(raw.get("input_ref"), "input_ref"),
            "corrected_value": raw.get("corrected_value"),
            "reviewer_ref": _text(raw.get("reviewer_ref"), "reviewer_ref"),
            "evidence_refs": [str(ref) for ref in refs],
            "training_eligible": True,
            "canon_write_allowed": False,
        }
        examples.append(payload)

    evaluation = master.get("model_evaluation") if isinstance(master.get("model_evaluation"), Mapping) else {}
    required = {"candidate_model_version", "baseline_model_version", "holdout_id", "metrics", "rollback_model_version"}
    proposal: dict[str, Any]
    if set(evaluation) == required and isinstance(evaluation.get("metrics"), Mapping):
        metrics = evaluation["metrics"]
        try:
            improved = all(float(row["candidate"]) >= float(row["baseline"]) for row in metrics.values() if isinstance(row, Mapping))
        except (KeyError, TypeError, ValueError):
            improved = False
        proposal = {
            "status": "HOLDOUT_PASS_CANDIDATE" if improved and metrics else "HOLDOUT_NOT_PROVEN",
            "candidate_model_version": _text(evaluation["candidate_model_version"], "candidate_model_version"),
            "baseline_model_version": _text(evaluation["baseline_model_version"], "baseline_model_version"),
            "holdout_id": _text(evaluation["holdout_id"], "holdout_id"),
            "metrics": dict(metrics),
            "rollback_model_version": _text(evaluation["rollback_model_version"], "rollback_model_version"),
            "deployment_allowed": False,
            "independent_review_required": True,
        }
    else:
        proposal = {
            "status": "HOLDOUT_NOT_PROVEN", "deployment_allowed": False,
            "reason": "versioned holdout evaluation missing", "independent_review_required": True,
        }
    return {
        "schema": SCHEMA,
        "training_examples": examples,
        "model_improvement_proposal": proposal,
        "authority": {
            "training_execution_allowed": False,
            "model_deployment_allowed": False,
            "canon_change_allowed": False,
            "rollback_required": True,
        },
    }


__all__ = ["SCHEMA", "VideoLearningFeedbackError", "build_learning_feedback"]
