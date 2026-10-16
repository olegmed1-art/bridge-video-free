"""Pinned synthetic producer -> consumer replay; no real lesson or Drive IO."""
import importlib.util
import json
import os
from pathlib import Path
import pytest
import sys

# CI imports the consumer from its separate, immutable merged checkout.
consumer_root = os.environ.get("COMPARISON_CONSUMER_ROOT")
if consumer_root:
    sys.path.insert(0, str(Path(consumer_root).resolve()))
from universal_video import comparison_artifacts as consumer
from universal_video import drive_results as publisher
from test_universal_video_result_conformance import _bundle, _verify
from universal_video.server_review import build_server_review

def test_pinned_r3_auction_output_is_lossless_consumer_input(tmp_path, monkeypatch):
    runner_root = os.environ.get("COMPARISON_R3_RUNNER_ROOT")
    if not runner_root:
        pytest.skip("explicit pinned producer checkout required")
    runner_root = Path(runner_root)
    spec = importlib.util.spec_from_file_location("published_r3_integration",
        runner_root / "tests/test_recognizer_compare_auction_integration.py")
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    runner_sha = os.environ.get("COMPARISON_R3_RUNNER_SHA",
        "e203e857ed59fa15e082638627fc6dd8a01b81d9")
    producer.runner.verify_checkout(runner_root, runner_sha)
    if consumer_root:
        consumer_sha = os.environ["COMPARISON_CONSUMER_SHA"]
        producer.runner.verify_checkout(consumer_root, consumer_sha)
        assert Path(consumer.__file__).resolve().is_relative_to(Path(consumer_root).resolve())
    producer_root = tmp_path / "producer"
    producer_root.mkdir()
    # Published decoder, observer, recorder and offline workers; explicit card stubs only.
    producer.test_candidate3_compare_embedded_auction_pixels_in_separate_offline_workers(
        producer_root, monkeypatch)
    raw = producer_root / "comparison"
    sealed_path = producer_root / "manifest.json"
    sealed = json.loads(sealed_path.read_text())
    summary = json.loads((raw / "comparison.json").read_text())
    assert summary["status"] == "CAPTURED_UNSCORED"
    assert summary["runs"]["candidate"]["auction_result_status"] == "OBSERVED"
    log_receipts = []
    for variant in ("baseline", "candidate"):
        log = raw / variant / (sealed["case_id"] + "-" + variant) / "process.log"
        text = log.read_text()
        log_receipts.append({"variant": variant, "bytes": log.stat().st_size,
            "sha256": producer.runner.digest(log),
            "warning_classes": sorted({name for name in
                ("DeprecationWarning", "UserWarning", "RuntimeWarning", "FutureWarning", "RequestsDependencyWarning")
                if name in text}),
            "runtime_markers": sorted({name for name in
                ("R26", "installed", "PATCH", "OpenCV", "opencv", "cv2", "numpy")
                if name in text})})
    print(json.dumps({"synthetic_process_logs": log_receipts}, sort_keys=True))
    parent_root = tmp_path / "parent"
    parent_root.mkdir()
    job, manifest = _bundle(parent_root)
    clip_sha = sealed["inputs"]["video"]["sha256"]
    source_id = manifest["source"]["file_id"]
    manifest["source"].update(file_id=source_id, version="1")
    manifest["media"]["sha256"] = clip_sha
    clip_path = producer_root / "synthetic-clip-binding.json"
    clip_path.write_bytes(consumer.encoded({
        "schema": "universal-video-source-clip-binding-v1", "status": "PASS",
        "source_file_id": source_id, "source_version": "1", "source_sha256": clip_sha,
        "clip_sha256": clip_sha, "source_offset_ms": 0, "duration_ms": 3000,
        "verification_receipt_sha256": producer.runner.digest(sealed_path),
    }))
    args = dict(sealed_manifest_path=sealed_path,
        sealed_manifest_sha256=producer.runner.digest(sealed_path),
        runner_commit=runner_sha,
        runner_sha256=producer.runner.digest(producer.RUNNER),
        job_id=manifest["job_id"], job_hash=manifest["job_hash"],
        source_file_id=source_id, source_version="1", source_sha256=clip_sha,
        clip_binding_path=clip_path, clip_binding_sha256=producer.runner.digest(clip_path))
    declaration = consumer.build_comparison_package(raw, job / "comparison", **args)
    manifest["comparison_artifacts"] = declaration
    (job / "manifest.json").write_bytes(consumer.encoded(manifest))
    paths = consumer.collect_comparison_paths(job, manifest)
    index = json.loads(paths[0].read_bytes())
    packed = b"".join(p.read_bytes() for p in paths[1:])
    actual = {r["path"]: packed[r["offset"]:r["offset"] + r["size_bytes"]] for r in index["files"]}
    expected = {p.relative_to(raw).as_posix(): p.read_bytes() for p in raw.rglob("*") if p.is_file()}
    expected["embedded-profile.json"] = Path(sealed["inputs"]["profile"]["path"]).read_bytes()
    expected["source-clip-binding.json"] = clip_path.read_bytes()
    assert actual == expected
    assert len([n for n in actual if "/auction-evidence/" in n]) == 3
    assert index["accuracy_evaluated"] is False and index["promotion_allowed"] is False
    again_root = tmp_path / "replay-again"
    again = consumer.build_comparison_package(raw, again_root, **args)
    assert declaration == again
    assert {p.name: p.read_bytes() for p in (job / "comparison").iterdir()} == {
        p.name: p.read_bytes() for p in again_root.iterdir()}
    report = _verify(job, evidence_phase="GENERATION_FINALIZATION")
    (job / "server_review.json").write_bytes(consumer.encoded(build_server_review(job, report)))
    artifacts = publisher.collect_compact_artifacts(job)
    assert all(a.size_bytes <= publisher.MULTIPART_UPLOAD_MAX_BYTES for a in artifacts)
    assert sum(a.size_bytes for a in artifacts) <= consumer.MAX_TOTAL_BYTES
    _verify(job, require_server_review=True)
    assert report["domain_analysis_status"] == "DEFERRED"
    print(json.dumps({"synthetic_producer_consumer_executed": True,
        "runner_sha": args["runner_commit"], "baseline_sha": sealed["baseline"]["sha"],
        "candidate_sha": sealed["candidate"]["sha"], "auction_pngs_retained": 3,
        "deterministic_adapter_repackage": True, "synthetic_card_calibration_stubs": True,
        "real_video_accuracy_evaluated": False, "package_sha256": declaration["manifest_sha256"]},
        sort_keys=True))
