"""Bounded production compatibility layer for native Gambler fan geometry.

The historical rank-layout backend is kept intact.  This adapter temporarily
supplies scale-aware fan geometry while one production recognition call runs,
then restores every patched symbol.  It does not change recognition thresholds,
use hidden cards, or write canonical data.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from itertools import product
from typing import Any, Mapping, Sequence

from bridge_vision import bridgit_rank_layout as base

MAX_VERTICAL_PADDING_PX = 120


def infer_horizontal_fan_model(widths: Sequence[int]) -> tuple[tuple[int, ...], float] | None:
    if len(widths) != len(base.SUITS) or any(int(value) <= 60 for value in widths):
        return None
    observed = [float(value) for value in widths]
    best = None
    for lengths in product(range(1, 11), repeat=len(base.SUITS)):
        if sum(lengths) != 13:
            continue
        xs = [float(length - 1) for length in lengths]
        mx, my = sum(xs) / len(xs), sum(observed) / len(observed)
        variance = sum((value - mx) ** 2 for value in xs)
        if variance <= 0:
            continue
        step = sum((x - mx) * (y - my) for x, y in zip(xs, observed)) / variance
        card_width = my - step * mx
        if not (60.0 <= card_width <= 150.0 and 10.0 <= step <= 50.0):
            continue
        error = math.sqrt(sum((card_width + step * x - y) ** 2 for x, y in zip(xs, observed)) / len(observed))
        candidate = (error, tuple(int(v) for v in lengths), step)
        if best is None or candidate < best:
            best = candidate
    if best is None or best[0] > 3.0:
        return None
    return best[1], round(best[2], 4)


def horizontal_geometry_detail(frame: Any, seat: str, profile: base.BridgitRankLayoutProfile):
    cv2, np = base._pixel_runtime()
    x_min, configured_x_max, configured_y = profile.horizontal_search[seat]
    x_max = min(profile.width, configured_x_max + max(64, profile.glyph_width * 4))
    candidates = []
    for y in range(max(0, configured_y - 10), min(profile.height - 34, configured_y + 10) + 1):
        gray = cv2.cvtColor(frame[y : y + 34, x_min:x_max], cv2.COLOR_BGR2GRAY)
        active = ((gray > profile.binary_threshold).mean(axis=0) > 0.30).astype("uint8")[None, :]
        active = cv2.morphologyEx(active, cv2.MORPH_CLOSE, np.ones((1, 4), "uint8")).ravel()
        runs = []
        start = None
        for index, value in enumerate(np.r_[active, 0]):
            if value and start is None:
                start = index
            elif not value and start is not None:
                if index - start > 60:
                    runs.append((x_min + start, index - start))
                start = None
        if len(runs) != len(base.SUITS):
            continue
        model = infer_horizontal_fan_model([width for _, width in runs])
        if model is not None:
            lengths, step = model
            candidates.append((abs(y - configured_y), y, runs, lengths, step))
    if not candidates:
        return {suit: 0 for suit in base.SUITS}, {}, None
    _, y, runs, raw_lengths, step = min(candidates)
    lengths = {suit: int(value) for suit, value in zip(base.SUITS, raw_lengths)}
    anchors = {suit: (start_x + 1, y) for suit, (start_x, _) in zip(base.SUITS, runs)}
    return lengths, anchors, float(step)


def register_same_width_vertical_padding(image: Any, profile: base.BridgitRankLayoutProfile):
    if not hasattr(image, "shape"):
        raise base.BridgitRankLayoutError("vertical padding input is not a raster")
    height, width = image.shape[:2]
    extra = height - profile.height
    if width != profile.width or not 1 <= extra <= MAX_VERTICAL_PADDING_PX:
        raise base.BridgitRankLayoutError("vertical padding is outside supported bounds")
    accepted = []
    for y0 in dict.fromkeys((extra, 0)):
        crop = image[y0 : y0 + profile.height, : profile.width]
        evidence = {}
        proven = tuple(crop.shape[:2]) == (profile.height, profile.width)
        for seat in ("N", "S"):
            if not proven:
                break
            lengths, anchors, step = horizontal_geometry_detail(crop, seat, profile)
            evidence[seat] = {"lengths": lengths, "anchors": anchors, "overlap_step": step}
            proven = bool(anchors) and sum(lengths.values()) == 13
        if proven:
            accepted.append((y0, crop.copy(), evidence))
    if len(accepted) != 1:
        raise base.BridgitRankLayoutError("vertical padding registration not uniquely proven")
    y0, registered, evidence = accepted[0]
    return registered, {
        "mode": "VERTICAL_PADDING_CROP",
        "input_size": {"width": width, "height": height},
        "registered_size": {"width": profile.width, "height": profile.height},
        "padding_top_px": y0,
        "geometry_evidence": evidence,
        "anchor_region": None,
        "game_window": {"coordinate_space": "NORMALIZED_INPUT_FRAME", "x": 0.0, "y": round(y0 / height, 8), "width": 1.0, "height": round(profile.height / height, 8)},
    }


def _fit_side_steps(lengths, anchors, side_chains, profile):
    adjusted = {seat: dict(values) for seat, values in anchors.items()}
    result = {seat: {} for seat in ("W", "E")}
    tolerance = profile.local_registration_px + 1
    for seat in ("W", "E"):
        for suit in base.SUITS:
            count = int(lengths[seat][suit])
            chain = tuple(int(value) for value in side_chains[seat][suit])
            if len(chain) != count:
                raise base._SideStepCalibrationAmbiguous("side chain count changed")
            if chain:
                _, current_y = adjusted[seat][suit]
                adjusted[seat][suit] = (chain[0], current_y)
            if count <= 1:
                result[seat][suit] = 25.0
                continue
            expected = chain if seat == "W" else tuple(reversed(chain))
            ranked = []
            for quarter in range(72, 145):
                step = quarter / 4.0
                trial = {seat: {suit: step}}
                observed = tuple(x for x, _ in base._coords(seat, suit, count, adjusted, trial))
                errors = tuple(abs(left - right) for left, right in zip(observed, expected))
                ranked.append((max(errors), sum(errors), abs(step - 25.0), step))
            max_error, _, _, best_step = min(ranked)
            if max_error > tolerance:
                raise base._SideStepCalibrationAmbiguous("side fan does not fit detected peaks")
            result[seat][suit] = best_step
    return result, adjusted


@contextmanager
def native_gambler_geometry():
    """Temporarily adapt the historical backend to native Gambler card scale."""
    old_horizontal = base._horizontal_geometry
    old_side_lengths = base._side_lengths
    old_calibrate = base._calibrate_side_steps
    old_match = base._calibrated_side_slots_match_chains
    horizontal_steps: dict[str, float] = {}
    latest_chains = None

    def horizontal(frame, seat, profile):
        lengths, anchors, step = horizontal_geometry_detail(frame, seat, profile)
        if step is not None:
            horizontal_steps[seat] = step
        return lengths, anchors

    def side_lengths(frames, bank, profile):
        nonlocal latest_chains
        cv2, np = base._pixel_runtime()
        result = {"W": {}, "E": {}, "_chains": {"W": {}, "E": {}}}
        for seat in ("W", "E"):
            x_min, x_max, edge = profile.vertical_search[seat]
            direction = 1 if seat == "W" else -1
            for suit in base.SUITS:
                y = profile.anchors[seat][suit][1]
                per_frame = []
                for frame in frames:
                    values = [max(base._similarity(base._glyph(frame, x, y, profile), bank[r]) for r in base.RANKS) for x in range(x_min, x_max)]
                    per_frame.append(values)
                values = np.median(np.asarray(per_frame), axis=0)
                chain = base.find_chain_peaks(values, origin=x_min, edge=edge, direction=direction, min_height=profile.min_peak_score, min_prominence=profile.min_peak_prominence, max_gap=36)
                result[seat][suit] = len(chain)
                result["_chains"][seat][suit] = tuple(chain)
        latest_chains = result["_chains"]
        return result

    def calibrate(frames, bank, lengths, anchors, profile):
        if latest_chains is None:
            return old_calibrate(frames, bank, lengths, anchors, profile)
        side_steps, adjusted = _fit_side_steps(lengths, anchors, latest_chains, profile)
        for seat in ("W", "E"):
            anchors[seat].update(adjusted[seat])
        return {
            "N": {suit: horizontal_steps.get("N", 25.0) for suit in base.SUITS},
            "S": {suit: horizontal_steps.get("S", 25.0) for suit in base.SUITS},
            **side_steps,
        }

    def match(lengths, anchors, side_steps, side_chains):
        return all(
            len(calibrated) == len(expected)
            and all(abs(left - right) <= 3 for left, right in zip(calibrated, expected))
            for seat in ("W", "E")
            for suit in base.SUITS
            for expected in [tuple(reversed(side_chains[seat][suit])) if seat == "E" else tuple(side_chains[seat][suit])]
            for calibrated in [tuple(x for x, _ in base._coords(seat, suit, lengths[seat][suit], anchors, side_steps))]
        )

    base._horizontal_geometry = horizontal
    base._side_lengths = side_lengths
    base._calibrate_side_steps = calibrate
    base._calibrated_side_slots_match_chains = match
    try:
        yield
    finally:
        base._horizontal_geometry = old_horizontal
        base._side_lengths = old_side_lengths
        base._calibrate_side_steps = old_calibrate
        base._calibrated_side_slots_match_chains = old_match


__all__ = ["MAX_VERTICAL_PADDING_PX", "horizontal_geometry_detail", "native_gambler_geometry", "register_same_width_vertical_padding"]
