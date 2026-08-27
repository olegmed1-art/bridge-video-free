"""Bounded pixel primitives for visible Bridgit card panels.

This locator does not recognize cards. It only identifies candidate white card
panels / exposed card starts inside an explicit table crop. Recognition is a
separate fail-closed stage. Platform-specific assumptions stay isolated here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

BRIDGIT_PIXEL_VERSION = "bridgit-visible-panels-v1"


@dataclass(frozen=True)
class PanelStart:
    x: int
    y: int
    exposed_width: int
    exposed_height: int


def _is_card_white(rgb: Sequence[int], *, floor: int = 238, spread: int = 12) -> bool:
    if len(rgb) < 3:
        return False
    r, g, b = (int(rgb[i]) for i in range(3))
    return min(r, g, b) >= floor and max(r, g, b) - min(r, g, b) <= spread


def white_runs(row: Iterable[Sequence[int]], *, min_width: int = 8) -> list[tuple[int, int]]:
    """Return inclusive-exclusive white runs; tiny highlights are ignored."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for x, rgb in enumerate(row):
        white = _is_card_white(rgb)
        if white and start is None:
            start = x
        elif not white and start is not None:
            if x - start >= min_width:
                runs.append((start, x))
            start = None
    if start is not None:
        end = x + 1 if 'x' in locals() else 0
        if end - start >= min_width:
            runs.append((start, end))
    return runs


def exposed_starts_from_scanlines(
    rows: Sequence[Sequence[Sequence[int]]], *, y0: int = 0,
    min_width: int = 8, min_support: int = 2, merge_tolerance: int = 3,
) -> list[PanelStart]:
    """Find repeated white-run starts across nearby scanlines.

    A candidate must occur on multiple scanlines. This prevents one-pixel UI
    highlights/noise from becoming card evidence. The result is geometry only.
    """
    if min_support < 1:
        raise ValueError("min_support must be positive")
    clusters: list[dict[str, int]] = []
    for offset, row in enumerate(rows):
        for start, end in white_runs(row, min_width=min_width):
            match = next((c for c in clusters if abs(c["x"] - start) <= merge_tolerance), None)
            if match is None:
                clusters.append({"x": start, "first_y": y0 + offset, "last_y": y0 + offset,
                                 "support": 1, "max_width": end - start})
            else:
                match["last_y"] = y0 + offset
                match["support"] += 1
                match["max_width"] = max(match["max_width"], end - start)
    return [PanelStart(c["x"], c["first_y"], c["max_width"], c["last_y"] - c["first_y"] + 1)
            for c in clusters if c["support"] >= min_support]


__all__ = ["BRIDGIT_PIXEL_VERSION", "PanelStart", "white_runs", "exposed_starts_from_scanlines"]
