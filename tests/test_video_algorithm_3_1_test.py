from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_universal_video_result_conformance import _bundle
from universal_video.algorithm_3_1_test import (
    ALGORITHM_REVISION,
    ALGORITHM_VERSION,
    BASE_ALGORITHM_VERSION,
    BRIDGE_EVIDENCE_POLICY,
    BRIDGIT_LAYOUT_POLICY,
    CAPABILITIES,
    DEFINITION_FILE,
    PROFILE_NAME,
    RELEASE_CHANNEL,
    RESULT_SCOPE,
    SPEAKER_EVIDENCE_POLICY,
    build_definition,
    definition_sha256,
    write_definition,
)
from universal_video.contract import validate_job
from universal_video.profiles import resolve_profile
from universal_video.result_conformance import ResultConformanceError, verify_result
from universal_video.readiness import build_test_readiness, deferred_stages
from universal_video.speaker_structure import MIN_TEST_LABEL_COVERAGE, TEST_SCHEMA


EXPECTED_RECENT_CAPABILITIES = {
    "runtime_attestation_v2",
    "timestamped_asr_and_acoustic_qc",
    "bridge_semantic_qc",
    "anonymous_speaker_structure_v3",
    "speaker_bounded_diagnostic_gate_v1",
    "speaker_label_coverage_gate_v1",
    "profiled_card_pixel_challenger_v2",
    "card_ocr_label_channel",
    "verified_layout_and_rotation",
    "direct_speech_card_evidence",
    "board_metadata",
    "deal_reconstruction_39_to_13",
    "result_conformance_and_evidence_export",
    "separated_readiness_matrix_v1",
    "post_run_audit_loop",
}


def _make_test_bundle(tmp_path: Path) -> tuple[Path, dict]:
    job_dir, manifest = _bundle(tmp_path)
    manifest["profile"] = PROFILE_NAME
    manifest["planned_stages"] = list(resolve_profile(PROFILE_NAME).stages)
    definition, digest = write_definition(
        job_dir / DEFINITION_FILE,
        source_revision=manifest["processing_revision"],
    )
    manifest["algorithm"] = {
        "version": ALGORITHM_VERSION,
        "revision": ALGORITHM_REVISION,
        "base_version": BASE_ALGORITHM_VERSION,
        "release_channel": RELEASE_CHANNEL,
        "result_scope": RESULT_SCOPE,
        "canonical_promotion_allowed": False,
        "production_activation_allowed": False,
        "definition": DEFINITION_FILE,
        "definition_sha256": digest,
    }
    speaker_report_path = job_dir / "speaker_diarization.json"
    speaker_report = json.loads(speaker_report_path.read_text(encoding="utf-8"))
    speaker_report.update(
        {
            "schema": TEST_SCHEMA,
            "label_coverage": 0.0,
            "speech_duration_coverage": 0.0,
            "minimum_label_coverage": MIN_TEST_LABEL_COVERAGE,
        }
    )
    speaker_report_path.write_text(json.dumps(speaker_report), encoding="utf-8")
    manifest["readiness"] = build_test_readiness(
        resolve_profile(PROFILE_NAME).stages,
        qc_pass=True,
        speaker_report=speaker_report,
    )
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return job_dir, definition


def _verify(job_dir: Path):
    return verify_result(
        job_dir,
        expected_job_id="exact-video-job",
        expected_profile=PROFILE_NAME,
        expected_job_hash="c" * 64,
        expected_source_file_id="1AbCdEfGhIjKlMnOpQrStUvWxYz",
    )


def test_definition_composes_base_and_all_recent_video_capabilities():
    definition = build_definition(source_revision="a" * 40)
    ids = {item["id"] for item in definition["capabilities"]}
    assert EXPECTED_RECENT_CAPABILITIES <= ids
    assert ids == {item["id"] for item in CAPABILITIES}
    assert definition["algorithm_version"] == "3.1-test"
    assert definition["base_algorithm_version"] == "3.1 FREE"
    assert definition["result_scope"] == "SHADOW_ONLY"
    assert definition["canonical_promotion_allowed"] is False
    assert definition["production_activation_allowed"] is False
    assert definition["next_video_auto_start_allowed"] is False
    assert definition["algorithm_revision"] == "3.1-test-r4"
    assert definition["bridgit_layout_policy"] == BRIDGIT_LAYOUT_POLICY
    assert definition["bridgit_layout_policy"]["suit_order"] == ["H", "C", "D", "S"]
    assert definition["bridgit_layout_policy"]["screen_axes"] == {
        "top": "LEFT_TO_RIGHT",
        "right": "TOP_TO_BOTTOM",
        "bottom": "LEFT_TO_RIGHT",
        "left": "TOP_TO_BOTTOM",
    }
    assert definition["bridgit_layout_policy"]["allowed_rotations_clockwise"] == [0, 90, 180, 270]
    assert definition["bridge_evidence_policy"] == BRIDGE_EVIDENCE_POLICY
    assert definition["bridge_evidence_policy"]["student_exact_card"] == "SUGGESTION_OR_CORROBORATION_ONLY"
    assert definition["speaker_evidence_policy"] == SPEAKER_EVIDENCE_POLICY
    assert definition["speaker_evidence_policy"]["minimum_segment_label_coverage"] == 0.80
    assert definition["speaker_evidence_policy"]["minimum_speech_duration_label_coverage"] == 0.80
    assert len(definition_sha256(definition)) == 64


