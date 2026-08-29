from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from universal_video.evidence_export import EvidenceExportError, build_evidence_export
from universal_video.result_conformance import verify_result
from universal_video.server_review import build_server_review


_bundle = runpy.run_path(
    str(Path(__file__).with_name("test_universal_video_result_conformance.py"))
)["_bundle"]


def _inputs(tmp_path: Path, *, status_v2: bool = True):
    spool = tmp_path / "spool"
    result_dir, manifest = _bundle(spool / "results")
    base = verify_result(
        result_dir,
        expected_job_id="exact-video-job",
        expected_profile="bridge_lesson",
        expected_job_hash="c" * 64,
        expected_source_file_id="1AbCdEfGhIjKlMnOpQrStUvWxYz",
        evidence_phase="GENERATION_FINALIZATION",
    )
    review = build_server_review(result_dir, base)
    (result_dir / "server_review.json").write_text(json.dumps(review), encoding="utf-8")
    final = verify_result(
        result_dir,
        expected_job_id="exact-video-job",
        expected_profile="bridge_lesson",
        expected_job_hash="c" * 64,
        expected_source_file_id="1AbCdEfGhIjKlMnOpQrStUvWxYz",
        evidence_phase="GENERATION_FINALIZATION",
        require_server_review=True,
    )
    done_dir = spool / "done"
    done_dir.mkdir(parents=True)
    attestation = {
        "schema": "universal-video-runtime-job-attestation-v1",
        "job_id": "exact-video-job",
        "request_commit": "e" * 40,
        "requested_runtime_commit": "a" * 40,
        "installed_runtime_commit": "a" * 40,
        "observed_job_runtime_commit": "a" * 40,
        "profile": "bridge_lesson",
        "job_hash": "c" * 64,
        "source_file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
        "canonical_output_untouched": True,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    done = {
        **manifest,
        "receipt_version": "universal-video-compute-receipt-v1",
        "compute_status": "COMPLETED",
        "result_conformance": final,
    }
    if status_v2:
        done["runtime_attestation"] = attestation
    (done_dir / "exact-video-job.json").write_text(json.dumps(done), encoding="utf-8")
    request = {
        "schema": "universal-video-evidence-export-request-v1",
        "job_id": "exact-video-job",
        "profile": "bridge_lesson",
        "job_hash": "c" * 64,
        "source_file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
        "request_commit": "e" * 40,
        "requested_runtime_commit": "a" * 40,
        "timeout_seconds": 20,
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    status = {
        "schema": "universal-video-resident-status-v1",
        "instance_state": "RUNNING",
        "active_jobs": [],
        "observed_at_unix": 1000.0,
    }
    if status_v2:
        status = {
            **status,
            "schema": "universal-video-resident-status-v2",
            "installed_runtime_commit": "a" * 40,
            "job_attestations": [attestation],
        }
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    return request_path, status_path, spool, result_dir, final


def _export(tmp_path: Path, *, status_v2: bool = True):
    request, status, spool, result_dir, final = _inputs(tmp_path, status_v2=status_v2)
    receipt = build_evidence_export(
        request_path=request,
        status_path=status,
        spool_root=spool,
        now=1010.0,
    )
    return receipt, request, status, spool, result_dir, final


def test_exact_runtime_bound_job_export_exposes_asr_and_keeps_deferred_stages_unavailable(tmp_path: Path):
    receipt, *_ = _export(tmp_path)
    assert receipt["state"] == "PASS"
    assert receipt["asr_qc"]["status"] == "PASS"
    assert receipt["asr_qc"]["segments"] == 1
    assert {item["locator"] for item in receipt["asr_qc"]["artifacts"]} == {
        "transcript.jsonl",
        "transcript.txt",
        "transcript_qc.json",
    }
    assert receipt["speakers"] == {
        "status": "UNAVAILABLE",
        "reason": "SPEAKER_LABELS_MISSING",
        "speaker_count": None,
        "labeled_segments": 0,
        "unlabeled_segments": 1,
        "collapse": "NOT_COMPUTABLE_WITHOUT_HUMAN_REFERENCE",
        "fragmentation": "NOT_COMPUTABLE_WITHOUT_HUMAN_REFERENCE",
        "teacher_student_attribution": "UNAVAILABLE",
    }
    assert receipt["cards"]["status"] == "UNAVAILABLE"
    assert receipt["cards"]["reason"] == "BRIDGE_POSITIONS_DEFERRED"
    assert receipt["cards"]["canonical_promotion_allowed"] is False
    assert receipt["runtime"]["binding"] == "OBSERVED_EXACT"
    assert receipt["publication_state"] == "NOT_PUBLISHED"
    assert receipt["school_canon_changed"] is False


def test_v2_status_can_prove_exact_observed_runtime_without_promoting(tmp_path: Path):
    receipt, *_ = _export(tmp_path, status_v2=True)
    assert receipt["runtime"]["binding"] == "OBSERVED_EXACT"
    assert receipt["runtime"]["installed_runtime_commit"] == "a" * 40
    assert receipt["technical"]["canonical_promotion_allowed"] is False


def test_v1_or_unbound_runtime_cannot_produce_pass(tmp_path: Path):
    request, status, spool, *_ = _inputs(tmp_path, status_v2=False)
    with pytest.raises(EvidenceExportError, match="resident status shape"):
        build_evidence_export(
            request_path=request, status_path=status, spool_root=spool, now=1010.0
        )


def test_status_attestation_must_be_bound_to_done_receipt(tmp_path: Path):
    request, status, spool, *_ = _inputs(tmp_path)
    done_path = spool / "done" / "exact-video-job.json"
    done = json.loads(done_path.read_text(encoding="utf-8"))
    done.pop("runtime_attestation")
    done_path.write_text(json.dumps(done), encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="bound to done receipt"):
        build_evidence_export(
            request_path=request, status_path=status, spool_root=spool, now=1010.0
        )


def test_manifest_processing_revision_must_match_requested_runtime(tmp_path: Path):
    request, status, spool, result_dir, *_ = _inputs(tmp_path)
    manifest_path = result_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["processing_revision"] = "b" * 40
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="result bundle is unavailable or invalid"):
        build_evidence_export(
            request_path=request, status_path=status, spool_root=spool, now=1010.0
        )


def test_shadow_cards_are_exported_only_with_complete_hash_bound_pair(tmp_path: Path):
    request, status, spool, result_dir, _final = _inputs(tmp_path)
    summary = {
        "status": "SHADOW_COMPLETED",
        "profiled_challenger_enabled": True,
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
        "recognized_frames": 2,
        "conflict_frames": 0,
        "derived_fourth_hand_frames": 0,
    }
    (result_dir / "bridge_positions_profiled_shadow_summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    (result_dir / "bridge_positions_profiled_shadow.jsonl").write_text(
        json.dumps({"status": "PASS", "canonical_promotion_allowed": False}) + "\n",
        encoding="utf-8",
    )
    receipt = build_evidence_export(
        request_path=request, status_path=status, spool_root=spool, now=1010.0
    )
    assert receipt["cards"]["status"] == "OBSERVED_SHADOW"
    assert receipt["cards"]["recognized_frames"] == 2
    assert len(receipt["cards"]["artifacts"]) == 2
    assert all(len(item["sha256"]) == 64 for item in receipt["cards"]["artifacts"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda status: status.update({"active_jobs": ["other"]}), "active job"),
        (lambda status: status.update({"observed_at_unix": 900.0}), "stale"),
        (lambda status: status.update({"unknown": True}), "status shape"),
    ],
)
def test_status_guard_fails_closed(tmp_path: Path, mutation, message: str):
    request, status_path, spool, *_ = _inputs(tmp_path)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    mutation(status)
    status_path.write_text(json.dumps(status), encoding="utf-8")
    with pytest.raises(EvidenceExportError, match=message):
        build_evidence_export(
            request_path=request, status_path=status_path, spool_root=spool, now=1010.0
        )


