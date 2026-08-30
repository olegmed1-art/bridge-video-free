"""Opt-in, profile-specific card-recognition challenger.

This module implements the deterministic decision boundary learned from
controlled card readers (Dealer4/BridgeSorter) and modern reference retrieval:

* a human-verified interface/teach profile is mandatory;
* every frame is registered to the profile through a bounded homography gate;
* rank and suit are read separately;
* the composed card must agree with an independent reference-card match;
* seat ownership comes only from registered geometry;
* a card becomes an observation only after independent-frame consensus;
* cross-seat disagreement fails closed.

The pixel recognizer is deliberately injected.  This module does not pretend
that a backend exists and is never registered by the default runtime.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from bridge_contracts.video_deal import (
    BridgeVideoDealContractError,
    canonicalize_video_deal,
)
from bridge_vision.native_cards import (
    NativeCardDetectorError,
    observations_from_backend,
)

PROFILE_SCHEMA = "bridge-vision-interface-profile/v2"
CHALLENGER_VERSION = "bridge-profiled-card-challenger-v2"
RANKS = tuple("AKQJT98765432")
SUITS = tuple("SHDC")
CARDS = tuple(rank + suit for rank in RANKS for suit in SUITS)
SEATS = ("N", "E", "S", "W")
LAYOUT_AXES = ("X_ASC", "X_DESC", "Y_ASC", "Y_DESC")
MAX_TRACKS = 32
MAX_FRAMES_PER_TRACK = 256
MAX_CARD_OBSERVATIONS_PER_FRAME = 104
MAX_PROFILE_BYTES = 1024 * 1024
MAX_DIAGNOSTIC_DETAIL = 160
VULNERABILITY_CYCLE = (
    "NONE", "NS", "EW", "BOTH", "NS", "EW", "BOTH", "NONE",
    "EW", "BOTH", "NONE", "NS", "BOTH", "NONE", "NS", "EW",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_CHANNEL_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")
_REVIEWER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:-]{1,127}$")
_UTC_TIMESTAMP = re.compile(r"^20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z$")

PixelRecognizer = Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]


class ProfiledChallengerError(ValueError):
    pass


def _finite_float(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProfiledChallengerError(f"invalid {field_name}") from exc
    if not math.isfinite(number):
        raise ProfiledChallengerError(f"invalid {field_name}")
    return number


def _probability(value: Any, field_name: str) -> float:
    number = _finite_float(value, field_name)
    if not 0.0 <= number <= 1.0:
        raise ProfiledChallengerError(f"{field_name} outside [0,1]")
    return number


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ProfiledChallengerError(f"invalid {field_name}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ProfiledChallengerError(f"invalid {field_name}") from exc
    if number < 1:
        raise ProfiledChallengerError(f"invalid {field_name}")
    return number


def _board_number(value: Any) -> int:
    if isinstance(value, bool):
        raise ProfiledChallengerError("invalid board number")
    text = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]{0,5}", text):
        raise ProfiledChallengerError("invalid board number")
    return int(text)


def _required_sha(value: Any, field_name: str) -> str:
    text = str(value or "").lower()
    if not _SHA256.fullmatch(text):
        raise ProfiledChallengerError(f"invalid {field_name}")
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _template_map(raw: Any, *, expected: Sequence[str], field_name: str) -> dict[str, str]:
    if not isinstance(raw, Mapping) or set(raw) != set(expected):
        raise ProfiledChallengerError(f"{field_name} must cover the complete symbol set")
    result = {symbol: _required_sha(raw[symbol], f"{field_name}.{symbol}") for symbol in expected}
    if len(set(result.values())) != len(result):
        raise ProfiledChallengerError(f"{field_name} contains duplicate template hashes")
    return result


def _channel_id(value: Any, field_name: str) -> str:
    channel = str(value or "")
    if not _CHANNEL_ID.fullmatch(channel):
        raise ProfiledChallengerError(f"invalid {field_name}")
    return channel


def _bounded_detail(value: Any) -> str:
    return str(value).replace("\n", " ").replace("\r", " ")[:MAX_DIAGNOSTIC_DETAIL]


def _ordering_prior(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("human_verified") is not True:
        raise ProfiledChallengerError("human-verified ordering prior is required")
    suits = raw.get("suit_order")
    ranks = raw.get("rank_order")
    axes = raw.get("seat_axes")
    positions = raw.get("seat_positions")
    if not isinstance(suits, Sequence) or isinstance(suits, (str, bytes)) or tuple(suits) != tuple("HCDS"):
        raise ProfiledChallengerError("ordering suit order must be H,C,D,S")
    if not isinstance(ranks, Sequence) or isinstance(ranks, (str, bytes)) or tuple(ranks) != RANKS:
        raise ProfiledChallengerError("ordering rank order must be A through 2")
    if not isinstance(axes, Mapping) or set(axes) != set(SEATS):
        raise ProfiledChallengerError("ordering seat axes must cover N,E,S,W")
    normalized_axes = {seat: str(axes[seat]).upper() for seat in SEATS}
    if any(axis not in LAYOUT_AXES for axis in normalized_axes.values()):
        raise ProfiledChallengerError("unsupported ordering seat axis")
    position_order = ("top", "right", "bottom", "left")
    if not isinstance(positions, Mapping):
        raise ProfiledChallengerError("verified seat positions are required")
    normalized_positions = {str(key).lower(): str(value).upper() for key, value in positions.items()}
    if set(normalized_positions) != set(position_order):
        raise ProfiledChallengerError("seat positions must cover top,right,bottom,left")
    rotations = (
        ("N", "E", "S", "W"),
        ("W", "N", "E", "S"),
        ("S", "W", "N", "E"),
        ("E", "S", "W", "N"),
    )
    position_cycle = tuple(normalized_positions[position] for position in position_order)
    if position_cycle not in rotations:
        raise ProfiledChallengerError("seat positions must be a 0/90/180/270 degree rotation")
    expected_axes = {
        logical_seat: "X_ASC" if position in {"top", "bottom"} else "Y_ASC"
        for position, logical_seat in normalized_positions.items()
    }
    if normalized_axes != expected_axes:
        raise ProfiledChallengerError("ordering axes do not match the verified rotated bridge layout")
    rotation_degrees = 90 * rotations.index(position_cycle)
    return {
        "human_verified": True,
        "suit_order": list(suits),
        "rank_order": list(ranks),
        "seat_axes": normalized_axes,
        "seat_positions": normalized_positions,
        "rotation_degrees_clockwise": rotation_degrees,
    }


def derive_duplicate_board_metadata(board_number: int) -> tuple[str, str]:
    number = _board_number(board_number)
    return SEATS[(number - 1) % 4], VULNERABILITY_CYCLE[(number - 1) % 16]


def _normalize_vulnerability(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "").replace(" ", "")
    aliases = {"NONE": "NONE", "LOVE": "NONE", "NS": "NS", "EW": "EW", "BOTH": "BOTH", "ALL": "BOTH"}
    normalized = aliases.get(text)
    if normalized is None:
        raise ProfiledChallengerError("invalid vulnerability")
    return normalized


def _observed_metadata_field(raw: Any, field_name: str) -> tuple[Any, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError(f"{field_name} observation must be an object")
    confidence = _probability(raw.get("confidence"), f"{field_name} confidence")
    if confidence < 0.90:
        raise ProfiledChallengerError(f"{field_name} confidence below gate")
    source = str(raw.get("source") or "").upper()
    # Speech may corroborate a review, but it cannot create board/dealer/
    # vulnerability facts.  Those values need source-bound visual evidence (or
    # an explicit human verification record).
    if source not in {"VISUAL_TEXT", "VISUAL_MARKER", "HUMAN_VERIFIED"}:
        raise ProfiledChallengerError(f"unsupported {field_name} source")
    locator = str(raw.get("evidence_locator") or "").strip()
    if not locator or len(locator) > 256:
        raise ProfiledChallengerError(f"invalid {field_name} evidence locator")
    return raw.get("value"), {
        "confidence": confidence,
        "source": source,
        "evidence_locator": locator,
    }


def _board_metadata_candidate(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError("board metadata must be an object")
    value, board_evidence = _observed_metadata_field(raw.get("board_number"), "board number")
    board_number = _board_number(value)
    dealer, vulnerability = derive_duplicate_board_metadata(board_number)
    observed_dealer = raw.get("dealer")
    dealer_evidence = None
    if observed_dealer is not None:
        dealer_value, dealer_evidence = _observed_metadata_field(observed_dealer, "dealer")
        if str(dealer_value or "").strip().upper() != dealer:
            raise ProfiledChallengerError("observed dealer conflicts with board cycle")
    observed_vulnerability = raw.get("vulnerability")
    vulnerability_evidence = None
    if observed_vulnerability is not None:
        vulnerability_value, vulnerability_evidence = _observed_metadata_field(
            observed_vulnerability,
            "vulnerability",
        )
        if _normalize_vulnerability(vulnerability_value) != vulnerability:
            raise ProfiledChallengerError("observed vulnerability conflicts with board cycle")
    return {
        "board_number": board_number,
        "dealer": dealer,
        "vulnerability": vulnerability,
        "independent_fields_complete": dealer_evidence is not None and vulnerability_evidence is not None,
        "provenance": {
            "board_number": {"class": "OBSERVED", **board_evidence},
            "dealer": {
                "class": "OBSERVED_AND_CYCLE_CONFIRMED" if dealer_evidence else "DERIVED_FROM_BOARD_NUMBER",
                **(dealer_evidence or {}),
            },
            "vulnerability": {
                "class": "OBSERVED_AND_CYCLE_CONFIRMED" if vulnerability_evidence else "DERIVED_FROM_BOARD_NUMBER",
                **(vulnerability_evidence or {}),
            },
        },
    }


def _template_fingerprint(
    ranks: Mapping[str, str],
    suits: Mapping[str, str],
    cards: Mapping[str, str],
    *,
    rank_suit_channel: str,
    reference_channel: str,
    ordering_prior: Mapping[str, Any],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "cards": dict(cards),
                "channels": {"rank_suit": rank_suit_channel, "reference": reference_channel},
                "ordering_prior": dict(ordering_prior),
                "ranks": dict(ranks),
                "suits": dict(suits),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _rect(raw: Any, field_name: str) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError(f"{field_name} must be an object")
    result = {key: _finite_float(raw.get(key), f"{field_name}.{key}") for key in ("x", "y", "w", "h")}
    if result["w"] <= 0 or result["h"] <= 0:
        raise ProfiledChallengerError(f"invalid {field_name} size")
    return result


@dataclass(frozen=True)
class InterfaceProfile:
    profile_id: str
    reference_frame_sha256: str
    reference_width: int
    reference_height: int
    verification: dict[str, str]
    verification_sha256: str
    table_region: dict[str, float]
    rank_templates: dict[str, str]
    suit_templates: dict[str, str]
    card_templates: dict[str, str]
    rank_suit_channel_id: str
    reference_channel_id: str
    ordering_prior: dict[str, Any]
    min_registration_inliers: int
    min_registration_inlier_ratio: float
    min_deal_match_inliers: int
    min_deal_match_inlier_ratio: float
    min_rank_confidence: float
    min_suit_confidence: float
    min_reference_confidence: float
    min_card_confidence: float
    min_ambiguous_candidate_confidence: float
    min_temporal_observations: int
    seat_dead_zone: float
    template_set_sha256: str

    def recognizer_view(self) -> dict[str, Any]:
        """Return the immutable, decision-relevant view exposed to a backend."""
        return {
            "schema": PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "reference_frame_sha256": self.reference_frame_sha256,
            "reference_size": {"width": self.reference_width, "height": self.reference_height},
            "table_region": dict(self.table_region),
            "rank_templates": dict(self.rank_templates),
            "suit_templates": dict(self.suit_templates),
            "card_templates": dict(self.card_templates),
            "rank_suit_channel_id": self.rank_suit_channel_id,
            "reference_channel_id": self.reference_channel_id,
            "ordering_prior": dict(self.ordering_prior),
            "template_set_sha256": self.template_set_sha256,
            "verification_sha256": self.verification_sha256,
        }


def parse_profile(raw: Mapping[str, Any]) -> InterfaceProfile:
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError("profile must be an object")
    if raw.get("schema") != PROFILE_SCHEMA:
        raise ProfiledChallengerError("unsupported profile schema")
    if raw.get("human_verified") is not True:
        raise ProfiledChallengerError("profile must be human verified")
    profile_id = str(raw.get("profile_id") or "")
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ProfiledChallengerError("invalid profile_id")

    verification = raw.get("verification")
    if not isinstance(verification, Mapping):
        raise ProfiledChallengerError("human verification evidence is required")
    if verification.get("method") != "HUMAN_LABEL_REVIEW":
        raise ProfiledChallengerError("unsupported verification method")
    reviewer_id = str(verification.get("reviewer_id") or "")
    if not _REVIEWER_ID.fullmatch(reviewer_id):
        raise ProfiledChallengerError("invalid verification reviewer_id")
    verified_at = str(verification.get("verified_at") or "")
    if not _UTC_TIMESTAMP.fullmatch(verified_at):
        raise ProfiledChallengerError("invalid verification timestamp")
    try:
        datetime.fromisoformat(verified_at)
    except ValueError as exc:
        raise ProfiledChallengerError("invalid verification timestamp") from exc
    verification_reference_sha = _required_sha(
        verification.get("reference_frame_sha256"),
        "verification.reference_frame_sha256",
    )
    profile_reference_sha = _required_sha(raw.get("reference_frame_sha256"), "reference_frame_sha256")
    if verification_reference_sha != profile_reference_sha:
        raise ProfiledChallengerError("verification reference does not match profile")
    verification_record = {
        "method": "HUMAN_LABEL_REVIEW",
        "reviewer_id": reviewer_id,
        "verified_at": verified_at,
        "reference_frame_sha256": verification_reference_sha,
    }
    verification_sha = hashlib.sha256(
        json.dumps(verification_record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    size = raw.get("reference_size")
    if not isinstance(size, Mapping):
        raise ProfiledChallengerError("reference_size must be an object")
    width = _positive_int(size.get("width"), "reference_size.width")
    height = _positive_int(size.get("height"), "reference_size.height")
    table = _rect(raw.get("table_region"), "table_region")
    if table["x"] < 0 or table["y"] < 0 or table["x"] + table["w"] > width or table["y"] + table["h"] > height:
        raise ProfiledChallengerError("table_region leaves reference frame")

    teach = raw.get("teach")
    if not isinstance(teach, Mapping) or teach.get("human_verified") is not True:
        raise ProfiledChallengerError("human-verified teach templates are required")
    ranks = _template_map(teach.get("rank_templates"), expected=RANKS, field_name="teach.rank_templates")
    suits = _template_map(teach.get("suit_templates"), expected=SUITS, field_name="teach.suit_templates")
    cards = _template_map(teach.get("card_templates"), expected=CARDS, field_name="teach.card_templates")
    channels = teach.get("channels")
    if not isinstance(channels, Mapping):
        raise ProfiledChallengerError("teach channels must be an object")
    rank_suit_channel = _channel_id(channels.get("rank_suit"), "teach.channels.rank_suit")
    reference_channel = _channel_id(channels.get("reference"), "teach.channels.reference")
    if rank_suit_channel == reference_channel:
        raise ProfiledChallengerError("rank/suit and reference channels must be independent")
    ordering = _ordering_prior(teach.get("ordering_prior"))
    expected_template_sha = _template_fingerprint(
        ranks,
        suits,
        cards,
        rank_suit_channel=rank_suit_channel,
        reference_channel=reference_channel,
        ordering_prior=ordering,
    )
    claimed_template_sha = _required_sha(teach.get("template_set_sha256"), "teach.template_set_sha256")
    if claimed_template_sha != expected_template_sha:
        raise ProfiledChallengerError("teach template-set hash mismatch")

    gates = raw.get("gates")
    if not isinstance(gates, Mapping):
        raise ProfiledChallengerError("profile gates must be an object")
    dead_zone = _probability(gates.get("seat_dead_zone"), "gates.seat_dead_zone")
    if dead_zone >= 0.5:
        raise ProfiledChallengerError("seat dead zone must be below 0.5")
    temporal = _positive_int(gates.get("min_temporal_observations"), "gates.min_temporal_observations")
    if temporal < 2:
        raise ProfiledChallengerError("at least two independent frame observations are required")
    if temporal > MAX_FRAMES_PER_TRACK:
        raise ProfiledChallengerError("temporal observation gate exceeds track capacity")

    return InterfaceProfile(
        profile_id=profile_id,
        reference_frame_sha256=profile_reference_sha,
        reference_width=width,
        reference_height=height,
        verification=verification_record,
        verification_sha256=verification_sha,
        table_region=table,
        rank_templates=ranks,
        suit_templates=suits,
        card_templates=cards,
        rank_suit_channel_id=rank_suit_channel,
        reference_channel_id=reference_channel,
        ordering_prior=ordering,
        min_registration_inliers=_positive_int(gates.get("min_registration_inliers"), "gates.min_registration_inliers"),
        min_registration_inlier_ratio=_probability(gates.get("min_registration_inlier_ratio"), "gates.min_registration_inlier_ratio"),
        min_deal_match_inliers=_positive_int(gates.get("min_deal_match_inliers"), "gates.min_deal_match_inliers"),
        min_deal_match_inlier_ratio=_probability(gates.get("min_deal_match_inlier_ratio"), "gates.min_deal_match_inlier_ratio"),
        min_rank_confidence=_probability(gates.get("min_rank_confidence"), "gates.min_rank_confidence"),
        min_suit_confidence=_probability(gates.get("min_suit_confidence"), "gates.min_suit_confidence"),
        min_reference_confidence=_probability(gates.get("min_reference_confidence"), "gates.min_reference_confidence"),
        min_card_confidence=_probability(gates.get("min_card_confidence"), "gates.min_card_confidence"),
        min_ambiguous_candidate_confidence=_probability(
            gates.get("min_ambiguous_candidate_confidence"),
            "gates.min_ambiguous_candidate_confidence",
        ),
        min_temporal_observations=temporal,
        seat_dead_zone=dead_zone,
        template_set_sha256=claimed_template_sha,
    )


def load_profile(path: Path) -> InterfaceProfile:
    try:
        profile_size = path.stat().st_size
    except OSError as exc:
        raise ProfiledChallengerError("profile file is unavailable") from exc
    if profile_size > MAX_PROFILE_BYTES:
        raise ProfiledChallengerError("profile exceeds size limit")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProfiledChallengerError("profile contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfiledChallengerError("profile is not valid UTF-8 JSON") from exc
    return parse_profile(raw)


def build_teach_profile(
    *,
    profile_id: str,
    reference_frame_sha256: str,
    reference_size: Mapping[str, Any],
    table_region: Mapping[str, Any],
    rank_templates: Mapping[str, Any],
    suit_templates: Mapping[str, Any],
    card_templates: Mapping[str, Any],
    rank_suit_channel_id: str,
    reference_channel_id: str,
    human_verified: bool,
    verification: Mapping[str, Any],
    ordering_prior: Mapping[str, Any],
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and validate a profile from already human-labelled template hashes.

    Template extraction is a pixel-backend concern.  This helper never guesses
    a label and therefore cannot turn an automatically harvested glyph into a
    trusted Teach template.
    """
    if human_verified is not True:
        raise ProfiledChallengerError("explicit human verification is required")
    ranks = _template_map(rank_templates, expected=RANKS, field_name="rank_templates")
    suits = _template_map(suit_templates, expected=SUITS, field_name="suit_templates")
    cards = _template_map(card_templates, expected=CARDS, field_name="card_templates")
    rank_suit_channel = _channel_id(rank_suit_channel_id, "rank_suit_channel_id")
    reference_channel = _channel_id(reference_channel_id, "reference_channel_id")
    if rank_suit_channel == reference_channel:
        raise ProfiledChallengerError("rank/suit and reference channels must be independent")
    ordering = _ordering_prior(ordering_prior)
    template_sha = _template_fingerprint(
        ranks,
        suits,
        cards,
        rank_suit_channel=rank_suit_channel,
        reference_channel=reference_channel,
        ordering_prior=ordering,
    )
    profile = {
        "schema": PROFILE_SCHEMA,
        "profile_id": profile_id,
        "human_verified": True,
        "reference_frame_sha256": reference_frame_sha256,
        "verification": dict(verification),
        "reference_size": dict(reference_size),
        "table_region": dict(table_region),
        "teach": {
            "human_verified": True,
            "rank_templates": ranks,
            "suit_templates": suits,
            "card_templates": cards,
            "channels": {"rank_suit": rank_suit_channel, "reference": reference_channel},
            "ordering_prior": ordering,
            "template_set_sha256": template_sha,
        },
        "gates": dict(gates),
    }
    parse_profile(profile)
    return profile


