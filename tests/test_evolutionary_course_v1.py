from __future__ import annotations

from copy import deepcopy

import pytest

from evolutionary_course.contract import (
    AUTHORITY_CLASS,
    COURSE_VERSION,
    EpisodeContractError,
    SCHEMA,
    build_skill_trajectory,
    canonical_sha256,
    validate_episode,
)


def _episode(
    *,
    episode_id: str = "diana.synthetic.episode-001",
    occurred_at: str = "2026-08-01T10:00:00+03:00",
    from_state: str = "NOT_INTRODUCED",
    to_state: str = "INTRODUCED",
    start: float = 120.0,
) -> dict:
    segment_id = f"{episode_id}:segment-1"
    return {
        "schema": SCHEMA,
        "course_version": COURSE_VERSION,
        "episode_id": episode_id,
        "occurred_at": occurred_at,
        "source": {
            "video_file_id": "synthetic-drive-file-id",
            "source_name": "synthetic-diana-shaped-lesson.mp4",
            "start_seconds": start,
            "end_seconds": start + 90.0,
            "transcript_segment_ids": [segment_id],
            "frame_sha256": ["a" * 64],
            "evidence_state": "VERIFIED",
        },
        "learning_task": {
            "skill_id": "play.trump.count-losses",
            "title": "Count potential losers before drawing trumps",
            "prerequisite_skill_ids": ["play.contract-identification"],
        },
        "interaction": {
            "teacher_actions": ["ASKED_FOR_PLAN", "EXPLAINED_LOSER_COUNT"],
            "student_actions": ["PROPOSED_PLAN", "REVISED_PLAN"],
            "outcome": "PARTIAL",
            "support_level": "GUIDED",
            "completed_cycle": True,
        },
        "claims": [
            {
                "claim_id": f"{episode_id}:claim-1",
                "epistemic_class": "FACT",
                "statement": "The learner revised the plan after a guided prompt.",
                "source_refs": [segment_id],
                "confidence": 0.99,
            }
        ],
        "mastery_transition": {
            "from_state": from_state,
            "to_state": to_state,
            "evidence_claim_ids": [f"{episode_id}:claim-1"],
        },
        "authority": {
            "authority_class": AUTHORITY_CLASS,
            "review_state": "REVIEW_REQUIRED",
            "canonical_promotion_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
    }


def test_valid_episode_is_canonicalized_and_hash_stable():
    episode = _episode()
    normalized = validate_episode(episode)
    assert normalized["occurred_at"] == "2026-08-01T07:00:00Z"
    assert normalized["source"]["start_seconds"] == 120.0
    assert normalized["authority"]["canonical_promotion_allowed"] is False
    assert canonical_sha256(episode) == canonical_sha256(deepcopy(episode))
    assert len(canonical_sha256(episode)) == 64


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("authority", "canonical_promotion_allowed"), True, "must be false"),
        (("authority", "curriculum_activation_allowed"), True, "must be false"),
        (("authority", "student_profile_write_allowed"), True, "must be false"),
        (("authority", "publication_allowed"), True, "must be false"),
        (("source", "evidence_state"), "GUESSED", "unverified source"),
        (("interaction", "completed_cycle"), False, "completed interaction cycle"),
    ],
)
def test_authority_and_evidence_escalation_fail_closed(path, value, match):
    episode = _episode()
    episode[path[0]][path[1]] = value
    with pytest.raises(EpisodeContractError, match=match):
        validate_episode(episode)


def test_claim_cannot_escape_exact_episode_provenance():
    episode = _episode()
    episode["claims"][0]["source_refs"] = ["another-video:segment-99"]
    with pytest.raises(EpisodeContractError, match="outside exact episode source"):
        validate_episode(episode)


def test_low_confidence_inference_cannot_be_relabeled_as_fact():
    episode = _episode()
    episode["claims"][0]["confidence"] = 0.70
    with pytest.raises(EpisodeContractError, match="FACT confidence below gate"):
        validate_episode(episode)


def test_uncertain_or_recommendation_claim_cannot_drive_mastery_transition():
    episode = _episode()
    episode["claims"][0]["epistemic_class"] = "UNCERTAIN"
    with pytest.raises(EpisodeContractError, match="needs FACT or INFERENCE"):
        validate_episode(episode)


def test_trajectory_orders_lessons_and_preserves_source_bindings():
    first = _episode()
    second = _episode(
        episode_id="diana.synthetic.episode-002",
        occurred_at="2026-08-08T10:00:00+03:00",
        from_state="INTRODUCED",
        to_state="SUPPORTED",
        start=300.0,
    )
    trajectory = build_skill_trajectory([second, first])
    assert trajectory["schema"] == "evolutionary-course-skill-trajectory-v1"
    assert trajectory["episode_count"] == 2
    assert trajectory["canonical_promotion_allowed"] is False
    assert trajectory["curriculum_activation_allowed"] is False
    assert trajectory["student_profile_write_allowed"] is False
    assert trajectory["publication_allowed"] is False
    skill = trajectory["skills"][0]
    assert skill["skill_id"] == "play.trump.count-losses"
    assert skill["current_candidate_state"] == "SUPPORTED"
    assert [item["episode_id"] for item in skill["transitions"]] == [
        "diana.synthetic.episode-001",
        "diana.synthetic.episode-002",
    ]
    assert all(len(item["episode_sha256"]) == 64 for item in skill["transitions"])


def test_discontinuous_longitudinal_claim_fails_closed():
    first = _episode()
    second = _episode(
        episode_id="diana.synthetic.episode-002",
        occurred_at="2026-08-08T10:00:00+03:00",
        from_state="INDEPENDENT",
        to_state="MASTERED",
        start=300.0,
    )
    with pytest.raises(EpisodeContractError, match="discontinuous mastery trajectory"):
        build_skill_trajectory([first, second])


def test_duplicate_episode_identity_fails_closed():
    episode = _episode()
    with pytest.raises(EpisodeContractError, match="duplicate episode_id"):
        build_skill_trajectory([episode, deepcopy(episode)])
