"""Policy-driven, research-only mastery evidence evaluation."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .contract import SKILL_STATES
from .skill_catalog import validate_catalog

MASTERY_REPORT_SCHEMA = "evolutionary-course-mastery-evidence-report-v1"
MASTERY_POLICY_SCHEMA = "evolutionary-course-mastery-policy-v1"
_LEVELS = ("RECOGNIZED", "SUPPORTED", "INDEPENDENT", "TRANSFERRED")
_TASK_TYPES = set(_LEVELS)
_SUPPORT_LEVELS = {"NONE", "PROMPT", "GUIDED", "MODELLED"}


class MasteryEvidenceError(ValueError):
    """Mastery evidence or policy is unsafe to consume."""


def _utc(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise MasteryEvidenceError("invalid evidence timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MasteryEvidenceError("evidence timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def validate_mastery_policy(candidate: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, Mapping) or set(candidate) != {
        "schema", "policy_id", "authority", "levels"
    }:
        raise MasteryEvidenceError("mastery policy fields mismatch")
    if candidate.get("schema") != MASTERY_POLICY_SCHEMA:
        raise MasteryEvidenceError("mastery policy schema mismatch")
    authority = candidate.get("authority")
    if authority != {
        "authority_class": "CANDIDATE_RESEARCH",
        "school_methodology_activation_allowed": False,
        "student_profile_write_allowed": False,
    }:
        raise MasteryEvidenceError("mastery policy authority mismatch")
    levels = candidate.get("levels")
    if not isinstance(levels, Mapping) or set(levels) != set(_LEVELS):
        raise MasteryEvidenceError("mastery policy levels mismatch")
    normalized_levels: dict[str, Any] = {}
    for level in _LEVELS:
        rule = levels[level]
        if not isinstance(rule, Mapping) or set(rule) != {
            "task_types", "allowed_support_levels", "minimum_successes",
            "minimum_independent_contexts", "maximum_errors", "maximum_age_days",
        }:
            raise MasteryEvidenceError("mastery level rule fields mismatch")
        tasks = list(rule["task_types"]) if isinstance(rule["task_types"], list) else []
        supports = (list(rule["allowed_support_levels"])
                    if isinstance(rule["allowed_support_levels"], list) else [])
        if not tasks or not set(tasks) <= _TASK_TYPES:
            raise MasteryEvidenceError("invalid mastery task types")
        if not supports or not set(supports) <= _SUPPORT_LEVELS:
            raise MasteryEvidenceError("invalid mastery support levels")
        numeric = {}
        for field in ("minimum_successes", "minimum_independent_contexts", "maximum_errors", "maximum_age_days"):
            value = rule[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MasteryEvidenceError("invalid mastery threshold")
            numeric[field] = value
        if numeric["minimum_successes"] < 2:
            raise MasteryEvidenceError("single observation cannot establish mastery level")
        if numeric["minimum_independent_contexts"] < 1:
            raise MasteryEvidenceError("independent context required")
        normalized_levels[level] = {
            "task_types": sorted(set(tasks)),
            "allowed_support_levels": sorted(set(supports)),
            **numeric,
        }
    return {**dict(candidate), "levels": normalized_levels}


def evaluate_mastery_evidence(
    events: Iterable[Mapping[str, Any]], *, policy: Mapping[str, Any],
    skill_id: str, current_state: str = "INTRODUCED", as_of: str,
) -> dict[str, Any]:
    """Evaluate actual learner attempts; never writes a Student Profile."""
    normalized_policy = validate_mastery_policy(policy)
    if current_state not in SKILL_STATES:
        raise MasteryEvidenceError("invalid current skill state")
    now = _utc(as_of)
    unique: dict[str, dict[str, Any]] = {}
    for raw in events:
        if not isinstance(raw, Mapping):
            raise MasteryEvidenceError("mastery event must be an object")
        event = dict(raw)
        required = {"event_id", "skill_id", "occurred_at", "origin", "task_type",
                    "outcome", "context_id", "support_level", "source_refs"}
        if set(event) != required:
            raise MasteryEvidenceError("mastery event fields mismatch")
        event_id = str(event["event_id"]).strip()
        if not event_id or event["skill_id"] != skill_id:
            raise MasteryEvidenceError("invalid mastery event identity")
        if event["origin"] != "STUDENT_ATTEMPT":
            raise MasteryEvidenceError("only actual student attempts count")
        if event["task_type"] not in _TASK_TYPES or event["outcome"] not in {"SUCCESS", "ERROR"}:
            raise MasteryEvidenceError("invalid mastery event result")
        if event["support_level"] not in _SUPPORT_LEVELS or not str(event["context_id"]).strip():
            raise MasteryEvidenceError("invalid mastery event context")
        if not isinstance(event["source_refs"], list) or not event["source_refs"]:
            raise MasteryEvidenceError("mastery evidence source required")
        event["occurred_at"] = _utc(event["occurred_at"])
        previous = unique.get(event_id)
        if previous is not None and previous != event:
            raise MasteryEvidenceError("conflicting duplicate mastery event")
        unique[event_id] = event

    level_results: dict[str, Any] = {}
    achieved = current_state
    for level in _LEVELS:
        rule = normalized_policy["levels"][level]
        eligible = [event for event in unique.values()
                    if event["task_type"] in rule["task_types"]
                    and event["support_level"] in rule["allowed_support_levels"]
                    and 0 <= (now - event["occurred_at"]).days <= rule["maximum_age_days"]]
        successes = [event for event in eligible if event["outcome"] == "SUCCESS"]
        errors = [event for event in eligible if event["outcome"] == "ERROR"]
        contexts = {event["context_id"] for event in successes}
        passed = (len(successes) >= rule["minimum_successes"]
                  and len(contexts) >= rule["minimum_independent_contexts"]
                  and len(errors) <= rule["maximum_errors"])
        level_results[level] = {"passed": passed, "successes": len(successes),
                                "errors": len(errors), "independent_contexts": len(contexts)}
        if passed:
            achieved = level
        else:
            break
    return {
        "schema": MASTERY_REPORT_SCHEMA, "policy_id": normalized_policy["policy_id"],
        "skill_id": skill_id, "from_state": current_state, "evidence_state": achieved,
        "profile_write_performed": False, "level_results": level_results,
    }


def eligible_next_skills(catalog: Mapping[str, Any], skill_states: Mapping[str, str]) -> list[str]:
    """Return reviewed skills whose prerequisites have independent evidence."""
    normalized = validate_catalog(catalog)
    rank = {state: index for index, state in enumerate(SKILL_STATES)}
    if any(state not in rank for state in skill_states.values()):
        raise MasteryEvidenceError("invalid prerequisite state")
    threshold = rank["INDEPENDENT"]
    return sorted(
        skill["skill_id"] for skill in normalized["skills"]
        if skill["review_state"] == "APPROVED_CANDIDATE"
        and all(rank.get(skill_states.get(required, "NOT_INTRODUCED"), -1) >= threshold
                for required in skill["prerequisite_skill_ids"])
    )


__all__ = ["MASTERY_POLICY_SCHEMA", "MASTERY_REPORT_SCHEMA", "MasteryEvidenceError",
           "eligible_next_skills", "evaluate_mastery_evidence", "validate_mastery_policy"]
