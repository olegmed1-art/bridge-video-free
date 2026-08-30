from __future__ import annotations

from copy import deepcopy

import pytest

from evolutionary_course.contract import validate_episode
from evolutionary_course.video31_adapter import (
    ADAPTER_SCHEMA,
    Video31AdapterError,
    adapt_video31_quality,
)


def _quality() -> dict:
    return {
        "schema": "diana-longitudinal-quality-v2",
        "job_id": "synthetic-job",
        "learning_interactions": [
            {
                "interaction_id": "interaction_one",
                "status": "COMPLETE_EVIDENCE_CANDIDATE",
                "start": 10.0,
                "end": 30.0,
                "task": "Сколько верхних взяток в контракте без козыря?",
                "student_action": "Ученица первоначально насчитала шесть взяток.",
                "teacher_intervention": "Преподаватель предложил считать отдельно по мастям.",
                "student_followup": "Ученица пересчитала и получила семь взяток.",
                "observed_outcome": "Наблюдается содержательный ответ после вмешательства.",
                "outcome_status": "PARTIAL",
                "help_state": "after_observed_intervention",
                "actor_attribution_status": "SUPPORTED",
                "evidence_refs": ["s1", "s2", "s3", "s4"],
                "visual_evidence_refs": ["a" * 64],
            },
            {
                "interaction_id": "interaction_partial",
                "status": "STAGING_PARTIAL",
                "start": 40.0,
                "end": 50.0,
                "task": "Технически неполный цикл",
                "student_action": None,
                "teacher_intervention": None,
                "student_followup": None,
                "observed_outcome": None,
                "help_state": "not_determined",
                "actor_attribution_status": "UNPROVEN",
                "evidence_refs": ["s5"],
                "visual_evidence_refs": [],
            },
        ],
        "authority": {
            "canon_activation": "DENY",
            "curriculum_activation": "DENY",
            "student_profile_production_write": "DENY",
            "methodology_activation": "DENY",
            "database_destination": "STAGING_ONLY",
        },
    }


def _lesson() -> dict:
    return {
        "lesson_date": "2021-02-22",
        "lesson_date_status": "CONFIRMED",
    }


def _source() -> dict:
    return {
        "video_file_id": "synthetic-drive-file",
        "source_name": "synthetic-diana-lesson.mp4",
        "evidence_state": "VERIFIED",
        "transcript_segment_ids": ["s1", "s2", "s3", "s4", "s5"],
        "frame_sha256": ["a" * 64],
    }


def test_adapter_accepts_only_complete_evidence_cycle():
    report = adapt_video31_quality(
        _quality(), lesson_identity=_lesson(), source=_source()
    )
    assert report["schema"] == ADAPTER_SCHEMA
    assert report["accepted_episode_count"] == 1
    assert report["rejected_interaction_count"] == 1
    assert "INTERACTION_NOT_COMPLETE" in report["rejected_interactions"][0]["reason_codes"]

    episode = report["episodes"][0]
    assert validate_episode(episode) == episode
    assert episode["occurred_at"] == "2021-02-22T00:00:10Z"
    assert episode["learning_task"]["skill_id"].startswith("candidate.skill.")
    assert episode["mastery_transition"] == {
        "from_state": "INTRODUCED",
        "to_state": "INTRODUCED",
        "evidence_claim_ids": [f"{episode['episode_id']}:claim-1"],
    }
    assert episode["claims"][0]["epistemic_class"] == "INFERENCE"
    assert episode["interaction"]["outcome"] == "PARTIAL"
    assert episode["authority"]["canonical_promotion_allowed"] is False
    assert episode["authority"]["student_profile_write_allowed"] is False
    assert report["authority"]["publication_allowed"] is False


def test_adapter_is_deterministic_for_identical_evidence():
    first = adapt_video31_quality(
        _quality(), lesson_identity=_lesson(), source=_source()
    )
    second = adapt_video31_quality(
        deepcopy(_quality()), lesson_identity=deepcopy(_lesson()), source=deepcopy(_source())
    )
    assert first == second


