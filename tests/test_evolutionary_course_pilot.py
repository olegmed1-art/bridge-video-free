from evolutionary_course.pilot import run_longitudinal_pilot


def _payload():
    return {"schema": "diana-longitudinal-extraction", "job_id": "job-1",
            "lesson_identity": {"lesson_number": 4, "lesson_date": "2021-03-29",
                                "lesson_date_status": "CANDIDATE_MEDIUM",
                                "original_source_drive_id": "drive-video-4"},
            "quality_v2": {"schema": "diana-longitudinal-quality-v2", "job_id": "job-1",
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
    report = run_longitudinal_pilot(_payload())
    assert report["status"] == "BLOCKED"
    assert "INDEPENDENT_LESSON_DATE_CONFIRMATION_REQUIRED" in report["blockers"]
    assert report["media_reprocessed"] is False


def test_mismatched_exact_source_blocks_pilot():
    confirmation = _confirmation(); confirmation["video_file_id"] = "another-video"
    report = run_longitudinal_pilot(_payload(), confirmation=confirmation)
    assert report["status"] == "BLOCKED"
    assert "VIDEO_FILE_ID_MISMATCH" in report["blockers"]


def test_confirmed_artifact_becomes_private_review_candidate():
    report = run_longitudinal_pilot(_payload(), confirmation=_confirmation())
    assert report["status"] == "READY_FOR_PRIVATE_REVIEW"
    episode = report["adapter_report"]["episodes"][0]
    assert episode["source"]["frame_sha256"] == ["a" * 64]
    assert episode["source"]["transcript_segment_ids"] == ["segment_1"]
    assert episode["authority"]["publication_allowed"] is False