def _matrix(raw: Any) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 3:
        raise ProfiledChallengerError("invalid homography")
    rows: list[tuple[float, float, float]] = []
    for row in raw:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) != 3:
            raise ProfiledChallengerError("invalid homography")
        rows.append(tuple(_finite_float(value, "homography") for value in row))
    a, b, c = rows
    determinant = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )
    if abs(determinant) < 1e-12:
        raise ProfiledChallengerError("singular homography")
    matrix = tuple(rows)
    p0 = _project(matrix, 0.0, 0.0)
    px = _project(matrix, 1.0, 0.0)
    py = _project(matrix, 0.0, 1.0)
    orientation = (px[0] - p0[0]) * (py[1] - p0[1]) - (px[1] - p0[1]) * (py[0] - p0[0])
    if orientation <= 1e-12:
        raise ProfiledChallengerError("homography is mirrored or locally degenerate")
    return matrix


def _project(matrix: tuple[tuple[float, float, float], ...], x: float, y: float) -> tuple[float, float]:
    denominator = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if abs(denominator) < 1e-12:
        raise ProfiledChallengerError("homography projects outside finite plane")
    px = (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / denominator
    py = (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / denominator
    if not math.isfinite(px) or not math.isfinite(py):
        raise ProfiledChallengerError("homography projection is not finite")
    return px, py


def _registered_box(raw: Any, matrix: tuple[tuple[float, float, float], ...]) -> dict[str, float]:
    box = _rect(raw, "card.box")
    points = [
        _project(matrix, box["x"], box["y"]),
        _project(matrix, box["x"] + box["w"], box["y"]),
        _project(matrix, box["x"], box["y"] + box["h"]),
        _project(matrix, box["x"] + box["w"], box["y"] + box["h"]),
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}


def _seat_for_registered_box(box: Mapping[str, float], table: Mapping[str, float], dead_zone: float) -> str | None:
    nx = (box["x"] + box["w"] / 2.0 - table["x"]) / table["w"]
    ny = (box["y"] + box["h"] / 2.0 - table["y"]) / table["h"]
    if not 0.0 <= nx <= 1.0 or not 0.0 <= ny <= 1.0:
        return None
    dx = nx - 0.5
    dy = ny - 0.5
    if abs(dx) < dead_zone and abs(dy) < dead_zone:
        return None
    if abs(dx) > abs(dy):
        return "E" if dx > 0 else "W"
    return "S" if dy > 0 else "N"


def _logical_seat(screen_seat: str, ordering_prior: Mapping[str, Any]) -> str:
    position_by_screen_seat = {"N": "top", "E": "right", "S": "bottom", "W": "left"}
    try:
        return str(ordering_prior["seat_positions"][position_by_screen_seat[screen_seat]])
    except (KeyError, TypeError) as exc:
        raise ProfiledChallengerError("profile seat rotation is incomplete") from exc


def _axis_position(box: Mapping[str, float], axis: str) -> float:
    coordinate = box["x"] + box["w"] / 2.0 if axis.startswith("X_") else box["y"] + box["h"] / 2.0
    return coordinate if axis.endswith("_ASC") else -coordinate


def _card_order(card: str, ordering: Mapping[str, Any]) -> int:
    rank_order = list(ordering["rank_order"])
    suit_order = list(ordering["suit_order"])
    return suit_order.index(card[1]) * len(rank_order) + rank_order.index(card[0])


def _rank(raw: Any) -> tuple[str, float]:
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError("rank channel is missing")
    value = str(raw.get("value") or "").upper()
    if value == "10":
        value = "T"
    if value not in RANKS:
        raise ProfiledChallengerError("invalid rank channel value")
    return value, _probability(raw.get("confidence"), "rank confidence")


def _suit(raw: Any) -> tuple[str, float]:
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError("suit channel is missing")
    value = str(raw.get("value") or "").upper()
    value = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}.get(value, value)
    if value not in SUITS:
        raise ProfiledChallengerError("invalid suit channel value")
    return value, _probability(raw.get("confidence"), "suit confidence")


def _card(value: Any) -> str:
    deal = canonicalize_video_deal({"hands": {"N": [value]}}).to_dict()
    return deal["hands"]["N"]["cards"][0]


@dataclass
class _Vote:
    confidence: float
    box: dict[str, float]
    channel_evidence: dict[str, Any]


@dataclass
class _Track:
    frame_sha256s: set[str] = field(default_factory=set)
    votes: dict[tuple[str, str], dict[str, _Vote]] = field(default_factory=dict)
    board_metadata_votes: dict[str, set[str]] = field(default_factory=dict)
    board_metadata_values: dict[str, dict[str, Any]] = field(default_factory=dict)


def _board_metadata_state(track: _Track, profile: InterfaceProfile) -> dict[str, Any]:
    if not track.board_metadata_votes:
        return {
            "status": "UNAVAILABLE",
            "seat_positions": dict(profile.ordering_prior["seat_positions"]),
            "rotation_degrees_clockwise": profile.ordering_prior["rotation_degrees_clockwise"],
        }
    candidates = [
        {
            **track.board_metadata_values[key],
            "independent_frames": len(frame_sha256s),
            "frame_sha256s": sorted(frame_sha256s),
        }
        for key, frame_sha256s in sorted(track.board_metadata_votes.items())
    ]
    if len(candidates) > 1:
        return {
            "status": "CONFLICT",
            "reason": "BOARD_METADATA_DISAGREEMENT",
            "candidates": candidates,
            "seat_positions": dict(profile.ordering_prior["seat_positions"]),
            "rotation_degrees_clockwise": profile.ordering_prior["rotation_degrees_clockwise"],
        }
    candidate = candidates[0]
    enough_frames = candidate["independent_frames"] >= profile.min_temporal_observations
    independently_observed = candidate.get("independent_fields_complete") is True
    return {
        "status": (
            "CONFIRMED"
            if enough_frames and independently_observed
            else "PARTIAL_VISUAL_EVIDENCE"
            if enough_frames
            else "PENDING_TEMPORAL_CONSENSUS"
        ),
        **candidate,
        "seat_positions": dict(profile.ordering_prior["seat_positions"]),
        "rotation_degrees_clockwise": profile.ordering_prior["rotation_degrees_clockwise"],
    }


class ProfiledCardChallenger:
    """Stateful opt-in detector requiring temporal card+seat consensus."""

    shadow_only = True

    def __init__(self, profile: InterfaceProfile, recognizer: PixelRecognizer):
        if not callable(recognizer):
            raise TypeError("recognizer must be callable")
        self.profile = profile
        self.recognizer = recognizer
        self._tracks: dict[str, _Track] = {}

    def _review(self, frame_sha: str, reason: str, **extra: Any) -> dict[str, Any]:
        if "detail" in extra:
            extra["detail"] = _bounded_detail(extra["detail"])
        return {
            "status": "REVIEW",
            "hands": {},
            "confidence": 0.0,
            "evidence": {
                "detector_version": CHALLENGER_VERSION,
                "profile_id": self.profile.profile_id,
                "profile_verification_sha256": self.profile.verification_sha256,
                "frame_sha256": frame_sha,
                "reason": reason,
                "canonical_promotion_allowed": False,
                **extra,
            },
        }

    def _registration(self, raw: Any) -> tuple[tuple[tuple[float, float, float], ...], dict[str, Any]]:
        if not isinstance(raw, Mapping):
            raise ProfiledChallengerError("registration is missing")
        reference_sha = _required_sha(raw.get("reference_frame_sha256"), "registration reference sha256")
        if reference_sha != self.profile.reference_frame_sha256:
            raise ProfiledChallengerError("registration reference does not match profile")
        inliers = _positive_int(raw.get("inliers"), "registration inliers")
        ratio = _probability(raw.get("inlier_ratio"), "registration inlier ratio")
        if inliers < self.profile.min_registration_inliers or ratio < self.profile.min_registration_inlier_ratio:
            raise ProfiledChallengerError("registration below profile gate")
        return _matrix(raw.get("homography")), {"inliers": inliers, "inlier_ratio": ratio, "reference_frame_sha256": reference_sha}

    def _deal_key(self, raw: Any) -> tuple[str, dict[str, Any]]:
        if not isinstance(raw, Mapping):
            raise ProfiledChallengerError("deal identity is missing")
        kind = str(raw.get("kind") or "").upper()
        if kind == "EXPLICIT_BOARD":
            scope = str(raw.get("scope") or "").strip()
            value = str(raw.get("value") or "").strip()
            if not scope or not value or len(scope) > 128 or len(value) > 128:
                raise ProfiledChallengerError("invalid explicit board identity")
            identity = json.dumps({"scope": scope, "value": value}, sort_keys=True, separators=(",", ":"))
            return "explicit:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(), {"kind": kind, "scope": scope, "value": value}
        if kind == "VISUAL_ANCHOR":
            anchor = _required_sha(raw.get("anchor_frame_sha256"), "deal anchor sha256")
            inliers = _positive_int(raw.get("inliers"), "deal match inliers")
            ratio = _probability(raw.get("inlier_ratio"), "deal match inlier ratio")
            if inliers < self.profile.min_deal_match_inliers or ratio < self.profile.min_deal_match_inlier_ratio:
                raise ProfiledChallengerError("deal match below profile gate")
            return f"visual:{anchor}", {"kind": kind, "anchor_frame_sha256": anchor, "inliers": inliers, "inlier_ratio": ratio}
        raise ProfiledChallengerError("unsupported deal identity")

    def _layout_suggestions(
        self,
        ambiguous: Sequence[Mapping[str, Any]],
        geometry: Mapping[str, Any],
        geometry_cards: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        known_by_seat: dict[str, list[tuple[float, int, str]]] = {seat: [] for seat in SEATS}
        known_cards: set[str] = set()
        for item in geometry["accepted"]:
            seat = item["seat"]
            card = item["card"]
            axis = self.profile.ordering_prior["seat_axes"][seat]
            box = geometry_cards[item["index"]]["box"]
            known_by_seat[seat].append(
                (_axis_position(box, axis), _card_order(card, self.profile.ordering_prior), card)
            )
            known_cards.add(card)

        suggestions: list[dict[str, Any]] = []
        for item in ambiguous:
            seat = str(item["seat"])
            axis = self.profile.ordering_prior["seat_axes"][seat]
            position = _axis_position(item["box"], axis)
            compatible: list[dict[str, Any]] = []
            for candidate in item["candidates"]:
                card = candidate["card"]
                if card in known_cards:
                    continue
                order = _card_order(card, self.profile.ordering_prior)
                valid = True
                for known_position, known_order, _known_card in known_by_seat[seat]:
                    if known_position < position and not known_order < order:
                        valid = False
                        break
                    if known_position > position and not order < known_order:
                        valid = False
                        break
                    if known_position == position:
                        valid = False
                        break
                if valid:
                    compatible.append(dict(candidate))

            resolution = (
                "LAYOUT_UNIQUE_SUGGESTION"
                if len(compatible) == 1
                else "LAYOUT_AMBIGUOUS"
                if compatible
                else "LAYOUT_NO_COMPATIBLE_CANDIDATE"
            )
            suggestion = {
                "index": item["index"],
                "seat": seat,
                "axis": axis,
                "provenance_class": "LAYOUT_SUGGESTION",
                "resolution": resolution,
                "accepted_as_observation": False,
                "input_candidates": [dict(candidate) for candidate in item["candidates"]],
                "compatible_candidates": compatible,
            }
            if len(compatible) == 1:
                suggestion["suggested_card"] = compatible[0]["card"]
            suggestions.append(suggestion)
        return suggestions

    def __call__(self, frame: Path) -> dict[str, Any]:
        frame_sha = _sha256(frame)
        try:
            raw = self.recognizer(frame, self.profile.recognizer_view())
            if not isinstance(raw, Mapping):
                raise ProfiledChallengerError("recognizer returned non-object")
            if _required_sha(raw.get("frame_sha256"), "recognizer frame sha256") != frame_sha:
                raise ProfiledChallengerError("recognizer frame hash mismatch")
            matrix, registration = self._registration(raw.get("registration"))
            deal_key, deal_identity = self._deal_key(raw.get("deal_identity"))
            board_metadata_candidate = _board_metadata_candidate(raw.get("board_metadata"))
        except ProfiledChallengerError as exc:
            return self._review(frame_sha, "FRAME_GATE_REJECTED", detail=str(exc))

        raw_cards = raw.get("cards")
        if not isinstance(raw_cards, Sequence) or isinstance(raw_cards, (str, bytes)):
            return self._review(frame_sha, "CARD_ARRAY_INVALID")
        if len(raw_cards) > MAX_CARD_OBSERVATIONS_PER_FRAME:
            return self._review(frame_sha, "CARD_ARRAY_LIMIT_EXCEEDED")

        geometry_cards: list[dict[str, Any]] = []
        ambiguous_observations: list[dict[str, Any]] = []
        channel_rejections: list[dict[str, Any]] = []
        channel_by_index: dict[int, dict[str, Any]] = {}
        for index, observation in enumerate(raw_cards):
            if not isinstance(observation, Mapping):
                channel_rejections.append({"index": index, "reason": "OBSERVATION_INVALID"})
                continue
            if "card_candidates" in observation:
                try:
                    registered_box = _registered_box(observation.get("box"), matrix)
                    screen_seat = _seat_for_registered_box(
                        registered_box,
                        self.profile.table_region,
                        self.profile.seat_dead_zone,
                    )
                    if screen_seat is None:
                        raise ProfiledChallengerError("ambiguous observation has no reliable seat")
                    seat = _logical_seat(screen_seat, self.profile.ordering_prior)
                    raw_candidates = observation.get("card_candidates")
                    if (
                        not isinstance(raw_candidates, Sequence)
                        or isinstance(raw_candidates, (str, bytes))
                        or not 2 <= len(raw_candidates) <= len(CARDS)
                    ):
                        raise ProfiledChallengerError("ambiguous candidate set must contain 2..52 cards")
                    candidates: list[dict[str, Any]] = []
                    seen_candidates: set[str] = set()
                    for raw_candidate in raw_candidates:
                        if not isinstance(raw_candidate, Mapping):
                            raise ProfiledChallengerError("ambiguous candidate must be an object")
                        if raw_candidate.get("channel_id") != self.profile.reference_channel_id:
                            raise ProfiledChallengerError("ambiguous candidate channel identity mismatch")
                        candidate_card = _card(raw_candidate.get("card"))
                        if candidate_card in seen_candidates:
                            raise ProfiledChallengerError("ambiguous candidate set contains duplicates")
                        seen_candidates.add(candidate_card)
                        candidate_confidence = _probability(
                            raw_candidate.get("confidence"),
                            "ambiguous candidate confidence",
                        )
                        if candidate_confidence >= self.profile.min_ambiguous_candidate_confidence:
                            candidates.append({
                                "card": candidate_card,
                                "confidence": candidate_confidence,
                                "channel_id": self.profile.reference_channel_id,
                            })
                    if not candidates:
                        raise ProfiledChallengerError("all ambiguous candidates are below confidence gate")
                    ambiguous_observations.append({
                        "index": index,
                        "seat": seat,
                        "box": registered_box,
                        "candidates": candidates,
                    })
                except (BridgeVideoDealContractError, ProfiledChallengerError, TypeError, ValueError) as exc:
                    channel_rejections.append({
                        "index": index,
                        "reason": "AMBIGUOUS_OBSERVATION_INVALID",
                        "detail": _bounded_detail(exc),
                    })
                continue
            try:
                rank, rank_confidence = _rank(observation.get("rank"))
                suit, suit_confidence = _suit(observation.get("suit"))
                if observation["rank"].get("channel_id") != self.profile.rank_suit_channel_id:
                    raise ProfiledChallengerError("rank channel identity mismatch")
                if observation["suit"].get("channel_id") != self.profile.rank_suit_channel_id:
                    raise ProfiledChallengerError("suit channel identity mismatch")
                reference = observation.get("reference_match")
                if not isinstance(reference, Mapping):
                    raise ProfiledChallengerError("reference channel is missing")
                if reference.get("channel_id") != self.profile.reference_channel_id:
                    raise ProfiledChallengerError("reference channel identity mismatch")
                reference_card = _card(reference.get("card"))
                reference_confidence = _probability(reference.get("confidence"), "reference confidence")
                composed = _card(rank + suit)
                if composed != reference_card:
                    channel_rejections.append({"index": index, "reason": "CHANNEL_DISAGREEMENT", "rank_suit_card": composed, "reference_card": reference_card})
                    continue
                if rank_confidence < self.profile.min_rank_confidence:
                    channel_rejections.append({"index": index, "reason": "LOW_RANK_CONFIDENCE", "confidence": rank_confidence})
                    continue
                if suit_confidence < self.profile.min_suit_confidence:
                    channel_rejections.append({"index": index, "reason": "LOW_SUIT_CONFIDENCE", "confidence": suit_confidence})
                    continue
                if reference_confidence < self.profile.min_reference_confidence:
                    channel_rejections.append({"index": index, "reason": "LOW_REFERENCE_CONFIDENCE", "confidence": reference_confidence})
                    continue
                confidence = min(rank_confidence, suit_confidence, reference_confidence, registration["inlier_ratio"])
                registered_box = _registered_box(observation.get("box"), matrix)
            except (BridgeVideoDealContractError, ProfiledChallengerError, TypeError, ValueError) as exc:
                # Canonical card validation can raise a contract error; it is a
                # rejected pixel observation, never a job-level inferred fact.
                channel_rejections.append({"index": index, "reason": "OBSERVATION_INVALID", "detail": _bounded_detail(exc)})
                continue
            channel_evidence = {
                "rank": {"value": rank, "confidence": rank_confidence, "channel_id": self.profile.rank_suit_channel_id},
                "suit": {"value": suit, "confidence": suit_confidence, "channel_id": self.profile.rank_suit_channel_id},
                "reference_match": {
                    "card": reference_card,
                    "confidence": reference_confidence,
                    "channel_id": self.profile.reference_channel_id,
                },
            }
            geometry_index = len(geometry_cards)
            channel_by_index[geometry_index] = channel_evidence
            geometry_cards.append({"card": composed, "confidence": confidence, "box": registered_box})

        try:
            frame_hands, geometry = observations_from_backend(
                {"table_region": self.profile.table_region, "cards": geometry_cards},
                min_card_confidence=self.profile.min_card_confidence,
                seat_dead_zone=self.profile.seat_dead_zone,
            )
        except (BridgeVideoDealContractError, NativeCardDetectorError, TypeError, ValueError) as exc:
            return self._review(frame_sha, "GEOMETRY_CONFLICT", detail=str(exc), channel_rejections=channel_rejections)

        frame_hands = {
            _logical_seat(screen_seat, self.profile.ordering_prior): cards
            for screen_seat, cards in frame_hands.items()
        }
        for item in geometry["accepted"]:
            item["screen_seat"] = item["seat"]
            item["seat"] = _logical_seat(item["seat"], self.profile.ordering_prior)

        layout_suggestions = self._layout_suggestions(
            ambiguous_observations,
            geometry,
            geometry_cards,
        )

        if not frame_hands:
            return self._review(
                frame_sha,
                "NO_ACCEPTED_CARD_OBSERVATIONS",
                channel_rejections=channel_rejections,
                geometry_rejections=geometry["rejected"],
                layout_suggestions=layout_suggestions,
            )

        if deal_key not in self._tracks:
            if len(self._tracks) >= MAX_TRACKS:
                return self._review(frame_sha, "TRACK_LIMIT_REACHED")
            self._tracks[deal_key] = _Track()
        track = self._tracks[deal_key]
        if frame_sha not in track.frame_sha256s and len(track.frame_sha256s) >= MAX_FRAMES_PER_TRACK:
            return self._review(frame_sha, "TRACK_FRAME_LIMIT_REACHED")
        track.frame_sha256s.add(frame_sha)
        if board_metadata_candidate is not None:
            metadata_key = json.dumps(
                {
                    key: board_metadata_candidate[key]
                    for key in ("board_number", "dealer", "vulnerability")
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            track.board_metadata_votes.setdefault(metadata_key, set()).add(frame_sha)
            track.board_metadata_values.setdefault(metadata_key, board_metadata_candidate)
        board_metadata = _board_metadata_state(track, self.profile)
        if board_metadata["status"] == "CONFLICT":
            return {
                "status": "CONFLICT",
                "hands": {},
                "confidence": 0.0,
                "conflicts": [{
                    "reason": "BOARD_METADATA_DISAGREEMENT",
                    "candidates": board_metadata["candidates"],
                }],
                "evidence": {
                    "detector_version": CHALLENGER_VERSION,
                    "profile_id": self.profile.profile_id,
                    "profile_verification_sha256": self.profile.verification_sha256,
                    "frame_sha256": frame_sha,
                    "deal_identity": deal_identity,
                    "board_metadata": board_metadata,
                    "registration": registration,
                    "canonical_promotion_allowed": False,
                },
            }

        accepted_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        for item in geometry["accepted"]:
            accepted_by_pair[(item["seat"], item["card"])] = item
        for seat, cards in frame_hands.items():
            for card in cards:
                item = accepted_by_pair[(seat, card)]
                channel = channel_by_index[item["index"]]
                vote = _Vote(float(item["confidence"]), dict(geometry_cards[item["index"]]["box"]), channel)
                pair_votes = track.votes.setdefault((seat, card), {})
                if frame_sha in pair_votes or len(pair_votes) < self.profile.min_temporal_observations:
                    pair_votes[frame_sha] = vote

        seats_by_card: dict[str, set[str]] = {}
        for (seat, card), votes in track.votes.items():
            if votes:
                seats_by_card.setdefault(card, set()).add(seat)
        conflicts = [
            {"card": card, "seats": sorted(seats), "reason": "TEMPORAL_CROSS_SEAT_DISAGREEMENT"}
            for card, seats in sorted(seats_by_card.items())
            if len(seats) > 1
        ]
        if conflicts:
            return {
                "status": "CONFLICT",
                "hands": {},
                "confidence": 0.0,
                "conflicts": conflicts,
                "evidence": {
                    "detector_version": CHALLENGER_VERSION,
                    "profile_id": self.profile.profile_id,
                    "profile_verification_sha256": self.profile.verification_sha256,
                    "frame_sha256": frame_sha,
                    "deal_identity": deal_identity,
                    "board_metadata": board_metadata,
                    "registration": registration,
                    "canonical_promotion_allowed": False,
                },
            }

        consensus_hands: dict[str, list[str]] = {seat: [] for seat in ("N", "E", "S", "W")}
        consensus_evidence: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        confidences: list[float] = []
        for (seat, card), votes in sorted(track.votes.items()):
            support = len(votes)
            if support < self.profile.min_temporal_observations:
                pending.append({"seat": seat, "card": card, "independent_frames": support})
                continue
            floor = min(vote.confidence for vote in votes.values())
            consensus_hands[seat].append(card)
            confidences.append(floor)
            consensus_evidence.append({
                "seat": seat,
                "card": card,
                "independent_frames": support,
                "frame_sha256s": sorted(votes),
                "confidence_floor": floor,
                "channels": [votes[key].channel_evidence for key in sorted(votes)],
            })

        consensus_hands = {seat: cards for seat, cards in consensus_hands.items() if cards}
        status = "PASS" if consensus_hands else "PENDING_TEMPORAL_CONSENSUS"
        return {
            "status": status,
            "hands": consensus_hands,
            "confidence": min(confidences, default=0.0),
            "evidence": {
                "detector_version": CHALLENGER_VERSION,
                "profile_id": self.profile.profile_id,
                "profile_verification_sha256": self.profile.verification_sha256,
                "template_set_sha256": self.profile.template_set_sha256,
                "frame_sha256": frame_sha,
                "deal_identity": deal_identity,
                "board_metadata": board_metadata,
                "registration": registration,
                "temporal_observations_required": self.profile.min_temporal_observations,
                "consensus": consensus_evidence,
                "pending": pending,
                "channel_rejections": channel_rejections,
                "geometry_rejections": geometry["rejected"],
                "layout_suggestions": layout_suggestions,
                "canonical_promotion_allowed": False,
            },
        }


__all__ = [
    "CARDS",
    "CHALLENGER_VERSION",
    "MAX_CARD_OBSERVATIONS_PER_FRAME",
    "MAX_PROFILE_BYTES",
    "PROFILE_SCHEMA",
    "InterfaceProfile",
    "ProfiledCardChallenger",
    "ProfiledChallengerError",
    "build_teach_profile",
    "derive_duplicate_board_metadata",
    "load_profile",
    "parse_profile",
]
