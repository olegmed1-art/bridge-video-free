"""Research-only learning-module contract for Evolutionary Course v1."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from .skill_catalog import validate_catalog

MODULE_SCHEMA = "evolutionary-course-learning-module-v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")
_STAGES = ("RECOGNITION", "SUPPORTED", "INDEPENDENT", "TRANSFER")
_AUTHORITIES = {"SCHOOL_CANON", "WORLD", "CANDIDATE_RESEARCH", "LEARNING_CONTENT"}
_PREFIX = {"SCHOOL_CANON": "school-canon:", "WORLD": "world:",
           "CANDIDATE_RESEARCH": "candidate:", "LEARNING_CONTENT": "learning:"}


class LearningModuleError(ValueError):
    """A module cannot be consumed safely."""


def _asset(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "content_id", "authority_class", "content_status", "source_refs"
    }:
        raise LearningModuleError(f"{label} asset fields mismatch")
    content_id = str(value.get("content_id") or "").strip()
    authority = value.get("authority_class")
    status = value.get("content_status")
    refs = value.get("source_refs")
    if not _ID.fullmatch(content_id) or authority not in _AUTHORITIES:
        raise LearningModuleError(f"invalid {label} asset identity")
    if status not in {"PLACEHOLDER", "VERIFIED_CONTENT"} or not isinstance(refs, list):
        raise LearningModuleError(f"invalid {label} content status")
    refs = [str(ref).strip() for ref in refs]
    if any(not ref for ref in refs) or len(refs) != len(set(refs)):
        raise LearningModuleError(f"invalid {label} source refs")
    if status == "PLACEHOLDER" and refs:
        raise LearningModuleError("placeholder cannot claim evidence")
    if status == "VERIFIED_CONTENT":
        if not refs or any(not ref.startswith(_PREFIX[authority]) for ref in refs):
            raise LearningModuleError("verified content provenance mismatch")
    return {"content_id": content_id, "authority_class": authority,
            "content_status": status, "source_refs": refs}


def _assets(value: Any, label: str, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise LearningModuleError(f"{label} assets required")
    result = [_asset(item, label) for item in value]
    ids = [item["content_id"] for item in result]
    if len(ids) != len(set(ids)):
        raise LearningModuleError(f"duplicate {label} content_id")
    return result


def validate_learning_module(candidate: Mapping[str, Any], *, catalog: Mapping[str, Any]) -> dict[str, Any]:
    normalized_catalog = validate_catalog(catalog)
    required = {"schema", "module_id", "module_version", "primary_skill_id",
                "prerequisite_skill_ids", "explanations", "demonstrations", "exercises",
                "typical_errors", "mastery_criteria_source", "remedial_path",
                "review_state", "authority"}
    if not isinstance(candidate, Mapping) or set(candidate) != required:
        raise LearningModuleError("module fields mismatch")
    if candidate.get("schema") != MODULE_SCHEMA:
        raise LearningModuleError("module schema mismatch")
    module_id = str(candidate.get("module_id") or "").strip()
    version = str(candidate.get("module_version") or "").strip()
    if not _ID.fullmatch(module_id) or not version:
        raise LearningModuleError("invalid module identity")
    skills = {item["skill_id"]: item for item in normalized_catalog["skills"]}
    skill_id = candidate.get("primary_skill_id")
    if skill_id not in skills:
        raise LearningModuleError("unknown primary skill")
    prerequisites = candidate.get("prerequisite_skill_ids")
    if prerequisites != skills[skill_id]["prerequisite_skill_ids"]:
        raise LearningModuleError("module prerequisites differ from catalog")
    explanations = _assets(candidate.get("explanations"), "explanation")
    demonstrations = _assets(candidate.get("demonstrations"), "demonstration", allow_empty=True)
    exercises = candidate.get("exercises")
    if not isinstance(exercises, Mapping) or set(exercises) != set(_STAGES):
        raise LearningModuleError("exercise stages mismatch")
    normalized_exercises = {stage: _assets(exercises[stage], stage, allow_empty=True)
                            for stage in _STAGES}
    errors = _assets(candidate.get("typical_errors"), "typical error", allow_empty=True)
    remedial = _assets(candidate.get("remedial_path"), "remedial", allow_empty=True)
    criteria = candidate.get("mastery_criteria_source")
    if criteria != {"catalog_version": normalized_catalog["catalog_version"],
                    "skill_id": skill_id}:
        raise LearningModuleError("mastery criteria source mismatch")
    if candidate.get("review_state") not in {"DRAFT", "REVIEW_REQUIRED"}:
        raise LearningModuleError("module is not research-review state")
    authority = candidate.get("authority")
    if authority != {"authority_class": "CANDIDATE_RESEARCH",
                     "school_canon_activation_allowed": False,
                     "curriculum_activation_allowed": False,
                     "student_profile_write_allowed": False,
                     "publication_allowed": False}:
        raise LearningModuleError("module authority mismatch")
    result = deepcopy(dict(candidate))
    result.update({"module_id": module_id, "module_version": version,
                   "explanations": explanations, "demonstrations": demonstrations,
                   "exercises": normalized_exercises, "typical_errors": errors,
                   "remedial_path": remedial})
    return result


__all__ = ["MODULE_SCHEMA", "LearningModuleError", "validate_learning_module"]
