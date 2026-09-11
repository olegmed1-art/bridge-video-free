"""Template-based observer for cards visibly played on a Bridgit table.

Rank templates come from the existing Oleg-reviewed visible-hand profile. Suit
templates are derived automatically from the same reference frames: the hand
observer has already established the fixed H/C/D/S run order, so no new labels
or screenshot review are needed. The observer reports direct pixel evidence
only; reconstruction lives in ``bridgit_autonomous_deals``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from bridge_vision.bridgit_visible_hand_observer import (
    RANKS,
    SUITS,
    ObserverProfile,
    VisibleHandObserverError,
    _run_color,
    _suit_indices,
    _white_runs,
)

PLAYED_OBSERVER_VERSION = "bridgit-played-card-observer-v1"
TABLE_GEOMETRY_VERSION = "bridgit-table-seat-geometry-v1"
CARD_SCALE_POLICY_VERSION = "bridgit-live-cardback-width-scale-v1"
_TABLE_RIGHT_FRACTION = 0.75
_MIN_LAYOUT_COMPONENT_FILL = 0.35
_MIN_LAYOUT_AXIS_SPAN = 0.25
_MIN_SEAT_GEOMETRY_MARGIN = 0.20
_MIN_CARDBACK_REFERENCE_SCORE = 0.70
_MIN_TRICK_RADIUS = 0.02
# Bridgit places the West/East trick cards close to the closed-hand trays.
# Production Diana frames measure about 0.83 of the authenticated W/E half-axis;
# 0.90 admits that layout while keeping player trays outside the trick zone.
_MAX_TRICK_RADIUS = 0.90
MIN_PLAYED_CARD_WIDTH_RATIO = 0.895
MAX_PLAYED_CARD_WIDTH_RATIO = 0.990


def _pixel_runtime():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependent
        raise RuntimeError("opencv-python-headless and numpy are required") from exc
    return cv2, np


def _binary(image: Any, threshold: int):
    cv2, np = _pixel_runtime()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return (gray < threshold).astype(np.uint8) * 255


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _components(mask: Any) -> list[dict[str, Any]]:
    cv2, _ = _pixel_runtime()
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    result = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if width <= 0 or height <= 0:
            continue
        result.append(
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "area": area,
                "fill": float(area) / float(width * height),
                "center_x": float(centroids[index][0]),
                "center_y": float(centroids[index][1]),
            }
        )
    return result


def _component_evidence(image: Any, item: Mapping[str, Any]) -> str:
    x = int(item["x"])
    y = int(item["y"])
    width = int(item["width"])
    height = int(item["height"])
    return hashlib.sha256(image[y : y + height, x : x + width].tobytes()).hexdigest()


def _cardback_face(image: Any, item: Mapping[str, Any]):
    cv2, _ = _pixel_runtime()
    x = int(item["x"])
    y = int(item["y"])
    width = int(item["width"])
    height = int(item["height"])
    crop = image[y : y + height, x : x + width]
    if width >= height * 1.5:
        face = crop[:, max(0, width - round(height * 1.15)) :]
        orientation = "HORIZONTAL_STACK"
    else:
        face = crop[: min(height, round(width * 1.55)), :]
        orientation = "VERTICAL_STACK"
    if not face.size:
        raise VisibleHandObserverError("card-back face is empty")
    normalized = cv2.resize(
        cv2.cvtColor(face, cv2.COLOR_BGR2GRAY),
        (64, 64),
        interpolation=cv2.INTER_AREA,
    )
    return face, normalized, orientation


def _cardback_signature(
    image: Any,
    item: Mapping[str, Any],
    profile: ObserverProfile,
    geometry_bank: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Prove Bridgit card-back texture, not merely a blue rectangle."""

    cv2, _ = _pixel_runtime()
    width = int(item["width"])
    height = int(item["height"])
    if geometry_bank.get("profile_sha256") != profile.profile_sha256:
        raise VisibleHandObserverError("table geometry bank does not match profile")
    face, normalized, orientation = _cardback_face(image, item)
    samples = geometry_bank.get("samples", {}).get(orientation) or ()
    if not samples:
        return None
    hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
    light_fraction = float(((hsv[:, :, 1] < 90) & (hsv[:, :, 2] > 150)).mean())
    dark_fraction = float((hsv[:, :, 2] < 110).mean())
    edges = cv2.Canny(cv2.cvtColor(face, cv2.COLOR_BGR2GRAY), 50, 150)
    edge_fraction = float((edges > 0).mean())
    # Every reviewed Bridgit card back contains the high-contrast bird/oval
    # mark.  Solid blocks and ordinary blue UI panels fail these three gates.
    if light_fraction < 0.02 or dark_fraction < 0.01 or edge_fraction < 0.02:
        return None
    reference_score = max(
        float(cv2.matchTemplate(normalized, sample, cv2.TM_CCOEFF_NORMED)[0, 0])
        for sample in samples
    )
    if reference_score < _MIN_CARDBACK_REFERENCE_SCORE:
        return None
    material = {
        "version": "bridgit-cardback-structural-signature-v1",
        "profile_sha256": profile.profile_sha256,
        "profile_verification_sha256": profile.verification_sha256,
        "geometry_bank_sha256": geometry_bank["bank_sha256"],
        "orientation": orientation,
        "normalized_width": round(width / profile.width, 8),
        "normalized_height": round(height / profile.height, 8),
        "light_fraction": round(light_fraction, 6),
        "dark_fraction": round(dark_fraction, 6),
        "edge_fraction": round(edge_fraction, 6),
        "reference_score": round(reference_score, 6),
        "face_pixel_sha256": hashlib.sha256(face.tobytes()).hexdigest(),
    }
    return {**material, "signature_sha256": _canonical_hash(material)}


