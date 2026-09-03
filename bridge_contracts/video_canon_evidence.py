"""Convert source-bound teacher video into AI-verifiable Canon candidates.

The adapter does not itself write authoritative tables.  It seals the exact
source, transcript assertion, teaching logic and tests that a separate guarded
AI promotion gate must verify before automatic Canon activation.
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


SCHEMA = "video-canon-evidence-v2"
AUTHORITY_CLASS = "SCHOOL_CANON_CANDIDATE"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_CLASSES = {"SCHOOL_PRIMARY_EVIDENCE", "TEACHING_CONTEXT", "WORLD_EXTERNAL"}
_FORBIDDEN_KEYS = {
    "partner_hand", "opponent_hand", "opponent_hands", "north_hand",
    "east_hand", "south_hand", "west_hand", "full_deal", "hidden_cards",
    "actual_partner_hand", "actual_opponent_hand", "actual_opponent_hands",
    "partner_cards", "opponent_cards", "all_hands",
}
_PBN_DEAL = re.compile(
    r"(?:^|\s)[NESW]\s*:\s*[-AKQJT2-9]{0,13}\."
    r"[-AKQJT2-9]{0,13}\.[-AKQJT2-9]{0,13}\.[-AKQJT2-9]{0,13}",
    re.IGNORECASE,
)
_LABELLED_HIDDEN_CARDS = re.compile(
    r"(?:partner|opponent|north|east|south|west)[ _-]*(?:hand|cards)\s*[:=]\s*[-AKQJT2-9.]"
    r"|(?:рука|карты)\s+(?:партн[её]ра|соперника)\s*[:=]\s*[-AKQJT2-9.]",
    re.IGNORECASE,
)
_NORMALIZED_RULE_FIELDS = {
    "rule_key", "rule_kind", "auction_pattern", "hand_constraints",
    "public_context_constraints", "action", "meaning", "public_inference",
    "alert_semantics", "forcing_semantics", "priority", "specificity",
    "condition_schema_version", "compiled_payload", "method_version",
}
_RULE_JSON_FIELDS = {
    "auction_pattern", "hand_constraints", "public_context_constraints", "action",
    "meaning", "public_inference", "alert_semantics", "forcing_semantics",
    "compiled_payload",
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
    if isinstance(value, (list, tuple)):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _has_forbidden_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_has_forbidden_value(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_forbidden_value(child) for child in value)
    if isinstance(value, str):
        return bool(_PBN_DEAL.search(value) or _LABELLED_HIDDEN_CARDS.search(value))
    return False


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha(value: Any, label: str) -> str:
    result = str(value or "").strip().lower()
    if not _SHA256.fullmatch(result):
        _fail(f"invalid {label}")
    return result


def _texts(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        _fail(f"{label} must be a list")
    result = [_text(item, label) for item in value]
    if len(result) != len(set(result)):
        _fail(f"duplicate {label}")
    return result


def build_video_canon_candidate(
    learning_candidate: Mapping[str, Any],
    assertion: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an immutable staging record from a validated video observation.

    ``assertion`` must be an explicit, locally evidenced teacher statement.  A
    teaching example or model inference cannot be relabelled as a statement.
    Source authorization is source-level, not per-rule human approval.  A
    complete candidate remains non-authoritative until the separate AI gate
    proves every required check and atomically activates the sealed payload.
    """
    learning = validate_learning_candidate(learning_candidate)
    expected = {
        "assertion_id", "statement", "statement_sha256", "speaker_id", "transcript_locators",
        "source_class", "source_authorization", "semantic_scope",
        "normalized_rule", "semantic_confidence", "ambiguities",
        "contradictions", "explanation", "tests",
    }
    if not isinstance(assertion, Mapping) or set(assertion) != expected:
        _fail("assertion fields mismatch")

    assertion_id = _text(assertion.get("assertion_id"), "assertion_id")
    statement = _text(assertion.get("statement"), "statement")
    statement_sha = _sha(assertion.get("statement_sha256"), "statement_sha256")
    if hashlib.sha256(statement.encode("utf-8")).hexdigest() != statement_sha:
        _fail("statement_sha256 does not match statement")
    speaker_id = _text(assertion.get("speaker_id"), "speaker_id")
    source_class = assertion.get("source_class")
    if source_class not in _SOURCE_CLASSES:
        _fail("invalid source class")

    locators = assertion.get("transcript_locators")
    if not isinstance(locators, list) or len(locators) != 1:
        _fail("one exact transcript locator required per assertion")
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
        if transcript["text_sha256"] != statement_sha:
            _fail("teacher statement is not bound to transcript digest")

    authorization = assertion.get("source_authorization")
    authorization_fields = {
        "status", "decision_ref", "policy_version", "authorized_source_sha256",
        "authorized_video_file_id", "authorized_teacher_ids",
        "approved_semantic_scopes", "authorization_evidence_sha256",
    }
    if not isinstance(authorization, Mapping) or set(authorization) != authorization_fields:
        _fail("source authorization fields mismatch")
    status = authorization.get("status")
    if status not in {"APPROVED", "NOT_APPROVED"}:
        _fail("invalid source authorization status")
    decision_ref = str(authorization.get("decision_ref") or "").strip()
    policy_version = str(authorization.get("policy_version") or "").strip()
    authorized_source_sha = str(authorization.get("authorized_source_sha256") or "").strip().lower()
    authorized_video_file_id = str(authorization.get("authorized_video_file_id") or "").strip()
    authorization_evidence_sha = str(authorization.get("authorization_evidence_sha256") or "").strip().lower()
    teacher_ids = authorization.get("authorized_teacher_ids")
    scopes = authorization.get("approved_semantic_scopes")
    if not isinstance(scopes, list):
        _fail("approved semantic scopes must be a list")
    scopes = [_text(value, "approved semantic scope") for value in scopes]
    if len(scopes) != len(set(scopes)):
        _fail("duplicate approved semantic scope")
    semantic_scope = _text(assertion.get("semantic_scope"), "semantic_scope")
    if status == "APPROVED":
        if not decision_ref or not policy_version or semantic_scope not in scopes:
            _fail("approved source lacks exact semantic scope authorization")
        if not _SHA256.fullmatch(authorized_source_sha) or not _SHA256.fullmatch(authorization_evidence_sha):
            _fail("approved source lacks immutable authorization evidence")
        if authorized_source_sha != learning["source"]["source_sha256"]:
            _fail("authorization source sha256 mismatch")
        if authorized_video_file_id != learning["source"]["video_file_id"]:
            _fail("authorization video file mismatch")
        teacher_ids = _texts(teacher_ids, "authorized teacher id")
        if speaker_id not in teacher_ids:
            _fail("teacher is outside source authorization")
    if status == "NOT_APPROVED" and any((decision_ref, policy_version, authorized_source_sha,
                                           authorized_video_file_id, authorization_evidence_sha,
                                           scopes, teacher_ids)):
        _fail("unapproved source must not carry approval evidence")

    normalized_rule = assertion.get("normalized_rule")
    if not isinstance(normalized_rule, Mapping):
        _fail("normalized rule fields mismatch")
    if _has_forbidden_key(normalized_rule) or _has_forbidden_value(normalized_rule):
        _fail("normalized rule contains hidden information")
    if set(normalized_rule) != _NORMALIZED_RULE_FIELDS:
        _fail("normalized rule fields mismatch")
    _text(normalized_rule.get("rule_key"), "normalized rule_key")
    if normalized_rule.get("rule_kind") not in {"bid", "inference", "priority", "exception", "fallback"}:
        _fail("invalid normalized rule_kind")
    for field in _RULE_JSON_FIELDS:
        if not isinstance(normalized_rule.get(field), Mapping):
            _fail(f"normalized {field} must be an object")
    for field in ("priority", "specificity"):
        value = normalized_rule.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            _fail(f"normalized {field} must be an integer")
    if normalized_rule["specificity"] < 0:
        _fail("normalized specificity must be non-negative")
    _text(normalized_rule.get("condition_schema_version"), "normalized condition_schema_version")
    _text(normalized_rule.get("method_version"), "normalized method_version")
    explanation = assertion.get("explanation")
    if not isinstance(explanation, Mapping) or set(explanation) != {
        "why_or_purpose", "consequences", "rejected_alternatives", "evidence_refs"
    }:
        _fail("explanation fields mismatch")
    why_or_purpose = _texts(explanation.get("why_or_purpose"), "why or purpose")
    consequences = _texts(explanation.get("consequences"), "consequence")
    rejected_alternatives = _texts(
        explanation.get("rejected_alternatives"), "rejected alternative", allow_empty=True
    )
    explanation_refs = _texts(explanation.get("evidence_refs"), "explanation evidence ref")
    if not set(explanation_refs) <= set(locators):
        _fail("explanation references evidence outside assertion")
    if _has_forbidden_key(explanation) or _has_forbidden_value(explanation):
        _fail("explanation contains hidden information")

    tests = assertion.get("tests")
    if not isinstance(tests, Mapping) or set(tests) != {
        "positive", "negative", "boundary", "interference"
    }:
        _fail("tests fields mismatch")
    if any(not isinstance(tests[kind], list) or not tests[kind] for kind in tests):
        _fail("all four test classes are required")
    if _has_forbidden_key(tests) or _has_forbidden_value(tests):
        _fail("tests contain hidden information")

    ambiguities = assertion.get("ambiguities")
    contradictions = assertion.get("contradictions")
    if not isinstance(ambiguities, list) or not isinstance(contradictions, list):
        _fail("ambiguities and contradictions must be lists")
    ambiguities = [_text(value, "ambiguity") for value in ambiguities]
    contradictions = [_text(value, "contradiction") for value in contradictions]
    confidence = _confidence(assertion.get("semantic_confidence"))

    ai_verification_eligible = (
        source_class == "SCHOOL_PRIMARY_EVIDENCE"
        and status == "APPROVED"
        and not ambiguities
        and not contradictions
        and confidence >= 0.95
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
            "statement_sha256": statement_sha,
            "speaker_id": speaker_id,
            "transcript_locators": locators,
        },
        "source_class": source_class,
        "source_authorization": {
            "status": status,
            "decision_ref": decision_ref or None,
            "policy_version": policy_version or None,
            "authorized_source_sha256": authorized_source_sha or None,
            "authorized_video_file_id": authorized_video_file_id or None,
            "authorized_teacher_ids": teacher_ids or [],
            "approved_semantic_scopes": scopes,
            "authorization_evidence_sha256": authorization_evidence_sha or None,
        },
        "semantic_scope": semantic_scope,
        "normalized_rule": deepcopy(dict(normalized_rule)),
        "semantic_confidence": confidence,
        "ambiguities": ambiguities,
        "contradictions": contradictions,
        "explanation": {
            "why_or_purpose": why_or_purpose,
            "consequences": consequences,
            "rejected_alternatives": rejected_alternatives,
            "evidence_refs": explanation_refs,
        },
        "tests": deepcopy(dict(tests)),
        "review_eligibility": (
            "AI_VERIFICATION_PENDING" if ai_verification_eligible else "EVIDENCE_ONLY"
        ),
        "activation": {
            "school_canon_write_allowed": False,
            "human_approval_required": False,
            "ai_verification_required": True,
            "regression_required": True,
            "integrity_required": True,
            "rollback_proof_required": True,
            "i2_review_required": True,
            "automatic_activation_after_all_gates": True,
        },
    }
    payload_hash = _digest(payload)
    return {
        "candidate_type": "video_school_canon_candidate",
        # One teacher assertion may be corrected over time. Content-address the
        # staging identity so every revision is preserved instead of colliding
        # with an older row that has the same logical assertion_id.
        "stable_key": f"{assertion_id}:sha256:{payload_hash}",
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
