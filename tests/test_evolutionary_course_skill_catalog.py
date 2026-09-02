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


def _skill(catalog, skill_id):
    return next(item for item in catalog["skills"] if item["skill_id"] == skill_id)


def test_seed_catalog_is_valid_research_only():
    catalog = validate_catalog(_catalog())
    assert catalog["schema"] == CATALOG_SCHEMA
    assert catalog["authority"]["authority_class"] == "CANDIDATE_RESEARCH"
    assert all(value is False for key, value in catalog["authority"].items() if key.endswith("_allowed"))
    assert len(catalog["skills"]) == 4


def test_diana2_probability_candidate_is_exact_and_stays_review_required():
    path = Path(
        "data/research/evolutionary_course_diana2_club_split_skill_candidate_v1.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    check = candidate["independent_probability_check"]
    assert sum(item["numerator"] for item in check["splits"]) == check["denominator"]["value"]
    split_32 = next(item for item in check["splits"] if item["split"] == "3-2")
    assert split_32["numerator"] / check["denominator"]["value"] == pytest.approx(
        split_32["probability"]
    )
    assert split_32["percent"] == pytest.approx(67.8260869565)
    assert candidate["authority"]["review_state"] == "REVIEW_REQUIRED"
    assert candidate["next_gate"] == "HUMAN_METHODOLOGY_APPROVAL_REQUIRED"


def test_unknown_and_unreviewed_wording_fail_closed():
    with pytest.raises(SkillCatalogError, match="not uniquely reviewed"):
        resolve_reviewed_skill(_catalog(), "Подсчёт потерь")
    with pytest.raises(SkillCatalogError, match="not uniquely reviewed"):
        resolve_reviewed_skill(_catalog(), "Похожая неизвестная формулировка")
    assert resolve_reviewed_skill(
        _catalog(),
        "Какие у нас шансы, примерно? А какие у нас шансы разыграть трефу?",
    ) == "candidate.skill.estimate-five-card-split"


def test_exact_reviewed_alias_resolves_without_similarity_guess():
    catalog = _catalog()
    _skill(catalog, "candidate.skill.count-losers")["review_state"] = "APPROVED_CANDIDATE"
    assert resolve_reviewed_skill(catalog, "  ПОДСЧЁТ   ПОТЕРЬ ") == "candidate.skill.count-losers"


def test_reviewed_alias_ignores_presentation_punctuation_only():
    catalog = _catalog()
    _skill(catalog, "candidate.skill.count-losers")["review_state"] = "APPROVED_CANDIDATE"
    assert resolve_reviewed_skill(catalog, "Подсчёт: потерь!") == "candidate.skill.count-losers"
    with pytest.raises(SkillCatalogError, match="not uniquely reviewed"):
        resolve_reviewed_skill(catalog, "Оценка потерь")


def test_ambiguous_alias_across_skills_is_rejected():
    catalog = _catalog()
    _skill(catalog, "candidate.skill.eliminate-extra-loser")["aliases"].append(
        "Подсчёт потерь"
    )
    with pytest.raises(SkillCatalogError, match="ambiguous wording"):
        validate_catalog(catalog)


def test_unknown_and_self_prerequisites_are_rejected():
    catalog = _catalog()
    _skill(catalog, "candidate.skill.trump-long-hand")["prerequisite_skill_ids"] = [
        "candidate.skill.missing"
    ]
    with pytest.raises(SkillCatalogError, match="unknown prerequisite"):
        validate_catalog(catalog)
    catalog = _catalog()
    skill = _skill(catalog, "candidate.skill.trump-long-hand")
    skill["prerequisite_skill_ids"] = [skill["skill_id"]]
    with pytest.raises(SkillCatalogError, match="require itself"):
        validate_catalog(catalog)


def test_multi_skill_prerequisite_cycle_is_rejected():
    catalog = _catalog()
    _skill(catalog, "candidate.skill.trump-long-hand")["prerequisite_skill_ids"] = [
        "candidate.skill.eliminate-extra-loser"
    ]
    with pytest.raises(SkillCatalogError, match="cyclic prerequisite"):
        validate_catalog(catalog)


def test_authority_escalation_is_rejected():
    catalog = deepcopy(_catalog())
    catalog["authority"]["school_canon_activation_allowed"] = True
    with pytest.raises(SkillCatalogError, match="authority boundary"):
        validate_catalog(catalog)