def test_request_done_tamper_duplicate_json_and_symlink_fail_closed(tmp_path: Path):
    request, status, spool, _result_dir, _final = _inputs(tmp_path)
    done_path = spool / "done" / "exact-video-job.json"
    done = json.loads(done_path.read_text(encoding="utf-8"))
    done["result_conformance"]["artifact_set_sha256"] = "f" * 64
    done_path.write_text(json.dumps(done), encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="mismatch"):
        build_evidence_export(request_path=request, status_path=status, spool_root=spool, now=1010.0)

    request, status, spool, *_ = _inputs(tmp_path / "duplicate")
    status.write_text('{"schema":"universal-video-resident-status-v1","schema":"x"}', encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="invalid fixed JSON"):
        build_evidence_export(request_path=request, status_path=status, spool_root=spool, now=1010.0)

    request, status, spool, *_ = _inputs(tmp_path / "symlink")
    target = status.with_name("real-status.json")
    status.rename(target)
    status.symlink_to(target)
    with pytest.raises(EvidenceExportError, match="unsafe fixed input"):
        build_evidence_export(request_path=request, status_path=status, spool_root=spool, now=1010.0)


def test_partial_or_promotable_shadow_artifact_fails_closed(tmp_path: Path):
    request, status, spool, result_dir, _final = _inputs(tmp_path)
    (result_dir / "bridge_positions_profiled_shadow_summary.json").write_text(
        json.dumps(
            {
                "profiled_challenger_enabled": True,
                "result_scope": "SHADOW_ONLY",
                "canonical_promotion_allowed": True,
            }
        ),
        encoding="utf-8",
    )
    (result_dir / "bridge_positions_profiled_shadow.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="promotion boundary"):
        build_evidence_export(request_path=request, status_path=status, spool_root=spool, now=1010.0)


def test_promotable_nested_shadow_record_fails_closed(tmp_path: Path):
    request, status, spool, result_dir, _final = _inputs(tmp_path)
    (result_dir / "bridge_positions_profiled_shadow_summary.json").write_text(
        json.dumps(
            {
                "profiled_challenger_enabled": True,
                "result_scope": "SHADOW_ONLY",
                "canonical_promotion_allowed": False,
            }
        ),
        encoding="utf-8",
    )
    (result_dir / "bridge_positions_profiled_shadow.jsonl").write_text(
        json.dumps({"evidence": {"canonical_promotion_allowed": True}}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(EvidenceExportError, match="record promotion boundary"):
        build_evidence_export(request_path=request, status_path=status, spool_root=spool, now=1010.0)
