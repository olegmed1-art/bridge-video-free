import json
from pathlib import Path

import pytest

from evolutionary_course.adaptive_selector import AdaptiveSelectorError, select_next_activity


def _catalog():
    data = json.loads(Path("data/research/evolutionary_course_skill_catalog_v1.json").read_text(encoding="utf-8"))
    for skill in data["skills"]:
        skill["review_state"] = "APPROVED_CANDIDATE"
    return data


def _policy():
    mapping = {state: "RECOGNITION" for state in ("NOT_INTRODUCED", "INTRODUCED", "UNSTABLE", "MASTERED")}
    mapping.update({"RECOGNIZED": "SUPPORTED", "SUPPORTED": "INDEPENDENT",
                    "INDEPENDENT": "TRANSFER", "TRANSFERRED": "REVIEW"})
    return {"schema": "evolutionary-course-adaptive-policy-v1", "policy_id": "research.selector.synthetic",
            "authority": {"authority_class": "CANDIDATE_RESEARCH", "curriculum_activation_allowed": False,
                          "student_profile_write_allowed": False},
            "required_prerequisite_state": "INDEPENDENT", "review_after_days": 30,
            "stage_by_state": mapping}


def _activity(activity_id, skill_id, stage="RECOGNITION", **overrides):
    value = {"activity_id": activity_id, "skill_id": skill_id, "stage": stage, "format": "ONLINE",
             "duration_minutes": 10, "difficulty": 3, "authority_class": "LEARNING_CONTENT",
             "content_status": "VERIFIED_CONTENT", "source_refs": ["learning:fixture"],
             "school_rule_claim": False, "hidden_information_used": False}
    value.update(overrides); return value


def _select(activities, states=None, errors=None, last=None):
    return select_next_activity(catalog=_catalog(), activities=activities,
        profile_snapshot={"skill_states": states or {}, "error_counts": errors or {},
                          "last_success_at": last or {}},
        session={"format": "ONLINE", "available_minutes": 20, "max_difficulty": 5},
        policy=_policy(), as_of="2026-08-30T00:00:00Z")


def test_unmet_prerequisite_blocks_next_skill():
    activity = _activity("activity.next", "candidate.skill.count-losers")
    report = _select([activity])
    assert report["status"] == "NO_ELIGIBLE_ACTIVITY"
    assert report["blockers"][0]["reason"] == "PREREQUISITE_NOT_MET"


def test_independent_prerequisite_opens_next_skill():
    activity = _activity("activity.next", "candidate.skill.count-losers")
    report = _select([activity], states={"candidate.skill.trump-long-hand": "INDEPENDENT"})
    assert report["selected_activity"]["activity_id"] == "activity.next"
    assert report["student_profile_write_performed"] is False


def test_errors_and_recency_prioritize_deterministically():
    activities = [_activity("activity.a", "candidate.skill.trump-long-hand"),
                  _activity("activity.b", "candidate.skill.eliminate-extra-loser")]
    states = {"candidate.skill.count-losers": "INDEPENDENT"}
    report = _select(activities, states=states,
                     errors={"candidate.skill.eliminate-extra-loser": 3})
    assert report["selected_activity"]["activity_id"] == "activity.b"


def test_placeholder_surfaces_gap_instead_of_being_selected():
    activity = _activity("activity.placeholder", "candidate.skill.trump-long-hand",
                         content_status="PLACEHOLDER", source_refs=[])
    report = _select([activity])
    assert report["status"] == "NO_ELIGIBLE_ACTIVITY"
    assert report["blockers"][0]["reason"] == "CONTENT_REVIEW_REQUIRED"


def test_world_cannot_claim_school_rule():
    activity = _activity("activity.world", "candidate.skill.trump-long-hand",
                         authority_class="WORLD", school_rule_claim=True,
                         source_refs=["world:source"])
    with pytest.raises(AdaptiveSelectorError, match="cannot claim school rule"):
        _select([activity])


def test_hidden_cards_are_rejected():
    activity = _activity("activity.hidden", "candidate.skill.trump-long-hand",
                         hidden_information_used=True)
    with pytest.raises(AdaptiveSelectorError, match="hidden information"):
        _select([activity])


def test_explicit_review_policy_selects_overdue_activity():
    activity = _activity("activity.review", "candidate.skill.trump-long-hand", stage="REVIEW")
    report = _select([activity], states={"candidate.skill.trump-long-hand": "INDEPENDENT"},
                     last={"candidate.skill.trump-long-hand": "2026-01-01T00:00:00Z"})
    assert report["selected_activity"]["activity_id"] == "activity.review"
