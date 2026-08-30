from __future__ import annotations

from copy import deepcopy

import pytest

from evolutionary_course.contract import validate_episode
from evolutionary_course.video31_adapter import (
    ADAPTER_SCHEMA,
    CATALOG_ADAPTER_SCHEMA,
    Video31AdapterError,
    adapt_video31_quality,
    adapt_video31_quality_with_catalog,
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


def _catalog(*, reviewed: bool = True) -> dict:
    return {
        "schema": "school-skill-catalog-v1",
        "catalog_version": "SCHOOL SKILL CATALOG v1",
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "school_canon_activation_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
        "skills": [{
            "skill_id": "candidate.skill.count-top-tricks",
            "title": "Подсчитать верхние взятки",
            "aliases": ["Сколько верхних взяток в контракте без козыря?"],
            "prerequisite_skill_ids": [],
            "mastery_criteria": {
                "RECOGNIZED": ["Узнаёт задачу с подсказкой."],
                "SUPPORTED": ["Решает после направляющего вопроса."],
                "INDEPENDENT": ["Решает типовую задачу без подсказки."],
                "TRANSFERRED": ["Применяет в новой структуре."],
            },
            "review_state": "APPROVED_CANDIDATE" if reviewed else "REVIEW_REQUIRED",
        }],
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


def test_catalog_adapter_uses_stable_reviewed_skill_id():
    report = adapt_video31_quality_with_catalog(
        _quality(), lesson_identity=_lesson(), source=_source(), catalog=_catalog()
    )
    assert report["schema"] == CATALOG_ADAPTER_SCHEMA
    assert report["accepted_episode_count"] == 1
    assert report["catalog_review_item_count"] == 0
    assert report["episodes"][0]["learning_task"]["skill_id"] == (
        "candidate.skill.count-top-tricks"
    )


def test_unknown_or_unreviewed_alias_never_creates_skill():
    report = adapt_video31_quality_with_catalog(
        _quality(), lesson_identity=_lesson(), source=_source(),
        catalog=_catalog(reviewed=False),
    )
    assert report["accepted_episode_count"] == 0
    assert report["catalog_review_item_count"] == 1
    assert report["catalog_review_items"][0]["match_status"] == "REVIEW_REQUIRED"
    assert report["episodes"] == []


def test_catalog_adapter_is_idempotent_and_uses_stable_prior_state():
    kwargs = dict(
        lesson_identity=_lesson(), source=_source(), catalog=_catalog(),
        prior_skill_states={"candidate.skill.count-top-tricks": "RECOGNIZED"},
    )
    first = adapt_video31_quality_with_catalog(_quality(), **kwargs)
    second = adapt_video31_quality_with_catalog(deepcopy(_quality()), **deepcopy(kwargs))
    assert first == second
    assert first["episodes"][0]["mastery_transition"]["from_state"] == "RECOGNIZED"


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
    assert report["episodes"][0]["mastery_transition"]["from_state"] == "UNSTABLE"
    assert report["episodes"][0]["mastery_transition"]["to_state"] == "UNSTABLE"

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


def _reviewed_catalog() -> dict:
    return {
        "schema": "school-skill-catalog-v1",
        "catalog_version": "SCHOOL SKILL CATALOG v1",
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "school_canon_activation_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
        "skills": [{
            "skill_id": "candidate.skill.count-top-tricks",
            "title": "Подсчитать верхние взятки",
            "aliases": ["Сколько верхних взяток в контракте без козыря?"],
            "prerequisite_skill_ids": [],
            "mastery_criteria": {
                "RECOGNIZED": ["Различает готовые и развиваемые взятки."],
                "SUPPORTED": ["Считает по мастям после вопроса."],
                "INDEPENDENT": ["Считает без подсказки."],
                "TRANSFERRED": ["Считает в новой структуре рук."],
            },
            "review_state": "APPROVED_CANDIDATE",
        }],
    }


def test_catalog_bound_mode_uses_stable_reviewed_skill_id():
    report = adapt_video31_quality(
        _quality(),
        lesson_identity=_lesson(),
        source=_source(),
        skill_catalog=_reviewed_catalog(),
        require_catalog_binding=True,
    )
    assert report["skill_binding_mode"] == "REVIEWED_CATALOG"
    episode = report["episodes"][0]
    assert episode["learning_task"]["skill_id"] == "candidate.skill.count-top-tricks"
    assert episode["mastery_transition"]["from_state"] == episode["mastery_transition"]["to_state"]


def test_catalog_bound_mode_rejects_unknown_or_unreviewed_wording():
    quality = _quality()
    quality["learning_interactions"][0]["task"] = "Похожая, но не проверенная формулировка"
    report = adapt_video31_quality(
        quality,
        lesson_identity=_lesson(),
        source=_source(),
        skill_catalog=_reviewed_catalog(),
        require_catalog_binding=True,
    )
    assert report["accepted_episode_count"] == 0
    assert report["rejected_interactions"][0]["reason_codes"] == [
        "SKILL_WORDING_NOT_REVIEWED"
    ]

    catalog = _reviewed_catalog()
    catalog["skills"][0]["review_state"] = "REVIEW_REQUIRED"
    report = adapt_video31_quality(
        _quality(),
        lesson_identity=_lesson(),
        source=_source(),
        skill_catalog=catalog,
        require_catalog_binding=True,
    )
    assert report["accepted_episode_count"] == 0


def test_required_catalog_binding_fails_without_catalog():
    with pytest.raises(Video31AdapterError, match="catalog binding required"):
        adapt_video31_quality(
            _quality(),
            lesson_identity=_lesson(),
            source=_source(),
            require_catalog_binding=True,
        )


def test_wrong_schema_job_and_free_text_outcome_fail_closed():
    quality = _quality()
    quality["schema"] = "diana-longitudinal-quality-v1"
    with pytest.raises(Video31AdapterError, match="unsupported quality schema"):
        adapt_video31_quality(quality, lesson_identity=_lesson(), source=_source())

    quality = _quality()
    quality["job_id"] = ""
    with pytest.raises(Video31AdapterError, match="source job identity required"):
        adapt_video31_quality(quality, lesson_identity=_lesson(), source=_source())

    quality = _quality()
    quality["learning_interactions"][0].pop("outcome_status")
    quality["learning_interactions"][0]["observed_outcome"] = "Правильный ответ"
    report = adapt_video31_quality(
        quality, lesson_identity=_lesson(), source=_source()
    )
    assert report["episodes"][0]["interaction"]["outcome"] == "NOT_ASSESSED"
