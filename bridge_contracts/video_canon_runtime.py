"""Fail-closed handoff from verified video evidence to a promotion delivery.

The runtime deliberately separates three responsibilities:

* the extractor converts already verified video-analysis observations into
  immutable ``TEACHER_VIDEO`` candidates;
* the verification worker accepts a candidate only when independent I2 and I3
  verdicts agree and the existing sixteen-check bundle is sealed;
* the promotion consumer envelope binds a lease/fencing token to that exact
  candidate and bundle.  Only the database consumer may perform the write.

This module performs no network or authoritative database writes.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence
from uuid import UUID

from bridge_contracts.video_canon_ai_promotion import build_ai_canon_promotion
from bridge_contracts.video_canon_evidence import (
    build_video_canon_candidate,
    contains_forbidden_hidden_information,
)


EXTRACTOR_SCHEMA = "verified-video-canon-input-v1"
VERDICT_SCHEMA = "video-canon-assurance-verdict-v1"
DELIVERY_SCHEMA = "video-canon-promotion-delivery-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_STATUS = "VIDEO_ANALYSIS_VERIFIED"
_VERDICT_STATUSES = {
    "VERIFIED_FOR_PROMOTION", "REJECTED", "NEEDS_EVIDENCE",
    "CANON_CONFLICT", "PROFILE_AMBIGUITY",
}


class VideoCanonRuntimeError(ValueError):
    """The end-to-end video-to-Canon handoff is incomplete or unsafe."""


def _fail(message: str) -> None:
    raise VideoCanonRuntimeError(message)


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


def _uuid(value: Any, label: str) -> str:
    try:
        return str(UUID(_text(value, label)))
    except ValueError:
        _fail(f"invalid {label}")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"invalid {label}")
    result = float(value)
    if not math.isfinite(result):
        _fail(f"invalid {label}")
    return result


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_canon_candidates(video_result: Mapping[str, Any]) -> dict[str, Any]:
    """Build immutable candidates only from a verified analyzer handoff.

    The semantic analyzer must emit explicit structured observations.  Missing
    structure is reported as a gap; this adapter never invents a bridge rule
    from free text.
    """
    expected = {
        "schema", "status", "job_id", "source", "algorithm_revision",
        "observations",
    }
    if not isinstance(video_result, Mapping) or set(video_result) != expected:
        _fail("video result fields mismatch")
    if video_result.get("schema") != EXTRACTOR_SCHEMA:
        _fail("video result schema mismatch")
    if video_result.get("status") != _TERMINAL_STATUS:
        _fail("video result is not verified terminal evidence")
    job_id = _text(video_result.get("job_id"), "job_id")
    algorithm_revision = _text(video_result.get("algorithm_revision"), "algorithm_revision")

    source = video_result.get("source")
    source_fields = {
        "video_file_id", "source_name", "source_sha256", "source_fingerprint",
        "master_artifact_sha256",
    }
    if not isinstance(source, Mapping) or set(source) != source_fields:
        _fail("video source fields mismatch")
    normalized_source = {
        "video_file_id": _text(source.get("video_file_id"), "video_file_id"),
        "source_name": _text(source.get("source_name"), "source_name"),
        "source_sha256": _sha(source.get("source_sha256"), "source_sha256"),
        "source_fingerprint": _text(source.get("source_fingerprint"), "source_fingerprint"),
    }
    master_sha = _sha(source.get("master_artifact_sha256"), "master_artifact_sha256")

    observations = video_result.get("observations")
    if not isinstance(observations, list):
        _fail("observations must be a list")
    candidates: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in observations:
        if not isinstance(raw, Mapping):
            gaps.append({"assertion_id": "UNKNOWN", "status": "NEEDS_EVIDENCE", "reason": "observation is not an object"})
            continue
        assertion_id = str(raw.get("assertion_id") or "UNKNOWN").strip() or "UNKNOWN"
        if assertion_id in seen:
            gaps.append({"assertion_id": assertion_id, "status": "NEEDS_EVIDENCE", "reason": "duplicate assertion_id"})
            continue
        seen.add(assertion_id)
        try:
            learning = _learning_candidate(job_id, algorithm_revision, normalized_source, raw)
            assertion = _teacher_assertion(raw)
            candidate = build_video_canon_candidate(learning, assertion)
        except (VideoCanonRuntimeError, ValueError, OverflowError) as exc:
            gaps.append({"assertion_id": assertion_id, "status": "NEEDS_EVIDENCE", "reason": str(exc)})
            continue
        candidate["payload"]["video_analysis_provenance"] = {
            "job_id": job_id,
            "algorithm_revision": algorithm_revision,
            "master_artifact_sha256": master_sha,
        }
        if contains_forbidden_hidden_information(candidate["payload"]):
            _fail("runtime provenance contains hidden information")
        # Metadata is not naturally actor-labelled. Probe it in an actor context
        # so a hand-shaped job/revision cannot bypass the final payload rescan.
        if contains_forbidden_hidden_information({
            "partner": {"cards": [job_id, algorithm_revision]}
        }):
            _fail("runtime provenance contains hidden information")
        candidate["payload_hash"] = _digest(candidate["payload"])
        candidate["stable_key"] = f"{assertion_id}:sha256:{candidate['payload_hash']}"
        if candidate["payload"].get("authority_class") != "TEACHER_VIDEO":
            _fail("extractor authority boundary mismatch")
        candidates.append(candidate)
    return {
        "schema": "video-canon-extractor-result-v1",
        "status": "EXTRACTED" if candidates else "NO_CANDIDATE_EXTRACTED",
        "job_id": job_id,
        "source_sha256": normalized_source["source_sha256"],
        "master_artifact_sha256": master_sha,
        "algorithm_revision": algorithm_revision,
        "candidates": candidates,
        "gaps": gaps,
        "authoritative_write_performed": False,
    }


def _learning_candidate(
    job_id: str,
    algorithm_revision: str,
    source: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "assertion_id", "statement", "statement_sha256", "speaker_id",
        "transcript_locators",
        "transcript", "frame_evidence", "observed_episode", "bridge_context",
        "preliminary_skill", "confidence", "source_class", "source_authorization",
        "semantic_scope", "system_profile", "learner_level", "normalized_rule",
        "semantic_confidence", "ambiguities", "contradictions", "explanation", "tests",
    }
    if set(observation) != required:
        _fail("observation fields mismatch")
    transcript = observation.get("transcript")
    transcript_fields = {
        "locator", "start", "end", "text", "text_sha256", "speaker_id",
        "speaker_identity_status",
    }
    if not isinstance(transcript, Mapping) or set(transcript) != transcript_fields:
        _fail("transcript fields mismatch")
    text = _text(transcript.get("text"), "transcript text")
    text_sha = _sha(transcript.get("text_sha256"), "transcript text_sha256")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != text_sha:
        _fail("transcript text hash mismatch")
    if transcript.get("speaker_identity_status") != "VERIFIED":
        _fail("teacher identity is not verified")
    if transcript.get("speaker_id") != observation.get("speaker_id"):
        _fail("teacher identity mismatch")
    locator = _text(transcript.get("locator"), "transcript locator")
    if observation.get("transcript_locators") != [locator]:
        _fail("assertion transcript locator mismatch")
    frame = observation.get("frame_evidence")
    if not isinstance(frame, Mapping):
        _fail("frame evidence required")
    frame = deepcopy(dict(frame))
    if frame.get("source_fingerprint") != source["source_fingerprint"]:
        _fail("frame source fingerprint mismatch")
    preliminary = deepcopy(observation.get("preliminary_skill"))
    return {
        "schema": "video31-learning-candidate-v1",
        "status": "CANDIDATE_RESEARCH",
        "candidate_id": f"video-canon:{job_id}:{_text(observation.get('assertion_id'), 'assertion_id')}",
        "observed_episode": deepcopy(observation.get("observed_episode")),
        "source": dict(source),
        "transcript_evidence": [{
            "locator": locator,
            "start": transcript.get("start"),
            "end": transcript.get("end"),
            "text_sha256": text_sha,
            "speaker_id": transcript.get("speaker_id"),
            "speaker_identity_status": transcript.get("speaker_identity_status"),
        }],
        "frame_evidence": [frame],
        "bridge_context": deepcopy(observation.get("bridge_context")),
        "preliminary_skill": preliminary,
        "confidence": deepcopy(observation.get("confidence")),
        "provenance": {
            "algorithm_revision": algorithm_revision,
            "contract_version": "video31-learning-candidate-v1",
        },
        "unresolved_questions": [],
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "school_canon_write_allowed": False,
            "student_profile_write_allowed": False,
            "approved_course_write_allowed": False,
            "publication_allowed": False,
        },
    }


def _teacher_assertion(observation: Mapping[str, Any]) -> dict[str, Any]:
    transcript = observation["transcript"]
    return {
        "assertion_id": observation["assertion_id"],
        "statement": observation["statement"],
        "statement_sha256": observation["statement_sha256"],
        "speaker_id": observation["speaker_id"],
        "transcript_locators": [transcript["locator"]],
        "source_class": observation["source_class"],
        "source_authorization": deepcopy(observation["source_authorization"]),
        "semantic_scope": observation["semantic_scope"],
        "system_profile": observation["system_profile"],
        "learner_level": observation["learner_level"],
        "normalized_rule": deepcopy(observation["normalized_rule"]),
        "semantic_confidence": observation["semantic_confidence"],
        "ambiguities": deepcopy(observation["ambiguities"]),
        "contradictions": deepcopy(observation["contradictions"]),
        "explanation": deepcopy(observation["explanation"]),
        "tests": deepcopy(observation["tests"]),
    }


def verify_canon_candidate(
    candidate: Mapping[str, Any],
    verification_bundle: Mapping[str, Any],
    assurance_verdicts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Require agreeing independent I2/I3 verdicts before promotion delivery."""
    payload = candidate.get("payload") if isinstance(candidate, Mapping) else None
    if not isinstance(payload, Mapping) or payload.get("authority_class") != "TEACHER_VIDEO":
        _fail("only TEACHER_VIDEO candidates may be verified for Canon")
    candidate_hash = _sha(candidate.get("payload_hash"), "candidate payload_hash")
    if _digest(payload) != candidate_hash:
        _fail("candidate changed before verification")
    candidate_profile = _text(payload.get("system_profile"), "candidate system profile")
    candidate_level = _text(payload.get("learner_level"), "candidate learner level")
    if not isinstance(assurance_verdicts, Sequence) or isinstance(assurance_verdicts, (str, bytes)):
        _fail("assurance verdicts must be a sequence")
    normalized: dict[str, dict[str, Any]] = {}
    for raw in assurance_verdicts:
        fields = {
            "schema", "candidate_payload_hash", "assurance_level", "verdict",
            "verifier_family", "verifier_version", "execution_principal",
            "evidence_sha256", "canon_snapshot_sha256", "system_profile",
            "learner_level", "provenance_verified", "hidden_information_clear",
            "profile_unambiguous", "canon_conflict", "deterministic",
        }
        if not isinstance(raw, Mapping) or set(raw) != fields:
            _fail("assurance verdict fields mismatch")
        if raw.get("schema") != VERDICT_SCHEMA:
            _fail("assurance verdict schema mismatch")
        level = raw.get("assurance_level")
        if level not in {"I2", "I3"} or level in normalized:
            _fail("exactly one I2 and one I3 verdict required")
        if _sha(raw.get("candidate_payload_hash"), "verdict candidate hash") != candidate_hash:
            _fail("verdict belongs to another candidate")
        verdict = raw.get("verdict")
        if verdict not in _VERDICT_STATUSES:
            _fail("invalid assurance verdict")
        normalized[level] = {
            **dict(raw),
            "verifier_family": _text(raw.get("verifier_family"), "verifier family"),
            "verifier_version": _text(raw.get("verifier_version"), "verifier version"),
            "execution_principal": _text(raw.get("execution_principal"), "execution principal"),
            "evidence_sha256": _sha(raw.get("evidence_sha256"), "verdict evidence_sha256"),
            "canon_snapshot_sha256": _sha(raw.get("canon_snapshot_sha256"), "canon snapshot"),
            "system_profile": _text(raw.get("system_profile"), "system profile"),
            "learner_level": _text(raw.get("learner_level"), "learner level"),
        }
    if set(normalized) != {"I2", "I3"}:
        _fail("I2 and I3 verdicts are both required")
    i2, i3 = normalized["I2"], normalized["I3"]
    if i2["verifier_family"] == i3["verifier_family"] or i2["execution_principal"] == i3["execution_principal"]:
        _fail("I2 and I3 must be independent")
    if i2["canon_snapshot_sha256"] != i3["canon_snapshot_sha256"]:
        _fail("I2 and I3 used different Canon snapshots")
    for row in (i2, i3):
        if row["verdict"] != "VERIFIED_FOR_PROMOTION":
            return _verification_stop(candidate_hash, row["verdict"], row["assurance_level"])
        if not all((
            row.get("provenance_verified") is True,
            row.get("hidden_information_clear") is True,
            row.get("profile_unambiguous") is True,
            row.get("canon_conflict") is False,
            row.get("deterministic") is True,
        )):
            return _verification_stop(candidate_hash, "NEEDS_EVIDENCE", row["assurance_level"])
        if row["system_profile"] != verification_bundle.get("system_profile") or row["learner_level"] != verification_bundle.get("learner_level"):
            return _verification_stop(candidate_hash, "PROFILE_AMBIGUITY", row["assurance_level"])
        if row["system_profile"] != candidate_profile or row["learner_level"] != candidate_level:
            return _verification_stop(candidate_hash, "PROFILE_AMBIGUITY", row["assurance_level"])
    if verification_bundle.get("canon_snapshot_sha256") != i2["canon_snapshot_sha256"]:
        _fail("verification bundle uses a different Canon snapshot")
    promotion = build_ai_canon_promotion(candidate, verification_bundle)
    return {
        "schema": "video-canon-verification-result-v1",
        "status": "VERIFIED_I2_I3",
        "candidate_payload_hash": candidate_hash,
        "verification_bundle_sha256": promotion["verification_bundle_sha256"],
        "assurance_verdicts": [i2, i3],
        "assurance_verdicts_sha256": _digest([i2, i3]),
        "promotion": promotion,
        "authoritative_write_performed": False,
    }