def test_unconfirmed_chronology_fails_closed():
    lesson = _lesson()
    lesson["lesson_date_status"] = "CANDIDATE_HIGH"
    with pytest.raises(Video31AdapterError, match="chronology is not confirmed"):
        adapt_video31_quality(_quality(), lesson_identity=lesson, source=_source())


def test_upstream_authority_escalation_fails_closed():
    quality = _quality()
    quality["authority"]["canon_activation"] = "ALLOW"
    with pytest.raises(Video31AdapterError, match="authority boundary"):
        adapt_video31_quality(quality, lesson_identity=_lesson(), source=_source())


def test_external_transcript_reference_rejects_only_that_interaction():
    quality = _quality()
    quality["learning_interactions"][0]["evidence_refs"].append("outside-source")
    report = adapt_video31_quality(
        quality, lesson_identity=_lesson(), source=_source()
    )
    assert report["accepted_episode_count"] == 0
    assert "EVIDENCE_OUTSIDE_SOURCE_TRANSCRIPT" in report["rejected_interactions"][0]["reason_codes"]


def test_external_frame_hash_rejects_only_that_interaction():
    quality = _quality()
    quality["learning_interactions"][0]["visual_evidence_refs"] = ["b" * 64]
    report = adapt_video31_quality(
        quality, lesson_identity=_lesson(), source=_source()
    )
    assert report["accepted_episode_count"] == 0
    assert "FRAME_EVIDENCE_OUTSIDE_SOURCE" in report["rejected_interactions"][0]["reason_codes"]


def test_prior_candidate_state_is_explicit_and_bounded():
    initial = adapt_video31_quality(
        _quality(), lesson_identity=_lesson(), source=_source()
    )["episodes"][0]
    skill_id = initial["learning_task"]["skill_id"]
    report = adapt_video31_quality(
        _quality(),
        lesson_identity=_lesson(),
        source=_source(),
        prior_skill_states={skill_id: "UNSTABLE"},
    )
    assert report["episodes"][0]["mastery_transition"] == {
        "from_state": "UNSTABLE",
        "to_state": "UNSTABLE",
        "evidence_claim_ids": [report["episodes"][0]["claims"][0]["claim_id"]],
    }

    with pytest.raises(Video31AdapterError, match="invalid prior skill state"):
        adapt_video31_quality(
            _quality(),
            lesson_identity=_lesson(),
            source=_source(),
            prior_skill_states={skill_id: "CANON_MASTERED"},
        )


def test_source_cannot_self_promote_unverified_evidence():
    source = _source()
    source["evidence_state"] = "OBSERVED"
    with pytest.raises(Video31AdapterError, match="source evidence is not verified"):
        adapt_video31_quality(_quality(), lesson_identity=_lesson(), source=source)


def test_invalid_frame_reference_rejects_interaction():
    quality = _quality()
    quality["learning_interactions"][0]["visual_evidence_refs"] = ["not-a-sha"]
    report = adapt_video31_quality(
        quality, lesson_identity=_lesson(), source=_source()
    )
    assert report["accepted_episode_count"] == 0
    assert "INVALID_FRAME_EVIDENCE" in report["rejected_interactions"][0]["reason_codes"]


def test_wrong_schema_and_missing_job_identity_fail_closed():
    quality = _quality()
    quality["schema"] = "diana-longitudinal-quality-v1"
    with pytest.raises(Video31AdapterError, match="unsupported quality schema"):
        adapt_video31_quality(quality, lesson_identity=_lesson(), source=_source())

    quality = _quality()
    quality["job_id"] = ""
    with pytest.raises(Video31AdapterError, match="source job identity required"):
        adapt_video31_quality(quality, lesson_identity=_lesson(), source=_source())


def test_outcome_is_not_inferred_from_free_text():
    quality = _quality()
    interaction = quality["learning_interactions"][0]
    interaction.pop("outcome_status")
    interaction["observed_outcome"] = "Правильный самостоятельный ответ"
    report = adapt_video31_quality(
        quality, lesson_identity=_lesson(), source=_source()
    )
    episode = report["episodes"][0]
    assert episode["interaction"]["outcome"] == "NOT_ASSESSED"
    assert episode["mastery_transition"]["from_state"] == episode["mastery_transition"]["to_state"]
