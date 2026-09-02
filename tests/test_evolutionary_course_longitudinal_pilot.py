from copy import deepcopy

from evolutionary_course.longitudinal_pilot import run_multi_lesson_pilot


def _episode(n, date, video):
    episode_id = f"evc.pilot.lesson-{n}"
    claim_id = f"{episode_id}:claim-1"
    return {
        "schema": "evolutionary-course-learning-episode-v1",
        "course_version": "Evolutionary Course v1",
        "episode_id": episode_id,
        "occurred_at": f"{date}T00:00:10Z",
        "source": {
            "video_file_id": video, "source_name": f"lesson-{n}.mp4",
            "start_seconds": 10, "end_seconds": 20,
            "transcript_segment_ids": [f"segment-{n}"],
            "frame_sha256": [], "evidence_state": "VERIFIED",
        },
        "learning_task": {
            "skill_id": "candidate.skill.count-losers",
            "title": "Посчитать потери",
            "prerequisite_skill_ids": [],
        },
        "interaction": {
            "teacher_actions": ["Задан вопрос."],
            "student_actions": ["Получен ответ."],
            "outcome": "NOT_ASSESSED", "support_level": "GUIDED",
            "completed_cycle": True,
        },
        "claims": [{
            "claim_id": claim_id, "epistemic_class": "INFERENCE",
            "statement": "Наблюдался полный учебный цикл.",
            "source_refs": [f"segment-{n}"], "confidence": 0.85,
        }],
        "mastery_transition": {
            "from_state": "INTRODUCED", "to_state": "INTRODUCED",
            "evidence_claim_ids": [claim_id],
        },
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "review_state": "REVIEW_REQUIRED",
            "canonical_promotion_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
    }


def _report(n, date):
    return {
        "source_job_id": f"job-{n}",
        "skill_binding_mode": "REVIEWED_CATALOG",
        "lesson_date": date,
        "episodes": [_episode(n, date, f"video-{n}")],
        "authority": {
            "canonical_promotion_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
    }


def test_three_distinct_catalog_bound_lessons_form_private_pilot():
    reports = [_report(1, "2021-01-01"), _report(2, "2021-02-01"), _report(3, "2021-03-01")]
    result = run_multi_lesson_pilot(reports)
    assert result["status"] == "READY_FOR_PRIVATE_LONGITUDINAL_REVIEW"
    assert result["lesson_count"] == 3
    assert result["trajectory"]["episode_count"] == 3
    assert result["authority"]["publication_allowed"] is False


def test_two_lessons_are_blocked():
    result = run_multi_lesson_pilot([_report(1, "2021-01-01"), _report(2, "2021-02-01")])
    assert result["status"] == "BLOCKED"
    assert "MINIMUM_THREE_DISTINCT_LESSONS_REQUIRED" in result["blockers"]


def test_duplicate_video_and_unbound_report_fail_closed():
    reports = [_report(1, "2021-01-01"), _report(2, "2021-02-01"), _report(3, "2021-03-01")]
    reports[1]["episodes"][0]["source"]["video_file_id"] = "video-1"
    reports[2]["skill_binding_mode"] = "LEGACY_TASK_HASH"
    result = run_multi_lesson_pilot(reports)
    assert result["status"] == "BLOCKED"
    assert "DUPLICATE_SOURCE_VIDEO" in result["blockers"]
    assert "REVIEWED_CATALOG_BINDING_REQUIRED" in result["blockers"]


def test_date_mismatch_and_authority_escalation_fail_closed():
    reports = [_report(1, "2021-01-01"), _report(2, "2021-02-01"), _report(3, "2021-03-01")]
    reports[0]["lesson_date"] = "2020-01-01"
    reports[1]["authority"]["curriculum_activation_allowed"] = True
    result = run_multi_lesson_pilot(reports)
    assert result["status"] == "BLOCKED"
    assert "EPISODE_DATE_MISMATCH" in result["blockers"]
    assert "AUTHORITY_BOUNDARY_MISMATCH" in result["blockers"]
