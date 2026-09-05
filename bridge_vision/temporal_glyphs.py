"""Visual stability check for repeated glyph masks on distinct frames."""
from __future__ import annotations

import re
from typing import Mapping, Sequence

from bridge_vision.glyph_scoring import Mask, mask_iou

TEMPORAL_GLYPH_VERSION = "bridge-temporal-glyph-v2"
MIN_PAIR_IOU = 0.90
MIN_SUPPORT = 2
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def stable_consensus(
    observations: Sequence[Mapping[str, object]], *,
    min_pair_iou: float = MIN_PAIR_IOU, min_support: int = MIN_SUPPORT,
) -> dict:
    if min_pair_iou < MIN_PAIR_IOU or min_pair_iou > 1:
        raise ValueError("temporal IoU threshold cannot be lowered")
    if min_support < MIN_SUPPORT:
        raise ValueError("temporal support cannot be lowered below two frames")
    parsed: list[tuple[str, Mask]] = []
    for raw in observations:
        frame_sha = str(raw.get("frame_sha256") or "")
        mask = raw.get("mask")
        if not _SHA256.fullmatch(frame_sha) or not isinstance(mask, Sequence) or isinstance(mask, (str, bytes)):
            raise ValueError("temporal observation requires a frame hash and mask")
        parsed.append((frame_sha, mask))  # type: ignore[arg-type]
    if len({frame for frame, _ in parsed}) != len(parsed):
        raise ValueError("temporal observations must come from distinct frames")
    if len(parsed) < min_support:
        return {"schema": TEMPORAL_GLYPH_VERSION, "status": "INSUFFICIENT_SUPPORT", "template": None, "stable_frames": []}
    stable = []
    for index, (frame_sha, mask) in enumerate(parsed):
        if any(index != other_index and mask_iou(mask, other) >= min_pair_iou for other_index, (_, other) in enumerate(parsed)):
            stable.append(index)
    if len(stable) < min_support:
        return {"schema": TEMPORAL_GLYPH_VERSION, "status": "UNSTABLE", "template": None, "stable_frames": [parsed[i][0] for i in stable]}
    height, width = len(parsed[0][1]), len(parsed[0][1][0])
    if any(len(mask) != height or any(len(row) != width for row in mask) for _, mask in parsed):
        raise ValueError("glyph mask dimensions differ")
    needed = len(stable) // 2 + 1
    template = [[sum(bool(parsed[i][1][y][x]) for i in stable) >= needed for x in range(width)] for y in range(height)]
    return {
        "schema": TEMPORAL_GLYPH_VERSION, "status": "STABLE", "template": template,
        "stable_frames": [parsed[i][0] for i in stable], "support": len(stable),
        "min_pair_iou": min_pair_iou,
    }


__all__ = ["MIN_PAIR_IOU", "MIN_SUPPORT", "TEMPORAL_GLYPH_VERSION", "stable_consensus"]
