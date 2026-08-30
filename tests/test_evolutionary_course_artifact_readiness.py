import json
from copy import deepcopy
from pathlib import Path

import pytest

from evolutionary_course.artifact_readiness import ArtifactReadinessError, assess_artifact_readiness


MANIFEST = Path("data/research/evolutionary_course_real_artifact_readiness_v1.json")


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_real_inventory_is_honestly_blocked_without_processing_media():
    report = assess_artifact_readiness(_manifest())
    assert report["status"] == "BLOCKED"
    assert report["candidate_count"] == 3
    assert report["eligible_count"] == 0
    assert report["media_processed"] is False
    assert report["episode_created"] is False


def test_diana_2_is_not_promoted_without_exact_source_and_adapter():
    report = assess_artifact_readiness(_manifest())
    diana2 = next(item for item in report["candidates"] if item["lesson_id"] == "diana-2")
    assert "EXACT_SOURCE_IDENTITY_NOT_VERIFIED" in diana2["blockers"]
    assert "COURSE_ADAPTER_REPORT_MISSING" in diana2["blockers"]
    assert "REVIEWED_CATALOG_BINDING_MISSING" in diana2["blockers"]


def test_nonterminal_result_cannot_claim_terminal_verification():
    manifest = _manifest()
    manifest["candidates"][1]["terminal_result_verified"] = True
    with pytest.raises(ArtifactReadinessError, match="cannot verify terminal"):
        assess_artifact_readiness(manifest)


def test_ready_requires_three_distinct_fully_verified_lessons():
    manifest = _manifest()
    for item in manifest["candidates"]:
        item["terminal_status"] = "COMPLETED"
        item["source_video_file_id"] = f"verified-{item['lesson_id']}"
        item["source_job_id"] = f"job-{item['lesson_id']}"
        for field in (
            "exact_source_identity_verified", "terminal_result_verified",
            "role_attribution_supported", "course_adapter_report_available",
            "reviewed_catalog_binding",
        ):
            item[field] = True
    report = assess_artifact_readiness(manifest)
    assert report["status"] == "READY"
    assert report["eligible_count"] == 3


def test_authority_escalation_fails_closed():
    manifest = deepcopy(_manifest())
    manifest["authority"]["episode_creation_allowed"] = True
    with pytest.raises(ArtifactReadinessError, match="authority boundary"):
        assess_artifact_readiness(manifest)
