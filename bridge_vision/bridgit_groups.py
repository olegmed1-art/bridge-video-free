"""Fail-closed grouping of visible Bridgit card starts into suit runs.

This stage is geometry only. It uses the characteristic overlap pitch of a run
of cards, but a large gap merely starts another group; it does not name a suit
or infer hidden/missing cards.
"""
from __future__ import annotations

from typing import Sequence


def group_card_starts(starts: Sequence[int], *, min_pitch: int = 24, max_pitch: int = 34) -> list[list[int]]:
    if min_pitch <= 0 or max_pitch < min_pitch:
        raise ValueError("invalid pitch bounds")
    values = [int(x) for x in starts]
    if values != sorted(set(values)):
        raise ValueError("card starts must be unique and sorted")
    if not values:
        return []
    groups = [[values[0]]]
    for x in values[1:]:
        gap = x - groups[-1][-1]
        if min_pitch <= gap <= max_pitch:
            groups[-1].append(x)
        elif gap > max_pitch:
            groups.append([x])
        else:
            raise ValueError("card starts contain an implausibly small gap")
    return groups


__all__ = ["group_card_starts"]
