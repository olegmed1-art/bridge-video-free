import hashlib
import json
from pathlib import Path

import pytest

from bridge_vision.pixel_backend import PixelBackendNotApproved, load_approved_backend
from bridge_vision.seed_corpus import build_seed_corpus


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_seed_corpus_is_review_first_and_hash_verified(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    entries = []
    for i in range(5):
        p = frames / f"f{i}.jpg"
        p.write_bytes(f"frame-{i}".encode())
        entries.append({"file": p.name, "time": i * 10.0, "sha256": _sha(p)})
    (tmp_path / "manifest.json").write_text(
        json.dumps({"job_id": "j1", "source_fingerprint": "fp", "frames": entries}), encoding="utf-8"
    )
    result = build_seed_corpus(tmp_path, target_frames=3)
    assert result["status"] == "NEEDS_HUMAN_LABELING"
    queue = json.loads((tmp_path / "bridge_vision_label_queue.json").read_text(encoding="utf-8"))
    assert queue["selected_frames"] == 3
    assert all(case["human_verified"] is False for case in queue["cases"])
    assert all(case["hands"] == {} for case in queue["cases"])


def test_seed_corpus_rejects_hash_mismatch(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    p = frames / "f.jpg"
    p.write_bytes(b"real")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"frames": [{"file": "f.jpg", "time": 0, "sha256": "0" * 64}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        build_seed_corpus(tmp_path, target_frames=1)


def test_pixel_backend_requires_human_verified_passing_gold(tmp_path: Path):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"school-model")
    manifest = {
        "schema": "bridge-vision-pixel-backend/v1",
        "artifact": artifact.name,
        "artifact_sha256": _sha(artifact),
        "human_gold_test_verified": True,
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
    path = tmp_path / "backend.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    backend = load_approved_backend(path, infer_factory=lambda artifact, cfg: (lambda frame: {"artifact": artifact.name}))
    assert backend.artifact_sha256 == _sha(artifact)


def test_pixel_backend_rejects_unverified_or_failed_gold(tmp_path: Path):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"x")
    manifest = {
        "schema": "bridge-vision-pixel-backend/v1",
        "artifact": artifact.name,
        "artifact_sha256": _sha(artifact),
        "human_gold_test_verified": False,
        "test_metrics": {},
    }
    path = tmp_path / "backend.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PixelBackendNotApproved):
        load_approved_backend(path, infer_factory=lambda artifact, cfg: (lambda frame: {}))
