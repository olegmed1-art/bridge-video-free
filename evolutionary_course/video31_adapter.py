"""Read-only adapter from Video 3.1 longitudinal evidence to Course v1."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any, Mapping, Sequence

from .contract import COURSE_VERSION, SCHEMA, SKILL_STATES, validate_episode
from .skill_catalog import SkillCatalogError, resolve_reviewed_skill, validate_catalog

ADAPTER_SCHEMA = "evolutionary-course-video31-adapter-report-v1"
CATALOG_ADAPTER_SCHEMA = "evolutionary-course-video31-catalog-adapter-report-v1"
_COMPLETE = "COMPLETE_EVIDENCE_CANDIDATE"
_EXPECTED_QUALITY_SCHEMA = "diana-longitudinal-quality-v2"
_REAL_QUALITY_SCHEMA = "diana-longitudinal-quality"
_EXPLICIT_OUTCOMES = {"SUCCESS", "PARTIAL", "ERROR", "UNRESOLVED", "NOT_ASSESSED"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Video31AdapterError(ValueError):
    """Video 3.1 evidence cannot be safely adapted."""


def _validate_quality_schema(quality: Mapping[str, Any]) -> None:
    schema = quality.get("schema")
    if schema == _EXPECTED_QUALITY_SCHEMA:
        if quality.get("schema_version") not in (None, 2):
            raise Video31AdapterError("unsupported quality schema version")
        return
    if schema == _REAL_QUALITY_SCHEMA and quality.get("schema_version") == 2:
        return
    raise Video31AdapterError("unsupported quality schema")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _digest(*values: Any, length: int = 24) -> str:
    raw = "|".join(_text(value).casefold() for value in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _confirmed_date(lesson_identity: Mapping[str, Any]) -> datetime:
    if lesson_identity.get("lesson_date_status") != "CONFIRMED":
        raise Video31AdapterError("lesson chronology is not confirmed")
    value = _text(lesson_identity.get("lesson_date"))
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise Video31AdapterError("invalid confirmed lesson date") from exc
    return parsed.replace(tzinfo=timezone.utc)


def _source_identity(source: Mapping[str, Any]) -> tuple[str, str]:
    file_id = _text(source.get("video_file_id"))
    name = _text(source.get("source_name"))
    if not file_id or not name:
        raise Video31AdapterError("exact source identity required")
    return file_id, name


def _refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out = [_text(item) for item in value]
    if any(not item for item in out) or len(out) != len(set(out)):
        return []
    return out


def _required_interaction_fields(interaction: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    for field in (
        "interaction_id",
        "task",
        "student_action",
        "teacher_intervention",
        "student_followup",
        "observed_outcome",
    ):
        if not _text(interaction.get(field)):
            reasons.append(f"{field.upper()}_MISSING")
    evidence = _refs(interaction.get("evidence_refs"))
    if not evidence:
        reasons.append("TRANSCRIPT_EVIDENCE_MISSING")
    if interaction.get("actor_attribution_status") != "SUPPORTED":
        reasons.append("ACTOR_ATTRIBUTION_UNPROVEN")
    return list(dict.fromkeys(reasons)), evidence


def _support_level(interaction: Mapping[str, Any]) -> str:
    help_state = _text(interaction.get("help_state")).casefold()
    if "model" in help_state:
        return "MODELLED"
    if "prompt" in help_state:
        return "PROMPT"
    return "GUIDED"


def _outcome(interaction: Mapping[str, Any]) -> str:
    """Use only explicit upstream assessment; never infer correctness from prose."""
    value = _text(interaction.get("outcome_status")).upper()
    return value if value in _EXPLICIT_OUTCOMES else "NOT_ASSESSED"


def _candidate_skill(task: str) -> str:
    return f"candidate.skill.{_digest(task)}"


def adapt_video31_quality(
    quality: Mapping[str, Any],
    *,
    lesson_identity: Mapping[str, Any],
    source: Mapping[str, Any],
    prior_skill_states: Mapping[str, str] | None = None,
    skill_catalog: Mapping[str, Any] | None = None,
    require_catalog_binding: bool = False,
) -> dict[str, Any]:
    """Adapt complete Video 3.1 interactions without activating any authority."""
    if not isinstance(quality, Mapping):
        raise Video31AdapterError("quality payload must be an object")
    _validate_quality_schema(quality)
    source_job_id = _text(quality.get("job_id"))
    if not source_job_id:
        raise Video31AdapterError("source job identity required")
    if require_catalog_binding and skill_catalog is None:
        raise Video31AdapterError("catalog binding required")
    try:
        normalized_catalog = validate_catalog(skill_catalog) if skill_catalog is not None else None
    except SkillCatalogError as exc:
        raise Video31AdapterError("invalid skill catalog") from exc
    catalog_skills = {
        item["skill_id"]: item for item in (normalized_catalog or {}).get("skills", [])
    }
    authority = quality.get("authority")
    if not isinstance(authority, Mapping) or any(
        authority.get(field) != "DENY"
        for field in (
            "canon_activation",
            "curriculum_activation",
            "student_profile_production_write",
            "methodology_activation",
        )
    ):
        raise Video31AdapterError("upstream authority boundary is missing")

    base_date = _confirmed_date(lesson_identity)
    file_id, source_name = _source_identity(source)
    if source.get("evidence_state") != "VERIFIED":
        raise Video31AdapterError("source evidence is not verified")
    source_transcript_ids = set(_refs(source.get("transcript_segment_ids")))
    source_frame_hashes = set(_refs(source.get("frame_sha256")))
    if not source_transcript_ids:
        raise Video31AdapterError("source transcript inventory required")
    if any(not _SHA256.fullmatch(item) for item in source_frame_hashes):
        raise Video31AdapterError("invalid source frame inventory")

    interactions = quality.get("learning_interactions")
    if not isinstance(interactions, list):
        raise Video31AdapterError("learning interactions missing")

    prior = dict(prior_skill_states or {})
    for state in prior.values():
        if state not in SKILL_STATES:
            raise Video31AdapterError("invalid prior skill state")

    episodes: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_interactions: set[str] = set()
    for position, interaction in enumerate(interactions):
        if not isinstance(interaction, Mapping):
            rejected.append({"position": position, "reason_codes": ["INTERACTION_NOT_OBJECT"]})
            continue
        interaction_id = _text(interaction.get("interaction_id"))
        if interaction_id and interaction_id in seen_interactions:
            rejected.append(
                {
                    "interaction_id": interaction_id,
                    "reason_codes": ["DUPLICATE_INTERACTION_ID"],
                }
            )
            continue
        if interaction_id:
            seen_interactions.add(interaction_id)

        reasons: list[str] = []
        if interaction.get("status") != _COMPLETE:
            reasons.append("INTERACTION_NOT_COMPLETE")
        required_reasons, evidence_refs = _required_interaction_fields(interaction)
        reasons.extend(required_reasons)
        if not set(evidence_refs) <= source_transcript_ids:
            reasons.append("EVIDENCE_OUTSIDE_SOURCE_TRANSCRIPT")

        try:
            start = float(interaction.get("start"))
            end = float(interaction.get("end"))
        except (TypeError, ValueError):
            start, end = -1.0, -1.0
        if start < 0 or end <= start or end - start > 7200:
            reasons.append("INVALID_INTERACTION_INTERVAL")

        visual_raw = interaction.get("visual_evidence_refs")
        visual_refs = _refs(visual_raw)
        if visual_raw not in (None, []) and not visual_refs:
            reasons.append("INVALID_FRAME_EVIDENCE")
        accepted_frames = [item for item in visual_refs if _SHA256.fullmatch(item)]
        if len(accepted_frames) != len(visual_refs):
            reasons.append("INVALID_FRAME_EVIDENCE")
        if any(item not in source_frame_hashes for item in accepted_frames):
            reasons.append("FRAME_EVIDENCE_OUTSIDE_SOURCE")

        reasons = list(dict.fromkeys(reasons))
        if reasons:
            rejected.append(
                {
                    "interaction_id": interaction_id or None,
                    "reason_codes": reasons,
                }
            )
            continue

        task = _text(interaction.get("task"))
        prerequisites: list[str] = []
        episode_review_state = "REVIEW_REQUIRED"
        if normalized_catalog is not None:
            try:
                skill_id = resolve_reviewed_skill(normalized_catalog, task)
            except SkillCatalogError:
                rejected.append({
                    "interaction_id": interaction_id or None,
                    "reason_codes": ["SKILL_WORDING_NOT_REVIEWED"],
                    "review_candidate": {
                        "task_wording": task,
                        "video_file_id": file_id,
                        "source_name": source_name,
                        "start_seconds": start,
                        "end_seconds": end,
                        "transcript_segment_ids": evidence_refs,
                    },
                })
                continue
            prerequisites = list(catalog_skills[skill_id]["prerequisite_skill_ids"])
            episode_review_state = catalog_skills[skill_id]["review_state"]
        else:
            skill_id = _candidate_skill(task)
        from_state = prior.get(skill_id, "INTRODUCED")
        # Interaction completeness is not evidence of mastery progression.
        to_state = from_state
        episode_token = _digest(file_id, interaction_id, start, end)
        episode_id = f"evc.video31.{episode_token}"
        claim_id = f"{episode_id}:claim-1"
        occurred_at = (base_date + timedelta(seconds=start)).isoformat().replace("+00:00", "Z")

        candidate = {
            "schema": SCHEMA,
            "course_version": COURSE_VERSION,
            "episode_id": episode_id,
            "occurred_at": occurred_at,
            "source": {
                "video_file_id": file_id,
                "source_name": source_name,
                "start_seconds": start,
                "end_seconds": end,
                "transcript_segment_ids": evidence_refs,
                "frame_sha256": accepted_frames,
                "evidence_state": "VERIFIED",
            },
            "learning_task": {
                "skill_id": skill_id,
                "title": task,
                "prerequisite_skill_ids": prerequisites,
            },
            "interaction": {
                "teacher_actions": [_text(interaction.get("teacher_intervention"))],
                "student_actions": list(
                    dict.fromkeys(
                        [
                            _text(interaction.get("student_action")),
                            _text(interaction.get("student_followup")),
                        ]
                    )
                ),
                "outcome": _outcome(interaction),
                "support_level": _support_level(interaction),
                "completed_cycle": True,
            },
            "claims": [
                {
                    "claim_id": claim_id,
                    "epistemic_class": "INFERENCE",
                    "statement": (
                        "Video 3.1 contains a complete attributed learning cycle "
                        "with learner action, teacher intervention and learner follow-up."
                    ),
                    "source_refs": evidence_refs,
                    "confidence": 0.85,
                }
            ],
            "mastery_transition": {
                "from_state": from_state,
                "to_state": to_state,
                "evidence_claim_ids": [claim_id],
            },
            "authority": {
                "authority_class": "CANDIDATE_RESEARCH",
                "review_state": episode_review_state,
                "canonical_promotion_allowed": False,
                "curriculum_activation_allowed": False,
                "student_profile_write_allowed": False,
                "publication_allowed": False,
            },
        }
        episodes.append(validate_episode(candidate))
        prior[skill_id] = to_state

    return {
        "schema": ADAPTER_SCHEMA,
        "source_quality_schema": quality.get("schema"),
        "source_job_id": source_job_id,
        "skill_binding_mode": "REVIEWED_CATALOG" if normalized_catalog is not None else "LEGACY_TASK_HASH",
        "lesson_date": lesson_identity.get("lesson_date"),
        "accepted_episode_count": len(episodes),
        "rejected_interaction_count": len(rejected),
        "episodes": episodes,
        "rejected_interactions": rejected,
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "canonical_promotion_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
    }


def adapt_video31_quality_with_catalog(
    quality: Mapping[str, Any],
    *,
    lesson_identity: Mapping[str, Any],
    source: Mapping[str, Any],
    catalog: Mapping[str, Any],
    prior_skill_states: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Match evidence-complete episodes to reviewed catalog skills only.

    Unknown or unreviewed wording is retained as a review item.  This function
    never creates a skill identifier from lesson text.
    """
    normalized_catalog = validate_catalog(catalog)
    prior = dict(prior_skill_states or {})
    if any(state not in SKILL_STATES for state in prior.values()):
        raise Video31AdapterError("invalid prior skill state")
    base = adapt_video31_quality(
        quality,
        lesson_identity=lesson_identity,
        source=source,
    )
    skills = {
        skill["skill_id"]: skill for skill in normalized_catalog["skills"]
    }
    episodes: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    for episode in base["episodes"]:
        wording = episode["learning_task"]["title"]
        try:
            skill_id = resolve_reviewed_skill(normalized_catalog, wording)
        except SkillCatalogError:
            review_items.append({
                "episode_id": episode["episode_id"],
                "proposed_alias": wording,
                "match_status": "REVIEW_REQUIRED",
                "reason_codes": ["SKILL_ALIAS_UNKNOWN_OR_UNREVIEWED"],
            })
            continue
        matched = skills[skill_id]
        candidate = dict(episode)
        candidate["learning_task"] = {
            "skill_id": skill_id,
            "title": matched["title"],
            "prerequisite_skill_ids": matched["prerequisite_skill_ids"],
        }
        transition = dict(candidate["mastery_transition"])
        transition["from_state"] = prior.get(skill_id, "INTRODUCED")
        candidate["mastery_transition"] = transition
        episodes.append(validate_episode(candidate))

    return {
        **base,
        "schema": CATALOG_ADAPTER_SCHEMA,
        "catalog_version": normalized_catalog["catalog_version"],
        "accepted_episode_count": len(episodes),
        "catalog_review_item_count": len(review_items),
        "episodes": episodes,
        "catalog_review_items": review_items,
    }


__all__ = [
    "ADAPTER_SCHEMA",
    "CATALOG_ADAPTER_SCHEMA",
    "Video31AdapterError",
    "adapt_video31_quality",
    "adapt_video31_quality_with_catalog",
]
