from copy import deepcopy

import pytest

from evolutionary_course.mastery import (
    MasteryEvidenceError, eligible_next_skills, evaluate_mastery_evidence,
)


def _policy():
    levels = {}
    for level in ("RECOGNIZED", "SUPPORTED", "INDEPENDENT", "TRANSFERRED"):
        levels[level] = {"task_types": [level], "allowed_support_levels": ["NONE"],
                         "minimum_successes": 2, "minimum_independent_contexts": 2,
                         "maximum_errors": 0, "maximum_age_days": 90}
    return {"schema": "evolutionary-course-mastery-policy-v1", "policy_id": "research.policy.synthetic",
            "authority": {"authority_class": "CANDIDATE_RESEARCH",
                          "school_methodology_activation_allowed": False,
                          "student_profile_write_allowed": False}, "levels": levels}


def _event(number, level="RECOGNIZED", context=None, outcome="SUCCESS"):
    return {"event_id": f"event-{number}", "skill_id": "candidate.skill.test",
            "occurred_at": f"2026-08-{number:02d}T10:00:00Z", "origin": "STUDENT_ATTEMPT",
            "task_type": level, "outcome": outcome, "context_id": context or f"ctx-{number}",
            "support_level": "NONE", "source_refs": [f"attempt-{number}"]}


def test_one_correct_answer_does_not_raise_level():
    result = evaluate_mastery_evidence([_event(1)], policy=_policy(),
                                       skill_id="candidate.skill.test", as_of="2026-08-30T00:00:00Z")
    assert result["evidence_state"] == "INTRODUCED"
    assert result["profile_write_performed"] is False


def test_independent_observations_raise_only_contiguous_levels():
    events = [_event(1), _event(2), _event(3, "SUPPORTED"), _event(4, "SUPPORTED")]
    result = evaluate_mastery_evidence(events, policy=_policy(), skill_id="candidate.skill.test",
                                       as_of="2026-08-30T00:00:00Z")
    assert result["evidence_state"] == "SUPPORTED"
    assert "INDEPENDENT" in result["level_results"]
    assert "TRANSFERRED" not in result["level_results"]


def test_video_observation_never_counts_as_student_mastery():
    event = _event(1); event["origin"] = "TEACHER_VIDEO_OBSERVATION"
    with pytest.raises(MasteryEvidenceError, match="actual student attempts"):
        evaluate_mastery_evidence([event], policy=_policy(), skill_id="candidate.skill.test",
                                  as_of="2026-08-30T00:00:00Z")


def test_reprocessing_same_event_is_idempotent_but_conflict_is_rejected():
    event = _event(1)
    result = evaluate_mastery_evidence([event, deepcopy(event)], policy=_policy(),
                                       skill_id="candidate.skill.test", as_of="2026-08-30T00:00:00Z")
    assert result["level_results"]["RECOGNIZED"]["successes"] == 1
    conflict = deepcopy(event); conflict["outcome"] = "ERROR"
    with pytest.raises(MasteryEvidenceError, match="conflicting duplicate"):
        evaluate_mastery_evidence([event, conflict], policy=_policy(),
                                  skill_id="candidate.skill.test", as_of="2026-08-30T00:00:00Z")


def test_expired_evidence_does_not_raise_level():
    events = [_event(1), _event(2)]
    result = evaluate_mastery_evidence(events, policy=_policy(), skill_id="candidate.skill.test",
                                       as_of="2027-08-30T00:00:00Z")
    assert result["evidence_state"] == "INTRODUCED"


def _catalog():
    criteria = {level: [level] for level in ("RECOGNIZED", "SUPPORTED", "INDEPENDENT", "TRANSFERRED")}
    base = {"aliases": [], "mastery_criteria": criteria, "review_state": "APPROVED_CANDIDATE"}
    return {"schema": "school-skill-catalog-v1", "catalog_version": "SCHOOL SKILL CATALOG v1",
            "authority": {"authority_class": "CANDIDATE_RESEARCH", "school_canon_activation_allowed": False,
                          "curriculum_activation_allowed": False, "student_profile_write_allowed": False,
                          "publication_allowed": False},
            "skills": [{**base, "skill_id": "candidate.skill.base", "title": "Основа",
                        "prerequisite_skill_ids": []},
                       {**base, "skill_id": "candidate.skill.next", "title": "Следующий",
                        "prerequisite_skill_ids": ["candidate.skill.base"]}]}


def test_mastered_prerequisite_opens_next_skill():
    assert eligible_next_skills(_catalog(), {}) == ["candidate.skill.base"]
    assert eligible_next_skills(_catalog(), {"candidate.skill.base": "SUPPORTED"}) == ["candidate.skill.base"]
    assert eligible_next_skills(_catalog(), {"candidate.skill.base": "INDEPENDENT"}) == [
        "candidate.skill.base", "candidate.skill.next"]
