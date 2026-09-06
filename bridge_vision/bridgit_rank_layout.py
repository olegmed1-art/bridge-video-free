"""Opt-in shadow recognizer for the profiled Bridgit desktop card layout.

The backend recognizes only pixels that are visibly present in supplied frames.
It uses a human-reviewed reference frame to build rank templates, detects suit
fan geometry, and solves the visible rank ordering as a per-suit bijection.

This is deliberately *not* a ``BridgeVisionEngine`` detector.  Rank matching,
layout position and the deck constraint are not independent recognition
channels, so even a complete result remains a shadow candidate.  A caller must
not relabel it as OBSERVED or use it to write SCHOOL CANON.

OpenCV and NumPy are loaded lazily.  The repository's contract-only CI can
therefore validate the fail-closed boundary without installing pixel runtime
dependencies; the Universal Video worker image already pins those packages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import sys
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from bridge_vision.anchor_registration import (
    AnchorRegistrationError,
    estimate_anchor_peak_scratch_bytes,
    register_from_upper_right_anchor,
    validate_anchor_job_budget,
    validate_anchor_spec,
)
from bridge_vision.deal_evidence import (
    MAX_POINTER_EVENTS,
    DealEvidenceError,
    build_deal_evidence_report,
    normalize_teacher_pointer_events,
)

PROFILE_SCHEMA = "bridge-vision-bridgit-rank-layout/v1"
JOB_TYPE = "BRIDGIT_RANK_LAYOUT_SHADOW_V1"
RECEIPT_TYPE = "BRIDGIT_RANK_LAYOUT_SHADOW_RECEIPT_V1"
BACKEND_VERSION = "bridge-vision-bridgit-rank-layout-v1"

RANKS = tuple("AKQJT98765432")
SUITS = tuple("HCDS")
SEATS = tuple("NESW")
CARDS = frozenset(rank + suit for rank in RANKS for suit in SUITS)

MAX_PROFILE_BYTES = 1024 * 1024
MAX_JOB_BYTES = 256 * 1024
MAX_FRAMES = 16
MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_DECODED_FRAME_BYTES = 64 * 1024 * 1024
MAX_DECODED_JOB_BYTES = 256 * 1024 * 1024
MAX_VERTICAL_SEARCH_SPAN = 512
MAX_TEMPLATE_SCORING_CALLS = 1_000_000
MAX_TEMPLATE_SCORING_DOT_PRODUCTS = 24_000_000_000
MAX_RECOGNITION_MISC_WORKSPACE_BYTES = 32 * 1024 * 1024
MAX_DIAGNOSTIC_DETAIL = 160

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")


class BridgitRankLayoutError(ValueError):
    """Input, profile or recognition evidence failed a closed gate."""


class BridgitPixelRuntimeUnavailable(RuntimeError):
    """The optional OpenCV/NumPy pixel runtime is not installed."""


@lru_cache(maxsize=1)
def _pixel_runtime():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on worker image
        raise BridgitPixelRuntimeUnavailable(
            "Bridgit shadow recognition requires opencv-python-headless and numpy"
        ) from exc
    return cv2, np


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bounded_bytes(path: Path, max_bytes: int, kind: str) -> bytes:
    try:
        with path.open("rb") as source:
            payload = source.read(max_bytes + 1)
    except OSError as exc:
        raise BridgitRankLayoutError(f"{kind} is unavailable") from exc
    if len(payload) > max_bytes:
        raise BridgitRankLayoutError(f"{kind} exceeds size limit")
    return payload


def _bounded_sha256(path: Path, max_bytes: int, kind: str) -> str:
    return hashlib.sha256(_read_bounded_bytes(path, max_bytes, kind)).hexdigest()


def canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required_sha(value: Any, field: str) -> str:
    text = str(value or "").lower()
    if not _SHA256.fullmatch(text):
        raise BridgitRankLayoutError(f"invalid {field}")
    return text


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise BridgitRankLayoutError(f"invalid {field}")
    if isinstance(value, str) and not re.fullmatch(r"-?[0-9]+", value.strip()):
        raise BridgitRankLayoutError(f"invalid {field}")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BridgitRankLayoutError(f"invalid {field}") from exc
    if number < minimum or number > maximum:
        raise BridgitRankLayoutError(f"{field} outside allowed range")
    return number


def _number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BridgitRankLayoutError(f"invalid {field}") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise BridgitRankLayoutError(f"{field} outside allowed range")
    return number


def _point(raw: Any, field: str, *, width: int, height: int) -> tuple[int, int]:
    if not isinstance(raw, Mapping):
        raise BridgitRankLayoutError(f"{field} must be an object")
    x = _integer(raw.get("x"), f"{field}.x", minimum=0, maximum=width - 1)
    y = _integer(raw.get("y"), f"{field}.y", minimum=0, maximum=height - 1)
    return x, y


def _seat_suit_points(
    raw: Any, field: str, *, width: int, height: int
) -> dict[str, dict[str, tuple[int, int]]]:
    if not isinstance(raw, Mapping) or set(raw) != set(SEATS):
        raise BridgitRankLayoutError(f"{field} must cover N,E,S,W")
    result: dict[str, dict[str, tuple[int, int]]] = {}
    for seat in SEATS:
        seat_raw = raw.get(seat)
        if not isinstance(seat_raw, Mapping) or set(seat_raw) != set(SUITS):
            raise BridgitRankLayoutError(f"{field}.{seat} must cover H,C,D,S")
        result[seat] = {
            suit: _point(
                seat_raw[suit], f"{field}.{seat}.{suit}", width=width, height=height
            )
            for suit in SUITS
        }
    return result


@dataclass(frozen=True)
class BridgitRankLayoutProfile:
    profile_id: str
    reference_frame_sha256: str
    verification_sha256: str
    width: int
    height: int
    template_slots: tuple[tuple[str, int, int], ...]
    anchors: dict[str, dict[str, tuple[int, int]]]
    horizontal_search: dict[str, tuple[int, int, int]]
    vertical_search: dict[str, tuple[int, int, int]]
    interface_anchor: dict[str, Any] | None
    glyph_width: int
    glyph_height: int
    local_registration_px: int
    binary_threshold: int
    min_template_score: float
    min_peak_score: float
    min_peak_prominence: float
    min_rank_ink_fraction: float
    min_assignment_margin: float
    min_independent_frames: int
    profile_sha256: str


def parse_profile(raw: Mapping[str, Any]) -> BridgitRankLayoutProfile:
    if not isinstance(raw, Mapping) or raw.get("schema") != PROFILE_SCHEMA:
        raise BridgitRankLayoutError("unsupported Bridgit rank-layout profile schema")
    if raw.get("human_verified") is not True:
        raise BridgitRankLayoutError("profile must be human verified")
    profile_id = str(raw.get("profile_id") or "")
    if not _PROFILE_ID.fullmatch(profile_id):
        raise BridgitRankLayoutError("invalid profile_id")
    reference_sha = _required_sha(
        raw.get("reference_frame_sha256"), "reference_frame_sha256"
    )

    verification = raw.get("verification")
    if not isinstance(verification, Mapping):
        raise BridgitRankLayoutError("human verification evidence is required")
    if verification.get("method") != "HUMAN_LABEL_REVIEW":
        raise BridgitRankLayoutError("unsupported verification method")
    if (
        _required_sha(
            verification.get("reference_frame_sha256"), "verification reference"
        )
        != reference_sha
    ):
        raise BridgitRankLayoutError("verification reference does not match profile")
    reviewer = str(verification.get("reviewer_id") or "").strip()
    verified_at = str(verification.get("verified_at") or "").strip()
    if (
        not reviewer
        or len(reviewer) > 128
        or not verified_at.endswith("Z")
        or len(verified_at) != 20
    ):
        raise BridgitRankLayoutError("incomplete human verification evidence")
    try:
        datetime.fromisoformat(verified_at)
    except ValueError as exc:
        raise BridgitRankLayoutError("invalid human verification timestamp") from exc
    verification_record = {
        "method": "HUMAN_LABEL_REVIEW",
        "reviewer_id": reviewer,
        "verified_at": verified_at,
        "reference_frame_sha256": reference_sha,
    }
    verification_sha = canonical_hash(verification_record)

    frame_size = raw.get("frame_size")
    if not isinstance(frame_size, Mapping):
        raise BridgitRankLayoutError("frame_size must be an object")
    width = _integer(
        frame_size.get("width"), "frame_size.width", minimum=320, maximum=8192
    )
    height = _integer(
        frame_size.get("height"), "frame_size.height", minimum=240, maximum=8192
    )
    _validate_decoded_budget(width, height, observation_count=0)

    ordering = raw.get("ordering")
    if not isinstance(ordering, Mapping):
        raise BridgitRankLayoutError("ordering must be an object")
    ordering_suits = ordering.get("suits")
    if (
        not isinstance(ordering_suits, Sequence)
        or isinstance(ordering_suits, (str, bytes))
        or tuple(ordering_suits) != SUITS
    ):
        raise BridgitRankLayoutError("verified suit order must be H,C,D,S")
    ordering_ranks = ordering.get("ranks")
    if (
        not isinstance(ordering_ranks, Sequence)
        or isinstance(ordering_ranks, (str, bytes))
        or tuple(ordering_ranks) != RANKS
    ):
        raise BridgitRankLayoutError("verified rank order must be A through 2")

    slots_raw = raw.get("template_slots")
    if not isinstance(slots_raw, Sequence) or isinstance(slots_raw, (str, bytes)):
        raise BridgitRankLayoutError("template_slots must be an array")
    slots: list[tuple[str, int, int]] = []
    rank_support = {rank: 0 for rank in RANKS}
    for index, slot in enumerate(slots_raw):
        if not isinstance(slot, Mapping):
            raise BridgitRankLayoutError(f"template_slots[{index}] must be an object")
        card = str(slot.get("card") or "").upper().replace("10", "T")
        if card not in CARDS:
            raise BridgitRankLayoutError(f"invalid template_slots[{index}].card")
        x = _integer(
            slot.get("x"), f"template_slots[{index}].x", minimum=0, maximum=width - 1
        )
        y = _integer(
            slot.get("y"), f"template_slots[{index}].y", minimum=0, maximum=height - 1
        )
        slots.append((card, x, y))
        rank_support[card[0]] += 1
    slot_cards = [card for card, _, _ in slots]
    slot_points = [(x, y) for _, x, y in slots]
    if len(slots) != len(CARDS) or set(slot_cards) != CARDS:
        raise BridgitRankLayoutError(
            "template_slots must contain every card exactly once"
        )
    if len(set(slot_points)) != len(slot_points):
        raise BridgitRankLayoutError(
            "template_slots contains duplicate pixel locations"
        )
    if any(count != len(SUITS) for count in rank_support.values()):
        raise BridgitRankLayoutError(
            "every rank requires four reviewed template samples"
        )

    geometry = raw.get("geometry")
    if not isinstance(geometry, Mapping):
        raise BridgitRankLayoutError("geometry must be an object")
    anchors = _seat_suit_points(
        geometry.get("anchors"), "geometry.anchors", width=width, height=height
    )
    horizontal_raw = geometry.get("horizontal_search")
    if not isinstance(horizontal_raw, Mapping) or set(horizontal_raw) != {"N", "S"}:
        raise BridgitRankLayoutError("horizontal_search must cover N,S")
    horizontal_search: dict[str, tuple[int, int, int]] = {}
    for seat in ("N", "S"):
        item = horizontal_raw[seat]
        if not isinstance(item, Mapping):
            raise BridgitRankLayoutError(f"horizontal_search.{seat} must be an object")
        x_min = _integer(
            item.get("x_min"),
            f"horizontal_search.{seat}.x_min",
            minimum=0,
            maximum=width - 2,
        )
        x_max = _integer(
            item.get("x_max"),
            f"horizontal_search.{seat}.x_max",
            minimum=x_min + 1,
            maximum=width,
        )
        y = _integer(
            item.get("y"), f"horizontal_search.{seat}.y", minimum=0, maximum=height - 1
        )
        horizontal_search[seat] = (x_min, x_max, y)
    vertical_raw = geometry.get("vertical_search")
    if not isinstance(vertical_raw, Mapping) or set(vertical_raw) != {"W", "E"}:
        raise BridgitRankLayoutError("vertical_search must cover W,E")
    vertical_search: dict[str, tuple[int, int, int]] = {}
    for seat in ("W", "E"):
        item = vertical_raw[seat]
        if not isinstance(item, Mapping):
            raise BridgitRankLayoutError(f"vertical_search.{seat} must be an object")
        x_min = _integer(
            item.get("x_min"),
            f"vertical_search.{seat}.x_min",
            minimum=0,
            maximum=width - 2,
        )
        x_max = _integer(
            item.get("x_max"),
            f"vertical_search.{seat}.x_max",
            minimum=x_min + 1,
            maximum=width,
        )
        edge_x = _integer(
            item.get("edge_x"),
            f"vertical_search.{seat}.edge_x",
            minimum=x_min,
            maximum=x_max - 1,
        )
        if x_max - x_min > MAX_VERTICAL_SEARCH_SPAN:
            raise BridgitRankLayoutError(
                f"vertical_search.{seat} span exceeds scoring budget"
            )
        vertical_search[seat] = (x_min, x_max, edge_x)

    interface_anchor_raw = geometry.get("interface_anchor")
    interface_anchor = None
    if interface_anchor_raw is not None:
        try:
            interface_anchor = validate_anchor_spec(interface_anchor_raw)
            validate_anchor_job_budget((width, height), (), interface_anchor)
        except AnchorRegistrationError as exc:
            raise BridgitRankLayoutError(f"invalid interface anchor: {exc}") from exc

    gates = raw.get("gates")
    if not isinstance(gates, Mapping):
        raise BridgitRankLayoutError("gates must be an object")
    glyph_width = _integer(
        gates.get("glyph_width"), "gates.glyph_width", minimum=8, maximum=64
    )
    glyph_height = _integer(
        gates.get("glyph_height"), "gates.glyph_height", minimum=8, maximum=64
    )
    registration = _integer(
        gates.get("local_registration_px"),
        "gates.local_registration_px",
        minimum=0,
        maximum=4,
    )
    binary_threshold = _integer(
        gates.get("binary_threshold"), "gates.binary_threshold", minimum=1, maximum=254
    )
    min_template_score = _number(
        gates.get("min_template_score"),
        "gates.min_template_score",
        minimum=0.000001,
        maximum=1,
    )
    min_peak_score = _number(
        gates.get("min_peak_score"),
        "gates.min_peak_score",
        minimum=0.000001,
        maximum=1,
    )
    min_peak_prominence = _number(
        gates.get("min_peak_prominence"),
        "gates.min_peak_prominence",
        minimum=0.000001,
        maximum=1,
    )
    min_rank_ink = _number(
        gates.get("min_rank_ink_fraction"),
        "gates.min_rank_ink_fraction",
        minimum=0.000001,
        maximum=1,
    )
    min_margin = _number(
        gates.get("min_assignment_margin"),
        "gates.min_assignment_margin",
        minimum=0.000001,
        maximum=5,
    )
    min_independent_frames = _integer(
        gates.get("min_independent_frames"),
        "gates.min_independent_frames",
        minimum=2,
        maximum=MAX_FRAMES,
    )

    for index, (_, x, y) in enumerate(slots):
        if x + glyph_width > width or y + glyph_height > height:
            raise BridgitRankLayoutError(
                f"template_slots[{index}] glyph crop leaves reference frame"
            )
    template_regions = [(x, y, x + glyph_width, y + glyph_height) for _, x, y in slots]
    if any(
        min(first[2], second[2]) > max(first[0], second[0])
        and min(first[3], second[3]) > max(first[1], second[1])
        for index, first in enumerate(template_regions)
        for second in template_regions[index + 1 :]
    ):
        raise BridgitRankLayoutError("template_slots glyph regions overlap")
    for seat in SEATS:
        for suit in SUITS:
            x, y = anchors[seat][suit]
            if (
                x - registration < 0
                or x + registration + glyph_width > width
                or y + glyph_height > height
            ):
                raise BridgitRankLayoutError(
                    f"geometry.anchors.{seat}.{suit} crop leaves reference frame"
                )
    for seat, (_, _, y) in horizontal_search.items():
        if y + 34 > height:
            raise BridgitRankLayoutError(
                f"horizontal_search.{seat} crop leaves reference frame"
            )
    for seat, (x_min, x_max, _) in vertical_search.items():
        if x_min - registration < 0 or x_max - 1 + registration + glyph_width > width:
            raise BridgitRankLayoutError(
                f"vertical_search.{seat} glyph scan leaves reference frame"
            )

    canonical_profile = dict(raw)
    claimed_profile_sha = _required_sha(raw.get("profile_sha256"), "profile_sha256")
    canonical_profile.pop("profile_sha256", None)
    calculated_profile_sha = canonical_hash(canonical_profile)
    if claimed_profile_sha != calculated_profile_sha:
        raise BridgitRankLayoutError("profile hash mismatch")

    return BridgitRankLayoutProfile(
        profile_id=profile_id,
        reference_frame_sha256=reference_sha,
        verification_sha256=verification_sha,
        width=width,
        height=height,
        template_slots=tuple(slots),
        anchors=anchors,
        horizontal_search=horizontal_search,
        vertical_search=vertical_search,
        interface_anchor=interface_anchor,
        glyph_width=glyph_width,
        glyph_height=glyph_height,
        local_registration_px=registration,
        binary_threshold=binary_threshold,
        min_template_score=min_template_score,
        min_peak_score=min_peak_score,
        min_peak_prominence=min_peak_prominence,
        min_rank_ink_fraction=min_rank_ink,
        min_assignment_margin=min_margin,
        min_independent_frames=min_independent_frames,
        profile_sha256=claimed_profile_sha,
    )


def _json_object(payload: bytes, kind: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BridgitRankLayoutError(f"{kind} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object)
    except BridgitRankLayoutError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BridgitRankLayoutError(f"{kind} is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise BridgitRankLayoutError(f"{kind} must be an object")
    return raw


def load_profile(path: Path) -> BridgitRankLayoutProfile:
    payload = _read_bounded_bytes(path, MAX_PROFILE_BYTES, "profile")
    return parse_profile(_json_object(payload, "profile"))


def load_job(path: Path) -> dict[str, Any]:
    payload = _read_bounded_bytes(path, MAX_JOB_BYTES, "job")
    return _json_object(payload, "job")


def ordered_assignments(
    matrix: Sequence[Sequence[float]],
    seat_for_slot: Sequence[str],
    lengths: Mapping[str, int],
) -> list[tuple[float, tuple[str, ...]]]:
    """Return the two best descending-rank assignments for one suit."""
    expected_slots = sum(int(lengths.get(seat, 0)) for seat in SEATS)
    if (
        expected_slots != len(RANKS)
        or len(matrix) != expected_slots
        or len(seat_for_slot) != expected_slots
    ):
        raise BridgitRankLayoutError(
            "assignment must contain exactly thirteen visible suit slots"
        )
    if tuple(seat for seat in SEATS for _ in range(int(lengths.get(seat, 0)))) != tuple(
        seat_for_slot
    ):
        raise BridgitRankLayoutError(
            "assignment slots are not grouped in N,E,S,W order"
        )
    rows = []
    for row in matrix:
        if len(row) != len(RANKS):
            raise BridgitRankLayoutError(
                "assignment matrix must have thirteen rank columns"
            )
        converted = tuple(float(value) for value in row)
        if not all(math.isfinite(value) for value in converted):
            raise BridgitRankLayoutError(
                "assignment matrix contains a non-finite score"
            )
        rows.append(converted)

    offsets: dict[str, int] = {}
    cursor = 0
    for seat in SEATS:
        offsets[seat] = cursor
        cursor += int(lengths.get(seat, 0))
    states: dict[tuple[int, int, int, int], list[tuple[float, tuple[str, ...]]]] = {
        (0, 0, 0, 0): [(0.0, ())]
    }
    for rank_index in range(len(RANKS)):
        next_states: dict[
            tuple[int, int, int, int], list[tuple[float, tuple[str, ...]]]
        ] = {}
        for state, candidates in states.items():
            for score, path in candidates:
                for seat_index, seat in enumerate(SEATS):
                    seat_length = int(lengths.get(seat, 0))
                    if state[seat_index] >= seat_length:
                        continue
                    row_index = offsets[seat] + state[seat_index]
                    new_state = list(state)
                    new_state[seat_index] += 1
                    key = tuple(new_state)
                    next_states.setdefault(key, []).append(
                        (score + rows[row_index][rank_index], path + (seat,))
                    )
        states = {
            key: sorted(values, key=lambda item: (-item[0], item[1]))[:2]
            for key, values in next_states.items()
        }
    final = tuple(int(lengths.get(seat, 0)) for seat in SEATS)
    if final not in states:
        raise BridgitRankLayoutError("no complete ordered assignment")
    return states[final]


def find_chain_peaks(
    values: Sequence[float],
    *,
    origin: int,
    edge: int,
    direction: int,
    min_height: float,
    min_prominence: float,
    min_gap: int = 17,
    max_gap: int = 31,
) -> list[int]:
    """Find a contiguous edge-anchored glyph chain without SciPy."""
    if direction not in {-1, 1} or len(values) == 0:
        raise BridgitRankLayoutError("invalid peak-chain input")
    candidates: list[tuple[int, float]] = []
    numeric = [float(value) for value in values]
    for index in range(1, len(numeric) - 1):
        center = numeric[index]
        if (
            center < min_height
            or center < numeric[index - 1]
            or center < numeric[index + 1]
        ):
            continue
        local_floor = max(
            min(numeric[max(0, index - 5) : index] or [center]),
            min(numeric[index + 1 : index + 6] or [center]),
        )
        if center - local_floor >= min_prominence:
            candidates.append((origin + index, center))
    edge_index = edge - origin
    if 0 <= edge_index < len(numeric) and numeric[edge_index] >= min_height:
        candidates.append((edge, numeric[edge_index]))

    merged: list[tuple[int, float]] = []
    for x, score in sorted(candidates):
        if merged and x - merged[-1][0] <= 6:
            if score > merged[-1][1]:
                merged[-1] = (x, score)
        else:
            merged.append((x, score))
    if not merged:
        return []
    current = min(merged, key=lambda item: (abs(item[0] - edge), -item[1]))
    if abs(current[0] - edge) > 5:
        return []
    chain = [current[0]]
    while True:
        options = [
            item
            for item in merged
            if min_gap <= direction * (item[0] - current[0]) <= max_gap
        ]
        if not options:
            break
        current = max(
            options,
            key=lambda item: (item[1], -abs(direction * (item[0] - chain[-1]) - 25)),
        )
        chain.append(current[0])
    return chain


def _validate_temporal_identities(
    reference_byte_sha256: str,
    reference_pixel_sha256: str,
    frame_byte_sha256s: Sequence[str],
    frame_pixel_sha256s: Sequence[str],
) -> None:
    if len(frame_byte_sha256s) != len(frame_pixel_sha256s):
        raise BridgitRankLayoutError("frame identity channels are incomplete")
    if len(set(frame_byte_sha256s)) != len(frame_byte_sha256s):
        raise BridgitRankLayoutError(
            "duplicate frame bytes do not provide independent evidence"
        )
    if len(set(frame_pixel_sha256s)) != len(frame_pixel_sha256s):
        raise BridgitRankLayoutError(
            "duplicate decoded frame pixels do not provide independent evidence"
        )
    if reference_byte_sha256 in frame_byte_sha256s:
        raise BridgitRankLayoutError(
            "reference template frame cannot count as an observation"
        )
    if reference_pixel_sha256 in frame_pixel_sha256s:
        raise BridgitRankLayoutError(
            "reference template pixels cannot count as an observation"
        )


def _frame_assignment_issues(
    frame_hashes: Sequence[str],
    fused_assignments: Mapping[str, Sequence[str]],
    frame_assignments: Sequence[Mapping[str, Sequence[str]]],
    frame_minimum_scores: Sequence[float],
    frame_minimum_margins: Sequence[float],
    frame_minimum_ink: Sequence[float],
    profile: BridgitRankLayoutProfile,
) -> list[dict[str, Any]]:
    channels = (
        frame_assignments,
        frame_minimum_scores,
        frame_minimum_margins,
        frame_minimum_ink,
    )
    if any(len(channel) != len(frame_hashes) for channel in channels):
        raise BridgitRankLayoutError("per-frame assignment evidence is incomplete")
    expected = {suit: tuple(fused_assignments[suit]) for suit in SUITS}
    issues: list[dict[str, Any]] = []
    for index, frame_hash in enumerate(frame_hashes):
        observed = {
            suit: tuple(frame_assignments[index].get(suit, ())) for suit in SUITS
        }
        reasons = []
        if observed != expected:
            reasons.append("deal_assignment_disagrees")
        if frame_minimum_scores[index] < profile.min_template_score:
            reasons.append("assigned_rank_score_below_threshold")
        if frame_minimum_margins[index] < profile.min_assignment_margin:
            reasons.append("assignment_margin_below_threshold")
        if frame_minimum_ink[index] < profile.min_rank_ink_fraction:
            reasons.append("rank_ink_below_threshold")
        if reasons:
            issues.append(
                {
                    "frame_sha256": frame_hash,
                    "reasons": reasons,
                    "minimum_assigned_score": round(frame_minimum_scores[index], 6),
                    "minimum_assignment_margin": round(frame_minimum_margins[index], 6),
                    "minimum_rank_ink_fraction": round(frame_minimum_ink[index], 6),
                }
            )
    return issues


def _temporal_support_gates(
    *, observed_frames: int, required_frames: int, minimum_ink_support: int
) -> tuple[bool, bool]:
    if (
        observed_frames < 1
        or required_frames < 2
        or minimum_ink_support < 0
        or minimum_ink_support > observed_frames
    ):
        raise BridgitRankLayoutError("invalid temporal support evidence")
    every_observed_frame_has_ink = minimum_ink_support == observed_frames
    enough_frames_for_consensus = observed_frames >= required_frames
    return every_observed_frame_has_ink, enough_frames_for_consensus


def _validate_decoded_budget(
    width: int, height: int, *, observation_count: int
) -> None:
    decoded_frame_bytes = width * height * 3
    if decoded_frame_bytes > MAX_DECODED_FRAME_BYTES:
        raise BridgitRankLayoutError("decoded frame exceeds raster memory budget")
    total_decoded_bytes = decoded_frame_bytes * (observation_count + 1)
    if total_decoded_bytes > MAX_DECODED_JOB_BYTES:
        raise BridgitRankLayoutError("decoded frames exceed job memory budget")


def _validate_decode_peak_budget(
    *,
    retained_decoded_bytes: int,
    decoded_frame_bytes: int,
    encoded_payload_bytes: int,
) -> None:
    # Conservatively reserve one decoded-frame-sized workspace for imdecode in
    # addition to its output and the still-live compressed payload.
    peak_bytes = (
        retained_decoded_bytes
        + encoded_payload_bytes
        + decoded_frame_bytes
        + decoded_frame_bytes
    )
    if peak_bytes > MAX_DECODED_JOB_BYTES:
        raise BridgitRankLayoutError("frame decode exceeds job memory budget")


def _validate_registration_retention_budget(
    profile: BridgitRankLayoutProfile,
    *,
    source_decoded_bytes: int,
    observation_count: int,
    matcher_scratch_bytes: int = 0,
) -> None:
    registered_bytes = profile.width * profile.height * 3 * observation_count
    if (
        source_decoded_bytes + registered_bytes + matcher_scratch_bytes
        > MAX_DECODED_JOB_BYTES
    ):
        raise BridgitRankLayoutError(
            "source and registered frames exceed job memory budget"
        )


def _estimate_recognition_workspace_bytes(profile: BridgitRankLayoutProfile) -> int:
    variants_per_rank = len(SUITS) * (2 * profile.local_registration_px + 1) ** 2
    glyph_pixels = profile.glyph_width * profile.glyph_height
    template_bank_bytes = len(RANKS) * variants_per_rank * glyph_pixels * 4
    one_rank_build_bytes = variants_per_rank * glyph_pixels * 5
    return (
        template_bank_bytes
        + one_rank_build_bytes
        + MAX_RECOGNITION_MISC_WORKSPACE_BYTES
    )


def _validate_recognition_memory_budget(
    profile: BridgitRankLayoutProfile, *, observation_count: int
) -> None:
    retained_frame_bytes = profile.width * profile.height * 3 * (observation_count + 1)
    if (
        retained_frame_bytes + _estimate_recognition_workspace_bytes(profile)
        > MAX_DECODED_JOB_BYTES
    ):
        raise BridgitRankLayoutError("recognition workspace exceeds job memory budget")


def _validate_input_raster_budget(
    frame_paths: Sequence[Path],
) -> list[tuple[int, int]]:
    total = 0
    dimensions = []
    for index, path in enumerate(frame_paths):
        payload = _read_bounded_bytes(Path(path), MAX_FRAME_BYTES, f"frame[{index}]")
        width, height = _encoded_image_dimensions(payload)
        dimensions.append((width, height))
        decoded = width * height * 3
        if decoded > MAX_DECODED_FRAME_BYTES:
            raise BridgitRankLayoutError("decoded frame exceeds raster memory budget")
        total += decoded
        if total > MAX_DECODED_JOB_BYTES:
            raise BridgitRankLayoutError("decoded frames exceed job memory budget")
    return dimensions


def _validate_scoring_budget(
    profile: BridgitRankLayoutProfile, *, observation_count: int
) -> None:
    """Reject a job whose conservative recognition-work bound is unsafe."""
    if observation_count < 1 or observation_count > MAX_FRAMES:
        raise BridgitRankLayoutError("frame count outside allowed range")

    side_span = sum(
        x_max - x_min for x_min, x_max, _ in profile.vertical_search.values()
    )
    registration_width = 2 * profile.local_registration_px + 1

    # Side calibration tries 29 step values over at most 26 side-hand cards,
    # then 25 west/east step pairs over 13 slots in each of four suits.  The
    # final fused and per-frame assignments each score all 52 visible cards.
    calibration_locations = 29 * 26 + 25 * len(SUITS) * len(RANKS)
    assignment_locations = 2 * len(CARDS)
    scoring_calls = (
        observation_count
        * len(RANKS)
        * (
            len(SUITS) * side_span
            + (calibration_locations + assignment_locations) * registration_width
        )
    )
    variants_per_rank = len(SUITS) * registration_width**2
    dot_products = (
        scoring_calls * variants_per_rank * profile.glyph_width * profile.glyph_height
    )
    if (
        scoring_calls > MAX_TEMPLATE_SCORING_CALLS
        or dot_products > MAX_TEMPLATE_SCORING_DOT_PRODUCTS
    ):
        raise BridgitRankLayoutError("template scoring-operation budget exceeded")


def _encoded_image_dimensions(payload: bytes) -> tuple[int, int]:
    png_signature = b"\x89PNG\r\n\x1a\n"
    if payload.startswith(png_signature):
        if (
            len(payload) < 24
            or payload[8:12] != b"\x00\x00\x00\r"
            or payload[12:16] != b"IHDR"
        ):
            raise BridgitRankLayoutError("malformed PNG frame header")
        width = int.from_bytes(payload[16:20], "big")
        height = int.from_bytes(payload[20:24], "big")
        if width <= 0 or height <= 0:
            raise BridgitRankLayoutError("invalid PNG frame dimensions")
        return width, height

    if payload.startswith(b"\xff\xd8"):
        start_of_frame = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        standalone = {0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)}
        cursor = 2
        while cursor < len(payload):
            if payload[cursor] != 0xFF:
                raise BridgitRankLayoutError("malformed JPEG frame header")
            while cursor < len(payload) and payload[cursor] == 0xFF:
                cursor += 1
            if cursor >= len(payload):
                break
            marker = payload[cursor]
            cursor += 1
            if marker in standalone:
                continue
            if cursor + 2 > len(payload):
                break
            segment_length = int.from_bytes(payload[cursor : cursor + 2], "big")
            segment_end = cursor + segment_length
            if segment_length < 2 or segment_end > len(payload):
                raise BridgitRankLayoutError("malformed JPEG frame header")
            if marker in start_of_frame:
                if segment_length < 7:
                    raise BridgitRankLayoutError("malformed JPEG size segment")
                height = int.from_bytes(payload[cursor + 3 : cursor + 5], "big")
                width = int.from_bytes(payload[cursor + 5 : cursor + 7], "big")
                if width <= 0 or height <= 0:
                    raise BridgitRankLayoutError("invalid JPEG frame dimensions")
                return width, height
            if marker == 0xDA:
                break
            cursor = segment_end
        raise BridgitRankLayoutError("JPEG frame has no supported size segment")

    raise BridgitRankLayoutError("frame encoding must be JPEG or PNG")


def _read_frame(
    path: Path,
    profile: BridgitRankLayoutProfile,
    *,
    registration_reference: Any | None = None,
    defer_registration: bool = False,
    decoded_job_bytes_so_far: int = 0,
):
    remaining_payload_headroom = MAX_DECODED_JOB_BYTES - decoded_job_bytes_so_far
    if remaining_payload_headroom <= 1:
        raise BridgitRankLayoutError("frame payload exceeds job memory budget")
    payload_limit = min(MAX_FRAME_BYTES, remaining_payload_headroom - 1)
    payload = _read_bounded_bytes(path, payload_limit, "frame")
    encoded_width, encoded_height = _encoded_image_dimensions(payload)
    if (registration_reference is None or profile.interface_anchor is None) and (
        encoded_width,
        encoded_height,
    ) != (profile.width, profile.height):
        raise BridgitRankLayoutError(
            "encoded frame dimensions do not match the verified profile"
        )
    _validate_decoded_budget(encoded_width, encoded_height, observation_count=0)
    decoded_frame_bytes = encoded_width * encoded_height * 3
    _validate_decode_peak_budget(
        retained_decoded_bytes=decoded_job_bytes_so_far,
        decoded_frame_bytes=decoded_frame_bytes,
        encoded_payload_bytes=len(payload),
    )
    cv2, np = _pixel_runtime()
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or tuple(image.shape[:2]) != (encoded_height, encoded_width):
        raise BridgitRankLayoutError(
            "decoded frame dimensions do not match encoded dimensions"
        )
    if (
        registration_reference is not None
        and profile.interface_anchor is not None
        and not defer_registration
    ):
        try:
            image, registration = register_from_upper_right_anchor(
                registration_reference, image, profile.interface_anchor
            )
        except AnchorRegistrationError as exc:
            raise BridgitRankLayoutError(
                f"interface registration failed: {exc}"
            ) from exc
    elif not defer_registration:
        registration = {
            "mode": "EXACT_PROFILE_DIMENSIONS",
            "input_size": {"width": encoded_width, "height": encoded_height},
            "registered_size": {"width": profile.width, "height": profile.height},
            "anchor_region": None,
            "game_window": {
                "coordinate_space": "NORMALIZED_INPUT_FRAME",
                "x": 0.0,
                "y": 0.0,
                "width": 1.0,
                "height": 1.0,
            },
        }
    else:
        registration = None
    return (
        image,
        hashlib.sha256(payload).hexdigest(),
        hashlib.sha256(image).hexdigest(),
        registration,
    )


def _glyph(image: Any, x: int, y: int, profile: BridgitRankLayoutProfile):
    cv2, _ = _pixel_runtime()
    x0 = int(x)
    y0 = int(y)
    if (
        x0 < 0
        or y0 < 0
        or x0 + profile.glyph_width > profile.width
        or y0 + profile.glyph_height > profile.height
    ):
        raise BridgitRankLayoutError("glyph crop leaves registered frame")
    crop = image[y0 : y0 + profile.glyph_height, x0 : x0 + profile.glyph_width]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.threshold(gray, profile.binary_threshold, 255, cv2.THRESH_BINARY)[1]


def _shift_without_wrap(sample: Any, dx: int, dy: int):
    _, np = _pixel_runtime()
    shifted = np.full_like(sample, 255)
    src_x0, src_x1 = max(0, -dx), sample.shape[1] - max(0, dx)
    src_y0, src_y1 = max(0, -dy), sample.shape[0] - max(0, dy)
    dst_x0, dst_x1 = max(0, dx), sample.shape[1] - max(0, -dx)
    dst_y0, dst_y1 = max(0, dy), sample.shape[0] - max(0, -dy)
    if src_x0 < src_x1 and src_y0 < src_y1:
        shifted[dst_y0:dst_y1, dst_x0:dst_x1] = sample[src_y0:src_y1, src_x0:src_x1]
    return shifted


def _template_bank(template: Any, profile: BridgitRankLayoutProfile) -> dict[str, Any]:
    _, np = _pixel_runtime()
    bank: dict[str, list[Any]] = {rank: [] for rank in RANKS}
    for card, x, y in profile.template_slots:
        bank[card[0]].append(_glyph(template, x, y, profile))
    result: dict[str, Any] = {}
    radius = profile.local_registration_px
    for rank, samples in bank.items():
        variants = np.stack(
            [
                _shift_without_wrap(sample, dx, dy)
                for sample in samples
                for dy in range(-radius, radius + 1)
                for dx in range(-radius, radius + 1)
            ]
        ).astype(np.float32)
        variants = variants.reshape(len(variants), -1)
        variants -= variants.mean(axis=1, keepdims=True)
        variants /= np.maximum(np.linalg.norm(variants, axis=1, keepdims=True), 1e-6)
        result[rank] = variants
    return result


def _similarity(target: Any, samples: Any) -> float:
    _, np = _pixel_runtime()
    vector = target.astype(np.float32).ravel()
    vector -= vector.mean()
    vector /= max(float(np.linalg.norm(vector)), 1e-6)
    return float((samples @ vector).max())


def _slot_score_components(
    frames: Sequence[Any],
    bank: Mapping[str, Any],
    xy: tuple[int, int],
    profile: BridgitRankLayoutProfile,
) -> dict[str, Any]:
    _, np = _pixel_runtime()
    x, y = xy
    radius = profile.local_registration_px
    per_frame_assignment = []
    per_frame_raw = []
    per_frame_ink = []
    per_frame_origins = []
    for frame in frames:
        origins = [
            (
                x + dx,
                _glyph(frame, x + dx, y, profile),
                _rank_hole_count(frame, (x + dx, y), profile),
                _rank_ink_fraction(frame, (x + dx, y), profile),
            )
            for dx in range(-radius, radius + 1)
        ]
        frame_assignment = []
        frame_raw = []
        frame_ink = []
        frame_origins = []
        for rank in RANKS:
            origin_x, _, hole_count, ink_fraction, raw_score = max(
                (
                    (
                        origin_x,
                        glyph,
                        hole_count,
                        ink_fraction,
                        _similarity(glyph, bank[rank]),
                    )
                    for origin_x, glyph, hole_count, ink_fraction in origins
                ),
                key=lambda item: (item[4], -abs(item[0] - x), -item[0]),
            )
            assignment_score = raw_score
            if rank == "8":
                assignment_score += 0.30 if hole_count >= 2 else -0.30
            elif rank == "6" and hole_count < 1:
                assignment_score -= 0.35
            frame_assignment.append(assignment_score)
            frame_raw.append(raw_score)
            frame_ink.append(ink_fraction)
            frame_origins.append(origin_x)
        per_frame_assignment.append(frame_assignment)
        per_frame_raw.append(frame_raw)
        per_frame_ink.append(frame_ink)
        per_frame_origins.append(frame_origins)
    assignment_array = np.asarray(per_frame_assignment)
    raw_array = np.asarray(per_frame_raw)
    ink_array = np.asarray(per_frame_ink)
    return {
        "assignment": np.median(assignment_array, axis=0),
        "raw": np.median(raw_array, axis=0),
        "ink": np.median(ink_array, axis=0),
        "per_frame_assignment": assignment_array,
        "per_frame_raw": raw_array,
        "per_frame_ink": ink_array,
        "per_frame_origins": per_frame_origins,
    }


def _slot_scores(
    frames: Sequence[Any],
    bank: Mapping[str, Any],
    xy: tuple[int, int],
    profile: BridgitRankLayoutProfile,
):
    return _slot_score_components(frames, bank, xy, profile)["assignment"]


def _rank_hole_count(
    frame: Any, xy: tuple[int, int], profile: BridgitRankLayoutProfile
) -> int:
    cv2, _ = _pixel_runtime()
    x, y = xy
    glyph_width = profile.glyph_width
    glyph_height = profile.glyph_height
    gray = cv2.cvtColor(
        frame[y : y + glyph_height, x : x + glyph_width],
        cv2.COLOR_BGR2GRAY,
    )
    binary = (gray <= profile.binary_threshold).astype("uint8") * 255
    _, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    return (
        sum(1 for item in hierarchy[0] if item[3] >= 0) if hierarchy is not None else 0
    )


def _rank_ink_fraction(
    frame: Any, xy: tuple[int, int], profile: BridgitRankLayoutProfile
) -> float:
    cv2, _ = _pixel_runtime()
    x, y = xy
    glyph_width = profile.glyph_width
    glyph_height = profile.glyph_height
    x_margin = max(1, round(glyph_width / 8))
    y_margin = max(1, round(glyph_height / 16))
    gray = cv2.cvtColor(
        frame[y : y + glyph_height, x : x + glyph_width],
        cv2.COLOR_BGR2GRAY,
    )
    return float(
        (
            gray[
                y_margin : glyph_height - y_margin,
                x_margin : glyph_width - x_margin,
            ]
            <= profile.binary_threshold
        ).mean()
    )


def _horizontal_geometry(frame: Any, seat: str, profile: BridgitRankLayoutProfile):
    cv2, np = _pixel_runtime()
    x_min, x_max, y = profile.horizontal_search[seat]
    gray = cv2.cvtColor(frame[y : y + 34, x_min:x_max], cv2.COLOR_BGR2GRAY)
    active = ((gray > profile.binary_threshold).mean(axis=0) > 0.30).astype("uint8")[
        None, :
    ]
    active = cv2.morphologyEx(active, cv2.MORPH_CLOSE, np.ones((1, 4), "uint8")).ravel()
    runs: list[tuple[int, int]] = []
    start = None
    for index, value in enumerate(np.r_[active, 0]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start > 60:
                runs.append((x_min + start, index - start))
            start = None
    if len(runs) != len(SUITS):
        return {suit: 0 for suit in SUITS}, {}
    lengths = {
        suit: max(1, min(13, round((width - 86) / 26) + 1))
        for suit, (_, width) in zip(SUITS, runs)
    }
    anchors = {suit: (start_x - 2, y) for suit, (start_x, _) in zip(SUITS, runs)}
    return lengths, anchors


def _side_lengths(
    frames: Sequence[Any], bank: Mapping[str, Any], profile: BridgitRankLayoutProfile
):
    _, np = _pixel_runtime()
    result = {seat: {} for seat in ("W", "E")}
    for seat in ("W", "E"):
        x_min, x_max, edge = profile.vertical_search[seat]
        direction = 1 if seat == "W" else -1
        for suit in SUITS:
            y = profile.anchors[seat][suit][1]
            per_frame = []
            for frame in frames:
                per_frame.append(
                    [
                        max(
                            _similarity(_glyph(frame, x, y, profile), bank[rank])
                            for rank in RANKS
                        )
                        for x in range(x_min, x_max)
                    ]
                )
            values = np.median(np.asarray(per_frame), axis=0)
            chain = find_chain_peaks(
                values,
                origin=x_min,
                edge=edge,
                direction=direction,
                min_height=profile.min_peak_score,
                min_prominence=profile.min_peak_prominence,
            )
            result[seat][suit] = len(chain)
    return result


def _coords(
    seat: str,
    suit: str,
    count: int,
    anchors: Mapping[str, Mapping[str, tuple[int, int]]],
    side_steps: Mapping[str, Mapping[str, float]],
) -> list[tuple[int, int]]:
    x, y = anchors[seat][suit]
    step = float(side_steps.get(seat, {}).get(suit, 25.0))
    if seat == "E":
        return [(round(x - step * index), y) for index in reversed(range(count))]
    return [(round(x + step * index), y) for index in range(count)]


def _glyph_coords_fit_frame(
    coords: Sequence[tuple[int, int]], profile: BridgitRankLayoutProfile
) -> bool:
    radius = profile.local_registration_px
    return all(
        x - radius >= 0
        and x + radius + profile.glyph_width <= profile.width
        and y >= 0
        and y + profile.glyph_height <= profile.height
        for x, y in coords
    )


def _generated_glyph_regions_are_disjoint(
    lengths: Mapping[str, Mapping[str, int]],
    anchors: Mapping[str, Mapping[str, tuple[int, int]]],
    side_steps: Mapping[str, Mapping[str, float]],
    profile: BridgitRankLayoutProfile,
) -> bool:
    radius = profile.local_registration_px
    regions = [
        (
            x - radius,
            y,
            x + radius + profile.glyph_width,
            y + profile.glyph_height,
        )
        for suit in SUITS
        for seat in SEATS
        for x, y in _coords(seat, suit, lengths[seat][suit], anchors, side_steps)
    ]
    return all(
        min(first[2], second[2]) <= max(first[0], second[0])
        or min(first[3], second[3]) <= max(first[1], second[1])
        for index, first in enumerate(regions)
        for second in regions[index + 1 :]
    )


def _calibrate_side_steps(
    frames: Sequence[Any],
    bank: Mapping[str, Any],
    lengths: Mapping[str, Mapping[str, int]],
    anchors: Mapping[str, Mapping[str, tuple[int, int]]],
    profile: BridgitRankLayoutProfile,
) -> dict[str, dict[str, float]]:
    result = {seat: {} for seat in ("W", "E")}
    score_cache: dict[tuple[int, int], Any] = {}
    candidates = {seat: {} for seat in ("W", "E")}
    for seat in ("W", "E"):
        for suit in SUITS:
            count = lengths[seat][suit]
            if count <= 1:
                candidates[seat][suit] = [25.0]
                continue
            scored = []
            for quarter in range(72, 101):
                step = quarter / 4.0
                trial = {seat: {suit: step}}
                score = 0.0
                coords = _coords(seat, suit, count, anchors, trial)
                if not _glyph_coords_fit_frame(coords, profile):
                    continue
                for xy in coords:
                    score_cache.setdefault(xy, _slot_scores(frames, bank, xy, profile))
                    score += float(max(score_cache[xy]))
                scored.append((score, step))
            candidates[seat][suit] = [
                step for _, step in sorted(scored, reverse=True)[:5]
            ]

    for suit in SUITS:
        if not candidates["W"][suit] or not candidates["E"][suit]:
            raise BridgitRankLayoutError(
                f"side fan {suit} has no in-frame calibration trial"
            )
        options = []
        for west_step in candidates["W"][suit]:
            for east_step in candidates["E"][suit]:
                trial = {"W": {suit: west_step}, "E": {suit: east_step}}
                slots = [
                    (seat, xy)
                    for seat in SEATS
                    for xy in _coords(seat, suit, lengths[seat][suit], anchors, trial)
                ]
                matrix = []
                for _, xy in slots:
                    score_cache.setdefault(xy, _slot_scores(frames, bank, xy, profile))
                    matrix.append(score_cache[xy])
                best = ordered_assignments(
                    matrix,
                    [seat for seat, _ in slots],
                    {seat: lengths[seat][suit] for seat in SEATS},
                )[0][0]
                options.append((best, west_step, east_step))
        _, result["W"][suit], result["E"][suit] = max(options)
    return result


def recognize_frames(
    reference_frame: Path,
    frame_paths: Sequence[Path],
    profile: BridgitRankLayoutProfile,
    *,
    expected_frame_sha256s: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Recognize one stable, fully visible deal as a shadow candidate."""
    if not frame_paths or len(frame_paths) > MAX_FRAMES:
        raise BridgitRankLayoutError("frame count outside allowed range")
    if expected_frame_sha256s is not None:
        if (
            not isinstance(expected_frame_sha256s, Sequence)
            or isinstance(expected_frame_sha256s, (str, bytes))
            or len(expected_frame_sha256s) != len(frame_paths)
        ):
            raise BridgitRankLayoutError("expected frame hashes are incomplete")
        expected_frame_sha256s = [
            _required_sha(value, f"expected_frame_sha256s[{index}]")
            for index, value in enumerate(expected_frame_sha256s)
        ]
    _validate_scoring_budget(profile, observation_count=len(frame_paths))
    _validate_recognition_memory_budget(profile, observation_count=len(frame_paths))
    _validate_decoded_budget(
        profile.width,
        profile.height,
        observation_count=len(frame_paths),
    )
    input_dimensions = _validate_input_raster_budget([reference_frame, *frame_paths])
    if profile.interface_anchor is not None:
        try:
            validate_anchor_job_budget(
                (profile.width, profile.height),
                input_dimensions[1:],
                profile.interface_anchor,
            )
        except AnchorRegistrationError as exc:
            raise BridgitRankLayoutError(
                f"interface registration budget failed: {exc}"
            ) from exc
    reference, reference_hash, reference_pixel_hash, _ = _read_frame(
        reference_frame, profile
    )
    if reference_hash != profile.reference_frame_sha256:
        raise BridgitRankLayoutError("reference frame hash mismatch")
    loaded_frames = []
    decoded_job_bytes = int(reference.shape[0] * reference.shape[1] * 3)
    for index, path in enumerate(frame_paths):
        loaded = _read_frame(
            Path(path),
            profile,
            registration_reference=reference,
            defer_registration=profile.interface_anchor is not None,
            decoded_job_bytes_so_far=decoded_job_bytes,
        )
        if (
            expected_frame_sha256s is not None
            and loaded[1] != expected_frame_sha256s[index]
        ):
            raise BridgitRankLayoutError("observation frame changed before recognition")
        loaded_frames.append(loaded)
        decoded_job_bytes += int(loaded[0].shape[0] * loaded[0].shape[1] * 3)
    del loaded
    if profile.interface_anchor is not None:
        actual_dimensions = [
            (int(image.shape[1]), int(image.shape[0]))
            for image, _, _, _ in loaded_frames
        ]
        matcher_scratch_bytes = estimate_anchor_peak_scratch_bytes(
            (profile.width, profile.height),
            actual_dimensions,
            profile.interface_anchor,
        )
        _validate_registration_retention_budget(
            profile,
            source_decoded_bytes=decoded_job_bytes,
            observation_count=len(loaded_frames),
            matcher_scratch_bytes=matcher_scratch_bytes,
        )
        try:
            validate_anchor_job_budget(
                (profile.width, profile.height),
                actual_dimensions,
                profile.interface_anchor,
            )
        except AnchorRegistrationError as exc:
            raise BridgitRankLayoutError(
                f"interface registration budget failed: {exc}"
            ) from exc
        registered_frames = []
        registered_pixel_hashes = {reference_pixel_hash}
        for image, frame_hash, _, _ in loaded_frames:
            try:
                registered, registration = register_from_upper_right_anchor(
                    reference, image, profile.interface_anchor
                )
            except AnchorRegistrationError as exc:
                raise BridgitRankLayoutError(
                    f"interface registration failed: {exc}"
                ) from exc
            registered_pixel_hash = hashlib.sha256(registered).hexdigest()
            if registered_pixel_hash == reference_pixel_hash:
                raise BridgitRankLayoutError(
                    "observation duplicates reference template pixels"
                )
            if registered_pixel_hash in registered_pixel_hashes:
                raise BridgitRankLayoutError("duplicate decoded frame pixels")
            registered_pixel_hashes.add(registered_pixel_hash)
            registered_frames.append(
                (
                    registered,
                    frame_hash,
                    registered_pixel_hash,
                    registration,
                )
            )
        loaded_frames = registered_frames
        del image
    frames = [image for image, _, _, _ in loaded_frames]
    frame_hashes = [frame_hash for _, frame_hash, _, _ in loaded_frames]
    pixel_hashes = [pixel_hash for _, _, pixel_hash, _ in loaded_frames]
    frame_registrations = [registration for _, _, _, registration in loaded_frames]
    frame_registration_receipts = [
        {"frame_sha256": frame_hash, **registration}
        for frame_hash, registration in zip(frame_hashes, frame_registrations)
    ]
    _validate_temporal_identities(
        reference_hash,
        reference_pixel_hash,
        frame_hashes,
        pixel_hashes,
    )
    input_hashes = {
        "reference_frame_sha256": reference_hash,
        "frame_sha256s": frame_hashes,
    }
    bank = _template_bank(reference, profile)

    anchors = {seat: dict(values) for seat, values in profile.anchors.items()}
    frame_geometries: list[dict[str, dict[str, int]]] = []
    frame_horizontal_anchors: list[dict[str, dict[str, tuple[int, int]]]] = []
    for frame in frames:
        frame_lengths: dict[str, dict[str, int]] = {}
        detected_anchors: dict[str, dict[str, tuple[int, int]]] = {}
        geometry_failure = None
        for seat in ("N", "S"):
            frame_lengths[seat], detected = _horizontal_geometry(frame, seat, profile)
            detected_anchors[seat] = detected
            visible = sum(frame_lengths[seat].values())
            if detected and 0 < visible < 13:
                geometry_failure = f"{seat}_has_fewer_than_13_visible_cards"
                break
            if not detected or visible != 13:
                geometry_failure = f"{seat}_fan_geometry_not_proven"
                break
            horizontal_coords = [
                xy
                for suit in SUITS
                for xy in _coords(
                    seat,
                    suit,
                    frame_lengths[seat][suit],
                    detected_anchors,
                    {},
                )
            ]
            if not _glyph_coords_fit_frame(horizontal_coords, profile):
                geometry_failure = f"{seat}_fan_geometry_out_of_frame"
                break
        if geometry_failure is None:
            side = _side_lengths([frame], bank, profile)
            frame_lengths["W"], frame_lengths["E"] = side["W"], side["E"]
            if any(sum(frame_lengths[seat].values()) != 13 for seat in SEATS):
                geometry_failure = "rank_peak_counts_fail_hand_total"
            elif any(
                sum(frame_lengths[seat][suit] for seat in SEATS) != 13 for suit in SUITS
            ):
                geometry_failure = "rank_peak_counts_fail_suit_total"
        if geometry_failure is not None:
            if geometry_failure.endswith("has_fewer_than_13_visible_cards"):
                status = "PARTIAL_PLAY"
            elif "fan_geometry" in geometry_failure:
                status = "LAYOUT_UNKNOWN"
            else:
                status = "LAYOUT_AMBIGUOUS"
            return _shadow_result(
                status,
                lengths=frame_lengths,
                reason=f"per_frame_geometry_not_proven:{geometry_failure}",
                input_hashes=input_hashes,
                frame_registrations=frame_registration_receipts,
            )
        frame_geometries.append(frame_lengths)
        frame_horizontal_anchors.append(detected_anchors)

    lengths = frame_geometries[0]
    if any(frame_lengths != lengths for frame_lengths in frame_geometries[1:]):
        return _shadow_result(
            "LAYOUT_AMBIGUOUS",
            lengths=lengths,
            reason="per_frame_geometry_disagreement",
            input_hashes=input_hashes,
            frame_registrations=frame_registration_receipts,
        )
    if any(
        frame_anchors != frame_horizontal_anchors[0]
        for frame_anchors in frame_horizontal_anchors[1:]
    ):
        return _shadow_result(
            "LAYOUT_AMBIGUOUS",
            lengths=lengths,
            reason="per_frame_anchor_disagreement",
            input_hashes=input_hashes,
            frame_registrations=frame_registration_receipts,
        )
    for seat in ("N", "S"):
        anchors[seat].update(frame_horizontal_anchors[0][seat])

    side_steps = _calibrate_side_steps(frames, bank, lengths, anchors, profile)
    if not _generated_glyph_regions_are_disjoint(lengths, anchors, side_steps, profile):
        return _shadow_result(
            "LAYOUT_AMBIGUOUS",
            lengths=lengths,
            reason="duplicate_glyph_coordinates",
            input_hashes=input_hashes,
            frame_registrations=frame_registration_receipts,
        )
    hands = {seat: {suit: [] for suit in SUITS} for seat in SEATS}
    uncertainties = []
    evidence_scores: list[float] = []
    ink_scores: list[float] = []
    ink_support_counts: list[int] = []
    fused_assignments: dict[str, tuple[str, ...]] = {}
    frame_assignments: list[dict[str, tuple[str, ...]]] = [{} for _ in frame_hashes]
    frame_assigned_scores: list[list[float]] = [[] for _ in frame_hashes]
    frame_assignment_margins: list[list[float]] = [[] for _ in frame_hashes]
    frame_ink_scores: list[list[float]] = [[] for _ in frame_hashes]
    raw_visual_observations: list[dict[str, Any]] = []
    for suit in SUITS:
        slots = [
            (seat, xy)
            for seat in SEATS
            for xy in _coords(seat, suit, lengths[seat][suit], anchors, side_steps)
        ]
        score_components = [
            _slot_score_components(frames, bank, xy, profile) for _, xy in slots
        ]
        matrix = [component["assignment"] for component in score_components]
        raw_matrix = [component["raw"] for component in score_components]
        ink_matrix = [component["ink"] for component in score_components]
        alternatives = ordered_assignments(
            matrix,
            [seat for seat, _ in slots],
            {seat: lengths[seat][suit] for seat in SEATS},
        )
        best_score, best_path = alternatives[0]
        fused_assignments[suit] = best_path
        used = {seat: 0 for seat in SEATS}
        for rank, seat in zip(RANKS, best_path):
            hands[seat][suit].append(rank)
            row = (
                sum(lengths[other][suit] for other in SEATS[: SEATS.index(seat)])
                + used[seat]
            )
            rank_index = RANKS.index(rank)
            evidence_scores.append(float(raw_matrix[row][rank_index]))
            ink_scores.append(float(ink_matrix[row][rank_index]))
            ink_support_counts.append(
                sum(
                    value >= profile.min_rank_ink_fraction
                    for value in score_components[row]["per_frame_ink"][:, rank_index]
                )
            )
            used[seat] += 1
        if len(alternatives) > 1:
            second_score, second_path = alternatives[1]
            loss = best_score - second_score
            changes = [
                {"rank": rank, "best_seat": best, "alternate_seat": alternate}
                for rank, best, alternate in zip(RANKS, best_path, second_path)
                if best != alternate
            ]
            if changes and loss < profile.min_assignment_margin:
                uncertainties.append(
                    {
                        "suit": suit,
                        "score_loss": round(loss, 6),
                        "alternate_assignment": changes,
                    }
                )

        for frame_index, _frame in enumerate(frames):
            single_matrix = [
                component["per_frame_assignment"][frame_index]
                for component in score_components
            ]
            single_raw_matrix = [
                component["per_frame_raw"][frame_index]
                for component in score_components
            ]
            single_ink_matrix = [
                component["per_frame_ink"][frame_index]
                for component in score_components
            ]
            single_alternatives = ordered_assignments(
                single_matrix,
                [seat for seat, _ in slots],
                {seat: lengths[seat][suit] for seat in SEATS},
            )
            single_best_score, single_path = single_alternatives[0]
            frame_assignments[frame_index][suit] = single_path
            single_used = {seat: 0 for seat in SEATS}
            for rank, seat in zip(RANKS, single_path):
                row = (
                    sum(lengths[other][suit] for other in SEATS[: SEATS.index(seat)])
                    + single_used[seat]
                )
                rank_index = RANKS.index(rank)
                assigned_score = float(single_raw_matrix[row][rank_index])
                frame_assigned_scores[frame_index].append(assigned_score)
                assigned_ink = float(single_ink_matrix[row][rank_index])
                frame_ink_scores[frame_index].append(assigned_ink)
                _, (_, y) = slots[row]
                x = score_components[row]["per_frame_origins"][frame_index][rank_index]
                game_window = frame_registrations[frame_index]["game_window"]
                normalized_x = x / profile.width
                normalized_y = y / profile.height
                normalized_width = profile.glyph_width / profile.width
                normalized_height = profile.glyph_height / profile.height
                region_x, region_width = _rounded_normalized_axis(
                    game_window["x"] + normalized_x * game_window["width"],
                    normalized_width * game_window["width"],
                )
                region_y, region_height = _rounded_normalized_axis(
                    game_window["y"] + normalized_y * game_window["height"],
                    normalized_height * game_window["height"],
                )
                raw_visual_observations.append(
                    {
                        "seat": seat,
                        "suit": suit,
                        "rank": rank,
                        "source": "VISUAL",
                        "frame_sha256": frame_hashes[frame_index],
                        "region": {
                            "coordinate_space": "NORMALIZED_FRAME",
                            "x": region_x,
                            "y": region_y,
                            "width": region_width,
                            "height": region_height,
                        },
                        "confidence": round(max(0.0, min(1.0, assigned_score)), 6),
                        "confidence_kind": "TEMPLATE_SIMILARITY_UNCALIBRATED",
                        "recognizer_version": BACKEND_VERSION,
                    }
                )
                single_used[seat] += 1
            if len(single_alternatives) > 1:
                frame_assignment_margins[frame_index].append(
                    float(single_best_score - single_alternatives[1][0])
                )
            else:
                frame_assignment_margins[frame_index].append(1_000_000.0)

    cards = [
        rank + suit for seat in SEATS for suit in SUITS for rank in hands[seat][suit]
    ]
    seat_counts = {
        seat: sum(len(hands[seat][suit]) for suit in SUITS) for seat in SEATS
    }
    frame_minimum_scores = [min(values) for values in frame_assigned_scores]
    frame_minimum_margins = [min(values) for values in frame_assignment_margins]
    frame_minimum_ink = [min(values) for values in frame_ink_scores]
    frame_assignment_issues = _frame_assignment_issues(
        frame_hashes,
        fused_assignments,
        frame_assignments,
        frame_minimum_scores,
        frame_minimum_margins,
        frame_minimum_ink,
        profile,
    )
    per_frame_assignment_receipts = [
        {
            "frame_sha256": frame_hash,
            "decoded_pixel_sha256": pixel_hashes[index],
            "assignment_sha256": canonical_hash(
                {suit: "".join(assignments[suit]) for suit in SUITS}
            ),
            "minimum_assigned_score": round(frame_minimum_scores[index], 6),
            "minimum_assignment_margin": round(frame_minimum_margins[index], 6),
            "minimum_rank_ink_fraction": round(frame_minimum_ink[index], 6),
        }
        for index, (frame_hash, assignments) in enumerate(
            zip(frame_hashes, frame_assignments)
        )
    ]
    evidence = {
        "minimum_assigned_score": round(min(evidence_scores), 6),
        "median_assigned_score": round(
            sorted(evidence_scores)[len(evidence_scores) // 2], 6
        ),
        "minimum_required": profile.min_template_score,
        "minimum_rank_ink_fraction": round(min(ink_scores), 6),
        "minimum_rank_ink_required": profile.min_rank_ink_fraction,
        "independent_frame_sha256s": sorted(frame_hashes),
        "independent_decoded_pixel_sha256s": sorted(pixel_hashes),
        "reference_decoded_pixel_sha256": reference_pixel_hash,
        "independent_frames_required": profile.min_independent_frames,
        "minimum_rank_ink_frame_support": min(ink_support_counts),
        "per_frame_deal_agreement": not frame_assignment_issues,
        "per_frame_assignment_receipts": per_frame_assignment_receipts,
        "reference_frame_sha256": profile.reference_frame_sha256,
        "profile_sha256": profile.profile_sha256,
        "profile_verification_sha256": profile.verification_sha256,
        "recognition_channels_independent": False,
    }
    ink_support_pass, temporal_pass = _temporal_support_gates(
        observed_frames=len(frame_hashes),
        required_frames=profile.min_independent_frames,
        minimum_ink_support=evidence["minimum_rank_ink_frame_support"],
    )
    weak = (
        evidence["minimum_assigned_score"] < profile.min_template_score
        or evidence["minimum_rank_ink_fraction"] < profile.min_rank_ink_fraction
        or not ink_support_pass
    )
    complete = (
        len(cards) == 52 and len(set(cards)) == 52 and set(seat_counts.values()) == {13}
    )
    if (
        complete
        and not weak
        and not uncertainties
        and not frame_assignment_issues
        and temporal_pass
    ):
        status = "SHADOW_FULL_LAYOUT_CANDIDATE"
        reason = None
    elif complete and not weak and not uncertainties and not frame_assignment_issues:
        status = "PENDING_TEMPORAL_CONSENSUS"
        reason = "independent_frame_gate_not_met"
    else:
        status = "AMBIGUOUS"
        reason = (
            "per_frame_deal_agreement_failed"
            if frame_assignment_issues
            else "weak_or_non_independent_evidence"
        )
    return _shadow_result(
        status,
        lengths=lengths,
        reason=reason,
        input_hashes=input_hashes,
        hands=hands,
        side_overlap_step={
            seat: {suit: round(value, 3) for suit, value in by_suit.items()}
            for seat, by_suit in side_steps.items()
        },
        integrity={
            "cards": len(cards),
            "unique": len(set(cards)),
            "seat_counts": seat_counts,
        },
        evidence=evidence,
        uncertainties=uncertainties,
        frame_assignment_issues=frame_assignment_issues,
        frame_registrations=frame_registration_receipts,
        _visual_observations=(
            raw_visual_observations
            if status in {"SHADOW_FULL_LAYOUT_CANDIDATE", "PENDING_TEMPORAL_CONSENSUS"}
            else []
        ),
    )


def _shadow_result(
    status: str, *, lengths: Mapping[str, Any], reason: str | None, **extra: Any
) -> dict[str, Any]:
    return {
        "backend_version": BACKEND_VERSION,
        "status": status,
        "result_scope": "SHADOW_ONLY",
        "provenance_class": "MODEL_CANDIDATE",
        "canonical_promotion_allowed": False,
        "school_canon_write_performed": False,
        "hidden_hand_reconstruction_performed": False,
        "suit_lengths": dict(lengths),
        "reason": reason,
        **extra,
    }


def _rounded_normalized_axis(start: float, size: float) -> tuple[float, float]:
    """Round an interval from its edges without moving its end past the frame."""
    bounded_start = max(0.0, min(1.0, start))
    bounded_end = max(bounded_start, min(1.0, start + size))
    rounded_start = round(bounded_start, 8)
    rounded_end = round(bounded_end, 8)
    return rounded_start, round(rounded_end - rounded_start, 8)


def _recognize_frames_with_opencv_rejection(
    reference_path: Path,
    frame_paths: Sequence[Path],
    profile: BridgitRankLayoutProfile,
    *,
    expected_frame_sha256s: Sequence[str],
) -> dict[str, Any]:
    try:
        return recognize_frames(
            reference_path,
            frame_paths,
            profile,
            expected_frame_sha256s=expected_frame_sha256s,
        )
    except Exception as exc:
        cv2, _ = _pixel_runtime()
        if isinstance(exc, cv2.error):
            raise BridgitRankLayoutError("OpenCV pixel operation failed") from exc
        raise


def _validated_input_root(value: Any) -> Path:
    raw = Path(str(value or ""))
    if not raw.is_absolute():
        raise BridgitRankLayoutError("input_root must be an absolute directory")
    try:
        root = raw.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise BridgitRankLayoutError("input_root is unavailable") from exc
    if not root.is_dir() or root == Path(root.anchor):
        raise BridgitRankLayoutError("input_root must be a bounded directory")
    return root


def _validated_ref(
    raw: Any,
    field: str,
    *,
    max_bytes: int,
    input_root: Path,
    fd_stack: ExitStack,
) -> Path:
    if not isinstance(raw, Mapping):
        raise BridgitRankLayoutError(f"{field} must be an object")
    declared = Path(str(raw.get("path") or ""))
    if not declared.is_absolute():
        raise BridgitRankLayoutError(f"{field} path must be an existing absolute file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(declared, flags)
    except OSError as exc:
        raise BridgitRankLayoutError(f"{field} escapes input_root") from exc
    fd_stack.callback(os.close, descriptor)
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise BridgitRankLayoutError(f"{field} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise BridgitRankLayoutError(f"{field} path must be an existing absolute file")
    pinned_path = Path(f"/proc/self/fd/{descriptor}")
    try:
        pinned_path.resolve(strict=True).relative_to(input_root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise BridgitRankLayoutError(f"{field} escapes input_root") from exc
    if metadata.st_size > max_bytes:
        raise BridgitRankLayoutError(f"{field} exceeds size limit")
    if _bounded_sha256(pinned_path, max_bytes, field) != _required_sha(
        raw.get("sha256"), f"{field}.sha256"
    ):
        raise BridgitRankLayoutError(f"{field} hash mismatch")
    return pinned_path


def _execute_shadow_job_pinned(
    job: Mapping[str, Any], fd_stack: ExitStack
) -> dict[str, Any]:
    if not isinstance(job, Mapping) or job.get("job_type") != JOB_TYPE:
        raise BridgitRankLayoutError("unknown job type")
    if job.get("production_write") is not False:
        raise BridgitRankLayoutError("production write is forbidden")
    if job.get("allow_hidden_information") is not False:
        raise BridgitRankLayoutError("hidden information must be explicitly forbidden")
    pointer_events = job.get("teacher_pointer_events", ())
    if not isinstance(pointer_events, Sequence) or isinstance(
        pointer_events, (str, bytes)
    ):
        raise BridgitRankLayoutError("teacher_pointer_events must be an array")
    if len(pointer_events) > MAX_POINTER_EVENTS:
        raise BridgitRankLayoutError("too many teacher_pointer_events")
    try:
        pointer_events = normalize_teacher_pointer_events(pointer_events)
    except DealEvidenceError as exc:
        raise BridgitRankLayoutError(
            f"invalid teacher pointer evidence: {exc}"
        ) from exc
    input_root = _validated_input_root(job.get("input_root"))
    profile_path = _validated_ref(
        job.get("profile_ref"),
        "profile_ref",
        max_bytes=MAX_PROFILE_BYTES,
        input_root=input_root,
        fd_stack=fd_stack,
    )
    reference_path = _validated_ref(
        job.get("reference_frame_ref"),
        "reference_frame_ref",
        max_bytes=MAX_FRAME_BYTES,
        input_root=input_root,
        fd_stack=fd_stack,
    )
    profile_payload = _read_bounded_bytes(profile_path, MAX_PROFILE_BYTES, "profile")
    profile_file_sha = hashlib.sha256(profile_payload).hexdigest()
    if profile_file_sha != _required_sha(
        job["profile_ref"].get("sha256"), "profile_ref.sha256"
    ):
        raise BridgitRankLayoutError("profile changed during validation")
    profile = parse_profile(_json_object(profile_payload, "profile"))
    declared_reference_sha = _required_sha(
        job["reference_frame_ref"].get("sha256"), "reference_frame_ref.sha256"
    )
    if profile.reference_frame_sha256 != declared_reference_sha:
        raise BridgitRankLayoutError("profile and job reference frame hashes differ")
    if (
        _bounded_sha256(reference_path, MAX_FRAME_BYTES, "reference frame")
        != declared_reference_sha
    ):
        raise BridgitRankLayoutError("reference frame changed during validation")
    if str(job.get("profile_id") or "") != profile.profile_id:
        raise BridgitRankLayoutError("profile_id mismatch")

    frame_refs = job.get("frame_refs")
    if (
        not isinstance(frame_refs, Sequence)
        or isinstance(frame_refs, (str, bytes))
        or not 1 <= len(frame_refs) <= MAX_FRAMES
    ):
        raise BridgitRankLayoutError("frame_refs count outside allowed range")
    frame_paths: list[Path] = []
    timestamps: set[int] = set()
    timestamp_values: list[int] = []
    frame_hashes: list[str] = []
    for index, ref in enumerate(frame_refs):
        if not isinstance(ref, Mapping):
            raise BridgitRankLayoutError(f"frame_refs[{index}] must be an object")
        timestamp = _integer(
            ref.get("timestamp_ms"),
            f"frame_refs[{index}].timestamp_ms",
            minimum=0,
            maximum=10**12,
        )
        if timestamp in timestamps:
            raise BridgitRankLayoutError("duplicate frame timestamp")
        timestamps.add(timestamp)
        timestamp_values.append(timestamp)
        path = _validated_ref(
            ref,
            f"frame_refs[{index}]",
            max_bytes=MAX_FRAME_BYTES,
            input_root=input_root,
            fd_stack=fd_stack,
        )
        frame_paths.append(path)
        declared_frame_sha = _required_sha(
            ref.get("sha256"), f"frame_refs[{index}].sha256"
        )
        if (
            _bounded_sha256(path, MAX_FRAME_BYTES, f"frame_refs[{index}]")
            != declared_frame_sha
        ):
            raise BridgitRankLayoutError(
                f"frame_refs[{index}] changed during validation"
            )
        frame_hashes.append(declared_frame_sha)
    if len(set(frame_hashes)) != len(frame_hashes):
        raise BridgitRankLayoutError(
            "duplicate frame bytes do not provide independent evidence"
        )
    if profile.reference_frame_sha256 in frame_hashes:
        raise BridgitRankLayoutError(
            "reference template frame cannot count as an observation"
        )

    result = _recognize_frames_with_opencv_rejection(
        reference_path,
        frame_paths,
        profile,
        expected_frame_sha256s=frame_hashes,
    )
    expected_result_input_hashes = {
        "reference_frame_sha256": profile.reference_frame_sha256,
        "frame_sha256s": frame_hashes,
    }
    if result.get("input_hashes") != expected_result_input_hashes:
        raise BridgitRankLayoutError("input changed during recognition")
    raw_visual_observations = result.pop("_visual_observations", [])
    if not isinstance(raw_visual_observations, list):
        raise BridgitRankLayoutError("recognizer returned invalid visual observations")
    timestamp_by_frame = {
        frame_hash: timestamp
        for frame_hash, timestamp in zip(frame_hashes, timestamp_values)
    }
    enriched_visual_observations = []
    for observation in raw_visual_observations:
        if not isinstance(observation, Mapping):
            raise BridgitRankLayoutError(
                "recognizer returned invalid visual observation"
            )
        frame_sha = str(observation.get("frame_sha256") or "")
        if frame_sha not in timestamp_by_frame:
            raise BridgitRankLayoutError("visual observation references unknown frame")
        enriched_visual_observations.append(
            {**dict(observation), "timestamp_ms": timestamp_by_frame[frame_sha]}
        )
    try:
        result["deal_evidence_report"] = build_deal_evidence_report(
            enriched_visual_observations,
            pointer_events,
            recognizer_version=BACKEND_VERSION,
            required_visual_frames=profile.min_independent_frames,
            allow_logical_inference=False,
        )
    except ValueError as exc:
        raise BridgitRankLayoutError(f"invalid deal evidence: {exc}") from exc
    receipt: dict[str, Any] = {
        "receipt_type": RECEIPT_TYPE,
        "backend_version": BACKEND_VERSION,
        "job_sha256": canonical_hash(job),
        "profile_sha256": profile.profile_sha256,
        "input_hashes": {
            "profile_file_sha256": profile_file_sha,
            **expected_result_input_hashes,
        },
        "result": result,
        "production_write_performed": False,
        "school_canon_write_performed": False,
    }
    receipt["receipt_sha256"] = canonical_hash(receipt)
    return receipt


def execute_shadow_job(job: Mapping[str, Any]) -> dict[str, Any]:
    with ExitStack() as fd_stack:
        return _execute_shadow_job_pinned(job, fd_stack)


def atomic_write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the opt-in Bridgit rank-layout shadow backend"
    )
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    exit_code = 0
    try:
        receipt = execute_shadow_job(load_job(arguments.job))
        status = str(receipt["result"]["status"])
    except (
        BridgitPixelRuntimeUnavailable,
        BridgitRankLayoutError,
        KeyError,
        MemoryError,
        OSError,
    ) as exc:
        detail = str(exc) or exc.__class__.__name__
        detail = detail.replace("\n", " ").replace("\r", " ")[:MAX_DIAGNOSTIC_DETAIL]
        receipt = {
            "receipt_type": RECEIPT_TYPE,
            "backend_version": BACKEND_VERSION,
            "status": "REJECTED",
            "reason": detail,
            "result_scope": "SHADOW_ONLY",
            "canonical_promotion_allowed": False,
            "production_write_performed": False,
            "school_canon_write_performed": False,
        }
        receipt["receipt_sha256"] = canonical_hash(receipt)
        status = "REJECTED"
        exit_code = 2
    atomic_write_receipt(arguments.output, receipt)
    print(
        json.dumps(
            {"status": status, "receipt_sha256": receipt["receipt_sha256"]},
            sort_keys=True,
        )
    )
    return exit_code


__all__ = [
    "BACKEND_VERSION",
    "JOB_TYPE",
    "PROFILE_SCHEMA",
    "RECEIPT_TYPE",
    "BridgitPixelRuntimeUnavailable",
    "BridgitRankLayoutError",
    "BridgitRankLayoutProfile",
    "atomic_write_receipt",
    "canonical_hash",
    "execute_shadow_job",
    "find_chain_peaks",
    "load_job",
    "load_profile",
    "ordered_assignments",
    "parse_profile",
    "recognize_frames",
    "sha256_file",
]


if __name__ == "__main__":  # pragma: no cover - exercised as an operational entry point
    sys.exit(main())
