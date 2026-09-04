"""Fail-closed AI-only promotion gate for teacher-video Canon knowledge.

This module emits a sealed, idempotent activation command.  A database writer
may execute it only through the corresponding guarded SQL promotion function.
No per-rule human approval is part of the contract.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID


SCHEMA = "video-canon-ai-promotion-v1"
POLICY = "school-video-auto-canon-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ASSURANCE = {"I0", "I1", "I2", "I3"}
REQUIRED_CHECKS = frozenset({
    "SOURCE_AUTHORITY",
    "SOURCE_BINDING",
    "SPEAKER_IDENTITY",
    "TRANSCRIPT_BINDING",
    "SEMANTIC_PARSE",
    "EXPLANATION_COMPLETENESS",
    "BRIDGE_LOGIC",
    "HIDDEN_INFORMATION_FIREWALL",
    "POSITIVE_TESTS",
    "NEGATIVE_TESTS",
    "BOUNDARY_TESTS",
    "INTERFERENCE_TESTS",
    "CANON_REGRESSION",
    "CANON_INTEGRITY",
    "CANON_CONFLICT_SCAN",
    "ROLLBACK_RESTORE",
})
STATE_DEPENDENT_CHECKS = frozenset({
    "CANON_REGRESSION", "CANON_INTEGRITY", "CANON_CONFLICT_SCAN", "ROLLBACK_RESTORE",
})


class VideoCanonAIPromotionError(ValueError):
    """Candidate is not safe to promote automatically."""


def _fail(message: str) -> None:
    raise VideoCanonAIPromotionError(message)


def _text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        _fail(f"{label} required")
    return result


def _sha(value: Any, label: str) -> str:
    result = str(value or "").strip().lower()
    if not _SHA256.fullmatch(result):
        _fail(f"invalid {label}")
    return result


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _optional_uuid(value: Any, label: str) -> str | None:
    if value is None:
        return None
    try:
        result = str(UUID(_text(value, label)))
    except ValueError:
        _fail(f"invalid {label}")
    return result


def _timestamp(value: Any, label: str) -> tuple[str, datetime]:
    raw = _text(value, label)
    normalized = f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except (ValueError, OverflowError):
        _fail(f"invalid {label} timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(f"{label} timestamp must include a UTC offset")
    return parsed.isoformat(), parsed


def build_ai_canon_promotion(
    candidate: Mapping[str, Any], verification_bundle: Mapping[str, Any]
) -> dict[str, Any]:
    """Return ``AUTO_PROMOTION_READY`` only when every AI gate is proven."""
    if not isinstance(candidate, Mapping):
        _fail("candidate must be an object")
    payload = candidate.get("payload")
    if not isinstance(payload, Mapping) or payload.get("schema") != "video-canon-evidence-v2":
        _fail("unsupported candidate schema")
    if candidate.get("candidate_type") != "video_school_canon_candidate":
        _fail("candidate type mismatch")
    candidate_hash = _sha(candidate.get("payload_hash"), "candidate payload_hash")
    if _digest(payload) != candidate_hash:
        _fail("candidate payload hash mismatch")
    if payload.get("review_eligibility") != "AI_VERIFICATION_PENDING":
        _fail("candidate is not eligible for AI verification")
    if payload.get("source_class") != "SCHOOL_PRIMARY_EVIDENCE":
        _fail("only School primary evidence may auto-promote")
    ambiguities = payload.get("ambiguities")
    contradictions = payload.get("contradictions")
    if not isinstance(ambiguities, list) or not isinstance(contradictions, list):
        _fail("ambiguities and contradictions must be explicit arrays")
    if ambiguities or contradictions:
        _fail("ambiguous or conflicting evidence cannot auto-promote")
    semantic_confidence = payload.get("semantic_confidence")
    if (
        type(semantic_confidence) not in (int, float)
        or not 0.95 <= semantic_confidence <= 1.0
        or not math.isfinite(semantic_confidence)
    ):
        _fail("semantic confidence must be a finite JSON number in [0.95, 1]")

    expected = {
        "schema", "policy_version", "candidate_payload_hash", "system_profile",
        "learner_level", "effective_period", "activation_scope", "canon_snapshot_sha256",
        "rule_test_state_sha256", "checks", "rollback",
    }
    if not isinstance(verification_bundle, Mapping) or set(verification_bundle) != expected:
        _fail("verification bundle fields mismatch")
    if verification_bundle.get("schema") != SCHEMA:
        _fail("verification schema mismatch")
    if verification_bundle.get("policy_version") != POLICY:
        _fail("promotion policy mismatch")
    if _sha(verification_bundle.get("candidate_payload_hash"), "candidate_payload_hash") != candidate_hash:
        _fail("verification is not bound to candidate")
    if (
        _text(payload.get("system_profile"), "candidate system_profile")
        != _text(verification_bundle.get("system_profile"), "system_profile")
        or _text(payload.get("learner_level"), "candidate learner_level")
        != _text(verification_bundle.get("learner_level"), "learner_level")
    ):
        _fail("candidate profile or learner level mismatch")
    authorization = payload.get("source_authorization") or {}
    if authorization.get("policy_version") != POLICY:
        _fail("source authorization policy mismatch")

    canon_snapshot_sha = _sha(
        verification_bundle.get("canon_snapshot_sha256"), "canon_snapshot_sha256"
    )
    rule_test_state_sha = _sha(
        verification_bundle.get("rule_test_state_sha256"), "rule_test_state_sha256"
    )
    checks = verification_bundle.get("checks")
    if not isinstance(checks, list):
        _fail("checks must be a list")
    normalized: dict[str, dict[str, Any]] = {}
    for row in checks:
        fields = {
            "check_id", "result", "verifier_family", "verifier_version",
            "execution_principal", "assurance_level", "evidence_sha256",
            "canon_snapshot_sha256",
        }
        if not isinstance(row, Mapping) or set(row) != fields:
            _fail("verification check fields mismatch")
        check_id = _text(row.get("check_id"), "check_id")
        if check_id in normalized:
            _fail("duplicate verification check")
        if row.get("result") != "PASS":
            _fail(f"verification check did not pass: {check_id}")
        assurance = row.get("assurance_level")
        if assurance not in _ASSURANCE:
            _fail("invalid assurance level")
        check_snapshot = row.get("canon_snapshot_sha256")
        if check_id in STATE_DEPENDENT_CHECKS:
            if _sha(check_snapshot, "check canon_snapshot_sha256") != canon_snapshot_sha:
                _fail(f"state-dependent check is stale: {check_id}")
            check_snapshot = canon_snapshot_sha
        elif check_snapshot is not None:
            _fail(f"stateless check must not claim Canon snapshot: {check_id}")
        normalized[check_id] = {
            "check_id": check_id,
            "result": "PASS",
            "verifier_family": _text(row.get("verifier_family"), "verifier_family"),
            "verifier_version": _text(row.get("verifier_version"), "verifier_version"),
            "execution_principal": _text(row.get("execution_principal"), "execution_principal"),
            "assurance_level": assurance,
            "evidence_sha256": _sha(row.get("evidence_sha256"), "evidence_sha256"),
            "canon_snapshot_sha256": check_snapshot,
        }
    if set(normalized) != REQUIRED_CHECKS:
        missing = sorted(REQUIRED_CHECKS - set(normalized))
        extra = sorted(set(normalized) - REQUIRED_CHECKS)
        _fail(f"verification check set mismatch: missing={missing}, extra={extra}")

    semantic = normalized["SEMANTIC_PARSE"]
    bridge_logic = normalized["BRIDGE_LOGIC"]
    if semantic["assurance_level"] not in {"I2", "I3"}:
        _fail("semantic verification requires I2 or I3")
    if bridge_logic["assurance_level"] not in {"I2", "I3"}:
        _fail("bridge logic verification requires I2 or I3")
    if semantic["verifier_family"] == bridge_logic["verifier_family"]:
        _fail("semantic and bridge verifiers must be independent")
    firewall = normalized["HIDDEN_INFORMATION_FIREWALL"]
    if firewall["assurance_level"] not in {"I2", "I3"}:
        _fail("hidden-information firewall requires I2 or I3")
    if len({
        semantic["execution_principal"], bridge_logic["execution_principal"],
        firewall["execution_principal"],
    }) != 3:
        _fail("semantic, bridge and firewall executions must be independent")

    period = verification_bundle.get("effective_period")
    if not isinstance(period, Mapping) or set(period) != {"valid_from", "valid_to"}:
        _fail("effective period fields mismatch")
    valid_from, valid_from_timestamp = _timestamp(period.get("valid_from"), "valid_from")
    valid_to = period.get("valid_to")
    if valid_to is not None:
        valid_to, valid_to_timestamp = _timestamp(valid_to, "valid_to")
        if valid_to_timestamp <= valid_from_timestamp:
            _fail("valid_to must be after valid_from")

    rollback = verification_bundle.get("rollback")
    if not isinstance(rollback, Mapping) or set(rollback) != {
        "strategy", "target_knowledge_version_id", "target_canon_activation_id",
        "restore_test_sha256", "result"
    }:
        _fail("rollback fields mismatch")
    if rollback.get("result") != "PASS":
        _fail("rollback restore test did not pass")
    normalized_rollback = {
        "strategy": _text(rollback.get("strategy"), "rollback strategy"),
        "target_knowledge_version_id": _optional_uuid(
            rollback.get("target_knowledge_version_id"), "rollback target_knowledge_version_id"
        ),
        "target_canon_activation_id": _optional_uuid(
            rollback.get("target_canon_activation_id"), "rollback target_canon_activation_id"
        ),
        "restore_test_sha256": _sha(rollback.get("restore_test_sha256"), "restore_test_sha256"),
        "result": "PASS",
    }

    sealed_bundle = {
        "schema": SCHEMA,
        "policy_version": POLICY,
        "candidate_payload_hash": candidate_hash,
        "candidate_payload": json.loads(_canonical_json(payload)),
        "system_profile": _text(verification_bundle.get("system_profile"), "system_profile"),
        "learner_level": _text(verification_bundle.get("learner_level"), "learner_level"),
        "effective_period": {"valid_from": valid_from, "valid_to": valid_to},
        "activation_scope": _text(verification_bundle.get("activation_scope"), "activation_scope"),
        "canon_snapshot_sha256": canon_snapshot_sha,
        "rule_test_state_sha256": rule_test_state_sha,
        "checks": [normalized[key] for key in sorted(normalized)],
        "rollback": normalized_rollback,
    }
    bundle_canonical_json = _canonical_json(sealed_bundle)
    bundle_hash = hashlib.sha256(bundle_canonical_json.encode("utf-8")).hexdigest()
    return {
        "schema": SCHEMA,
        "status": "AUTO_PROMOTION_READY",
        "authority_class": "SCHOOL_CANON",
        "candidate_id": payload.get("candidate_id"),
        "candidate_payload_hash": candidate_hash,
        "verification_bundle": sealed_bundle,
        "verification_bundle_canonical_json": bundle_canonical_json,
        "verification_bundle_sha256": bundle_hash,
        "human_approval_required": False,
        "promotion_command": {
            "operation": "ACTIVATE_AI_VERIFIED_VIDEO_CANON",
            "idempotency_key": f"video-canon:{candidate_hash}:{bundle_hash}",
            "activation_scope": sealed_bundle["activation_scope"],
            "expected_candidate_payload_hash": candidate_hash,
            "expected_verification_bundle_sha256": bundle_hash,
            "expected_rule_test_state_sha256": rule_test_state_sha,
        },
        "safety": {
            "world_evidence_used": False,
            "world_to_canon_promotion_allowed": False,
            "activation_on_conflict_allowed": False,
            "activation_on_gap_allowed": False,
            "rollback_required": True,
        },
    }


__all__ = [
    "POLICY", "REQUIRED_CHECKS", "SCHEMA", "STATE_DEPENDENT_CHECKS",
    "VideoCanonAIPromotionError",
    "build_ai_canon_promotion",
]
