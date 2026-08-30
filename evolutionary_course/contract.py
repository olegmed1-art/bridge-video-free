"""Evidence-bound contract for Evolutionary Course v1.

This module is deliberately downstream of Video 3.1.  It validates research
candidates and builds deterministic longitudinal views; it has no publication,
curriculum-activation, student-profile, or School Canon write path.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

SCHEMA = "evolutionary-course-learning-episode-v1"
COURSE_VERSION = "Evolutionary Course v1"
AUTHORITY_CLASS = "CANDIDATE_RESEARCH"

SKILL_STATES = (
    "NOT_INTRODUCED",
    "INTRODUCED",
    "RECOGNIZED",
    "SUPPORTED",
    "INDEPENDENT",
    "TRANSFERRED",
    "UNSTABLE",
    "MASTERED",
)
EPISTEMIC_CLASSES = ("FACT", "INFERENCE", "RECOMMENDATION", "UNCERTAIN")
OUTCOMES = ("SUCCESS", "PARTIAL", "ERROR", "UNRESOLVED", "NOT_ASSESSED")
SUPPORT_LEVELS = ("NONE", "PROMPT", "GUIDED", "MODELLED", "UNKNOWN")
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_TOP = {
    "schema",
    "course_version",
    "episode_id",
    "occurred_at",
    "source",
    "learning_task",
    "interaction",
    "claims",
    "mastery_transition",
    "authority",
}


class EpisodeContractError(ValueError):
    """A learning episode is not safe to consume."""


def _fail(message: str) -> None:
    raise EpisodeContractError(message)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        _fail(f"{label} fields mismatch")


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID.fullmatch(text):
        _fail(f"invalid {label}")
    return text


def _nonempty_strings(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        _fail(f"invalid {label}")
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            _fail(f"invalid {label}")
        result.append(text)
    if len(result) != len(set(result)):
        _fail(f"duplicate {label}")
    return result


def _timestamp(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EpisodeContractError("invalid occurred_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("occurred_at must include timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_episode(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize one evidence-bound learning episode."""
    if not isinstance(candidate, Mapping):
        _fail("episode must be an object")
    _exact_keys(candidate, _REQUIRED_TOP, "episode")
    if candidate.get("schema") != SCHEMA:
        _fail("schema mismatch")
    if candidate.get("course_version") != COURSE_VERSION:
        _fail("course version mismatch")

    episode_id = _safe_id(candidate.get("episode_id"), "episode_id")
    occurred_at = _timestamp(candidate.get("occurred_at"))

    source = candidate.get("source")
    if not isinstance(source, Mapping):
        _fail("source must be an object")
    _exact_keys(
        source,
        {
            "video_file_id",
            "source_name",
            "start_seconds",
            "end_seconds",
            "transcript_segment_ids",
            "frame_sha256",
            "evidence_state",
        },
        "source",
    )
    video_file_id = str(source.get("video_file_id") or "").strip()
    source_name = str(source.get("source_name") or "").strip()
    if not video_file_id or not source_name:
        _fail("source identity required")
    try:
        start = float(source.get("start_seconds"))
        end = float(source.get("end_seconds"))
    except (TypeError, ValueError) as exc:
        raise EpisodeContractError("invalid source interval") from exc
    if start < 0 or end <= start or end - start > 7200:
        _fail("invalid source interval")
    transcript_ids = _nonempty_strings(
        source.get("transcript_segment_ids"), "transcript segment ids"
    )
    frame_hashes = _nonempty_strings(
        source.get("frame_sha256"), "frame hashes", allow_empty=True
    )
    if any(not _SHA256.fullmatch(value) for value in frame_hashes):
        _fail("invalid frame hash")
    if source.get("evidence_state") not in {"VERIFIED", "OBSERVED"}:
        _fail("unverified source evidence")

    task = candidate.get("learning_task")
    if not isinstance(task, Mapping):
        _fail("learning_task must be an object")
    _exact_keys(task, {"skill_id", "title", "prerequisite_skill_ids"}, "learning_task")
    skill_id = _safe_id(task.get("skill_id"), "skill_id")
    title = str(task.get("title") or "").strip()
    if not title:
        _fail("learning task title required")
    prerequisites = [
        _safe_id(value, "prerequisite skill id")
        for value in _nonempty_strings(
            task.get("prerequisite_skill_ids"),
            "prerequisite skill ids",
            allow_empty=True,
        )
    ]
    if skill_id in prerequisites:
        _fail("skill cannot require itself")

    interaction = candidate.get("interaction")
    if not isinstance(interaction, Mapping):
        _fail("interaction must be an object")
    _exact_keys(
        interaction,
        {
            "teacher_actions",
            "student_actions",
            "outcome",
            "support_level",
            "completed_cycle",
        },
        "interaction",
    )
    teacher_actions = _nonempty_strings(interaction.get("teacher_actions"), "teacher actions")
    student_actions = _nonempty_strings(interaction.get("student_actions"), "student actions")
    if interaction.get("outcome") not in OUTCOMES:
        _fail("invalid interaction outcome")
    if interaction.get("support_level") not in SUPPORT_LEVELS:
        _fail("invalid support level")
    if interaction.get("completed_cycle") is not True:
        _fail("learning episode must be a completed interaction cycle")

    claims = candidate.get("claims")
    if not isinstance(claims, list) or not claims:
        _fail("at least one claim required")
    normalized_claims: list[dict[str, Any]] = []
    claim_ids: set[str] = set()
    available_refs = set(transcript_ids) | set(frame_hashes)
    for claim in claims:
        if not isinstance(claim, Mapping):
            _fail("claim must be an object")
        _exact_keys(
            claim,
            {"claim_id", "epistemic_class", "statement", "source_refs", "confidence"},
            "claim",
        )
        claim_id = _safe_id(claim.get("claim_id"), "claim_id")
        if claim_id in claim_ids:
            _fail("duplicate claim_id")
        claim_ids.add(claim_id)
        epistemic_class = claim.get("epistemic_class")
        if epistemic_class not in EPISTEMIC_CLASSES:
            _fail("invalid epistemic class")
        statement = str(claim.get("statement") or "").strip()
        if not statement:
            _fail("claim statement required")
        refs = _nonempty_strings(claim.get("source_refs"), "claim source refs")
        if not set(refs) <= available_refs:
            _fail("claim references evidence outside exact episode source")
        confidence = claim.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            _fail("invalid claim confidence")
        confidence = float(confidence)
        if confidence < 0 or confidence > 1:
            _fail("invalid claim confidence")
        if epistemic_class == "FACT" and confidence < 0.95:
            _fail("FACT confidence below gate")
        normalized_claims.append(
            {
                "claim_id": claim_id,
                "epistemic_class": epistemic_class,
                "statement": statement,
                "source_refs": refs,
                "confidence": confidence,
            }
        )

    transition = candidate.get("mastery_transition")
    if not isinstance(transition, Mapping):
        _fail("mastery_transition must be an object")
    _exact_keys(
        transition, {"from_state", "to_state", "evidence_claim_ids"}, "mastery_transition"
    )
    if transition.get("from_state") not in SKILL_STATES:
        _fail("invalid mastery from_state")
    if transition.get("to_state") not in SKILL_STATES:
        _fail("invalid mastery to_state")
    evidence_claim_ids = _nonempty_strings(
        transition.get("evidence_claim_ids"), "transition evidence claim ids"
    )
    if not set(evidence_claim_ids) <= claim_ids:
        _fail("mastery transition references unknown claim")
    if any(
        claim["epistemic_class"] in {"RECOMMENDATION", "UNCERTAIN"}
        for claim in normalized_claims
        if claim["claim_id"] in evidence_claim_ids
    ):
        _fail("mastery transition needs FACT or INFERENCE evidence")

    authority = candidate.get("authority")
    if not isinstance(authority, Mapping):
        _fail("authority must be an object")
    _exact_keys(
        authority,
        {
            "authority_class",
            "review_state",
            "canonical_promotion_allowed",
            "curriculum_activation_allowed",
            "student_profile_write_allowed",
            "publication_allowed",
        },
        "authority",
    )
    if authority.get("authority_class") != AUTHORITY_CLASS:
        _fail("authority class mismatch")
    if authority.get("review_state") not in {"DRAFT", "REVIEW_REQUIRED", "APPROVED_CANDIDATE"}:
        _fail("invalid review state")
    for field in (
        "canonical_promotion_allowed",
        "curriculum_activation_allowed",
        "student_profile_write_allowed",
        "publication_allowed",
    ):
        if authority.get(field) is not False:
            _fail(f"{field} must be false")

    result = deepcopy(dict(candidate))
    result["episode_id"] = episode_id
    result["occurred_at"] = occurred_at
    result["source"]["start_seconds"] = start
    result["source"]["end_seconds"] = end
    result["learning_task"]["skill_id"] = skill_id
    result["learning_task"]["prerequisite_skill_ids"] = prerequisites
    result["claims"] = normalized_claims
    return result


