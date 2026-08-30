from copy import deepcopy
import json
from pathlib import Path

import pytest

from evolutionary_course.skill_catalog import (
    CATALOG_SCHEMA,
    SkillCatalogError,
    resolve_reviewed_skill,
    validate_catalog,
)


def _catalog():
    path = Path("data/research/evolutionary_course_skill_catalog_v1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_seed_catalog_is_valid_research_only():
    catalog = validate_catalog(_catalog())
    assert catalog["schema"] == CATALOG_SCHEMA
    assert catalog["authority"]["authority_class"] == "CANDIDATE_RESEARCH"
    assert all(value is False for key, value in catalog["authority"].items() if key.endswith("_allowed"))
    assert len(catalog["skills"]) == 3


def test_unknown_and_unreviewed_wording_fail_closed():
    with pytest.raises(SkillCatalogError, match="not uniquely reviewed"):
        resolve_reviewed_skill(_catalog(), "Подсчёт потерь")
    with pytest.raises(SkillCatalogError, match="not uniquely reviewed"):
        resolve_reviewed_skill(_catalog(), "Похожая неизвестная формулировка")


def test_exact_reviewed_alias_resolves_without_similarity_guess():
    catalog = _catalog()
    catalog["skills"][1]["review_state"] = "APPROVED_CANDIDATE"
    assert resolve_reviewed_skill(catalog, "  ПОДСЧЁТ   ПОТЕРЬ ") == "candidate.skill.count-losers"


def test_ambiguous_alias_across_skills_is_rejected():
    catalog = _catalog()
    catalog["skills"][2]["aliases"].append("Подсчёт потерь")
    with pytest.raises(SkillCatalogError, match="ambiguous wording"):
        validate_catalog(catalog)


def test_unknown_and_self_prerequisites_are_rejected():
    catalog = _catalog()
    catalog["skills"][0]["prerequisite_skill_ids"] = ["candidate.skill.missing"]
    with pytest.raises(SkillCatalogError, match="unknown prerequisite"):
        validate_catalog(catalog)
    catalog = _catalog()
    catalog["skills"][0]["prerequisite_skill_ids"] = [catalog["skills"][0]["skill_id"]]
    with pytest.raises(SkillCatalogError, match="require itself"):
        validate_catalog(catalog)


def test_authority_escalation_is_rejected():
    catalog = deepcopy(_catalog())
    catalog["authority"]["school_canon_activation_allowed"] = True
    with pytest.raises(SkillCatalogError, match="authority boundary"):
        validate_catalog(catalog)
