"""Build a bounded, review-first seed corpus from real Universal Video keyframes.

This module never labels cards automatically. It only selects immutable frame
references for later human verification and records SHA-256 provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class SeedFrame:
    frame_file: str
    sha256: str
    time: float | None
    source_job_id: str | None
    source_fingerprint: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_file": self.frame_file,
            "sha256": self.sha256,
            "time": self.time,
            "source_job_id": self.source_job_id,
            "source_fingerprint": self.source_fingerprint,
            "human_verified": False,
            "hands": {},
            "notes": "",
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _indices(count: int, target: int) -> list[int]:
    if count <= 0 or target <= 0:
        return []
    if count <= target:
        return list(range(count))
    # Deterministic stratified coverage including both ends of the video.
    return sorted({round(i * (count - 1) / (target - 1)) for i in range(target)})


def build_seed_corpus(job_dir: Path, *, target_frames: int = 80) -> dict[str, Any]:
    root = job_dir.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    frames = manifest.get("frames")
    if not isinstance(frames, list):
        raise ValueError("manifest frames must be an array")
    selected: list[SeedFrame] = []
    for index in _indices(len(frames), target_frames):
        item = frames[index]
        if not isinstance(item, dict):
            raise ValueError("manifest frame entry must be an object")
        name = item.get("file")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("unsafe frame filename")
        path = (root / "frames" / name).resolve()
        path.relative_to((root / "frames").resolve())
        if not path.is_file():
            raise ValueError(f"missing frame: {name}")
        actual = _sha256(path)
        claimed = item.get("sha256")
        if claimed and claimed != actual:
            raise ValueError(f"frame hash mismatch: {name}")
        selected.append(
            SeedFrame(
                frame_file=name,
                sha256=actual,
                time=float(item["time"]) if item.get("time") is not None else None,
                source_job_id=manifest.get("job_id"),
                source_fingerprint=manifest.get("source_fingerprint"),
            )
        )

    queue = {
        "schema": "bridge-vision-gold-label-queue/v1",
        "status": "NEEDS_HUMAN_LABELING",
        "source_job_id": manifest.get("job_id"),
        "source_fingerprint": manifest.get("source_fingerprint"),
        "requested_frames": target_frames,
        "selected_frames": len(selected),
        "cases": [frame.to_dict() for frame in selected],
    }
    out = root / "bridge_vision_label_queue.json"
    out.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": queue["status"], "selected_frames": len(selected), "output": out.name}


__all__ = ["SeedFrame", "build_seed_corpus"]
