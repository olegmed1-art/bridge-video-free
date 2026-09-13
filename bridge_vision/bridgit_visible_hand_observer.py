"""Reviewed-template observer for visible horizontal Bridgit hands.

This opt-in shadow component recognizes only cards whose rank glyph and suit
run are visible in the supplied frame.  It never fills a hidden hand and does
not write canonical state.  Pixel I/O is byte based; the module deliberately
does not use ``cv2.imread`` or ``cv2.imwrite``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import itertools
import json
import math
import re
from typing import Any, Mapping, Sequence

PROFILE_SCHEMA = "bridgit-visible-hand-observer-profile/v1"
OBSERVER_VERSION = "bridgit-visible-hand-observer-v1"
RANKS = tuple("AKQJT98765432")
SUITS = tuple("HCDS")
HAND_SEATS = ("N", "S")
MAX_ENCODED_FRAME_BYTES = 32 * 1024 * 1024
MAX_TEMPLATES_PER_RANK = 16
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class VisibleHandObserverError(ValueError):
    """The observer cannot proceed without weakening a closed gate."""


@dataclass(frozen=True)
class ObserverProfile:
    profile_id: str
    width: int
    height: int
    binary_threshold: int
    rank_width: int
    rank_height: int
    rows: dict[str, tuple[int, int, int]]
    references: dict[str, tuple[str, str]]
    rank_templates: dict[str, tuple[tuple[str, int, int], ...]]
    min_rank_score: float
    min_rank_margin: float
    min_run_width: int
    min_suit_runs: int
    min_rank_gap: int
    red_dark_ratio: float
    review_sheet_sha256: str
    verification_sha256: str
    profile_sha256: str


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha(value: Any, field: str) -> str:
    text = str(value or "")
    if not _SHA256.fullmatch(text):
        raise VisibleHandObserverError(f"{field} must be lowercase SHA-256")
    return text


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise VisibleHandObserverError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VisibleHandObserverError(f"{field} must be an integer") from exc
    if result < minimum or result > maximum:
        raise VisibleHandObserverError(f"{field} is outside the allowed range")
    return result


def _number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise VisibleHandObserverError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VisibleHandObserverError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise VisibleHandObserverError(f"{field} is outside the allowed range")
    return result


def parse_profile(raw: Mapping[str, Any]) -> ObserverProfile:
    if not isinstance(raw, Mapping) or raw.get("schema") != PROFILE_SCHEMA:
        raise VisibleHandObserverError("unsupported observer profile schema")
    if raw.get("human_verified") is not True:
        raise VisibleHandObserverError("observer profile must be human verified")
    profile_id = str(raw.get("profile_id") or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,95}", profile_id):
        raise VisibleHandObserverError("invalid profile_id")

    frame_size = raw.get("frame_size")
    if not isinstance(frame_size, Mapping):
        raise VisibleHandObserverError("frame_size must be an object")
    width = _integer(frame_size.get("width"), "frame_size.width", 320, 8192)
    height = _integer(frame_size.get("height"), "frame_size.height", 240, 8192)

    verification = raw.get("verification")
    if not isinstance(verification, Mapping):
        raise VisibleHandObserverError("verification record is required")
    if verification.get("method") != "HUMAN_LABEL_REVIEW_IN_CHATGPT_WORK":
        raise VisibleHandObserverError("unsupported verification method")
    reviewer = str(verification.get("reviewer_id") or "").strip()
    verified_at = str(verification.get("verified_at") or "").strip()
    review_sheet_sha = _sha(
        verification.get("review_sheet_sha256"), "review_sheet_sha256"
    )
    if not reviewer or len(reviewer) > 128 or not verified_at.endswith("Z"):
        raise VisibleHandObserverError("verification record is incomplete")
    verification_record = {
        "method": verification["method"],
        "reviewer_id": reviewer,
        "verified_at": verified_at,
        "review_sheet_sha256": review_sheet_sha,
    }

    pixel = raw.get("pixel")
    if not isinstance(pixel, Mapping):
        raise VisibleHandObserverError("pixel settings are required")
    threshold = _integer(pixel.get("binary_threshold"), "binary_threshold", 1, 254)
    rank_width = _integer(pixel.get("rank_width"), "rank_width", 4, 64)
    rank_height = _integer(pixel.get("rank_height"), "rank_height", 4, 64)

    raw_rows = raw.get("rows")
    if not isinstance(raw_rows, Mapping) or set(raw_rows) != set(HAND_SEATS):
        raise VisibleHandObserverError("rows must cover exactly N and S")
    rows: dict[str, tuple[int, int, int]] = {}
    for seat in HAND_SEATS:
        row = raw_rows[seat]
        if not isinstance(row, Mapping):
            raise VisibleHandObserverError(f"rows.{seat} must be an object")
        y = _integer(row.get("y"), f"rows.{seat}.y", 4, height - rank_height - 5)
        x_min = _integer(row.get("x_min"), f"rows.{seat}.x_min", 4, width - 1)
        x_max = _integer(row.get("x_max"), f"rows.{seat}.x_max", 5, width - rank_width)
        if x_max <= x_min or x_max - x_min > 2048:
            raise VisibleHandObserverError(f"rows.{seat} has an invalid span")
        rows[seat] = (y, x_min, x_max)

    raw_references = raw.get("references")
    if not isinstance(raw_references, Mapping) or not 1 <= len(raw_references) <= 8:
        raise VisibleHandObserverError("references must contain 1..8 frames")
    references: dict[str, tuple[str, str]] = {}
    for reference_id, item in raw_references.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", str(reference_id)):
            raise VisibleHandObserverError("invalid reference id")
        if not isinstance(item, Mapping):
            raise VisibleHandObserverError("reference must be an object")
        path = str(item.get("path") or "")
        if not path or len(path) > 256:
            raise VisibleHandObserverError("reference path is invalid")
        references[str(reference_id)] = (
            path,
            _sha(item.get("sha256"), "reference sha256"),
        )

    raw_templates = raw.get("rank_templates")
    if not isinstance(raw_templates, Mapping) or set(raw_templates) != set(RANKS):
        raise VisibleHandObserverError("rank_templates must cover A through 2")
    rank_templates: dict[str, tuple[tuple[str, int, int], ...]] = {}
    for rank in RANKS:
        items = raw_templates[rank]
        if (
            not isinstance(items, Sequence)
            or isinstance(items, (str, bytes))
            or not 1 <= len(items) <= MAX_TEMPLATES_PER_RANK
        ):
            raise VisibleHandObserverError("rank template count is invalid")
        parsed = []
        for item in items:
            if not isinstance(item, Mapping):
                raise VisibleHandObserverError("rank template must be an object")
            reference_id = str(item.get("reference_id") or "")
            if reference_id not in references:
                raise VisibleHandObserverError("rank template reference is unknown")
            x = _integer(item.get("x"), "rank template x", 0, width - rank_width)
            y = _integer(item.get("y"), "rank template y", 0, height - rank_height)
            parsed.append((reference_id, x, y))
        rank_templates[rank] = tuple(parsed)

    gates = raw.get("gates")
    if not isinstance(gates, Mapping):
        raise VisibleHandObserverError("gates must be an object")
    min_score = _number(gates.get("min_rank_score"), "min_rank_score", 0.5, 1.0)
    min_margin = _number(gates.get("min_rank_margin"), "min_rank_margin", 0.001, 1.0)
    min_run_width = _integer(gates.get("min_run_width"), "min_run_width", 20, 512)
    min_suit_runs = _integer(gates.get("min_suit_runs"), "min_suit_runs", 3, 4)
    min_rank_gap = _integer(gates.get("min_rank_gap"), "min_rank_gap", 8, 64)
    red_dark_ratio = _number(gates.get("red_dark_ratio"), "red_dark_ratio", 0.01, 2.0)

    canonical = dict(raw)
    claimed_sha = _sha(canonical.pop("profile_sha256", None), "profile_sha256")
    if claimed_sha != _canonical_hash(canonical):
        raise VisibleHandObserverError("profile hash mismatch")
    return ObserverProfile(
        profile_id=profile_id,
        width=width,
        height=height,
        binary_threshold=threshold,
        rank_width=rank_width,
        rank_height=rank_height,
        rows=rows,
        references=references,
        rank_templates=rank_templates,
        min_rank_score=min_score,
        min_rank_margin=min_margin,
        min_run_width=min_run_width,
        min_suit_runs=min_suit_runs,
        min_rank_gap=min_rank_gap,
        red_dark_ratio=red_dark_ratio,
        review_sheet_sha256=review_sheet_sha,
        verification_sha256=_canonical_hash(verification_record),
        profile_sha256=claimed_sha,
    )


@lru_cache(maxsize=1)
def _pixel_runtime():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependent
        raise RuntimeError("opencv-python-headless and numpy are required") from exc
    return cv2, np


def decode_frame(payload: bytes, profile: ObserverProfile):
    if not payload or len(payload) > MAX_ENCODED_FRAME_BYTES:
        raise VisibleHandObserverError("encoded frame is outside the byte bound")
    cv2, np = _pixel_runtime()
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or tuple(image.shape[:2]) != (profile.height, profile.width):
        raise VisibleHandObserverError("decoded frame dimensions do not match profile")
    return image


def _binary_crop(image: Any, x: int, y: int, profile: ObserverProfile):
    cv2, np = _pixel_runtime()
    crop = image[y : y + profile.rank_height, x : x + profile.rank_width]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return (gray < profile.binary_threshold).astype(np.uint8) * 255


def build_rank_bank(
    profile: ObserverProfile, reference_images: Mapping[str, Any]
) -> dict[str, tuple[Any, ...]]:
    if set(reference_images) != set(profile.references):
        raise VisibleHandObserverError("reference images do not match profile")
    bank: dict[str, tuple[Any, ...]] = {}
    owners: dict[str, str] = {}
    for rank in RANKS:
        samples = []
        for reference_id, x, y in profile.rank_templates[rank]:
            image = reference_images[reference_id]
            if tuple(image.shape[:2]) != (profile.height, profile.width):
                raise VisibleHandObserverError(
                    "reference dimensions do not match profile"
                )
            sample = _binary_crop(image, x, y, profile)
            digest = hashlib.sha256(sample.tobytes()).hexdigest()
            previous = owners.get(digest)
            if previous is not None and previous != rank:
                raise VisibleHandObserverError("one template is labelled as two ranks")
            owners[digest] = rank
            samples.append(sample)
        bank[rank] = tuple(samples)
    return bank


def _white_runs(image: Any, y: int, x_min: int, x_max: int, profile: ObserverProfile):
    cv2, np = _pixel_runtime()
    gray = cv2.cvtColor(image[y : y + 34, x_min:x_max], cv2.COLOR_BGR2GRAY)
    active = ((gray > profile.binary_threshold).mean(axis=0) > 0.30).astype(np.uint8)[
        None, :
    ]
    active = cv2.morphologyEx(
        active, cv2.MORPH_CLOSE, np.ones((1, 4), np.uint8)
    ).ravel()
    runs = []
    start = None
    for index, value in enumerate(np.r_[active, 0]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= profile.min_run_width:
                runs.append((x_min + start, index - start))
            start = None
    return runs


def _run_color(
    image: Any, y: int, run: tuple[int, int], profile: ObserverProfile
) -> str:
    cv2, _ = _pixel_runtime()
    x, width = run
    crop = image[y : y + 52, max(0, x - 3) : min(profile.width, x + width)]
    blue, green, red = cv2.split(crop)
    red_pixels = ((red > 130) & (red > green * 1.35) & (red > blue * 1.35)).sum()
    dark_pixels = ((red < 120) & (green < 120) & (blue < 120)).sum()
    return "R" if red_pixels > dark_pixels * profile.red_dark_ratio else "B"


def _suit_indices(colors: Sequence[str]) -> tuple[int, ...] | None:
    target = ("R", "B", "R", "B")
    matches = [
        indices
        for indices in itertools.combinations(range(4), len(colors))
        if tuple(target[index] for index in indices) == tuple(colors)
    ]
    return matches[0] if len(matches) == 1 else None


def _rank_detections(
    image: Any,
    y: int,
    x_min: int,
    x_max: int,
    bank: Mapping[str, Sequence[Any]],
    profile: ObserverProfile,
):
    cv2, np = _pixel_runtime()
    gray = cv2.cvtColor(
        image[y - 4 : y + profile.rank_height + 4, x_min : x_max + profile.rank_width],
        cv2.COLOR_BGR2GRAY,
    )
    binary = (gray < profile.binary_threshold).astype(np.uint8) * 255
    matrices = []
    for rank in RANKS:
        matrices.append(
            np.maximum.reduce(
                [
                    cv2.matchTemplate(binary, sample, cv2.TM_CCOEFF_NORMED)[
                        :, : x_max - x_min + 1
                    ]
                    for sample in bank[rank]
                ]
            )
        )
    candidates = []
    for rank_index, rank in enumerate(RANKS):
        matrix = matrices[rank_index]
        ys, xs = np.where(matrix >= profile.min_rank_score)
        for relative_y, relative_x in zip(ys, xs):
            values = sorted(float(item[relative_y, relative_x]) for item in matrices)
            score = float(matrix[relative_y, relative_x])
            margin = score - values[-2]
            if margin >= profile.min_rank_margin:
                candidates.append(
                    (
                        score,
                        x_min + int(relative_x),
                        y - 4 + int(relative_y),
                        rank,
                        margin,
                    )
                )
    selected = []
    for candidate in sorted(candidates, reverse=True):
        if all(
            abs(candidate[1] - item[1]) >= profile.min_rank_gap for item in selected
        ):
            selected.append(candidate)
    return sorted(selected, key=lambda item: item[1])


def observe_frame(
    image: Any, bank: Mapping[str, Sequence[Any]], profile: ObserverProfile
) -> dict[str, Any]:
    if tuple(image.shape[:2]) != (profile.height, profile.width):
        raise VisibleHandObserverError("frame dimensions do not match profile")
    cards = []
    hands: dict[str, Any] = {}
    rejected = []
    for seat in HAND_SEATS:
        y, x_min, x_max = profile.rows[seat]
        runs = _white_runs(image, y, x_min, x_max, profile)
        colors = [_run_color(image, y, run, profile) for run in runs]
        suit_indices = _suit_indices(colors)
        if len(runs) < profile.min_suit_runs or suit_indices is None:
            rejected.append(
                {
                    "seat": seat,
                    "reason": "SUIT_RUN_GEOMETRY_AMBIGUOUS",
                    "run_count": len(runs),
                    "colors": colors,
                }
            )
            hands[seat] = {"status": "REVIEW", "cards": []}
            continue
        detections = _rank_detections(image, y, x_min, x_max, bank, profile)
        observed = []
        for score, x, observed_y, rank, margin in detections:
            matches = [
                index
                for index, (run_x, width) in enumerate(runs)
                if run_x - 5 <= x < run_x + width
            ]
            if len(matches) != 1:
                continue
            suit = SUITS[suit_indices[matches[0]]]
            region_x0 = max(0, x - 3)
            region_y0 = max(0, observed_y - 4)
            region_x1 = min(profile.width, x + profile.rank_width + 12)
            region_y1 = min(profile.height, observed_y + 52)
            evidence = image[region_y0:region_y1, region_x0:region_x1]
            card = rank + suit
            observed.append(card)
            cards.append(
                {
                    "card": card,
                    "seat": seat,
                    "source": "HAND",
                    "confidence": round(score, 6),
                    "rank_margin": round(margin, 6),
                    "evidence_pixel_sha256": hashlib.sha256(
                        evidence.tobytes()
                    ).hexdigest(),
                    "region": {
                        "x": region_x0,
                        "y": region_y0,
                        "width": region_x1 - region_x0,
                        "height": region_y1 - region_y0,
                    },
                }
            )
        if len(observed) > 13 or len(observed) != len(set(observed)):
            return {
                "status": "CONFLICT",
                "cards": [],
                "hands": hands,
                "rejected": [{"seat": seat, "reason": "HAND_CARD_CONFLICT"}],
            }
        hands[seat] = {
            "status": "OBSERVED" if observed else "REVIEW",
            "cards": observed,
            "runs": [
                {
                    "x": x,
                    "width": width,
                    "color": color,
                    "suit": SUITS[suit_indices[index]],
                }
                for index, ((x, width), color) in enumerate(zip(runs, colors))
            ],
        }

    owners: dict[str, set[str]] = {}
    for item in cards:
        owners.setdefault(item["card"], set()).add(item["seat"])
    conflicts = [card for card, seats in owners.items() if len(seats) > 1]
    if conflicts:
        return {
            "status": "CONFLICT",
            "cards": [],
            "hands": hands,
            "rejected": [
                {"reason": "CROSS_SEAT_CARD_CONFLICT", "cards": sorted(conflicts)}
            ],
        }
    return {
        "status": "SHADOW_VISIBLE_HANDS" if cards else "REVIEW",
        "cards": cards,
        "hands": hands,
        "rejected": rejected,
    }


__all__ = [
    "MAX_ENCODED_FRAME_BYTES",
    "OBSERVER_VERSION",
    "PROFILE_SCHEMA",
    "ObserverProfile",
    "VisibleHandObserverError",
    "build_rank_bank",
    "decode_frame",
    "observe_frame",
    "parse_profile",
]
