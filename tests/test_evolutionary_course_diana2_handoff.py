import json
from pathlib import Path

from evolutionary_course.artifact_readiness import assess_artifact_readiness


HANDOFF = Path("data/research/evolutionary_course_diana2_handoff_v1.json")
READINESS = Path("data/research/evolutionary_course_real_artifact_readiness_v1.json")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_diana2_exact_source_is_bound_across_handoff_and_readiness():
    handoff = _load(HANDOFF)
    readiness = assess_artifact_readiness(_load(READINESS))
    item = next(candidate for candidate in readiness["candidates"] if candidate["lesson_id"] == "diana-2")
    assert handoff["source"]["video_file_id"] == item["source_video_file_id"]
    assert handoff["source"]["source_job_id"] == item["source_job_id"]
    assert item["exact_source_identity_verified"] is True
    assert "EXACT_SOURCE_IDENTITY_NOT_VERIFIED" not in item["blockers"]


def test_handoff_preserves_existing_result_hashes_and_revision():
    handoff = _load(HANDOFF)
    result = handoff["existing_result"]
    assert result["master_pdf_sha256"] == "f429f69a53938923f4c5bbca37fc1fc815dc8eded8eb9e6b4d080d0368ea9ac9"
    assert result["embedded_master_analysis_sha256"] == "fb7c6292f7386aa04ed9d91a101adbd13470d637c2eae1b71c7a37e871f20987"
    assert result["video31_revision"] == "3.1-free-r25.15"
    assert result["identity_overlay_revision"] == "3.1-free-r29"


def test_handoff_cannot_claim_adapter_or_episode_side_effects():
    handoff = _load(HANDOFF)
    assert handoff["status"] == "SOURCE_BOUND_ADAPTER_INPUT_MISSING"
    assert handoff["effects"] == {
        "media_processed": False,
        "adapter_report_created": False,
        "learning_episode_created": False,
        "catalog_mutated": False,
        "student_profile_written": False,
    }
    assert "EXPORTED_QUALITY_V2_JSON_BOUND_TO_MASTER_ANALYSIS_SHA256" in handoff["required_before_adapter"]
    assert "REVIEWED_SKILL_CATALOG_BINDING" in handoff["required_before_adapter"]
