from copy import deepcopy

import pytest

from evolutionary_course.episode_review import (
    EpisodeReviewError, build_episode_review_request, record_episode_review_decision,
)
from evolutionary_course.video31_adapter import adapt_video31_quality
from test_evolutionary_course_video31_adapter import _lesson, _quality, _reviewed_catalog, _source


def _episode_and_catalog():
    catalog = _reviewed_catalog()
    report = adapt_video31_quality(
        _quality(), lesson_identity=_lesson(), source=_source(),
        skill_catalog=catalog, require_catalog_binding=True,
    )
    return report["episodes"][0], catalog


def test_private_episode_review_request_is_unsigned_and_non_mutating():
    episode, catalog = _episode_and_catalog()
    request = build_episode_review_request(episode, catalog=catalog)
    assert request == build_episode_review_request(episode, catalog=catalog)
    assert request["status"] == "AWAITING_PRIVATE_REVIEW"
    assert request["allowed_decisions"] == ["ACCEPT", "REVISE", "REJECT"]
    assert all(value is None for value in request["decision_input"].values())
    assert all(value is False for value in request["authority"].values())
    assert request["review_summary"]["mastery_from_state"] == (
        request["review_summary"]["mastery_to_state"]
    )


def test_private_review_rejects_unapproved_or_mismatched_episode():
    episode, catalog = _episode_and_catalog()
    unapproved = deepcopy(episode)
    unapproved["authority"]["review_state"] = "REVIEW_REQUIRED"
    with pytest.raises(EpisodeReviewError, match="approved candidate episode"):
        build_episode_review_request(unapproved, catalog=catalog)
    mismatched = deepcopy(episode)
    mismatched["learning_task"]["skill_id"] = "candidate.skill.other"
    with pytest.raises(EpisodeReviewError, match="skill binding mismatch"):
        build_episode_review_request(mismatched, catalog=catalog)


@pytest.mark.parametrize("decision", ["ACCEPT", "REVISE", "REJECT"])
def test_private_episode_decision_is_attributed_and_non_persisting(decision):
    episode, catalog = _episode_and_catalog()
    receipt = record_episode_review_decision(
        episode, catalog=catalog, decision=decision, reviewer_id="reviewer-7",
        reviewer_authority="AUTHORIZED_EPISODE_REVIEWER",
        reviewed_at="2026-08-30T21:00:00+03:00", rationale="Evidence reviewed.",
    )
    assert receipt["decision"] == decision
    assert receipt["episode_persisted"] is False
    assert receipt["follow_up_required"] is True
    assert receipt["student_profile_written"] is False


@pytest.mark.parametrize("field,value,error", [
    ("decision", None, "invalid private"),
    ("reviewer_id", "", "reviewer identity"),
    ("reviewer_authority", "BOT", "authorized episode reviewer"),
    ("reviewed_at", "2026-08-30T18:00:00", "timezone"),
    ("rationale", "", "rationale"),
])
def test_private_episode_decision_requires_explicit_authorized_human(field, value, error):
    episode, catalog = _episode_and_catalog()
    kwargs = dict(
        catalog=catalog, decision="ACCEPT", reviewer_id="director",
        reviewer_authority="SCHOOL_DIRECTOR", reviewed_at="2026-08-30T18:00:00Z",
        rationale="Reviewed.",
    )
    kwargs[field] = value
    with pytest.raises(EpisodeReviewError, match=error):
        record_episode_review_decision(episode, **kwargs)
