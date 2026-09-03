"""Convert source-bound video observations into canon-review staging records.

This module is deliberately a one-way, non-promoting adapter.  It produces a
payload suitable for ``public.analysis_candidate`` but has no database writer
and cannot create or activate ``bidding.rule`` rows.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping

from bridge_contracts.video_learning_candidate import (
    canonical_sha256 as learning_candidate_sha256,
    validate_learning_candidate,
)


SCHEMA = "video-canon-evidence-v1"
AUTHORITY_CLASS = "SCHOOL_CANON_CANDIDATE"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_CLASSES = {"SCHOOL_PRIMARY_EVIDENCE", "TEACHING_CONTEXT", "WORLD_EXTERNAL"}
_FORBIDDEN_KEYS = {
    "partner_hand", "opponent_hand", "opponent_hands", "north_hand",
    "east_hand", "south_hand", "west_hand", "full_deal", "hidden_cards",
    "actual_partner_hand", "actual_opponent_hand", "actual_opponent_hands",
    "partner_cards", "opponent_cards", "all_hands",
}


class VideoCanonEvidenceError(ValueError):
    """The video evidence is unsafe, incomplete or authority-escalating."""


def _fail(message: str) -> None:
    raise VideoCanonEvidenceError(message)


def _text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        _fail(f"{label} required")
    return result


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("invalid semantic confidence")
    result = float(value)
    if not 0 <= result <= 1:
        _fail("invalid semantic confidence")
    return result


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in _FORBIDDEN_KEYS or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_video_canon_candidate(
    learning_candidate: Mapping[str, Any],
    assertion: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an immutable staging record from a validated video observation.

    ``assertion`` must be an explicit, locally evidenced teacher statement.  A
    teaching example or model inference cannot be relabelled as a statement.
    Source authorization only controls review eligibility; even an eligible
    record remains a candidate and requires the separate Canon approval gates.
    """
    learning = validate_learning_candidate(learning_candidate)
    expected = {
        "assertion_id", "statement", "speaker_id", "transcript_locators",
        "source_class", "source_authorization", "semantic_scope",
        "normalized_rule", "semantic_confidence", "ambiguities",
        "contradictions", "tests",
    }
    if not isinstance(assertion, Mapping) or set(assertion) != expected:
        _fail("assertion fields mismatch")

    assertion_id = _text(assertion.get("assertion_id"), "assertion_id")
    statement = _text(assertion.get("statement"), "statement")
    speaker_id = _text(assertion.get("speaker_id"), "speaker_id")
    source_class = assertion.get("source_class")
    if source_class not in _SOURCE_CLASSES:
        _fail("invalid source class")

    locators = assertion.get("transcript_locators")
    if not isinstance(locators, list) or not locators:
        _fail("transcript locators required")
    locators = [_text(value, "transcript locator") for value in locators]
    if len(locators) != len(set(locators)):
        _fail("duplicate transcript locator")
    transcript_by_locator = {
        row["locator"]: row for row in learning["transcript_evidence"]
    }
    if not set(locators) <= set(transcript_by_locator):
        _fail("assertion references evidence outside learning candidate")
    for locator in locators:
        transcript = transcript_by_locator[locator]
        if transcript["speaker_identity_status"] != "VERIFIED":
            _fail("teacher assertion requires verified speaker identity")
        if transcript["speaker_id"] != speaker_id:
            _fail("teacher assertion speaker mismatch")

    authorization = assertion.get("source_authorization")
    if not isinstance(authorization, Mapping) or set(authorization) != {
        "status", "decision_ref", "approved_semantic_scopes"
    }:
        _fail("source authorization fields mismatch")
    status = authorization.get("status")
    if status not in {"APPROVED", "NOT_APPROVED"}:
        _fail("invalid source authorization status")
    decision_ref = str(authorization.get("decision_ref") or "").strip()
    scopes = authorization.get("approved_semantic_scopes")
    if not isinstance(scopes, list):
        _fail("approved semantic scopes must be a list")
    scopes = [_text(value, "approved semantic scope") for value in scopes]
    if len(scopes) != len(set(scopes)):
        _fail("duplicate approved semantic scope")
    semantic_scope = _text(assertion.get("semantic_scope"), "semantic_scope")
    if status == "APPROVED" and (not decision_ref or semantic_scope not in scopes):
        _fail("approved source lacks exact semantic scope authorization")
    if status == "NOT_APPROVED" and (decision_ref or scopes):
        _fail("unapproved source must not carry approval evidence")

    normalized_rule = assertion.get("normalized_rule")
    if not isinstance(normalized_rule, Mapping) or not normalized_rule:
        _fail("normalized rule required")
    if _has_forbidden_key(normalized_rule):
        _fail("normalized rule contains hidden information")

    tests = assertion.get("tests")
    if not isinstance(tests, Mapping) or set(tests) != {
        "positive", "negative", "boundary", "interference"
    }:
        _fail("tests fields mismatch")
    if any(not isinstance(tests[kind], list) or not tests[kind] for kind in tests):
        _fail("all four test classes are required")
    if _has_forbidden_key(tests):
        _fail("tests contain hidden information")

    ambiguities = assertion.get("ambiguities")
    contradictions = assertion.get("contradictions")
    if not isinstance(ambiguities, list) or not isinstance(contradictions, list):
        _fail("ambiguities and contradictions must be lists")
    ambiguities = [_text(value, "ambiguity") for value in ambiguities]
    contradictions = [_text(value, "contradiction") for value in contradictions]
    confidence = _confidence(assertion.get("semantic_confidence"))

    review_eligible = (
        source_class == "SCHOOL_PRIMARY_EVIDENCE"
        and status == "APPROVED"
        and not ambiguities
        and not contradictions
    )
    payload = {
        "schema": SCHEMA,
        "authority_class": AUTHORITY_CLASS,
        "candidate_id": assertion_id,
        "source": deepcopy(learning["source"]),
        "observed_episode": deepcopy(learning["observed_episode"]),
        "learning_candidate_id": learning["candidate_id"],
        "learning_candidate_sha256": learning_candidate_sha256(learning),
        "teacher_assertion": {
            "statement": statement,
            "speaker_id": speaker_id,
            "transcript_locators": locators,
        },
        "source_class": source_class,
        "source_authorization": {
            "status": status,
            "decision_ref": decision_ref or None,
            "approved_semantic_scopes": scopes,
        },
        "semantic_scope": semantic_scope,
        "normalized_rule": deepcopy(dict(normalized_rule)),
        "semantic_confidence": confidence,
        "ambiguities": ambiguities,
        "contradictions": contradictions,
        "tests": deepcopy(dict(tests)),
        "review_eligibility": "ELIGIBLE" if review_eligible else "EVIDENCE_ONLY",
        "activation": {
            "school_canon_write_allowed": False,
            "approval_required": True,
            "regression_required": True,
            "integrity_required": True,
            "rollback_proof_required": True,
            "i2_review_required": True,
        },
    }
    payload_hash = _digest(payload)
    return {
        "candidate_type": "video_school_canon_candidate",
        "stable_key": assertion_id,
        "quality_status": payload["review_eligibility"],
        "promotion_status": "STAGING_ONLY",
        "payload": payload,
        "payload_hash": payload_hash,
        "evidence_refs": locators,
        "method_version": SCHEMA,
        "authoritative_tables_modified": False,
    }


__all__ = [
    "AUTHORITY_CLASS", "SCHEMA", "VideoCanonEvidenceError",
    "build_video_canon_candidate",
]
