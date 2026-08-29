from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from universal_video.result_conformance import ResultConformanceError, verify_result
from universal_video.profiles import resolve_profile
from universal_video.server_review import build_server_review


def _fingerprint(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _bundle(tmp_path: Path, *, status: str = "COMPLETED") -> tuple[Path, dict]:
    job_id = "exact-video-job"
    job_dir = tmp_path / job_id
    frames = job_dir / "frames"
    frames.mkdir(parents=True)
    transcript_line = {
        "start": 0.0,
        "end": 1.5,
        "text": "Opening bid",
        "chunk": 0,
        "unreliable": False,
    }
    (job_dir / "transcript.jsonl").write_text(json.dumps(transcript_line) + "\n", encoding="utf-8")
    (job_dir / "transcript.txt").write_text("[0.0-1.5] Opening bid", encoding="utf-8")
    qc = [
        {
            "chunk": 0,
            "start": 0.0,
            "end": 90.0,
            "ok": True,
            "no_speech": False,
            "critical": False,
            "nonspeech_hallucination": False,
            "retry_used": False,
            "primary_words": 2,
            "strict_words": 2,
            "similarity": 1.0,
            "repetition_ratio": 0.0,
            "strict_repetition_ratio": 0.0,
            "selected_attempt": "primary",
            "selected_consensus": 1.0,
            "failure_reasons": [],
        }
    ]
    (job_dir / "transcript_qc.json").write_text(json.dumps(qc), encoding="utf-8")
    speaker_report = {
        "schema": "universal-video-speaker-structure-v1",
        "revision": "test-v1",
        "status": "UNAVAILABLE_INSUFFICIENT_SEGMENTS",
        "quality_gate": "INCONCLUSIVE",
        "reason": "INSUFFICIENT_SEGMENTS",
        "segments_total": 1,
        "segments_labeled": 0,
        "speaker_count": 0,
        "speaker_labels": [],
        "speaker_clusters": {},
        "role_mapping_supported": False,
        "teacher_student_attribution": "UNAVAILABLE",
        "privacy": {
            "real_person_identity_claimed": False,
            "raw_audio_persisted": False,
            "voice_embedding_persisted": False,
            "cross_lesson_voice_profile_persisted": False,
            "source_speaker_labels_persisted": False,
        },
    }
    (job_dir / "speaker_diarization.json").write_text(json.dumps(speaker_report), encoding="utf-8")
    frame = frames / "frame-001.jpg"
    frame.write_bytes(b"jpeg-data")
    frame_hash = hashlib.sha256(frame.read_bytes()).hexdigest()

    revision = "a" * 40
    processing = _fingerprint(
        {"contract": "universal-video-v1", "source_revision": revision, "whisper_model": "small"}
    )
    source_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
    provider_md5 = "b" * 32
    source_fp = _fingerprint(
        {
            "kind": "google_drive",
            "file_id": source_id,
            "size_bytes": 1234,
            "checksum_kind": "md5Checksum",
            "checksum": provider_md5,
        }
    )
    manifest = {
        "contract": "universal-video-v1",
        "status": status,
        "job_id": job_id,
        "job_hash": "c" * 64,
        "profile": "bridge_lesson",
        "processing_fingerprint": processing,
        "processing_revision": revision,
        "processing_whisper_model": "small",
        "source_fingerprint": source_fp,
        "source_fingerprint_basis": "md5Checksum+size+file_id",
        "source_reuse_safe": True,
        "source": {
            "kind": "google_drive",
            "file_id": source_id,
            "size": "1234",
            "md5Checksum": provider_md5,
            "fingerprint": source_fp,
            "fingerprint_basis": "md5Checksum+size+file_id",
            "reuse_safe": True,
        },
        "media": {"sha256": "d" * 64, "size_bytes": 1234, "duration_seconds": 90.0},
        "domain_plugin": "bridge",
        "planned_stages": list(resolve_profile("bridge_lesson").stages),
        "transcript": {
            "segments": 1,
            "words": 2,
            "deduplicated_overlap_segments": 0,
            "qc_pass": True,
            "qc_blocks": 1,
            "qc_speech_blocks": 1,
            "qc_no_speech_blocks": 0,
            "qc_failed": 0,
            "qc_allowed_failed": 0,
            "qc_critical_failed": 0,
            "qc_hallucination_blocks": 0,
            "jsonl": "transcript.jsonl",
            "text": "transcript.txt",
            "qc": "transcript_qc.json",
        },
        "frames": [{"time": 0.0, "file": frame.name, "sha256": frame_hash}],
        "speaker_structure": {
            "report": "speaker_diarization.json",
            "status": "UNAVAILABLE_INSUFFICIENT_SEGMENTS",
            "speaker_count": 0,
            "segments_labeled": 0,
        },
        "deferred_analysis": [
            "bridge_context",
            "bridge_positions",
            "dds3_optional",
            "educational_candidates",
        ],
    }
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return job_dir, manifest


def _verify(job_dir: Path, **overrides):
    args = {
        "expected_job_id": "exact-video-job",
        "expected_profile": "bridge_lesson",
        "expected_job_hash": "c" * 64,
        "expected_source_file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
    }
    args.update(overrides)
    return verify_result(job_dir, **args)


def test_conformance_pass_separates_bundle_from_domain_and_pedagogical_readiness(tmp_path: Path):
    job_dir, _ = _bundle(tmp_path)
    report = _verify(job_dir)
    assert report["state"] == "PASS"
    assert report["technical_bundle_ready"] is True
    assert report["domain_analysis_status"] == "DEFERRED"
    assert report["bridge_production_ready"] is False
    assert report["pedagogical_status"] == "NOT_EVALUATED"
    assert report["source_binding_status"] == "RUNNER_ATTESTED_PROVIDER_MD5_AND_DOWNLOADED_SHA256"
    assert report["processing_origin_status"] == "SELF_REPORTED_MANIFEST_BOUND"
    assert report["code_origin_verified"] is False
    assert report["evidence_phase"] == "POST_HOC_OBSERVATION"
    assert report["artifact_count"] == 6
    assert len(report["artifact_set_sha256"]) == 64


def test_default_conformance_has_no_300_frame_count_cap(tmp_path: Path):
    job_dir, manifest = _bundle(tmp_path)
    frames_dir = job_dir / "frames"
    for index in range(2, 302):
        frame = frames_dir / f"frame-{index:03d}.jpg"
        frame.write_bytes(f"jpeg-{index}".encode())
        manifest["frames"].append(
            {
                "time": round(index / 4, 3),
                "file": frame.name,
                "sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
            }
        )
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert _verify(job_dir)["artifact_count"] == 306
    with pytest.raises(ResultConformanceError, match="keyframe count exceeds cap"):
        _verify(job_dir, max_frames=300)


def test_server_review_compacts_any_conformant_bundle_without_canon_promotion(tmp_path: Path):
    job_dir, _ = _bundle(tmp_path)
    base = _verify(job_dir, evidence_phase="GENERATION_FINALIZATION")
    review = build_server_review(job_dir, base)
    (job_dir / "server_review.json").write_text(json.dumps(review), encoding="utf-8")

    final = _verify(
        job_dir,
        evidence_phase="GENERATION_FINALIZATION",
        require_server_review=True,
    )
    assert final["server_final_review_status"] == "REVIEW_REQUIRED"
    assert final["chat_handoff_mode"] == "EXCEPTIONS_ONLY"
    assert final["artifact_count"] == 7
    assert review["execution_location"] == "RESIDENT_SERVER_POSTPROCESS"
    assert review["handoff"]["technical_final_review_completed"] is True
    assert review["handoff"]["canonical_promotion_allowed"] is False
    assert review["handoff"]["raw_media_included"] is False
    assert review["handoff"]["full_transcript_included"] is False
    assert review["summary"]["deferred_analysis"] == [
        "bridge_context",
        "bridge_positions",
        "dds3_optional",
        "educational_candidates",
    ]
    publication = _verify(
        job_dir,
        evidence_phase="PUBLICATION_PREFLIGHT",
        require_server_review=True,
    )
    assert publication["artifact_set_sha256"] == final["artifact_set_sha256"]


def test_server_review_tamper_and_missing_packet_fail_closed(tmp_path: Path):
    job_dir, _ = _bundle(tmp_path)
    with pytest.raises(ResultConformanceError, match="required server review"):
        _verify(job_dir, require_server_review=True)

    base = _verify(job_dir, evidence_phase="GENERATION_FINALIZATION")
    review = build_server_review(job_dir, base)
    review["handoff"]["canonical_promotion_allowed"] = True
    (job_dir / "server_review.json").write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="handoff boundary"):
        _verify(job_dir, require_server_review=True)


