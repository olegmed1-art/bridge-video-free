"""Deterministic, offline result-bundle replay. No promotion or writer imports.

Legacy longitudinal artifacts are admissible inputs, not proof of a teacher
assertion. Missing evidence produces a content-addressed staging gap. A producer
may supply an explicit learning_observation and teacher_assertions; this module
only wraps and validates them, never extracts or invents teaching semantics.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Callable, Mapping

from bridge_contracts.video_learning_candidate import (
    LearningCandidateError, validate_learning_candidate,
)
from bridge_contracts.video_canon_evidence import (
    VideoCanonEvidenceError, build_video_canon_candidate,
)

SCHEMA = "video-canon-staging-replay-v1"
INPUT_KEYS = (
    "video_canon_learning_candidate", "video_canon_assertions",
    "video_canon_verification_bundles",
)
OBSERVATION_FIELDS = (
    "source", "observed_episode", "transcript_evidence", "frame_evidence",
    "bridge_context", "preliminary_skill", "confidence", "provenance",
    "unresolved_questions",
)
TrustedEvidenceResolver = Callable[[str], Mapping[str, Any] | None]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _bound_parent_evidence(bundle, learning, assertion, resolver):
    """Resolve a parent outside caller JSON; never trust a manifest in the bundle."""
    provenance = bundle.get("replay_provenance")
    if not isinstance(provenance, Mapping) or set(provenance) != {
        "parent_artifact_sha256", "parent_hash_domain"
    }:
        return None, "PARENT_PROVENANCE_MISSING"
    parent_sha = provenance.get("parent_artifact_sha256")
    if (not isinstance(parent_sha, str) or not _SHA256.fullmatch(parent_sha)
            or provenance.get("parent_hash_domain") != "RAW_BYTES_SHA256"):
        return None, "PARENT_PROVENANCE_INVALID"
    if resolver is None:
        return None, "TRUSTED_EVIDENCE_RESOLVER_MISSING"
    try:
        manifest = resolver(parent_sha)
        if not isinstance(manifest, Mapping):
            return None, "PARENT_EVIDENCE_NOT_FOUND"
        manifest_hash = digest(manifest)
        if manifest.get("schema") != "video-canon-replay-parent-evidence-v1":
            return None, "PARENT_EVIDENCE_SCHEMA_INVALID"
        if manifest.get("evidence_class") != "REAL":
            return None, "NON_REAL_EVIDENCE"
        if (manifest.get("parent_artifact_sha256") != parent_sha
                or manifest.get("parent_hash_domain") != "RAW_BYTES_SHA256"):
            return None, "PARENT_EVIDENCE_MISMATCH"
        if manifest.get("source") != learning["source"]:
            return None, "PARENT_SOURCE_MISMATCH"
        locator = assertion["transcript_locators"][0]
        transcripts = manifest.get("transcripts")
        if not isinstance(transcripts, list):
            return None, "PARENT_TRANSCRIPT_MISSING"
        matches = [row for row in transcripts
                   if isinstance(row, Mapping) and row.get("locator") == locator]
        if len(matches) != 1:
            return None, "PARENT_TRANSCRIPT_NOT_UNIQUE"
        trusted = matches[0]
        observed = next(row for row in learning["transcript_evidence"] if row["locator"] == locator)
        if any(trusted.get(field) != observed[field] for field in observed):
            return None, "PARENT_TRANSCRIPT_MISMATCH"
        if trusted.get("speaker_role") != "teacher" or trusted.get("speaker_identity_status") != "VERIFIED":
            return None, "TEACHER_IDENTITY_UNPROVEN"
        frame = next(row for row in learning["frame_evidence"] if row["transcript_locator"] == locator)
        if trusted.get("frame_evidence") != frame:
            return None, "PARENT_FRAME_MISMATCH"
        authorization = assertion["source_authorization"]
        if authorization["status"] == "APPROVED":
            authorizations = manifest.get("source_authorizations")
            if not isinstance(authorizations, list) or authorization not in authorizations:
                return None, "SOURCE_AUTHORIZATION_UNPROVEN"
        return {
            "parent_artifact_sha256": parent_sha, "parent_hash_domain": "RAW_BYTES_SHA256",
            "trusted_manifest_sha256": manifest_hash,
        }, None
    except Exception:
        # Resolver failures and malformed external evidence are gaps, not a
        # reason to trust caller labels or disclose private backend errors.
        return None, "TRUSTED_EVIDENCE_RESOLUTION_FAILED"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def staging_replay_required(bundle: Mapping[str, Any]) -> bool:
    return (bundle.get("schema") == "video-bounded-source-inspection-v1"
            or bundle.get("video_canon_analysis_requested") is True
            or "source_constraints" in bundle)


def replay_requested(bundle: Mapping[str, Any]) -> bool:
    return (staging_replay_required(bundle) or any(key in bundle for key in INPUT_KEYS)
            or "learning_observation" in bundle or "teacher_assertions" in bundle)


def replay_result_bundle(
    bundle: Mapping[str, Any], *, evidence_resolver: TrustedEvidenceResolver | None = None,
) -> dict[str, Any]:
    """Return validated video_canon inputs and staging rows, or exact gaps.

    Invocation itself requests analysis. Status/coverage/role labels and legacy
    canon_observation rows never manufacture the missing contract fields.
    Verification bundles are deliberately not forwarded or evaluated here.
    """
    if not isinstance(bundle, Mapping):
        raise ValueError("result bundle must be an object")
    bundle_hash = digest(bundle)  # Strict JSON, including finite numbers.
    if bundle.get("schema") == "video-bounded-source-inspection-v1":
        from bridge_contracts.video_source_draft import normalize_source_packet
        draft = normalize_source_packet(bundle)
        sha = digest(draft)
        result = {
            "schema": SCHEMA, "status": "BLOCKED", "bundle_sha256": bundle_hash,
            "video_canon_inputs": {}, "draft_knowledge": draft,
            "candidates": [{"candidate_type": "video_source_knowledge_draft",
                "stable_key": "video-source-draft:sha256:" + sha, "payload_hash": sha,
                "payload": draft, "quality_status": "BLOCKED", "promotion_status": "STAGING_ONLY",
                "evidence_refs": [], "method_version": draft["schema"],
                "authoritative_tables_modified": False}],
            "gaps": draft["gaps"], "promotion_commands": [],
            "verification_bundle_status": "NOT_EVALUATED_STAGING_ONLY",
            "world_lookup_performed": False, "authoritative_write_performed": False,
        }
        result["replay_sha256"] = digest(result)
        return result
    gaps: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    bindings: list[dict[str, str]] = []

    def gap(code: str, path: str, reason: str) -> None:
        gaps.append({"code": code, "path": path, "reason": reason})

    from bridge_contracts.video_source_draft import source_constraint_gaps
    constraint_gaps = source_constraint_gaps(bundle)
    gaps.extend(constraint_gaps)
    conflicting_aliases = bool(constraint_gaps)
    for canonical, alias in ((INPUT_KEYS[0], "learning_observation"),
                             (INPUT_KEYS[1], "teacher_assertions")):
        if canonical in bundle and alias in bundle:
            # Learning aliases are different schemas; never silently select one.
            if canonical == INPUT_KEYS[0] or bundle[canonical] != bundle[alias]:
                gap("CONFLICTING_INPUT_ALIASES", alias, "Supply one unambiguous input representation")
                conflicting_aliases = True

    raw_learning = bundle.get(INPUT_KEYS[0])
    if raw_learning is None:
        observation = bundle.get("learning_observation")
        if isinstance(observation, Mapping):
            if set(observation) - set(OBSERVATION_FIELDS):
                gap("OBSERVATION_FIELDS_INVALID", "learning_observation",
                    "Only the declared observation fields are allowed")
                conflicting_aliases = True
            for field in OBSERVATION_FIELDS:
                if field not in observation or observation[field] is None:
                    gap("EVIDENCE_NOT_SUPPLIED", "learning_observation." + field,
                        "Required observation field is missing or null")
            # Only administrative fields are constructed. All observations,
            # uncertainties and evidence must already exist in the producer.
            raw_learning = {
                **deepcopy(dict(observation)),
                "schema": "video31-learning-candidate-v1",
                "status": "CANDIDATE_RESEARCH",
                "candidate_id": "replay-observation:" + digest(observation),
                "authority": {
                    "authority_class": "CANDIDATE_RESEARCH",
                    "school_canon_write_allowed": False,
                    "student_profile_write_allowed": False,
                    "approved_course_write_allowed": False,
                    "publication_allowed": False,
                },
            }
        else:
            gap("LEARNING_OBSERVATION_MISSING", "learning_observation",
                "A source-bound observed interaction is required; archival status is insufficient")
            for field in OBSERVATION_FIELDS:
                gap("EVIDENCE_NOT_SUPPLIED", "learning_observation." + field,
                    "Supply the existing contract evidence; do not infer or generate it")

    learning = None
    if raw_learning is not None:
        for field in OBSERVATION_FIELDS:
            if isinstance(raw_learning, Mapping) and (field not in raw_learning or raw_learning[field] is None):
                path = ("learning_observation." if INPUT_KEYS[0] not in bundle else INPUT_KEYS[0] + ".") + field
                if not any(row["path"] == path for row in gaps):
                    gap("EVIDENCE_NOT_SUPPLIED", path, "Required learning field is missing or null")
        try:
            learning = validate_learning_candidate(raw_learning)
        except (LearningCandidateError, TypeError, KeyError, OverflowError) as exc:
            gap("LEARNING_EVIDENCE_REJECTED", INPUT_KEYS[0], str(exc))

    raw_assertions = bundle.get(INPUT_KEYS[1], bundle.get("teacher_assertions"))
    if not isinstance(raw_assertions, list) or not raw_assertions:
        gap("TEACHER_ASSERTIONS_MISSING", "teacher_assertions",
            "Need an exact teacher span, identity, source authorization, semantic scope, "
            "normalized rule, source-quoted explanation and four test classes")
    elif learning is not None and not conflicting_aliases:
        seen: dict[str, str] = {}
        conflicting = set()
        for raw in raw_assertions:
            if isinstance(raw, Mapping) and "assertion_id" in raw:
                # Invalid scalar IDs cannot shadow a valid string identity.
                normalized = {**raw, "assertion_id": str(raw["assertion_id"] or "").strip()}
                key, sha = normalized["assertion_id"], digest(normalized)
                if key in seen and seen[key] != sha:
                    conflicting.add(key)
                seen[key] = sha
        for index, raw in enumerate(raw_assertions):
            path = f"teacher_assertions[{index}]"
            if (not isinstance(raw, Mapping) or not isinstance(raw.get("assertion_id"), str)
                    or not raw["assertion_id"].strip()):
                gap("ASSERTION_ID_INVALID", path + ".assertion_id", "A nonempty string assertion identity is required")
                continue
            if (isinstance(raw, Mapping) and isinstance(raw.get("assertion_id"), str)
                    and raw["assertion_id"].strip() in conflicting):
                gap("CONFLICTING_ASSERTION_ID", path, "Different evidence uses the same assertion identity")
                continue
            try:
                candidate = build_video_canon_candidate(learning, raw)
                # This minimal replay accepts literal source quotes only. The
                # general evidence contract also allows richer reviewed inputs.
                statement = raw["statement"]
                explanation = raw["explanation"]
                if any(not isinstance(quote, str) or quote not in statement for field in (
                    "why_or_purpose", "consequences", "rejected_alternatives"
                ) for quote in explanation[field]):
                    gap("EXPLANATION_NOT_SOURCE_QUOTE", path + ".explanation",
                        "Every explanation clause must occur in the exact cited teacher span")
                    continue
            except (VideoCanonEvidenceError, LearningCandidateError, TypeError, KeyError, OverflowError) as exc:
                gap("ASSERTION_EVIDENCE_REJECTED", path, str(exc))
                continue
            binding, reason = _bound_parent_evidence(bundle, learning, raw, evidence_resolver)
            if reason:
                gap(reason, path + ".replay_provenance",
                    "An out-of-band trusted parent resolver must bind source, teacher span, frame and authorization")
                continue
            if candidate["payload_hash"] not in {row["payload_hash"] for row in candidates}:
                candidates.append(candidate)
                accepted.append(deepcopy(dict(raw)))
                bindings.append({**binding, "candidate_payload_sha256": candidate["payload_hash"]})

    inputs = {}
    if accepted:
        inputs = {INPUT_KEYS[0]: learning, INPUT_KEYS[1]: accepted, INPUT_KEYS[2]: {}}
        # Preserve provenance through persisters that retain only row.payload.
        payload = {"schema": SCHEMA, "bundle_sha256": bundle_hash,
                   "provenance_bindings": bindings, "school_canon_write_allowed": False}
        sha = digest(payload)
        candidates.append({
            "candidate_type": "video_canon_replay_receipt",
            "stable_key": "video-canon-replay-receipt:sha256:" + sha,
            "payload_hash": sha, "payload": payload,
            "quality_status": "EVIDENCE_BOUND", "promotion_status": "STAGING_ONLY",
            "evidence_refs": [], "method_version": SCHEMA,
            "authoritative_tables_modified": False,
        })
    if gaps:
        payload = {"schema": SCHEMA, "bundle_sha256": bundle_hash,
                   "status": "BLOCKED", "gaps": gaps,
                   "school_canon_write_allowed": False}
        if "source_constraints" in bundle:
            payload["source_constraints"] = deepcopy(bundle["source_constraints"])
        sha = digest(payload)
        candidates.append({
            "candidate_type": "video_canon_replay_gap",
            "stable_key": "video-canon-replay-gap:sha256:" + sha,
            "payload_hash": sha, "payload": payload,
            "quality_status": "BLOCKED", "promotion_status": "STAGING_ONLY",
            "evidence_refs": [], "method_version": SCHEMA,
            "authoritative_tables_modified": False,
        })
    result = {
        "schema": SCHEMA, "status": "PARTIAL" if accepted and gaps else (
            "STAGING_READY" if accepted else "BLOCKED"),
        "bundle_sha256": bundle_hash, "video_canon_inputs": inputs,
        "candidates": candidates, "gaps": gaps, "promotion_commands": [],
        "verification_bundle_status": "NOT_EVALUATED_STAGING_ONLY",
        "world_lookup_performed": False, "authoritative_write_performed": False,
    }
    result["replay_sha256"] = digest(result)
    return result
