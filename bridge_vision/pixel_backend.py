"""Gated school-owned pixel backend contract.

A backend artifact is never active merely because it exists. It must carry an
immutable test-set evaluation that passes the school gold gate. This module
provides the loading/activation boundary; training is deliberately separate.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from bridge_vision.gold import GoldMetrics, passes_card_gold_gate

PIXEL_BACKEND_SCHEMA = "bridge-vision-pixel-backend/v1"


class PixelBackendNotApproved(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovedPixelBackend:
    artifact_path: Path
    artifact_sha256: str
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
    artifact = (manifest_path.parent / str(manifest.get("artifact") or "")).resolve()
    if not artifact.is_file():
        raise PixelBackendNotApproved("pixel backend artifact is missing")
    expected_sha = str(manifest.get("artifact_sha256") or "")
    actual_sha = _sha256(artifact)
    if len(expected_sha) != 64 or expected_sha != actual_sha:
        raise PixelBackendNotApproved("pixel backend artifact hash mismatch")
    raw = manifest.get("test_metrics") or {}
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
    if not passes_card_gold_gate(metrics):
        raise PixelBackendNotApproved("pixel backend failed gold gate")
    infer = infer_factory(artifact, manifest)
    return ApprovedPixelBackend(artifact, actual_sha, metrics, infer)


__all__ = [
    "PIXEL_BACKEND_SCHEMA",
    "ApprovedPixelBackend",
    "PixelBackendNotApproved",
    "load_approved_backend",
]