def test_review_is_not_a_conformant_completed_bundle(tmp_path: Path):
    job_dir, _ = _bundle(tmp_path, status="REVIEW")
    with pytest.raises(ResultConformanceError, match="not technical COMPLETED"):
        _verify(job_dir)


def test_exact_job_hash_and_source_identity_are_enforced(tmp_path: Path):
    job_dir, _ = _bundle(tmp_path)
    with pytest.raises(ResultConformanceError, match="unexpected job hash"):
        _verify(job_dir, expected_job_hash="e" * 64)
    with pytest.raises(ResultConformanceError, match="unexpected source file id"):
        _verify(job_dir, expected_source_file_id="1DifferentDriveFileId000000")


def test_frame_tamper_symlink_and_raw_media_fail_closed(tmp_path: Path):
    job_dir, _ = _bundle(tmp_path)
    (job_dir / "frames" / "frame-001.jpg").write_bytes(b"tampered")
    with pytest.raises(ResultConformanceError, match="keyframe hash mismatch"):
        _verify(job_dir)

    job_dir, _ = _bundle(tmp_path / "second")
    (job_dir / "source.mp4").write_bytes(b"raw")
    with pytest.raises(ResultConformanceError, match="raw media"):
        _verify(job_dir)

    job_dir, _ = _bundle(tmp_path / "third")
    transcript = job_dir / "transcript.txt"
    transcript.unlink()
    transcript.symlink_to(job_dir / "transcript.jsonl")
    with pytest.raises(ResultConformanceError, match="symlink"):
        _verify(job_dir)


