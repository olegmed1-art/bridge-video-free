"""Fail-closed learning feedback for the video analyzer.

It creates versioned training examples and an improvement proposal only.  It
never trains, deploys, activates a model, or modifies Canon.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping


SCHEMA = "video-analyzer-learning-feedback-v2"
_KINDS = {"ASR", "SPEAKER", "CARD", "AUCTION", "EXTRACTION", "PEDAGOGY"}


class VideoLearningFeedbackError(ValueError):
    pass


CorrectionReceiptResolver = Callable[[str], Mapping[str, Any] | None]


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


def build_learning_feedback(
    master: Mapping[str, Any],
    quality: Mapping[str, Any],
    *,
    correction_receipt_resolver: CorrectionReceiptResolver | None = None,
) -> dict[str, Any]:
    """Create immutable example candidates and a holdout-gated proposal."""
    source = master.get("source") if isinstance(master.get("source"), Mapping) else {}
    corrections = master.get("human_corrections") or []
    if not isinstance(corrections, list):
        raise VideoLearningFeedbackError("human_corrections must be a list")
    source_sha = None
    if corrections:
        source_sha = _sha(source.get("sha256") or source.get("source_sha256"))
    raw_receipts = quality.get("correction_review_receipts") or []
    if not isinstance(raw_receipts, list):
        raise VideoLearningFeedbackError("correction_review_receipts must be a list")
    if raw_receipts and correction_receipt_resolver is None:
        raise VideoLearningFeedbackError("trusted correction review receipt resolver required")
    receipts: dict[str, Mapping[str, Any]] = {}
    for receipt in raw_receipts:
        fields = {
            "correction_id", "reviewer_ref", "source_sha256", "input_ref",
            "corrected_value_sha256", "evidence_refs", "status", "receipt_sha256",
        }
        if not isinstance(receipt, Mapping) or set(receipt) != fields:
            raise VideoLearningFeedbackError("correction review receipt fields mismatch")
        receipt_id = _text(receipt.get("correction_id"), "receipt correction_id")
        if receipt_id in receipts:
            raise VideoLearningFeedbackError("duplicate correction review receipt")
        sealed = {key: receipt[key] for key in sorted(fields - {"receipt_sha256"})}
        receipt_sha = _sha(receipt.get("receipt_sha256"))
        if receipt_sha != _digest(sealed):
            raise VideoLearningFeedbackError("correction review receipt digest mismatch")
        trusted = correction_receipt_resolver(receipt_sha) if correction_receipt_resolver else None
        if not isinstance(trusted, Mapping) or dict(trusted) != dict(receipt):
            raise VideoLearningFeedbackError("correction review receipt is not in trusted storage")
        receipts[receipt_id] = receipt
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
        evidence_refs = [_text(ref, "correction evidence ref") for ref in refs]
        correction_id = _text(raw.get("correction_id"), "correction_id")
        input_ref = _text(raw.get("input_ref"), "input_ref")
        reviewer_ref = _text(raw.get("reviewer_ref"), "reviewer_ref")
        corrected_value_sha = _digest(raw.get("corrected_value"))
        receipt = receipts.get(correction_id)
        if not receipt or receipt.get("status") != "VERIFIED":
            raise VideoLearningFeedbackError("verified correction review receipt required")
        if (
            _sha(receipt.get("source_sha256")) != source_sha
            or receipt.get("input_ref") != input_ref
            or receipt.get("reviewer_ref") != reviewer_ref
            or _sha(receipt.get("corrected_value_sha256")) != corrected_value_sha
            or receipt.get("evidence_refs") != evidence_refs
        ):
            raise VideoLearningFeedbackError("correction review receipt binding mismatch")
        content_version = _digest({
            "source_sha256": source_sha,
            "correction_id": correction_id,
            "kind": kind,
            "input_ref": input_ref,
            "corrected_value_sha256": corrected_value_sha,
            "reviewer_ref": reviewer_ref,
            "evidence_refs": evidence_refs,
            "receipt_sha256": receipt["receipt_sha256"],
        })
        payload = {
            "training_example_id": f"train_{content_version[:20]}",
            "content_version_sha256": content_version,
            "source_sha256": source_sha,
            "kind": kind,
            "input_ref": input_ref,
            "corrected_value": raw.get("corrected_value"),
            "reviewer_ref": reviewer_ref,
            "review_receipt_sha256": receipt["receipt_sha256"],
            "review_receipt_authentication": "TRUSTED_STORAGE_RESOLVED",
            "evidence_refs": evidence_refs,
            "training_eligible": True,
            "canon_write_allowed": False,
        }
        examples.append(payload)

    evaluation = master.get("model_evaluation") if isinstance(master.get("model_evaluation"), Mapping) else {}
    required = {"candidate_model_version", "baseline_model_version", "holdout_id", "metrics", "rollback_model_version"}
    proposal: dict[str, Any]
    if set(evaluation) == required and isinstance(evaluation.get("metrics"), Mapping):
        metrics = evaluation["metrics"]
        improved = bool(metrics)
        for row in metrics.values():
            if not isinstance(row, Mapping) or set(row) != {
                "candidate", "baseline", "direction", "minimum_delta"
            }:
                improved = False
                break
            try:
                candidate_value = float(row["candidate"])
                baseline_value = float(row["baseline"])
                minimum_delta = float(row["minimum_delta"])
            except (TypeError, ValueError):
                improved = False
                break
            if minimum_delta < 0 or row["direction"] not in {"HIGHER_IS_BETTER", "LOWER_IS_BETTER"}:
                improved = False
                break
            delta = candidate_value - baseline_value
            if row["direction"] == "HIGHER_IS_BETTER":
                improved = improved and delta >= minimum_delta
            else:
                improved = improved and -delta >= minimum_delta
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


__all__ = [
    "SCHEMA", "CorrectionReceiptResolver", "VideoLearningFeedbackError",
    "build_learning_feedback",
]