def _choose_unique_component(
    items: Sequence[Mapping[str, Any]], *, field: str
) -> Mapping[str, Any]:
    ordered = sorted(
        items, key=lambda item: (-int(item["area"]), int(item["x"]), int(item["y"]))
    )
    if not ordered:
        raise VisibleHandObserverError(f"{field} seat landmark is missing")
    if len(ordered) > 1 and int(ordered[1]["area"]) >= int(ordered[0]["area"]) * 0.80:
        raise VisibleHandObserverError(f"{field} seat landmark is ambiguous")
    return ordered[0]


def _raw_blue_candidates(image: Any, profile: ObserverProfile) -> list[dict[str, Any]]:
    cv2, np = _pixel_runtime()
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([85, 35, 70], dtype=np.uint8),
        np.array([135, 220, 245], dtype=np.uint8),
    )
    pixels = profile.width * profile.height
    return [
        item
        for item in _components(mask)
        if (
            item["area"] >= pixels * 0.002
            and item["area"] <= pixels * 0.10
            and item["fill"] >= _MIN_LAYOUT_COMPONENT_FILL
            and item["center_x"] < profile.width * _TABLE_RIGHT_FRACTION
        )
    ]


def _classify_blue_candidates(
    candidates: Sequence[Mapping[str, Any]], profile: ObserverProfile
) -> dict[str, list[Mapping[str, Any]]]:
    return {
        "N": [
            item
            for item in candidates
            if item["width"] >= item["height"] * 1.50
            and item["center_y"] < profile.height * 0.35
            and profile.width * 0.15 < item["center_x"] < profile.width * 0.60
        ],
        "W": [
            item
            for item in candidates
            if item["height"] >= item["width"] * 1.50
            and item["center_x"] < profile.width * 0.20
            and profile.height * 0.20 < item["center_y"] < profile.height * 0.80
        ],
        "E": [
            item
            for item in candidates
            if item["height"] >= item["width"] * 1.50
            and profile.width * 0.55
            < item["center_x"]
            < profile.width * _TABLE_RIGHT_FRACTION
            and profile.height * 0.20 < item["center_y"] < profile.height * 0.80
        ],
    }


