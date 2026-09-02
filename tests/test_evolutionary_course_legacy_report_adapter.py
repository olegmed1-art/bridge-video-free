import pytest

from evolutionary_course.legacy_report_adapter import (
    LegacyReportAdapterError,
    adapt_legacy_report_pointers,
)


def _payload():
    return {
        "schema": "evolutionary-course-legacy-report-pointers-v1",
        "report": {
            "drive_file_id": "legacy-report-123",
            "title": "Synthetic Diana analysis.pdf",
            "sha256": "a" * 64,
            "content_extraction_status": "TEXT_EXTRACTED",
        },
        "source_video_candidate": {
            "video_file_id": "candidate-video-123",
            "source_name": "Synthetic lesson.mp4",
            "identity_status": "REPORT_STATED",
        },
        "pointers": [{
            "pointer_id": "section-1-row-2",
            "report_section": "Timeline",
            "topic_label": "Count losers",
            "approx_start_seconds": 600,
            "approx_end_seconds": 900,
        }],
    }


def test_legacy_pointer_never_becomes_episode_evidence():
    result = adapt_legacy_report_pointers(_payload())
    assert result["candidate_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["evidence_state"] == "LEGACY_REPORT_POINTER"
    assert candidate["status"] == "MANUAL_SOURCE_REVIEW_REQUIRED"
    assert result["episode_creation_allowed"] is False
    assert result["mastery_inference_allowed"] is False
    assert result["longitudinal_pilot_input_allowed"] is False
    assert result["authority"]["publication_allowed"] is False


def test_adapter_is_deterministic_and_deduplicates_identical_pointer():
    payload = _payload()
    payload["pointers"].append(dict(payload["pointers"][0]))
    first = adapt_legacy_report_pointers(payload)
    second = adapt_legacy_report_pointers(payload)
    assert first == second
    assert first["candidate_count"] == 1


def test_report_hash_and_complete_interval_are_required():
    payload = _payload()
    payload["report"]["sha256"] = ""
    with pytest.raises(LegacyReportAdapterError, match="sha256 required"):
        adapt_legacy_report_pointers(payload)
    payload = _payload()
    payload["pointers"][0]["approx_end_seconds"] = None
    with pytest.raises(LegacyReportAdapterError, match="interval must be complete"):
        adapt_legacy_report_pointers(payload)


def test_source_identity_cannot_be_self_verified():
    payload = _payload()
    payload["source_video_candidate"]["identity_status"] = "VERIFIED"
    with pytest.raises(LegacyReportAdapterError, match="cannot verify source identity"):
        adapt_legacy_report_pointers(payload)
