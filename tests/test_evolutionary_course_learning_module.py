from copy import deepcopy
import json
from pathlib import Path

import pytest

from evolutionary_course.learning_module import LearningModuleError, validate_learning_module


def _catalog():
    return json.loads(Path("data/research/evolutionary_course_skill_catalog_v1.json").read_text(encoding="utf-8"))


def _placeholder(content_id):
    return {"content_id": content_id, "authority_class": "LEARNING_CONTENT",
            "content_status": "PLACEHOLDER", "source_refs": []}


def _module():
    return {"schema": "evolutionary-course-learning-module-v1",
            "module_id": "candidate.module.count-losers", "module_version": "1.0.0",
            "primary_skill_id": "candidate.skill.count-losers",
            "prerequisite_skill_ids": ["candidate.skill.trump-long-hand"],
            "explanations": [_placeholder("learning.explanation.count-losers")],
            "demonstrations": [_placeholder("learning.demo.count-losers")],
            "exercises": {stage: [_placeholder(f"learning.exercise.{stage.lower()}")]
                          for stage in ("RECOGNITION", "SUPPORTED", "INDEPENDENT", "TRANSFER")},
            "typical_errors": [_placeholder("learning.error.count-losers")],
            "mastery_criteria_source": {"catalog_version": "SCHOOL SKILL CATALOG v1",
                                        "skill_id": "candidate.skill.count-losers"},
            "remedial_path": [_placeholder("learning.remedial.count-losers")],
            "review_state": "REVIEW_REQUIRED",
            "authority": {"authority_class": "CANDIDATE_RESEARCH",
                          "school_canon_activation_allowed": False,
                          "curriculum_activation_allowed": False,
                          "student_profile_write_allowed": False,
                          "publication_allowed": False}}


def test_placeholder_module_is_structurally_valid_but_not_published():
    result = validate_learning_module(_module(), catalog=_catalog())
    assert result["review_state"] == "REVIEW_REQUIRED"
    assert result["explanations"][0]["content_status"] == "PLACEHOLDER"
    assert result["authority"]["publication_allowed"] is False


def test_module_cannot_override_catalog_prerequisites():
    module = _module(); module["prerequisite_skill_ids"] = []
    with pytest.raises(LearningModuleError, match="differ from catalog"):
        validate_learning_module(module, catalog=_catalog())


def test_placeholder_cannot_claim_evidence():
    module = _module(); module["explanations"][0]["source_refs"] = ["learning:invented"]
    with pytest.raises(LearningModuleError, match="placeholder cannot claim"):
        validate_learning_module(module, catalog=_catalog())


def test_world_content_cannot_masquerade_as_school_canon():
    module = _module()
    module["explanations"][0] = {"content_id": "world.explanation.example",
        "authority_class": "SCHOOL_CANON", "content_status": "VERIFIED_CONTENT",
        "source_refs": ["world:external-source"]}
    with pytest.raises(LearningModuleError, match="provenance mismatch"):
        validate_learning_module(module, catalog=_catalog())


def test_verified_learning_content_requires_matching_provenance():
    module = deepcopy(_module())
    module["demonstrations"][0] = {"content_id": "learning.demo.verified",
        "authority_class": "LEARNING_CONTENT", "content_status": "VERIFIED_CONTENT",
        "source_refs": ["learning:reviewed-demo-1"]}
    result = validate_learning_module(module, catalog=_catalog())
    assert result["demonstrations"][0]["content_status"] == "VERIFIED_CONTENT"


def test_missing_transfer_stage_is_rejected():
    module = _module(); del module["exercises"]["TRANSFER"]
    with pytest.raises(LearningModuleError, match="exercise stages"):
        validate_learning_module(module, catalog=_catalog())
