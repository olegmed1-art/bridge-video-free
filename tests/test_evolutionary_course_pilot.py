import json
from pathlib import Path

from evolutionary_course.pilot import run_longitudinal_pilot


def _catalog(*, include_alias=True):
    catalog = json.loads(Path("data/research/evolutionary_course_skill_catalog_v1.json").read_text())
    for skill in catalog["skills"]:
        skill["review_state"] = "APPROVED_CANDIDATE"
    if include_alias:
        catalog["skills"][0]["aliases"].append("Выбрать заявку")
    return catalog


def _payload():
    return {"schema": "diana-longitudinal-extraction", "job_id": "job-1",
            "lesson_identity": {"lesson_number": 4, "lesson_date": "2021-03-29",
                                "lesson_date_status": "CANDIDATE_MEDIUM",
                                "original_source_drive_id": "original-video-4",
                                "master_source_drive_id": "drive-video-4"},
            "quality_v2": {"schema": "diana-longitudinal-quality", "schema_version": 2,
                "job_id": "job-1",
                "learning_interactions": [{"interaction_id": "cycle-1", "status": "COMPLETE_EVIDENCE_CANDIDATE",
                    "start": 10, "end": 20, "task": "Выбрать заявку", "student_action": "Назвала заявку.",
                    "teacher_intervention": "Попросил проверить очки.", "student_followup": "Пересчитала очки.",
                    "observed_outcome": "Ответ после подсказки.", "help_state": "after_observed_intervention",
                    "actor_attribution_status": "SUPPORTED", "evidence_refs": ["segment_1", "{'evidence_id': 'frame_1'}"],
                    "visual_evidence_refs": [{"evidence_id": "frame_1"}]}],
                "authority": {"canon_activation": "DENY", "curriculum_activation": "DENY",
                              "student_profile_production_write": "DENY", "methodology_activation": "DENY"}},
            "technical_qc": {"transcript": {"segments": [{"segment_id": "segment_1"}]},
                             "visual": {"items": [{"evidence_id": "frame_1", "sha256": "a" * 64}]}}}


def _confirmation():
    return {"lesson_date": "2021-03-29", "lesson_date_status": "CONFIRMED",
            "video_file_id": "drive-video-4", "source_name": "Диана 4.mp4"}


def test_unconfirmed_drive_date_blocks_pilot():
    report = run_longitudinal_pilot(_payload(), catalog=_catalog())
    assert report["status"] == "BLOCKED"
    assert "INDEPENDENT_LESSON_DATE_CONFIRMATION_REQUIRED" in report["blockers"]
    assert report["media_reprocessed"] is False


def test_mismatched_exact_source_blocks_pilot():
    confirmation = _confirmation(); confirmation["video_file_id"] = "another-video"
    report = run_longitudinal_pilot(_payload(), confirmation=confirmation, catalog=_catalog())
    assert report["status"] == "BLOCKED"
    assert "VIDEO_FILE_ID_MISMATCH" in report["blockers"]


def test_confirmed_artifact_becomes_private_review_candidate():
    report = run_longitudinal_pilot(_payload(), confirmation=_confirmation(), catalog=_catalog())
    assert report["status"] == "READY_FOR_PRIVATE_REVIEW"
    episode = report["adapter_report"]["episodes"][0]
    assert episode["source"]["frame_sha256"] == ["a" * 64]
    assert episode["source"]["transcript_segment_ids"] == ["segment_1"]
    assert episode["authority"]["publication_allowed"] is False
    assert report["adapter_report"]["skill_binding_mode"] == "REVIEWED_CATALOG"
    assert report["analyzed_source_video_file_id"] == "drive-video-4"
    assert report["original_source_video_file_id"] == "original-video-4"


def test_pilot_requires_reviewed_catalog_instead_of_hashed_skill():
    report = run_longitudinal_pilot(_payload(), confirmation=_confirmation())
    assert report["status"] == "BLOCKED"
    assert "REVIEWED_SKILL_CATALOG_REQUIRED" in report["blockers"]


def test_unknown_real_wording_goes_to_methodology_review_without_episode():
    payload = _payload()
    payload["quality_v2"]["learning_interactions"][0]["task"] = (
        "Какие у нас шансы разыграть трефу?"
    )
    report = run_longitudinal_pilot(
        payload, confirmation=_confirmation(), catalog=_catalog(include_alias=False)
    )
    assert report["status"] == "METHODOLOGY_REVIEW_REQUIRED"
    assert report["adapter_report"]["accepted_episode_count"] == 0
    rejected = report["adapter_report"]["rejected_interactions"]
    assert rejected[0]["reason_codes"] == ["SKILL_WORDING_NOT_REVIEWED"]
    assert "review_candidate" in rejected[0]


def test_real_quality_schema_requires_exact_version_two():
    payload = _payload()
    payload["quality_v2"]["schema_version"] = 3
    report = run_longitudinal_pilot(
        payload, confirmation=_confirmation(), catalog=_catalog()
    )
    assert report["status"] == "BLOCKED"
    assert report["blockers"] == ["ADAPTER_REJECTED: unsupported quality schema"]


def test_original_id_does_not_replace_analyzed_master_identity():
    confirmation = _confirmation()
    confirmation["video_file_id"] = "original-video-4"
    report = run_longitudinal_pilot(
        _payload(), confirmation=confirmation, catalog=_catalog()
    )
    assert report["status"] == "BLOCKED"
    assert "VIDEO_FILE_ID_MISMATCH" in report["blockers"]
