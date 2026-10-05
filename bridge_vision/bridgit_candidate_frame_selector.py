"""Candidate-owned selector state; historical runtime patches cannot replace it.

The class preserves the frozen selector's thresholds and event state machine.
Its separate type is intentional: the pinned r26 runtime mutates the historical
class globally. Pure signature/distance helpers remain shared.
"""
from __future__ import annotations

from typing import Any

from bridge_vision.bridgit_event_frame_selector import FrameEvent, signature_distance


class CandidateFrameSelector:
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


__all__ = ["CandidateFrameSelector"]
