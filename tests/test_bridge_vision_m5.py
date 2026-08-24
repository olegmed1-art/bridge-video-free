import hashlib
import json
from pathlib import Path

import pytest

from bridge_vision.gold_corpus import GoldCorpusError, to_detector_cases
from bridge_vision.pixel_backend import PixelBackendNotApproved, load_approved_backend
from bridge_vision.seed_corpus import build_seed_corpus


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _job_manifest(entries):
    return {"job_id": "j1", "source_fingerprint": "fp", "frames": entries}


def _approved_manifest(artifact: Path):
    return {
        "schema": "bridge-vision-pixel-backend/v2",
        "artifact": artifact.name,
        "artifact_sha256": _sha(artifact),
        "human_gold_test_verified": True,
        "split_policy": "immutable-train-test-v1",
        "training_corpus_sha256": "1" * 64,
        "test_corpus_sha256": "2" * 64,
        "test_metrics": {
            "frames": 100,
            "expected_cards": 1000,
            "predicted_cards": 960,
            "true_positive_cards": 960,
            "seat_errors": 0,
            "precision": 1.0,
            "recall": 0.96,
        },
    }


def test_seed_corpus_is_review_first_and_hash_verified(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    entries = []
    for i in range(5):
        p = frames / f"f{i}.jpg"
        p.write_bytes(f"frame-{i}".encode())
        entries.append({"file": p.name, "time": i * 10.0, "sha256": _sha(p)})
    (tmp_path / "manifest.json").write_text(json.dumps(_job_manifest(entries)), encoding="utf-8")
    result = build_seed_corpus(tmp_path, target_frames=3)
    assert result["status"] == "NEEDS_HUMAN_LABELING"
    queue = json.loads((tmp_path / "bridge_vision_label_queue.json").read_text(encoding="utf-8"))
    assert queue["schema"] == "bridge-vision-gold-label-queue/v2"
    assert queue["selected_frames"] == 3
    assert all(case["human_verified"] is False for case in queue["cases"])
    assert all(case["hands"] == {} for case in queue["cases"])


def test_seed_corpus_target_one_is_safe(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    entries = []
    for i in range(3):
        p = frames / f"f{i}.jpg"
        p.write_bytes(str(i).encode())
        entries.append({"file": p.name, "time": i, "sha256": _sha(p)})
    (tmp_path / "manifest.json").write_text(json.dumps(_job_manifest(entries)), encoding="utf-8")
    result = build_seed_corpus(tmp_path, target_frames=1)
    assert result["selected_frames"] == 1


def test_seed_corpus_requires_manifest_hash_and_provenance(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    p = frames / "f.jpg"
    p.write_bytes(b"real")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"job_id": "j1", "source_fingerprint": "fp", "frames": [{"file": "f.jpg", "time": 0}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sha256 is required"):
        build_seed_corpus(tmp_path, target_frames=1)


def test_seed_corpus_rejects_hash_mismatch(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    p = frames / "f.jpg"
    p.write_bytes(b"real")
    (tmp_path / "manifest.json").write_text(
        json.dumps(_job_manifest([{"file": "f.jpg", "time": 0, "sha256": "0" * 64}])), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        build_seed_corpus(tmp_path, target_frames=1)


def test_gold_corpus_verifies_actual_frame_bytes(tmp_path: Path):
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"actual")
    case = {
        "frame": frame.name,
        "frame_sha256": "0" * 64,
        "human_verified": True,
        "hands": {"N": ["AS"]},
    }
    with pytest.raises(GoldCorpusError, match="hash mismatch"):
        to_detector_cases([case], tmp_path)


def test_pixel_backend_requires_bound_verified_passing_gold(tmp_path: Path):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"school-model")
    manifest = _approved_manifest(artifact)
    path = tmp_path / "backend.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    backend = load_approved_backend(path, infer_factory=lambda artifact, cfg: (lambda frame: {"artifact": artifact.name}))
    assert backend.artifact_sha256 == _sha(artifact)
    assert backend.test_corpus_sha256 == "2" * 64


def test_pixel_backend_rejects_forged_metric_ratios(tmp_path: Path):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"school-model")
    manifest = _approved_manifest(artifact)
    manifest["test_metrics"]["precision"] = 0.999
    path = tmp_path / "backend.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PixelBackendNotApproved, match="ratios do not match counts"):
        load_approved_backend(path, infer_factory=lambda artifact, cfg: (lambda frame: {}))


def test_pixel_backend_rejects_path_escape_and_train_test_reuse(tmp_path: Path):
    outside = tmp_path.parent / "outside-model.bin"
    outside.write_bytes(b"x")
    manifest = _approved_manifest(outside)
    manifest["artifact"] = "../outside-model.bin"
    path = tmp_path / "backend.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PixelBackendNotApproved, match="escapes"):
        load_approved_backend(path, infer_factory=lambda artifact, cfg: (lambda frame: {}))

    local = tmp_path / "model.bin"
    local.write_bytes(b"x")
    manifest = _approved_manifest(local)
    manifest["training_corpus_sha256"] = manifest["test_corpus_sha256"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PixelBackendNotApproved, match="must differ"):
        load_approved_backend(path, infer_factory=lambda artifact, cfg: (lambda frame: {}))


def test_pixel_backend_rejects_unverified_or_failed_gold(tmp_path: Path):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"x")
    manifest = _approved_manifest(artifact)
    manifest["human_gold_test_verified"] = False
    path = tmp_path / "backend.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PixelBackendNotApproved):
        load_approved_backend(path, infer_factory=lambda artifact, cfg: (lambda frame: {}))