def test_generation_inventory_detects_later_artifact_change(tmp_path: Path):
    job_dir, _ = _bundle(tmp_path)
    report = _verify(job_dir, evidence_phase="GENERATION_FINALIZATION")
    item = json.loads((job_dir / "transcript.jsonl").read_text(encoding="utf-8"))
    item["text"] = "Closing pass"
    (job_dir / "transcript.jsonl").write_text(json.dumps(item) + "\n", encoding="utf-8")
    (job_dir / "transcript.txt").write_text("[0.0-1.5] Closing pass", encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="artifact set hash mismatch"):
        _verify(job_dir, expected_artifact_set_sha256=report["artifact_set_sha256"])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("planned_stages", ["transcribe", "keyframes"], "planned stages"),
        ("domain_plugin", None, "domain plugin"),
        ("deferred_analysis", [], "deferred analysis"),
    ],
)
def test_profile_execution_boundary_must_match_canonical_contract(
    tmp_path: Path, field: str, value, message: str
):
    job_dir, manifest = _bundle(tmp_path)
    manifest[field] = value
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match=message):
        _verify(job_dir)


def test_transcript_and_qc_are_recomputed_instead_of_trusting_manifest(tmp_path: Path):
    job_dir, manifest = _bundle(tmp_path)
    qc = json.loads((job_dir / "transcript_qc.json").read_text(encoding="utf-8"))
    qc[0].update({"ok": False, "critical": True, "nonspeech_hallucination": True})
    (job_dir / "transcript_qc.json").write_text(json.dumps(qc), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="QC evidence|qc_critical_failed mismatch|QC pass mismatch"):
        _verify(job_dir)

    job_dir, _ = _bundle(tmp_path / "text")
    (job_dir / "transcript.txt").write_text("different words", encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="does not match JSONL"):
        _verify(job_dir)

    job_dir, manifest = _bundle(tmp_path / "words")
    manifest["transcript"]["words"] = 99
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="word count mismatch"):
        _verify(job_dir)


def test_provider_sha256_must_match_materialized_media(tmp_path: Path):
    job_dir, manifest = _bundle(tmp_path)
    source = manifest["source"]
    source.pop("md5Checksum")
    source["sha256Checksum"] = "e" * 64
    source["fingerprint_basis"] = "sha256Checksum+size+file_id"
    manifest["source_fingerprint_basis"] = "sha256Checksum+size+file_id"
    basis = {
        "kind": "google_drive",
        "file_id": source["file_id"],
        "size_bytes": 1234,
        "checksum_kind": "sha256Checksum",
        "checksum": "e" * 64,
    }
    source_fp = _fingerprint(basis)
    source["fingerprint"] = source_fp
    manifest["source_fingerprint"] = source_fp
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match="provider/media SHA-256 mismatch"):
        _verify(job_dir)