def canonical_sha256(episode: Mapping[str, Any]) -> str:
    normalized = validate_episode(episode)
    payload = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_skill_trajectory(episodes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a deterministic candidate trajectory without mutating any authority store."""
    normalized = [validate_episode(item) for item in episodes]
    ids = [item["episode_id"] for item in normalized]
    if len(ids) != len(set(ids)):
        _fail("duplicate episode_id")
    normalized.sort(key=lambda item: (item["occurred_at"], item["episode_id"]))

    skills: dict[str, list[dict[str, Any]]] = {}
    previous_state: dict[str, str] = {}
    for episode in normalized:
        skill_id = episode["learning_task"]["skill_id"]
        transition = episode["mastery_transition"]
        expected = previous_state.get(skill_id)
        if expected is not None and transition["from_state"] != expected:
            _fail("discontinuous mastery trajectory")
        previous_state[skill_id] = transition["to_state"]
        skills.setdefault(skill_id, []).append(
            {
                "episode_id": episode["episode_id"],
                "occurred_at": episode["occurred_at"],
                "from_state": transition["from_state"],
                "to_state": transition["to_state"],
                "source_video_file_id": episode["source"]["video_file_id"],
                "source_interval": [
                    episode["source"]["start_seconds"],
                    episode["source"]["end_seconds"],
                ],
                "episode_sha256": canonical_sha256(episode),
            }
        )

    return {
        "schema": "evolutionary-course-skill-trajectory-v1",
        "course_version": COURSE_VERSION,
        "authority_class": AUTHORITY_CLASS,
        "canonical_promotion_allowed": False,
        "curriculum_activation_allowed": False,
        "student_profile_write_allowed": False,
        "publication_allowed": False,
        "episode_count": len(normalized),
        "skills": [
            {
                "skill_id": skill_id,
                "current_candidate_state": transitions[-1]["to_state"],
                "transitions": transitions,
            }
            for skill_id, transitions in sorted(skills.items())
        ],
    }


__all__ = [
    "AUTHORITY_CLASS",
    "COURSE_VERSION",
    "EPISTEMIC_CLASSES",
    "EpisodeContractError",
    "SCHEMA",
    "SKILL_STATES",
    "build_skill_trajectory",
    "canonical_sha256",
    "validate_episode",
]