def test_test_profile_is_explicit_and_does_not_change_stable_bridge_lesson(tmp_path: Path):
    test_profile = resolve_profile(PROFILE_NAME)
    stable_profile = resolve_profile("bridge_lesson")
    assert "algorithm_manifest" in test_profile.stages
    assert "speaker_structure" in test_profile.stages
    assert "bridge_positions" in test_profile.stages
    assert "algorithm_manifest" not in stable_profile.stages

    source = tmp_path / "lesson.mp4"
    source.write_bytes(b"placeholder")
    job = validate_job(
        {
            "job_id": "video31-test-one",
            "profile": PROFILE_NAME,
            "source": {"kind": "local_path", "path": str(source)},
        },
        allowed_local_root=str(tmp_path),
    )
    assert job.profile == PROFILE_NAME


def test_result_conformance_binds_definition_and_keeps_domain_shadowed(tmp_path: Path):
    job_dir, _ = _make_test_bundle(tmp_path)
    report = _verify(job_dir)
    assert report["state"] == "PASS"
    assert report["profile"] == PROFILE_NAME
    assert report["bridge_production_ready"] is False
    assert report["domain_analysis_status"] == "DEFERRED"
    assert report["publication_eligible"] is False
    assert report["canonical_publication_eligible"] is False
    assert report["readiness"]["publication_readiness"] == "BLOCKED_SHADOW_ONLY"
    assert report["readiness"]["bridge_positions_readiness"] == "DEFERRED"
    assert report["readiness"]["content_result"] == "ARCHIVE_ONLY"
    assert report["artifact_count"] == 7
    assert any(item["relative_name"] == DEFINITION_FILE for item in report["artifacts"])


def test_definition_or_promotion_tamper_fails_closed(tmp_path: Path):
    job_dir, definition = _make_test_bundle(tmp_path)
    definition["canonical_promotion_allowed"] = True
    (job_dir / DEFINITION_FILE).write_text(json.dumps(definition), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="definition mismatch"):
        _verify(job_dir)

    job_dir, _ = _make_test_bundle(tmp_path / "manifest")
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["algorithm"]["canonical_promotion_allowed"] = True
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="canonical_promotion_allowed mismatch"):
        _verify(job_dir)


def test_stable_profile_rejects_a_forged_test_algorithm_claim(tmp_path: Path):
    job_dir, manifest = _bundle(tmp_path)
    manifest["algorithm"] = {"version": "3.1-test"}
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="stable profile cannot claim"):
        verify_result(
            job_dir,
            expected_job_id="exact-video-job",
            expected_profile="bridge_lesson",
            expected_job_hash="c" * 64,
            expected_source_file_id="1AbCdEfGhIjKlMnOpQrStUvWxYz",
        )


def test_executed_test_stages_are_never_reported_as_deferred():
    stages = resolve_profile(PROFILE_NAME).stages
    deferred = deferred_stages(stages)
    assert "algorithm_manifest" not in deferred
    assert "speaker_structure" not in deferred
    assert deferred == [
        "bridge_context",
        "bridge_positions",
        "dds3_optional",
        "educational_candidates",
    ]


def test_readiness_tamper_fails_closed(tmp_path: Path):
    job_dir, _ = _make_test_bundle(tmp_path)
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["readiness"]["bridge_positions_readiness"] = "PASS"
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="readiness matrix mismatch"):
        _verify(job_dir)


def test_test_speaker_coverage_policy_tamper_fails_closed(tmp_path: Path):
    job_dir, _ = _make_test_bundle(tmp_path)
    path = job_dir / "speaker_diarization.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["minimum_label_coverage"] = 0.10
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="minimum label coverage mismatch"):
        _verify(job_dir)
