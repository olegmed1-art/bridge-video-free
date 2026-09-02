"""Research-only stable skill catalog for Evolutionary Course v1."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

CATALOG_SCHEMA = "school-skill-catalog-v1"
CATALOG_VERSION = "SCHOOL SKILL CATALOG v1"
AUTHORITY_CLASS = "CANDIDATE_RESEARCH"
_REQUIRED_CRITERIA = ("RECOGNIZED", "SUPPORTED", "INDEPENDENT", "TRANSFERRED")
_SKILL_ID = re.compile(r"^candidate\.skill\.[a-z0-9][a-z0-9._-]{2,95}$")


class SkillCatalogError(ValueError):
    """Catalog cannot be consumed safely."""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value: Any) -> str:
    # Aliases are reviewed explicitly; normalization only removes presentation
    # differences and never guesses semantic similarity.
    return re.sub(r"[\W_]+", " ", _text(value).casefold(), flags=re.UNICODE).strip()


def _reject_prerequisite_cycles(skills: list[dict[str, Any]]) -> None:
    graph = {
        skill["skill_id"]: tuple(skill["prerequisite_skill_ids"])
        for skill in skills
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(skill_id: str) -> None:
        if skill_id in visiting:
            raise SkillCatalogError("cyclic prerequisite dependency")
        if skill_id in visited:
            return
        visiting.add(skill_id)
        for prerequisite in graph[skill_id]:
            visit(prerequisite)
        visiting.remove(skill_id)
        visited.add(skill_id)

    for skill_id in sorted(graph):
        visit(skill_id)


def _strings(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise SkillCatalogError(f"invalid {label}")
    result = [_text(item) for item in value]
    if any(not item for item in result):
        raise SkillCatalogError(f"invalid {label}")
    if len({_key(item) for item in result}) != len(result):
        raise SkillCatalogError(f"duplicate {label}")
    return result


def validate_catalog(candidate: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise SkillCatalogError("catalog must be an object")
    if set(candidate) != {"schema", "catalog_version", "authority", "skills"}:
        raise SkillCatalogError("catalog fields mismatch")
    if candidate.get("schema") != CATALOG_SCHEMA:
        raise SkillCatalogError("schema mismatch")
    if candidate.get("catalog_version") != CATALOG_VERSION:
        raise SkillCatalogError("catalog version mismatch")
    authority = candidate.get("authority")
    expected_authority = {
        "authority_class": AUTHORITY_CLASS,
        "school_canon_activation_allowed": False,
        "curriculum_activation_allowed": False,
        "student_profile_write_allowed": False,
        "publication_allowed": False,
    }
    if authority != expected_authority:
        raise SkillCatalogError("authority boundary mismatch")
    skills = candidate.get("skills")
    if not isinstance(skills, list) or not skills:
        raise SkillCatalogError("skills required")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    wording_owner: dict[str, str] = {}
    for skill in skills:
        if not isinstance(skill, Mapping) or set(skill) != {
            "skill_id", "title", "aliases", "prerequisite_skill_ids",
            "mastery_criteria", "review_state",
        }:
            raise SkillCatalogError("skill fields mismatch")
        skill_id = _text(skill.get("skill_id"))
        if not _SKILL_ID.fullmatch(skill_id) or skill_id in ids:
            raise SkillCatalogError("invalid or duplicate skill_id")
        ids.add(skill_id)
        title = _text(skill.get("title"))
        if not title:
            raise SkillCatalogError("skill title required")
        aliases = _strings(skill.get("aliases"), "aliases", allow_empty=True)
        prerequisites = _strings(
            skill.get("prerequisite_skill_ids"), "prerequisites", allow_empty=True
        )
        if skill_id in prerequisites:
            raise SkillCatalogError("skill cannot require itself")
        criteria = skill.get("mastery_criteria")
        if not isinstance(criteria, Mapping) or set(criteria) != set(_REQUIRED_CRITERIA):
            raise SkillCatalogError("mastery criteria fields mismatch")
        normalized_criteria = {
            state: _strings(criteria.get(state), f"{state} criteria")
            for state in _REQUIRED_CRITERIA
        }
        if skill.get("review_state") not in {"DRAFT", "REVIEW_REQUIRED", "APPROVED_CANDIDATE"}:
            raise SkillCatalogError("invalid review_state")
        for wording in [title, *aliases]:
            key = _key(wording)
            owner = wording_owner.get(key)
            if owner is not None and owner != skill_id:
                raise SkillCatalogError("ambiguous wording across skills")
            wording_owner[key] = skill_id
        normalized.append({
            "skill_id": skill_id,
            "title": title,
            "aliases": aliases,
            "prerequisite_skill_ids": prerequisites,
            "mastery_criteria": normalized_criteria,
            "review_state": skill.get("review_state"),
        })
    unknown = {
        prerequisite
        for skill in normalized
        for prerequisite in skill["prerequisite_skill_ids"]
        if prerequisite not in ids
    }
    if unknown:
        raise SkillCatalogError("unknown prerequisite skill")
    _reject_prerequisite_cycles(normalized)
    result = deepcopy(dict(candidate))
    result["skills"] = sorted(normalized, key=lambda item: item["skill_id"])
    return result


def resolve_reviewed_skill(catalog: Mapping[str, Any], wording: str) -> str:
    """Resolve only an exact reviewed title/alias; unknown wording fails closed."""
    normalized = validate_catalog(catalog)
    target = _key(wording)
    if not target:
        raise SkillCatalogError("wording required")
    matches = [
        skill["skill_id"]
        for skill in normalized["skills"]
        if skill["review_state"] == "APPROVED_CANDIDATE"
        and target in {_key(skill["title"]), *(_key(a) for a in skill["aliases"])}
    ]
    if len(matches) != 1:
        raise SkillCatalogError("wording is not uniquely reviewed")
    return matches[0]


__all__ = [
    "AUTHORITY_CLASS", "CATALOG_SCHEMA", "CATALOG_VERSION",
    "SkillCatalogError", "resolve_reviewed_skill", "validate_catalog",
]
