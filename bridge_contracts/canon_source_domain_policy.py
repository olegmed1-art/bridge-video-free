"""Machine-enforced Canon source/domain admission policy.

The policy keeps bidding anchored in School primary evidence while allowing
authoritative external material to enrich card play and defense when every
high-confidence safety check passes.  It is an admission gate only: a positive
decision never writes or activates Canon by itself.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

POLICY_VERSION = "canon-source-domain-v1"
MIN_CONFIDENCE = 0.95

SCHOOL_VIDEO_CHECKS = frozenset({
    "asr_verified",
    "slip_checked",
    "interpretation_verified",
    "context_verified",
    "canon_conflict_free",
    "provenance_bound",
})
EXTERNAL_CHECKS = frozenset({
    "source_authoritative",
    "semantic_verified",
    "interpretation_verified",
    "context_verified",
    "canon_conflict_free",
    "provenance_bound",
    "school_method_conflict_free",
})

_ALLOWED = {
    ("BIDDING", "SCHOOL_PRIMARY_EVIDENCE"): SCHOOL_VIDEO_CHECKS,
    ("CARD_PLAY", "SCHOOL_PRIMARY_EVIDENCE"): SCHOOL_VIDEO_CHECKS,
    ("DEFENSE", "SCHOOL_PRIMARY_EVIDENCE"): SCHOOL_VIDEO_CHECKS,
    ("CARD_PLAY", "WORLD_EXTERNAL"): EXTERNAL_CHECKS,
    ("DEFENSE", "WORLD_EXTERNAL"): EXTERNAL_CHECKS,
}
_KNOWN = {
    ("BIDDING", "SCHOOL_PRIMARY_EVIDENCE"),
    ("BIDDING", "WORLD_EXTERNAL"),
    ("CARD_PLAY", "SCHOOL_PRIMARY_EVIDENCE"),
    ("CARD_PLAY", "WORLD_EXTERNAL"),
    ("DEFENSE", "SCHOOL_PRIMARY_EVIDENCE"),
    ("DEFENSE", "WORLD_EXTERNAL"),
}


def evaluate_canon_source_domain_policy(
    domain: str,
    evidence_lane: str,
    confidence: float,
    checks: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a fail-closed, non-mutating admission decision."""
    key = (str(domain or "").strip().upper(), str(evidence_lane or "").strip().upper())
    if key not in _KNOWN:
        return _decision(False, "UNSUPPORTED_DOMAIN_OR_LANE", key, ())
    required = _ALLOWED.get(key)
    if required is None:
        return _decision(False, "SOURCE_LANE_NOT_AUTO_CANON", key, ())
    if type(confidence) not in (int, float) or not math.isfinite(confidence):
        return _decision(False, "INVALID_CONFIDENCE", key, required)
    if not MIN_CONFIDENCE <= float(confidence) <= 1.0:
        return _decision(False, "CONFIDENCE_BELOW_THRESHOLD", key, required)
    if not isinstance(checks, Mapping):
        return _decision(False, "CHECKS_NOT_OBJECT", key, required)
    missing = sorted(name for name in required if checks.get(name) is not True)
    if missing:
        result = _decision(False, "REQUIRED_CHECK_FAILED", key, required)
        result["failed_checks"] = missing
        return result
    return _decision(True, "ELIGIBLE_FOR_CANON_PIPELINE", key, required)


def _decision(
    allowed: bool, reason: str, key: tuple[str, str], required: object
) -> dict[str, Any]:
    return {
        "schema": "canon-source-domain-decision-v1",
        "policy_version": POLICY_VERSION,
        "domain": key[0],
        "evidence_lane": key[1],
        "min_confidence": MIN_CONFIDENCE,
        "auto_canon_allowed": allowed,
        "reason": reason,
        "required_checks": sorted(required),
        "canon_mutation_performed": False,
    }


__all__ = [
    "EXTERNAL_CHECKS",
    "MIN_CONFIDENCE",
    "POLICY_VERSION",
    "SCHOOL_VIDEO_CHECKS",
    "evaluate_canon_source_domain_policy",
]
