"""Build a bounded, review-first seed corpus from real Universal Video keyframes.

This module never labels cards automatically. It only selects immutable frame
references for later human verification and records SHA-256 provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_SEED_FRAMES = 500


@dataclass(frozen=True)
class SeedFrame:
    frame_file: str
    sha256: str
    time: float | None
    source_job_id: str
    source_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_file": self.frame_file,
            "sha256": self.sha256,
            "time": self.time,
            "source_job_id": self.source_job_id,
            "source_fingerprint": self.source_fingerprint,
            "human_verified": False,
            "hands": {},
            "review_tags": [],
            "notes": "",
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _indices(count: int, target: int) -> list[int]:
    if count <= 0:
        return []
    if target == 1:
        return [(count - 1) // 2]
    if count <= target:
        return list(range(count))
    # Deterministic stratified coverage including both ends of the video.
    return sorted({round(i * (count - 1) / (target - 1)) for i in range(target)})


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"manifest {field} is required for gold provenance")
    return text


def build_seed_corpus(job_dir: Path, *, target_frames: int = 80) -> dict[str, Any]:
    if isinstance(target_frames, bool) or not isinstance(target_frames, int):
        raise ValueError("target_frames must be an integer")
    if not 1 <= target_frames <= MAX_SEED_FRAMES:
        raise ValueError(f"target_frames must be in [1,{MAX_SEED_FRAMES}]")

    root = job_dir.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    source_job_id = _required_text(manifest.get("job_id"), "job_id")
    source_fingerprint = _required_text(manifest.get("source_fingerprint"), "source_fingerprint")
    frames = manifest.get("frames")
    if not isinstance(frames, list):
        raise ValueError("manifest frames must be an array")
    if not frames:
        raise ValueError("manifest frames must not be empty")

    frames_root = (root / "frames").resolve()
    selected: list[SeedFrame] = []
    for index in _indices(len(frames), target_frames):
        item = frames[index]
        if not isinstance(item, dict):
            raise ValueError("manifest frame entry must be an object")
        name = item.get("file")
        if not isinstance(name, str) or not name.strip() or Path(name).name != name:
            raise ValueError("unsafe frame filename")
        path = (frames_root / name).resolve()
        try:
            path.relative_to(frames_root)
        except ValueError as exc:
            raise ValueError("frame path escapes frames directory") from exc
        if not path.is_file():
            raise ValueError(f"missing frame: {name}")

        claimed = str(item.get("sha256") or "").lower()
        if not _SHA256.fullmatch(claimed):
            raise ValueError(f"frame sha256 is required and must be lowercase hex: {name}")
        actual = _sha256(path)
        if claimed != actual:
            raise ValueError(f"frame hash mismatch: {name}")

        raw_time = item.get("time")
        time_value = None
        if raw_time is not None:
            try:
                time_value = float(raw_time)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid frame time: {name}") from exc
            if not math.isfinite(time_value) or time_value < 0:
                raise ValueError(f"invalid frame time: {name}")

        selected.append(
            SeedFrame(
                frame_file=name,
                sha256=actual,
                time=time_value,
                source_job_id=source_job_id,
                source_fingerprint=source_fingerprint,
            )
        )

    queue = {
        "schema": "bridge-vision-gold-label-queue/v2",
        "status": "NEEDS_HUMAN_LABELING",
        "source_job_id": source_job_id,
        "source_fingerprint": source_fingerprint,
        "requested_frames": target_frames,
        "selected_frames": len(selected),
        "selection_policy": "deterministic-stratified-v2",
        "cases": [frame.to_dict() for frame in selected],
    }
    out = root / "bridge_vision_label_queue.json"
    out.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": queue["status"], "selected_frames": len(selected), "output": out.name}


__all__ = ["MAX_SEED_FRAMES", "SeedFrame", "build_seed_corpus"]