def build_table_geometry_bank(
    profile: ObserverProfile, reference_images: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind closed-seat texture to the self-hashed reviewed references."""

    if set(reference_images) != set(profile.references):
        raise VisibleHandObserverError("reference images do not match profile")
    samples: dict[str, list[Any]] = {
        "HORIZONTAL_STACK": [],
        "VERTICAL_STACK": [],
    }
    cv2, _ = _pixel_runtime()
    sample_records = []
    for reference_id, image in sorted(reference_images.items()):
        if tuple(image.shape[:2]) != (profile.height, profile.width):
            raise VisibleHandObserverError("reference dimensions do not match profile")
        by_seat = _classify_blue_candidates(
            _raw_blue_candidates(image, profile), profile
        )
        for seat in ("W", "E"):
            try:
                chosen = _choose_unique_component(by_seat[seat], field=seat)
            except VisibleHandObserverError:
                continue
            face, normalized, orientation = _cardback_face(image, chosen)
            hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
            light_fraction = float(((hsv[:, :, 1] < 90) & (hsv[:, :, 2] > 150)).mean())
            dark_fraction = float((hsv[:, :, 2] < 110).mean())
            edges = cv2.Canny(cv2.cvtColor(face, cv2.COLOR_BGR2GRAY), 50, 150)
            edge_fraction = float((edges > 0).mean())
            if (
                light_fraction < 0.02
                or dark_fraction < 0.01
                or edge_fraction < 0.02
            ):
                continue
            digest = hashlib.sha256(normalized.tobytes()).hexdigest()
            if all(
                hashlib.sha256(sample.tobytes()).hexdigest() != digest
                for sample in samples[orientation]
            ):
                samples[orientation].append(normalized)
                sample_records.append(
                    {
                        "reference_id": reference_id,
                        "seat": seat,
                        "orientation": orientation,
                        "normalized_pixel_sha256": digest,
                    }
                )
    if not samples["VERTICAL_STACK"]:
        raise VisibleHandObserverError(
            "reviewed references do not prove vertical Bridgit card backs"
        )
    material = {
        "version": "bridgit-table-geometry-bank-v1",
        "profile_sha256": profile.profile_sha256,
        "profile_verification_sha256": profile.verification_sha256,
        "reference_samples": sample_records,
    }
    return {
        **material,
        "bank_sha256": _canonical_hash(material),
        "samples": {key: tuple(value) for key, value in samples.items()},
    }


def build_card_scale_policy(
    profile: ObserverProfile, geometry_bank: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind accepted played-card scale to one verified profile and back bank."""

    if geometry_bank.get("profile_sha256") != profile.profile_sha256:
        raise VisibleHandObserverError("table geometry bank does not match profile")
    material = {
        "version": CARD_SCALE_POLICY_VERSION,
        "profile_sha256": profile.profile_sha256,
        "geometry_bank_sha256": geometry_bank["bank_sha256"],
        "width_ratio_minimum": MIN_PLAYED_CARD_WIDTH_RATIO,
        "width_ratio_maximum": MAX_PLAYED_CARD_WIDTH_RATIO,
        "reference": "MEAN_LIVE_W_E_REVIEWED_CARDBACK_WIDTH",
    }
    return {**material, "policy_sha256": _canonical_hash(material)}


def _blue_seat_landmarks(
    image: Any, profile: ObserverProfile, geometry_bank: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Find reference-bound closed-hand trays independently of chrome."""

    candidates = []
    for item in _raw_blue_candidates(image, profile):
        signature = _cardback_signature(image, item, profile, geometry_bank)
        if signature is not None:
            candidates.append({**item, "structural_signature": signature})
    by_seat = _classify_blue_candidates(candidates, profile)
    result = {}
    for seat, items in by_seat.items():
        try:
            chosen = _choose_unique_component(items, field=seat)
        except VisibleHandObserverError:
            continue
        result[seat] = {
            "x": float(chosen["center_x"]),
            "y": float(chosen["center_y"]),
            "source": "CLOSED_HAND_TRAY",
            "region": {key: int(chosen[key]) for key in ("x", "y", "width", "height")},
            "pixel_sha256": _component_evidence(image, chosen),
            "structural_signature": chosen["structural_signature"],
        }
    return result


def _verified_visible_hand_landmark(
    visible_hand_cards: Sequence[Mapping[str, Any]],
    profile: ObserverProfile,
    seat: str,
    lateral_center_x: float,
) -> dict[str, Any] | None:
    """Bind an N/S axis to already template-proven HAND card evidence."""

    items = []
    for item in visible_hand_cards:
        if item.get("seat") != seat or item.get("source") != "HAND":
            continue
        region = item.get("region")
        evidence_sha = str(item.get("evidence_pixel_sha256") or "")
        if not isinstance(region, Mapping) or len(evidence_sha) != 64:
            continue
        try:
            left = int(region["x"])
            top = int(region["y"])
            width = int(region["width"])
            height = int(region["height"])
            confidence = float(item["confidence"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (
            confidence < profile.min_rank_score
            or width <= 0
            or height <= 0
            or not 0 <= left < profile.width
            or not 0 <= top < profile.height
            or left + width > profile.width
            or top + height > profile.height
        ):
            continue
        items.append(
            {
                "card": str(item.get("card") or ""),
                "evidence_pixel_sha256": evidence_sha,
                "region": {"x": left, "y": top, "width": width, "height": height},
                "center_y": top + height / 2.0,
            }
        )
    if len(items) < 2:
        return None
    center_ys = sorted(float(item["center_y"]) for item in items)
    center_y = center_ys[len(center_ys) // 2]
    # ``observe_frame`` hashes from four pixels above the glyph through
    # ``observed_y + 52``; its evidence-region center is therefore row + 24.
    expected_y = profile.rows[seat][0] + 24.0
    if abs(center_y - expected_y) > max(12.0, profile.rank_height * 1.5):
        return None
    left = min(item["region"]["x"] for item in items)
    top = min(item["region"]["y"] for item in items)
    right = max(item["region"]["x"] + item["region"]["width"] for item in items)
    bottom = max(item["region"]["y"] + item["region"]["height"] for item in items)
    evidence_material = {
        "version": "bridgit-verified-hand-seat-landmark-v1",
        "profile_sha256": profile.profile_sha256,
        "seat": seat,
        "cards": sorted(
            (
                {
                    "card": item["card"],
                    "evidence_pixel_sha256": item["evidence_pixel_sha256"],
                    "region": item["region"],
                }
                for item in items
            ),
            key=lambda item: (item["card"], item["evidence_pixel_sha256"]),
        ),
    }
    return {
        # The horizontal center of remaining cards moves as a hand is played
        # down.  Use only their proven vertical row, aligned to the invariant
        # W/E lateral axis, so holdings can never rotate ownership sectors.
        "x": lateral_center_x,
        "y": center_y,
        "source": "VERIFIED_VISIBLE_HAND_ROW_Y",
        "region": {"x": left, "y": top, "width": right - left, "height": bottom - top},
        "pixel_sha256": _canonical_hash(evidence_material),
        "structural_signature": {
            **evidence_material,
            "signature_sha256": _canonical_hash(evidence_material),
        },
    }


def detect_table_geometry(
    image: Any,
    profile: ObserverProfile,
    *,
    geometry_bank: Mapping[str, Any],
    visible_hand_cards: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Prove four seat axes from table content, independently of UI chrome."""

    if tuple(image.shape[:2]) != (profile.height, profile.width):
        raise VisibleHandObserverError("frame dimensions do not match profile")
    anchors = _blue_seat_landmarks(image, profile, geometry_bank)
    if "W" not in anchors or "E" not in anchors:
        raise VisibleHandObserverError("verified W/E seat landmarks are incomplete")
    lateral_center_x = (float(anchors["W"]["x"]) + float(anchors["E"]["x"])) / 2
    for seat in ("N", "S"):
        visible = _verified_visible_hand_landmark(
            visible_hand_cards, profile, seat, lateral_center_x
        )
        if visible is not None:
            anchors[seat] = visible
    missing = [seat for seat in ("N", "E", "S", "W") if seat not in anchors]
    if missing:
        raise VisibleHandObserverError(
            "table seat landmarks are incomplete: " + ",".join(missing)
        )

    north, east, south, west = (anchors[seat] for seat in ("N", "E", "S", "W"))
    # A closed hand's bounding box shrinks as cards are played.  Its center is
    # therefore evidence about card count, not a stable seat coordinate.  Use
    # W/E only for the invariant lateral span; align N/S to that axis and use
    # the two verified vertical rows for the table center.
    north["x"] = lateral_center_x
    south["x"] = lateral_center_x
    horizontal = ((float(east["x"]) - float(west["x"])) / 2.0, 0.0)
    vertical = (0.0, (float(south["y"]) - float(north["y"])) / 2.0)
    horizontal_span = math.hypot(*horizontal) * 2.0
    vertical_span = math.hypot(*vertical) * 2.0
    if (
        horizontal_span < profile.width * _MIN_LAYOUT_AXIS_SPAN
        or vertical_span < profile.height * _MIN_LAYOUT_AXIS_SPAN
    ):
        raise VisibleHandObserverError("table seat landmark span is too small")
    center = (
        lateral_center_x,
        (float(north["y"]) + float(south["y"])) / 2.0,
    )
    west_east_y_gap = abs(float(west["y"]) - float(east["y"]))
    west_east_y = (float(west["y"]) + float(east["y"])) / 2.0
    if west_east_y_gap > vertical_span * 0.20 or not float(
        north["y"]
    ) < west_east_y < float(south["y"]):
        raise VisibleHandObserverError("W/E landmarks do not agree with the table")
    determinant = horizontal[0] * vertical[1] - horizontal[1] * vertical[0]
    if abs(determinant) < horizontal_span * vertical_span * 0.10:
        raise VisibleHandObserverError("table seat axes are degenerate")
    west_card_width = float(west["region"]["width"])
    east_card_width = float(east["region"]["width"])
    card_width_reference = (west_card_width + east_card_width) / 2.0
    if (
        card_width_reference <= 0
        or abs(west_card_width - east_card_width) / card_width_reference > 0.12
    ):
        raise VisibleHandObserverError("W/E card-back widths do not agree")
    scale_policy = build_card_scale_policy(profile, geometry_bank)
    normalized_anchors = {
        seat: {
            "x": round(float(item["x"]) / profile.width, 8),
            "y": round(float(item["y"]) / profile.height, 8),
            "source": item["source"],
            "region": {
                "x": round(item["region"]["x"] / profile.width, 8),
                "y": round(item["region"]["y"] / profile.height, 8),
                "width": round(item["region"]["width"] / profile.width, 8),
                "height": round(item["region"]["height"] / profile.height, 8),
            },
            "pixel_sha256": item["pixel_sha256"],
            "structural_signature_sha256": item["structural_signature"][
                "signature_sha256"
            ],
        }
        for seat, item in sorted(anchors.items())
    }
    transform_material = {
        "version": TABLE_GEOMETRY_VERSION,
        "coordinate_space": "NORMALIZED_PROFILE_FRAME",
        "anchors": {
            seat: {key: item[key] for key in ("x", "y", "source")}
            for seat, item in normalized_anchors.items()
        },
        "center": {
            "x": round(center[0] / profile.width, 8),
            "y": round(center[1] / profile.height, 8),
        },
        "horizontal_half_axis": {
            "x": round(horizontal[0] / profile.width, 8),
            "y": round(horizontal[1] / profile.height, 8),
        },
        "vertical_half_axis": {
            "x": round(vertical[0] / profile.width, 8),
            "y": round(vertical[1] / profile.height, 8),
        },
    }
    evidence_material = {
        **transform_material,
        "geometry_bank_sha256": geometry_bank["bank_sha256"],
        "card_scale_policy": scale_policy,
        "anchors": normalized_anchors,
        "anchor_pixel_sha256s": {
            seat: item["pixel_sha256"] for seat, item in normalized_anchors.items()
        },
        "anchor_structural_signature_sha256s": {
            seat: item["structural_signature_sha256"]
            for seat, item in normalized_anchors.items()
        },
    }
    return {
        **evidence_material,
        "transform_sha256": _canonical_hash(transform_material),
        "geometry_sha256": _canonical_hash(evidence_material),
        "_pixel_center": center,
        "_pixel_horizontal_half_axis": horizontal,
        "_pixel_vertical_half_axis": vertical,
        "_pixel_card_width_reference": card_width_reference,
        "_card_scale_policy_sha256": scale_policy["policy_sha256"],
    }


def build_suit_bank(
    profile: ObserverProfile, reference_images: Mapping[str, Any]
) -> dict[str, tuple[Any, ...]]:
    """Derive H/C/D/S glyph samples from already reviewed hand references."""

    if set(reference_images) != set(profile.references):
        raise VisibleHandObserverError("reference images do not match profile")
    bank: dict[str, list[Any]] = {suit: [] for suit in SUITS}
    owners: dict[str, str] = {}
    for items in profile.rank_templates.values():
        for reference_id, x, y in items:
            image = reference_images[reference_id]
            if tuple(image.shape[:2]) != (profile.height, profile.width):
                raise VisibleHandObserverError(
                    "reference dimensions do not match profile"
                )
            seat = min(profile.rows, key=lambda key: abs(profile.rows[key][0] - y))
            row_y, x_min, x_max = profile.rows[seat]
            # Transferred profiles can retain the reviewed glyph's top-left
            # while the white-run row is calibrated a few pixels lower.  The
            # glyph still belongs to that row when both y coordinates overlap
            # within one complete rank-height; N/S are hundreds of pixels
            # apart, so this does not make the owning hand ambiguous.
            if abs(row_y - y) > max(8, profile.rank_height):
                raise VisibleHandObserverError(
                    "rank template is not bound to a reviewed hand row"
                )
            # Rank templates are bound to the reviewed glyph's own top-left.
            # A transferred profile may recalibrate the live hand scan row a
            # few pixels lower (for example after a video-height change), but
            # using that live row here can change the fan-overlap geometry and
            # make the reviewed template appear outside its white run.  Derive
            # the suit at the immutable reviewed template row instead.
            runs = _white_runs(image, y, x_min, x_max, profile)
            colors = [_run_color(image, y, run, profile) for run in runs]
            suit_indices = _suit_indices(colors)
            matches = [
                index
                for index, (run_x, width) in enumerate(runs)
                if run_x - 5 <= x < run_x + width
            ]
            if suit_indices is None or len(matches) != 1:
                raise VisibleHandObserverError(
                    "rank template suit cannot be derived from reviewed geometry"
                )
            suit = SUITS[suit_indices[matches[0]]]
            x0 = max(0, x - 2)
            y0 = max(0, y + profile.rank_height)
            x1 = min(profile.width, x + profile.rank_width + 4)
            y1 = min(profile.height, y + profile.rank_height + 28)
            sample = _binary(image[y0:y1, x0:x1], profile.binary_threshold)
            digest = hashlib.sha256(sample.tobytes()).hexdigest()
            previous = owners.get(digest)
            if previous is not None and previous != suit:
                raise VisibleHandObserverError(
                    "one derived suit template has conflicting labels"
                )
            owners[digest] = suit
            if all(not (sample == existing).all() for existing in bank[suit]):
                bank[suit].append(sample)
    if any(not samples for samples in bank.values()):
        raise VisibleHandObserverError("derived suit bank does not cover H/C/D/S")
    return {suit: tuple(samples) for suit, samples in bank.items()}


def _white_card_rectangles(image: Any, profile: ObserverProfile):
    cv2, np = _pixel_runtime()
    top = min(profile.rows["N"][0] + 2 * profile.rank_height, profile.height - 1)
    bottom = max(profile.rows["S"][0] - profile.rank_height, top + 1)
    # Bridgit keeps a white player-tray card at the extreme lower-left.  Its
    # dimensions match a played card, but its left edge is outside the green
    # table.  Crop that fixed UI gutter before connected-component detection;
    # the real West trick card remains inside this boundary.
    left = max(profile.rank_width * 5, round(profile.width * 0.055))
    right = min(profile.width, round(profile.width * 0.75))
    table = image[top:bottom, left:right]
    hsv = cv2.cvtColor(table, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(
        hsv,
        np.array([0, 0, 165], dtype=np.uint8),
        np.array([180, 90, 255], dtype=np.uint8),
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(white)
    # Played cards can be resized independently of the surrounding chrome at
    # responsive breakpoints.  Keep this deliberately broad; rank/suit and
    # live-seat geometry gates below still have to prove every emitted claim.
    minimum_width = max(24, round(profile.rank_width * 3.5))
    maximum_width = min(320, round(profile.rank_width * 12.0))
    minimum_height = max(36, round(profile.rank_height * 4.5))
    maximum_height = min(360, round(profile.rank_height * 15.0))
    rectangles = []
    for x, relative_y, width, height, area in stats[1:count]:
        x = int(x) + left
        y = int(relative_y) + top
        fill = float(area) / float(width * height)
        ratio = float(height) / float(width)
        if (
            minimum_width <= width <= maximum_width
            and minimum_height <= height <= maximum_height
            and 1.15 <= ratio <= 1.75
            and fill >= 0.55
        ):
            rectangles.append((int(x), y, int(width), int(height), fill))
    return rectangles


def _rank(
    image: Any,
    rectangle: tuple[int, int, int, int, float],
    rank_bank: Mapping[str, Sequence[Any]],
    profile: ObserverProfile,
):
    cv2, _ = _pixel_runtime()
    x, y, width, _, _ = rectangle
    search = _binary(
        image[
            y : y + profile.rank_height + 14,
            x : x + min(width, profile.rank_width + 18),
        ],
        profile.binary_threshold,
    )
    matches = []
    for rank in RANKS:
        samples = rank_bank.get(rank) or ()
        if not samples:
            raise VisibleHandObserverError("rank bank does not cover A through 2")
        score = -1.0
        position = (0, 0)
        for sample in samples:
            matrix = cv2.matchTemplate(search, sample, cv2.TM_CCOEFF_NORMED)
            _, current, _, location = cv2.minMaxLoc(matrix)
            if current > score:
                score = float(current)
                position = location
        matches.append((score, rank, position))
    matches.sort(reverse=True)
    best, second = matches[:2]
    margin = best[0] - second[0]
    if best[0] < profile.min_rank_score or margin < profile.min_rank_margin:
        return None
    return best[1], x + best[2][0], y + best[2][1], best[0], margin


def _suit_color(image: Any, x: int, y: int, profile: ObserverProfile) -> str:
    crop = image[
        y + profile.rank_height : y + profile.rank_height + 30,
        max(0, x - 2) : x + profile.rank_width + 6,
    ]
    blue = crop[:, :, 0]
    green = crop[:, :, 1]
    red = crop[:, :, 2]
    red_pixels = ((red > 130) & (red > green * 1.35) & (red > blue * 1.35)).sum()
    dark_pixels = ((red < 120) & (green < 120) & (blue < 120)).sum()
    return "R" if red_pixels > max(2, dark_pixels * profile.red_dark_ratio) else "B"


def _suit(
    image: Any,
    rank_x: int,
    rank_y: int,
    suit_bank: Mapping[str, Sequence[Any]],
    profile: ObserverProfile,
):
    cv2, _ = _pixel_runtime()
    x0 = max(0, rank_x - 4)
    y0 = max(0, rank_y + profile.rank_height - 4)
    search = _binary(
        image[
            y0 : rank_y + profile.rank_height + 34,
            x0 : rank_x + profile.rank_width + 10,
        ],
        profile.binary_threshold,
    )
    color = _suit_color(image, rank_x, rank_y, profile)
    allowed = "HD" if color == "R" else "CS"
    matches = []
    for suit in allowed:
        samples = suit_bank.get(suit) or ()
        if not samples:
            raise VisibleHandObserverError("suit bank does not cover H/C/D/S")
        score = max(
            float(cv2.matchTemplate(search, sample, cv2.TM_CCOEFF_NORMED).max())
            for sample in samples
        )
        matches.append((score, suit))
    matches.sort(reverse=True)
    margin = matches[0][0] - matches[1][0]
    if matches[0][0] < 0.82 or margin < 0.06:
        return None
    return matches[0][1], matches[0][0], margin


def _seat(rectangle: tuple[int, int, int, int, float], geometry: Mapping[str, Any]):
    """Assign ownership in live table-axis coordinates, not raw screen pixels."""

    x, y, width, height, _ = rectangle
    center_x, center_y = geometry["_pixel_center"]
    horizontal_x, horizontal_y = geometry["_pixel_horizontal_half_axis"]
    vertical_x, vertical_y = geometry["_pixel_vertical_half_axis"]
    delta_x = x + width / 2 - center_x
    delta_y = y + height / 2 - center_y
    determinant = horizontal_x * vertical_y - horizontal_y * vertical_x
    if abs(determinant) < 1e-9:
        raise VisibleHandObserverError("table seat axes are degenerate")
    horizontal_coordinate = (delta_x * vertical_y - delta_y * vertical_x) / determinant
    vertical_coordinate = (
        horizontal_x * delta_y - horizontal_y * delta_x
    ) / determinant
    radius = math.hypot(horizontal_coordinate, vertical_coordinate)
    if not _MIN_TRICK_RADIUS <= radius <= _MAX_TRICK_RADIUS:
        return None
    if abs(horizontal_coordinate) > abs(vertical_coordinate):
        seat = "E" if horizontal_coordinate > 0 else "W"
    else:
        seat = "S" if vertical_coordinate > 0 else "N"
    dominance = abs(abs(horizontal_coordinate) - abs(vertical_coordinate)) / max(
        1e-9, radius
    )
    return seat, dominance, horizontal_coordinate, vertical_coordinate


def _card_scale(
    rectangle: tuple[int, int, int, int, float], geometry: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Prove played-card scale against authenticated live W/E card backs."""

    width = int(rectangle[2])
    reference = float(geometry["_pixel_card_width_reference"])
    ratio = width / reference
    if not MIN_PLAYED_CARD_WIDTH_RATIO <= ratio <= MAX_PLAYED_CARD_WIDTH_RATIO:
        return None
    material = {
        "version": CARD_SCALE_POLICY_VERSION,
        "card_scale_policy_sha256": geometry["_card_scale_policy_sha256"],
        "played_card_width_pixels": width,
        "live_cardback_width_pixels": round(reference, 6),
        "played_card_width_ratio": round(ratio, 6),
    }
    return {**material, "measurement_sha256": _canonical_hash(material)}


def observe_played_cards(
    image: Any,
    rank_bank: Mapping[str, Sequence[Any]],
    suit_bank: Mapping[str, Sequence[Any]],
    profile: ObserverProfile,
    *,
    geometry_bank: Mapping[str, Any],
    visible_hand_cards: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Observe the current trick, assigning ownership by verified table geometry."""

    if tuple(image.shape[:2]) != (profile.height, profile.width):
        raise VisibleHandObserverError("frame dimensions do not match profile")
    try:
        geometry = detect_table_geometry(
            image,
            profile,
            geometry_bank=geometry_bank,
            visible_hand_cards=visible_hand_cards,
        )
    except VisibleHandObserverError as exc:
        return {
            "status": "REVIEW",
            "cards": [],
            "rejected": [{"reason": "SEAT_LAYOUT_UNPROVEN", "detail": str(exc)}],
            "conflicts": [],
            "layout_geometry": None,
            "observer_version": PLAYED_OBSERVER_VERSION,
        }
    candidates = []
    rejected = []
    for rectangle in _white_card_rectangles(image, profile):
        card_scale = _card_scale(rectangle, geometry)
        if card_scale is None:
            rejected.append(
                {"region": rectangle[:4], "reason": "CARD_SCALE_UNVERIFIED"}
            )
            continue
        rank = _rank(image, rectangle, rank_bank, profile)
        if rank is None:
            rejected.append({"region": rectangle[:4], "reason": "RANK_AMBIGUOUS"})
            continue
        rank_value, rank_x, rank_y, rank_score, rank_margin = rank
        suit = _suit(image, rank_x, rank_y, suit_bank, profile)
        if suit is None:
            rejected.append({"region": rectangle[:4], "reason": "SUIT_AMBIGUOUS"})
            continue
        suit_value, suit_score, suit_margin = suit
        ownership = _seat(rectangle, geometry)
        if ownership is None:
            rejected.append(
                {"region": rectangle[:4], "reason": "SEAT_OUTSIDE_TRICK_ZONE"}
            )
            continue
        seat, geometry_margin, horizontal_coordinate, vertical_coordinate = ownership
        if geometry_margin < _MIN_SEAT_GEOMETRY_MARGIN:
            rejected.append({"region": rectangle[:4], "reason": "SEAT_AMBIGUOUS"})
            continue
        x, y, width, height, fill = rectangle
        evidence = image[y : y + height, x : x + width]
        candidates.append(
            {
                "card": rank_value + suit_value,
                "seat": seat,
                "source": "PLAYED",
                "confidence": round(min(rank_score, suit_score), 6),
                "rank_score": round(rank_score, 6),
                "suit_score": round(suit_score, 6),
                "card_fill": round(fill, 6),
                "rank_margin": round(rank_margin, 6),
                "suit_margin": round(suit_margin, 6),
                "geometry_margin": round(geometry_margin, 6),
                "seat_coordinates": {
                    "horizontal": round(horizontal_coordinate, 6),
                    "vertical": round(vertical_coordinate, 6),
                },
                "layout_geometry_sha256": geometry["geometry_sha256"],
                "layout_transform_sha256": geometry["transform_sha256"],
                "card_scale_policy_sha256": card_scale[
                    "card_scale_policy_sha256"
                ],
                "card_scale_measurement_sha256": card_scale[
                    "measurement_sha256"
                ],
                "played_card_width_ratio": card_scale["played_card_width_ratio"],
                "played_card_width_pixels": card_scale[
                    "played_card_width_pixels"
                ],
                "live_cardback_width_pixels": card_scale[
                    "live_cardback_width_pixels"
                ],
                "card_scale_policy_version": card_scale["version"],
                "evidence_pixel_sha256": hashlib.sha256(evidence.tobytes()).hexdigest(),
                "region": {"x": x, "y": y, "width": width, "height": height},
            }
        )

    by_seat: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        by_seat.setdefault(item["seat"], []).append(item)
    conflicts = [seat for seat, cards in by_seat.items() if len(cards) > 1]
    if conflicts:
        return {
            "status": "CONFLICT",
            "cards": [],
            "rejected": rejected,
            "conflicts": [
                {"seat": seat, "reason": "MULTIPLE_CURRENT_TRICK_CARDS"}
                for seat in sorted(conflicts)
            ],
            "layout_geometry": {
                key: value for key, value in geometry.items() if not key.startswith("_")
            },
        }
    owners: dict[str, set[str]] = {}
    for item in candidates:
        owners.setdefault(item["card"], set()).add(item["seat"])
    repeated = [card for card, seats in owners.items() if len(seats) > 1]
    if repeated:
        return {
            "status": "CONFLICT",
            "cards": [],
            "rejected": rejected,
            "conflicts": [
                {"card": card, "reason": "CROSS_SEAT_CARD_CONFLICT"}
                for card in sorted(repeated)
            ],
            "layout_geometry": {
                key: value for key, value in geometry.items() if not key.startswith("_")
            },
        }
    return {
        "status": "SHADOW_PLAYED_CARDS" if candidates else "REVIEW",
        "cards": sorted(candidates, key=lambda item: item["seat"]),
        "rejected": rejected,
        "conflicts": [],
        "layout_geometry": {
            key: value for key, value in geometry.items() if not key.startswith("_")
        },
        "observer_version": PLAYED_OBSERVER_VERSION,
    }


__all__ = [
    "CARD_SCALE_POLICY_VERSION",
    "MAX_PLAYED_CARD_WIDTH_RATIO",
    "MIN_PLAYED_CARD_WIDTH_RATIO",
    "PLAYED_OBSERVER_VERSION",
    "TABLE_GEOMETRY_VERSION",
    "build_card_scale_policy",
    "build_suit_bank",
    "build_table_geometry_bank",
    "detect_table_geometry",
    "observe_played_cards",
]
