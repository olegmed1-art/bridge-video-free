"""Event-driven selection of stable bridge-table video frames.

The selector is intentionally lightweight: it watches only configured game
regions, ignores tiny localized changes such as a mouse cursor, and emits a
frame only after a materially changed state has become stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class FrameEvent:
    timestamp_ms: int
    reason: str
    change_score: float
    stable_ms: int


def bridge_layout_regions(viewport_y: int = 0) -> list[tuple[int, int, int, int]]:
    """Return card-layout regions as x0, y0, x1, y1 rectangles."""
    return [
        (285, 5 + viewport_y, 1075, 110 + viewport_y),
        (1145, 275 + viewport_y, 1375, 505 + viewport_y),
        (285, 765 + viewport_y, 1075, 915 + viewport_y),
        (0, 275 + viewport_y, 190, 505 + viewport_y),
        # Centre table/auction area: catches a played card or a new call even
        # when the four exposed hand bands have not otherwise changed.
        (190, 110 + viewport_y, 1145, 765 + viewport_y),
    ]


def frame_signature(frame: Any, regions: Sequence[tuple[int, int, int, int]]) -> Any:
    """Build a compact blurred signature from server-decoded pixels."""
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
        raise ValueError("frame is invalid")
    height, width = frame.shape[:2]
    pieces = []
    for x0, y0, x1, y1 in regions:
        xa, xb = max(0, int(x0)), min(width, int(x1))
        ya, yb = max(0, int(y0)), min(height, int(y1))
        if xb <= xa or yb <= ya:
            continue
        gray = cv2.cvtColor(frame[ya:yb, xa:xb], cv2.COLOR_BGR2GRAY)
        reduced = cv2.resize(gray, (96, 24), interpolation=cv2.INTER_AREA)
        pieces.append(cv2.GaussianBlur(reduced, (5, 5), 0))
    if not pieces:
        raise ValueError("no event region intersects the frame")
    return np.concatenate(pieces, axis=0)


def signature_distance(first: Any, second: Any) -> float:
    """Return robust normalized visual distance in [0,1]."""
    import numpy as np  # type: ignore

    if first.shape != second.shape:
        return 1.0
    difference = np.abs(first.astype(np.float32) - second.astype(np.float32)) / 255.0
    global_change = float(difference.mean())
    block_scores = []
    for y in range(0, difference.shape[0], 8):
        for x in range(0, difference.shape[1], 8):
            block_scores.append(float(difference[y : y + 8, x : x + 8].mean()))
    block_scores.sort(reverse=True)
    # One isolated block is treated as cursor/noise. A card-place change spans
    # multiple neighbouring blocks and is detected by the second strongest.
    localized_change = block_scores[1] if len(block_scores) > 1 else block_scores[0]
    return max(global_change, localized_change)


class EventFrameSelector:
    """Emit initial/change/watchdog events only for stable visual states."""

    def __init__(
        self,
        *,
        settle_ms: int = 750,
        change_threshold: float = 0.018,
        stable_threshold: float = 0.004,
        watchdog_ms: int = 60_000,
    ) -> None:
        if settle_ms < 0 or watchdog_ms <= 0:
            raise ValueError("event timing is invalid")
        if not 0.0 <= stable_threshold < change_threshold <= 1.0:
            raise ValueError("event thresholds are invalid")
        self.settle_ms = settle_ms
        self.change_threshold = change_threshold
        self.stable_threshold = stable_threshold
        self.watchdog_ms = watchdog_ms
        self.baseline = None
        self.previous = None
        self.previous_ms: int | None = None
        self.stable_since_ms: int | None = None
        self.last_emit_ms: int | None = None

    def observe(self, signature: Any, timestamp_ms: int) -> FrameEvent | None:
        if timestamp_ms < 0 or (self.previous_ms is not None and timestamp_ms <= self.previous_ms):
            raise ValueError("timestamps must be strictly increasing")
        if self.previous is None:
            self.previous = signature.copy()
            self.previous_ms = timestamp_ms
            self.stable_since_ms = timestamp_ms
            return None

        movement = signature_distance(self.previous, signature)
        if movement <= self.stable_threshold:
            if self.stable_since_ms is None:
                self.stable_since_ms = self.previous_ms
        else:
            self.stable_since_ms = None
        stable_ms = 0 if self.stable_since_ms is None else timestamp_ms - self.stable_since_ms
        baseline_delta = 1.0 if self.baseline is None else signature_distance(self.baseline, signature)
        reason = None
        if stable_ms >= self.settle_ms:
            if self.baseline is None:
                reason = "INITIAL_STABLE_STATE"
            elif baseline_delta >= self.change_threshold:
                reason = "STABLE_VISUAL_CHANGE"
            elif self.last_emit_ms is not None and timestamp_ms - self.last_emit_ms >= self.watchdog_ms:
                reason = "WATCHDOG_STABLE_STATE"

        event = None
        if reason is not None:
            event = FrameEvent(timestamp_ms, reason, round(baseline_delta, 6), stable_ms)
            self.baseline = signature.copy()
            self.last_emit_ms = timestamp_ms
        self.previous = signature.copy()
        self.previous_ms = timestamp_ms
        return event

    def schedule_retry(self, timestamp_ms: int, delay_ms: int = 1_500) -> None:
        """Make one stable-state watchdog retry due after a short delay."""
        if self.baseline is None or delay_ms <= 0 or delay_ms >= self.watchdog_ms:
            raise ValueError("retry timing is invalid")
        self.last_emit_ms = timestamp_ms - self.watchdog_ms + delay_ms


__all__ = [
    "EventFrameSelector", "FrameEvent", "bridge_layout_regions",
    "frame_signature", "signature_distance",
]
