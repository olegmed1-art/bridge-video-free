"""Gated school-owned pixel backend contract.

A backend artifact is never active merely because it exists. It must be bound to
an immutable, human-verified test corpus and pass the school gold gate. Training
and evaluation stay separate from this activation boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from bridge_vision.gold import GoldMetrics, passes_card_gold_gate

PIXEL_BACKEND_SCHEMA = "bridge-vision-pixel-backend/v2"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MIN_GOLD_FRAMES = 20
MIN_EXPECTED_CARDS = 100


class PixelBackendNotApproved(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovedPixelBackend:
    artifact_path: Path
    artifact_sha256: str
    test_corpus_sha256: str
    metrics: GoldMetrics
    infer: Callable[[Path], Mapping[str, Any]]

    def __call__(self, frame: Path) -> Mapping[str, Any]:
        return self.infer(frame)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _required_sha(manifest: Mapping[str, Any], field: str) -> str:
    value = str(manifest.get(field) or "").lower()
    if not _SHA256.fullmatch(value):
        raise PixelBackendNotApproved(f"{field} must be 64 lowercase hex characters")
    return value


def _validate_metrics(raw: Mapping[str, Any]) -> GoldMetrics:
    try:
        metrics = GoldMetrics(
            frames=int(raw["frames"]),
            expected_cards=int(raw["expected_cards"]),
            predicted_cards=int(raw["predicted_cards"]),
            true_positive_cards=int(raw["true_positive_cards"]),
            seat_errors=int(raw["seat_errors"]),
            precision=float(raw["precision"]),
            recall=float(raw["recall"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PixelBackendNotApproved("invalid test metrics") from exc

    counts = (
        metrics.frames,
        metrics.expected_cards,
        metrics.predicted_cards,
        metrics.true_positive_cards,
        metrics.seat_errors,
    )
    if any(value < 0 for value in counts):
        raise PixelBackendNotApproved("test metric counts must be non-negative")
    if metrics.frames < MIN_GOLD_FRAMES or metrics.expected_cards < MIN_EXPECTED_CARDS:
        raise PixelBackendNotApproved("gold test support is too small")
    if metrics.true_positive_cards > metrics.expected_cards or metrics.true_positive_cards > metrics.predicted_cards:
        raise PixelBackendNotApproved("inconsistent test metric counts")
    if not math.isfinite(metrics.precision) or not math.isfinite(metrics.recall):
        raise PixelBackendNotApproved("test metric ratios must be finite")

    expected_precision = (
        metrics.true_positive_cards / metrics.predicted_cards
        if metrics.predicted_cards
        else (1.0 if metrics.expected_cards == 0 else 0.0)
    )
    expected_recall = metrics.true_positive_cards / metrics.expected_cards if metrics.expected_cards else 1.0
    if abs(metrics.precision - expected_precision) > 1e-12 or abs(metrics.recall - expected_recall) > 1e-12:
        raise PixelBackendNotApproved("test metric ratios do not match counts")
    return metrics


def load_approved_backend(
    manifest_path: Path,
    *,
    infer_factory: Callable[[Path, Mapping[str, Any]], Callable[[Path], Mapping[str, Any]]],
) -> ApprovedPixelBackend:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != PIXEL_BACKEND_SCHEMA:
        raise PixelBackendNotApproved("unsupported pixel backend schema")
    if manifest.get("human_gold_test_verified") is not True:
        raise PixelBackendNotApproved("immutable human-verified test set is required")
    if str(manifest.get("split_policy") or "") != "immutable-train-test-v1":
        raise PixelBackendNotApproved("approved immutable train/test split policy is required")

    test_corpus_sha = _required_sha(manifest, "test_corpus_sha256")
    training_corpus_sha = _required_sha(manifest, "training_corpus_sha256")
    if test_corpus_sha == training_corpus_sha:
        raise PixelBackendNotApproved("training and test corpus fingerprints must differ")

    artifact_text = str(manifest.get("artifact") or "").strip()
    if not artifact_text:
        raise PixelBackendNotApproved("pixel backend artifact is missing")
    artifact_rel = Path(artifact_text)
    if artifact_rel.is_absolute():
        raise PixelBackendNotApproved("pixel backend artifact must be relative to manifest")
    artifact_root = manifest_path.parent.resolve()
    artifact = (artifact_root / artifact_rel).resolve()
    try:
        artifact.relative_to(artifact_root)
    except ValueError as exc:
        raise PixelBackendNotApproved("pixel backend artifact escapes manifest directory") from exc
    if not artifact.is_file():
        raise PixelBackendNotApproved("pixel backend artifact is missing")

    expected_sha = _required_sha(manifest, "artifact_sha256")
    actual_sha = _sha256(artifact)
    if expected_sha != actual_sha:
        raise PixelBackendNotApproved("pixel backend artifact hash mismatch")

    raw = manifest.get("test_metrics")
    if not isinstance(raw, Mapping):
        raise PixelBackendNotApproved("test_metrics must be an object")
    metrics = _validate_metrics(raw)
    if not passes_card_gold_gate(metrics):
        raise PixelBackendNotApproved("pixel backend failed gold gate")

    infer = infer_factory(artifact, manifest)
    if not callable(infer):
        raise PixelBackendNotApproved("infer_factory did not return a callable")
    return ApprovedPixelBackend(artifact, actual_sha, test_corpus_sha, metrics, infer)


__all__ = [
    "MIN_EXPECTED_CARDS",
    "MIN_GOLD_FRAMES",
    "PIXEL_BACKEND_SCHEMA",
    "ApprovedPixelBackend",
    "PixelBackendNotApproved",
    "load_approved_backend",
]
