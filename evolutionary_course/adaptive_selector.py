"""Deterministic, read-only next-activity selection for Course v1."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .contract import SKILL_STATES
from .skill_catalog import validate_catalog

SELECTOR_SCHEMA = "evolutionary-course-adaptive-selection-v1"
SELECTOR_POLICY_SCHEMA = "evolutionary-course-adaptive-policy-v1"
_STAGES = {"RECOGNITION", "SUPPORTED", "INDEPENDENT", "TRANSFER", "REVIEW"}
_AUTHORITIES = {"SCHOOL_CANON", "WORLD", "CANDIDATE_RESEARCH", "LEARNING_CONTENT"}


class AdaptiveSelectorError(ValueError):
    """Selection input violates a safety or determinism boundary."""


def _utc(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdaptiveSelectorError("invalid selector timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdaptiveSelectorError("selector timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _policy(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "policy_id", "authority", "required_prerequisite_state",
        "review_after_days", "stage_by_state"
    }:
        raise AdaptiveSelectorError("selector policy fields mismatch")
    if value.get("schema") != SELECTOR_POLICY_SCHEMA:
        raise AdaptiveSelectorError("selector policy schema mismatch")
    if value.get("authority") != {"authority_class": "CANDIDATE_RESEARCH",
                                  "curriculum_activation_allowed": False,
                                  "student_profile_write_allowed": False}:
        raise AdaptiveSelectorError("selector policy authority mismatch")
    required = value.get("required_prerequisite_state")
    if required not in SKILL_STATES:
        raise AdaptiveSelectorError("invalid prerequisite policy state")
    days = value.get("review_after_days")
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise AdaptiveSelectorError("invalid review interval")
    mapping = value.get("stage_by_state")
    if not isinstance(mapping, Mapping) or set(mapping) != set(SKILL_STATES):
        raise AdaptiveSelectorError("stage mapping mismatch")
    if any(stage not in _STAGES for stage in mapping.values()):
        raise AdaptiveSelectorError("invalid mapped activity stage")
    return dict(value)


def _activity(value: Mapping[str, Any], skill_ids: set[str]) -> dict[str, Any]:
    required = {"activity_id", "skill_id", "stage", "format", "duration_minutes",
                "difficulty", "authority_class", "content_status", "source_refs",
                "school_rule_claim", "hidden_information_used"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise AdaptiveSelectorError("activity fields mismatch")
    result = dict(value)
    if not str(result["activity_id"]).strip() or result["skill_id"] not in skill_ids:
        raise AdaptiveSelectorError("invalid activity identity")
    if result["stage"] not in _STAGES or not str(result["format"]).strip():
        raise AdaptiveSelectorError("invalid activity mode")
    if (isinstance(result["duration_minutes"], bool)
            or not isinstance(result["duration_minutes"], int)
            or result["duration_minutes"] < 1):
        raise AdaptiveSelectorError("invalid activity duration")
    if (isinstance(result["difficulty"], bool) or not isinstance(result["difficulty"], int)
            or not 1 <= result["difficulty"] <= 10):
        raise AdaptiveSelectorError("invalid activity difficulty")
    if result["authority_class"] not in _AUTHORITIES:
        raise AdaptiveSelectorError("invalid activity authority")
    if result["content_status"] not in {"PLACEHOLDER", "VERIFIED_CONTENT"}:
        raise AdaptiveSelectorError("invalid activity content status")
    if not isinstance(result["source_refs"], list):
        raise AdaptiveSelectorError("invalid activity source refs")
    if result["content_status"] == "VERIFIED_CONTENT" and not result["source_refs"]:
        raise AdaptiveSelectorError("verified activity needs provenance")
    if result["authority_class"] == "WORLD" and result["school_rule_claim"] is not False:
        raise AdaptiveSelectorError("WORLD activity cannot claim school rule")
    if result["hidden_information_used"] is not False:
        raise AdaptiveSelectorError("hidden information is forbidden")
    return result


def select_next_activity(
    *, catalog: Mapping[str, Any], activities: Iterable[Mapping[str, Any]],
    profile_snapshot: Mapping[str, Any], session: Mapping[str, Any],
    policy: Mapping[str, Any], as_of: str,
) -> dict[str, Any]:
    """Choose one eligible verified activity without mutating student state."""
    normalized_catalog = validate_catalog(catalog)
    normalized_policy = _policy(policy)
    now = _utc(as_of)
    skills = {skill["skill_id"]: skill for skill in normalized_catalog["skills"]}
    if not isinstance(profile_snapshot, Mapping) or set(profile_snapshot) != {
        "skill_states", "error_counts", "last_success_at"
    }:
        raise AdaptiveSelectorError("profile snapshot fields mismatch")
    states = dict(profile_snapshot["skill_states"])
    errors = dict(profile_snapshot["error_counts"])
    last_success = dict(profile_snapshot["last_success_at"])
    if any(skill_id not in skills or state not in SKILL_STATES for skill_id, state in states.items()):
        raise AdaptiveSelectorError("invalid profile skill state")
    if any(skill_id not in skills or isinstance(count, bool) or not isinstance(count, int) or count < 0
           for skill_id, count in errors.items()):
        raise AdaptiveSelectorError("invalid profile error count")
    if any(skill_id not in skills for skill_id in last_success):
        raise AdaptiveSelectorError("invalid profile recency skill")
    if not isinstance(session, Mapping) or set(session) != {"format", "available_minutes", "max_difficulty"}:
        raise AdaptiveSelectorError("session fields mismatch")
    if not str(session["format"]).strip() or any(
        isinstance(session[field], bool) or not isinstance(session[field], int) or session[field] < 1
        for field in ("available_minutes", "max_difficulty")
    ):
        raise AdaptiveSelectorError("invalid session limits")
    rank = {state: index for index, state in enumerate(SKILL_STATES)}
    prerequisite_rank = rank[normalized_policy["required_prerequisite_state"]]
    normalized_activities = [_activity(item, set(skills)) for item in activities]
    ids = [item["activity_id"] for item in normalized_activities]
    if len(ids) != len(set(ids)):
        raise AdaptiveSelectorError("duplicate activity_id")
    candidates = []
    blockers = []
    for activity in normalized_activities:
        skill = skills[activity["skill_id"]]
        missing = [required for required in skill["prerequisite_skill_ids"]
                   if rank[states.get(required, "NOT_INTRODUCED")] < prerequisite_rank]
        if missing:
            blockers.append({"activity_id": activity["activity_id"],
                             "reason": "PREREQUISITE_NOT_MET", "skill_ids": missing})
            continue
        state = states.get(activity["skill_id"], "NOT_INTRODUCED")
        desired_stage = normalized_policy["stage_by_state"][state]
        last = _utc(last_success[activity["skill_id"]]) if activity["skill_id"] in last_success else None
        overdue = last is not None and (now - last).days >= normalized_policy["review_after_days"]
        if overdue:
            desired_stage = "REVIEW"
        if activity["stage"] != desired_stage or activity["format"] != session["format"]:
            continue
        if activity["duration_minutes"] > session["available_minutes"] or activity["difficulty"] > session["max_difficulty"]:
            continue
        if activity["content_status"] != "VERIFIED_CONTENT":
            blockers.append({"activity_id": activity["activity_id"],
                             "reason": "CONTENT_REVIEW_REQUIRED", "skill_ids": []})
            continue
        age = (now - last).days if last is not None else 10**6
        score = (-errors.get(activity["skill_id"], 0), -age,
                 activity["difficulty"], activity["duration_minutes"], activity["activity_id"])
        candidates.append((score, activity))
    selected = min(candidates, default=(None, None), key=lambda item: item[0])[1]
    return {"schema": SELECTOR_SCHEMA, "policy_id": normalized_policy["policy_id"],
            "status": "SELECTED" if selected else "NO_ELIGIBLE_ACTIVITY",
            "selected_activity": selected, "blockers": sorted(blockers, key=lambda item: item["activity_id"]),
            "student_profile_write_performed": False,
            "authority": {"authority_class": "CANDIDATE_RESEARCH",
                          "curriculum_activation_allowed": False, "publication_allowed": False}}


__all__ = ["SELECTOR_POLICY_SCHEMA", "SELECTOR_SCHEMA", "AdaptiveSelectorError",
           "select_next_activity"]