def _verification_stop(candidate_hash: str, status: str, level: str) -> dict[str, Any]:
    return {
        "schema": "video-canon-verification-result-v1",
        "status": status,
        "candidate_payload_hash": candidate_hash,
        "failed_assurance_level": level,
        "promotion": None,
        "authoritative_write_performed": False,
    }


def build_promotion_delivery(
    verified: Mapping[str, Any],
    *,
    delivery_id: Any,
    rule_id: Any,
    lease_owner: Any,
    lease_token: Any,
    fencing_token: Any,
) -> dict[str, Any]:
    """Bind the verified result to one fenced, retry-safe delivery."""
    if not isinstance(verified, Mapping) or verified.get("status") != "VERIFIED_I2_I3":
        _fail("promotion requires VERIFIED_I2_I3")
    promotion = verified.get("promotion")
    if not isinstance(promotion, Mapping) or promotion.get("status") != "AUTO_PROMOTION_READY":
        _fail("sealed automatic promotion is missing")
    candidate_hash = _sha(verified.get("candidate_payload_hash"), "candidate payload hash")
    bundle_hash = _sha(verified.get("verification_bundle_sha256"), "verification bundle hash")
    if promotion.get("candidate_payload_hash") != candidate_hash or promotion.get("verification_bundle_sha256") != bundle_hash:
        _fail("promotion delivery hash mismatch")
    if promotion.get("authority_class") != "SCHOOL_CANON" or promotion.get("human_approval_required") is not False:
        _fail("promotion target authority mismatch")
    token = fencing_token
    if isinstance(token, bool) or not isinstance(token, int) or token < 1:
        _fail("invalid fencing token")
    result = {
        "schema": DELIVERY_SCHEMA,
        "delivery_id": _uuid(delivery_id, "delivery_id"),
        "rule_id": _uuid(rule_id, "rule_id"),
        "candidate_payload_hash": candidate_hash,
        "verification_bundle_sha256": bundle_hash,
        "assurance_verdicts_sha256": _sha(verified.get("assurance_verdicts_sha256"), "assurance verdicts hash"),
        "lease_owner": _text(lease_owner, "lease_owner"),
        "lease_token": _uuid(lease_token, "lease_token"),
        "fencing_token": token,
        "idempotency_key": f"video-canon-delivery:{candidate_hash}:{bundle_hash}",
        "operation": "ATOMIC_PROMOTION",
        "post_write_integrity_required": True,
        "rollback_on_any_error": True,
    }
    return {**result, "delivery_sha256": _digest(result)}


__all__ = [
    "DELIVERY_SCHEMA", "EXTRACTOR_SCHEMA", "VERDICT_SCHEMA",
    "VideoCanonRuntimeError", "build_promotion_delivery",
    "extract_canon_candidates", "verify_canon_candidate",
]
