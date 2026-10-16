"""Versioned, conservative timing profile for adjacent candidate grouping.

The 10-second bound carries forward the explicit temporal-pair limit from
``bridgit_primary_video_r264.MAX_TEMPORAL_PAIR_GAP_MS``. It is separate from
the frozen gold-v2 visual profile and does not alter rank geometry or scores.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdjacentCandidateProfile:
    version: str
    max_gap_ms: int


PROFILE = AdjacentCandidateProfile(
    version="bridgit-adjacent-candidate-v1",
    max_gap_ms=10_000,
)


__all__ = ["AdjacentCandidateProfile", "PROFILE"]
