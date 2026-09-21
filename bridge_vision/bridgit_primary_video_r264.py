"""Production adapter: old rank-layout recognizer first, Gambler recovery second.

The primary path is the proven geometry/ordered-DP Bridgit recognizer with the
hash-bound original Gambler classic deck as rank authority. This module never
writes SCHOOL CANON. A wholly absent fourth hand may be reconstructed only as
an explicitly marked deck complement of three complete visual hands.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from bridge_contracts.video_deal_r264 import canonicalize_video_deal
from bridge_vision import bridgit_rank_layout_r264 as rank_layout
from bridge_vision.bridgit_gambler_rank_layout_r264 import (
    derive_original_asset_reference,
    recognize_frames_with_original_gambler_deck,
)
from bridge_vision.gambler_classic_reference import (
    GamblerClassicReferenceError,
    load_sprite,
    variant_candidates_for_card_size,
)
from bridge_vision.bridgit_event_frame_selector import EventFrameSelector, bridge_layout_regions, frame_signature
from bridge_vision.gambler_reference_authority import (
    GamblerReferenceAuthorityError,
    pinned_sprite_sha256,
    variant_for_pinned_sprite_sha256,
)
from bridge_vision.bridgit_primary_compat import (MAX_VERTICAL_PADDING_PX, horizontal_geometry_detail, native_gambler_geometry, register_same_width_vertical_padding)

PRIMARY_VIDEO_VERSION = "bridgit-primary-video-gambler-v2"
PRIMARY_VIDEO_TEMPORAL_COMPLEMENT_VERSION = "bridgit-primary-video-gambler-v3"
PRIMARY_VIDEO_PARTIAL_OBSERVATION_VERSION = "bridgit-primary-video-gambler-v4"
DEFAULT_SCAN_MS = 3000
DEFAULT_ATTEMPT_GAP_MS = 15000
ALLOW_FOURTH_HAND_DERIVATION = False
ALLOW_PARTIAL_OBSERVATIONS = False
MAX_TEMPORAL_PAIR_GAP_MS = 10_000


class PrimaryVideoRecognitionError(ValueError):
    """The bounded primary-video pass cannot continue safely."""


def _runtime_version() -> str:
    if ALLOW_PARTIAL_OBSERVATIONS:
        return PRIMARY_VIDEO_PARTIAL_OBSERVATION_VERSION
    return (
        PRIMARY_VIDEO_TEMPORAL_COMPLEMENT_VERSION
        if ALLOW_FOURTH_HAND_DERIVATION and rank_layout.TEMPORAL_CARD_UNION_ENABLED
        else PRIMARY_VIDEO_VERSION
    )


def resolve_original_gambler_asset(
    asset_root: Path,
    *,
    verified_card_width_px: float,
    verified_card_height_px: float,
) -> tuple[int, Path, str]:
    """Resolve one of the eight pinned native Gambler classic sprites.

    Variants are tried by deterministic distance from the measured card scale.
    Every fallback remains bound to its own fixed SHA-256; arbitrary decks are
    never accepted. Template synthesis later rescales the pinned source to the
    measured dimensions when they are not native.
    """
    root = Path(asset_root)
    for _, variant in variant_candidates_for_card_size(
        verified_card_width_px, verified_card_height_px
    ):
        expected_sha = pinned_sprite_sha256(variant)
        candidates = (root / f"all-v{variant}.png", root / str(variant) / "all.png")
        existing = [path for path in candidates if path.is_file()]
        if not existing:
            continue
        selected = existing[0]
        try:
            sprite = load_sprite(
                selected, expected_sha256=expected_sha, expected_variant=variant
            )
        except GamblerClassicReferenceError as exc:
            raise PrimaryVideoRecognitionError(
                f"pinned Gambler classic variant {variant} failed integrity validation"
            ) from exc
        if any(
            path != selected
            and path.is_file()
            and _sha256_file(path) != sprite.sprite_sha256
            for path in candidates
        ):
            raise PrimaryVideoRecognitionError(
                f"conflicting Gambler classic variant {variant} assets"
            )
        return variant, selected, expected_sha
    raise PrimaryVideoRecognitionError("no pinned Gambler classic sprite is available")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_at(capture: Any, timestamp_ms: int) -> Any | None:
    cv2, _ = rank_layout._pixel_runtime()
    capture.set(cv2.CAP_PROP_POS_MSEC, float(max(0, timestamp_ms)))
    ok, frame = capture.read()
    return frame if ok else None


def _write_png(path: Path, image: Any) -> str:
    cv2, _ = rank_layout._pixel_runtime()
    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise PrimaryVideoRecognitionError("cannot encode recognition frame")
    payload = encoded.tobytes()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _registered_candidate(image: Any, profile: rank_layout.BridgitRankLayoutProfile):
    height, width = image.shape[:2]
    if (width, height) == (profile.width, profile.height):
        return image
    if width == profile.width and profile.height < height <= profile.height + MAX_VERTICAL_PADDING_PX:
        registered, _ = register_same_width_vertical_padding(image, profile)
        return registered
    return None


def _full_geometry_gate(image: Any, bank: Mapping[str, Any], profile: rank_layout.BridgitRankLayoutProfile):
    registered = _registered_candidate(image, profile)
    if registered is None:
        return None
    lengths: dict[str, dict[str, int]] = {}
    for seat in ("N", "S"):
        lengths[seat], anchors, _ = horizontal_geometry_detail(registered, seat, profile)
        total = sum(lengths[seat].values())
        if total not in {0, 13} or (total == 13 and not anchors):
            return None
    side = rank_layout._side_lengths([registered], bank, profile)
    lengths["W"], lengths["E"] = side["W"], side["E"]
    seat_totals = {seat: sum(lengths[seat].values()) for seat in rank_layout.SEATS}
    absent = [seat for seat, total in seat_totals.items() if total == 0]
    complete = [seat for seat, total in seat_totals.items() if total == 13]
    three_hand = len(complete) == 3 and len(absent) == 1
    if not (len(complete) == 4 or (ALLOW_FOURTH_HAND_DERIVATION and three_hand)):
        return None
    if any(
        sum(lengths[seat][suit] for seat in rank_layout.SEATS) > 13
        or (
            not absent
            and sum(lengths[seat][suit] for seat in rank_layout.SEATS) != 13
        )
        for suit in rank_layout.SUITS
    ):
        return None
    return lengths


def _partial_geometry_gate(
    image: Any,
    bank: Mapping[str, Any],
    profile: rank_layout.BridgitRankLayoutProfile,
) -> dict[str, Any] | None:
    """Locate any bounded visible hand without claiming a complete deal.

    The legacy full-layout gate intentionally remains unchanged.  r26.6 uses
    this additive gate to retain partial visual evidence before completeness is
    evaluated.  Geometry supplies slot locations only; it never invents ranks.
    """

    registered = _registered_candidate(image, profile)
    if registered is None:
        return None
    lengths: dict[str, dict[str, int]] = {}
    anchors = {seat: dict(values) for seat, values in profile.anchors.items()}
    horizontal_steps: dict[str, float] = {}
    for seat in ("N", "S"):
        seat_lengths, detected, step = horizontal_geometry_detail(
            registered, seat, profile
        )
        total = sum(seat_lengths.values())
        if total > 13 or (total and not detected):
            return None
        lengths[seat] = seat_lengths
        if detected:
            anchors[seat].update(detected)
            if step is None:
                return None
            horizontal_steps[seat] = float(step)
    side = rank_layout._side_lengths([registered], bank, profile)
    lengths["W"], lengths["E"] = side["W"], side["E"]
    if any(sum(lengths[seat].values()) > 13 for seat in rank_layout.SEATS):
        return None
    if not any(sum(lengths[seat].values()) for seat in rank_layout.SEATS):
        return None
    if any(
        sum(lengths[seat][suit] for seat in rank_layout.SEATS) > 13
        for suit in rank_layout.SUITS
    ):
        return None
    chains = side.get("_chains") or {"W": {}, "E": {}}
    return {
        "lengths": lengths,
        "anchors": anchors,
        "horizontal_steps": horizontal_steps,
        "side_chains": {
            seat: {
                suit: tuple(int(value) for value in chains.get(seat, {}).get(suit, ()))
                for suit in rank_layout.SUITS
            }
            for seat in ("W", "E")
        },
    }


def _ordered_subset_assignments(
    matrix: Sequence[Sequence[float]],
) -> list[tuple[float, tuple[int, ...]]]:
    """Return the two best monotone rank subsets for visible suit slots."""

    rows = [tuple(float(value) for value in row) for row in matrix]
    if not rows:
        return [(0.0, ())]
    if len(rows) > len(rank_layout.RANKS) or any(
        len(row) != len(rank_layout.RANKS) for row in rows
    ):
        raise PrimaryVideoRecognitionError("partial rank matrix is invalid")
    states: dict[tuple[int, int], list[tuple[float, tuple[int, ...]]]] = {
        (0, -1): [(0.0, ())]
    }
    for row_index, row in enumerate(rows):
        next_states: dict[tuple[int, int], list[tuple[float, tuple[int, ...]]]] = {}
        for (_, previous_rank), values in states.items():
            remaining = len(rows) - row_index - 1
            for rank_index in range(previous_rank + 1, len(rank_layout.RANKS) - remaining):
                key = (row_index + 1, rank_index)
                for score, path in values:
                    next_states.setdefault(key, []).append(
                        (score + row[rank_index], path + (rank_index,))
                    )
        states = {
            key: sorted(values, key=lambda item: (-item[0], item[1]))[:2]
            for key, values in next_states.items()
        }
    ranked = sorted(
        (item for values in states.values() for item in values),
        key=lambda item: (-item[0], item[1]),
    )
    distinct: list[tuple[float, tuple[int, ...]]] = []
    for item in ranked:
        if item[1] not in {path for _, path in distinct}:
            distinct.append(item)
        if len(distinct) == 2:
            break
    return distinct


def _partial_slot_coordinates(
    geometry: Mapping[str, Any],
    seat: str,
    suit: str,
    profile: rank_layout.BridgitRankLayoutProfile,
) -> list[tuple[int, int]]:
    count = int(geometry["lengths"][seat][suit])
    if not count:
        return []
    if seat in {"N", "S"}:
        x, y = geometry["anchors"][seat][suit]
        step = float(geometry["horizontal_steps"][seat])
        return [(round(x + step * index), y) for index in range(count)]
    chain = list(geometry["side_chains"][seat][suit])
    if len(chain) != count:
        raise PrimaryVideoRecognitionError("partial side geometry lost its peak chain")
    if seat == "E":
        chain.reverse()
    y = profile.anchors[seat][suit][1]
    return [(x, y) for x in chain]


def _recognize_partial_pair(
    first: Any,
    second: Any,
    *,
    bank: Mapping[str, Any],
    profile: rank_layout.BridgitRankLayoutProfile,
    geometry: Mapping[str, Any],
    frame_sha256s: Sequence[str],
    timestamps_ms: Sequence[int],
) -> dict[str, Any]:
    """Recognize only proven visible ranks and retain UNKNOWN elsewhere."""

    frames = [
        _registered_candidate(frame, profile) for frame in (first, second)
    ]
    if any(frame is None for frame in frames):
        raise PrimaryVideoRecognitionError("partial observation registration failed")
    if len(frame_sha256s) != 2 or len(set(frame_sha256s)) != 2:
        raise PrimaryVideoRecognitionError("partial observation frames are not independent")
    if len(timestamps_ms) != 2 or len(set(int(value) for value in timestamps_ms)) != 2:
        raise PrimaryVideoRecognitionError("partial observation timestamps are not independent")
    pixel_hashes = [hashlib.sha256(frame.tobytes()).hexdigest() for frame in frames]
    if len(set(pixel_hashes)) != 2:
        raise PrimaryVideoRecognitionError("partial observation pixels are not independent")
    hands = {seat: [] for seat in rank_layout.SEATS}
    observations: list[dict[str, Any]] = []
    rejected_suits: list[dict[str, Any]] = []
    for seat in rank_layout.SEATS:
        for suit in rank_layout.SUITS:
            coords = _partial_slot_coordinates(geometry, seat, suit, profile)
            if not coords:
                continue
            components = [
                rank_layout._slot_score_components(frames, bank, xy, profile)
                for xy in coords
            ]
            alternatives = _ordered_subset_assignments(
                [component["assignment"] for component in components]
            )
            if not alternatives:
                rejected_suits.append({"seat": seat, "suit": suit, "reason": "NO_ORDERED_ASSIGNMENT"})
                continue
            score, path = alternatives[0]
            margin = (
                score - alternatives[1][0]
                if len(alternatives) > 1
                else 1_000_000.0
            )
            if margin < profile.min_assignment_margin:
                rejected_suits.append({
                    "seat": seat,
                    "suit": suit,
                    "reason": "ASSIGNMENT_MARGIN_BELOW_THRESHOLD",
                    "margin": round(margin, 6),
                })
                continue
            suit_cards: list[tuple[str, dict[str, Any]]] = []
            for component, rank_index in zip(components, path):
                per_frame_score = [
                    float(value) for value in component["per_frame_raw"][:, rank_index]
                ]
                per_frame_ink = [
                    float(value) for value in component["per_frame_ink"][:, rank_index]
                ]
                support = [
                    index
                    for index, (assigned_score, ink) in enumerate(
                        zip(per_frame_score, per_frame_ink)
                    )
                    if assigned_score >= profile.min_template_score
                    and ink >= profile.min_rank_ink_fraction
                ]
                if not support:
                    suit_cards = []
                    rejected_suits.append({
                        "seat": seat,
                        "suit": suit,
                        "reason": "CARD_WITHOUT_CONFIDENT_FRAME_SUPPORT",
                    })
                    break
                rank = rank_layout.RANKS[rank_index]
                card = rank + suit
                best_frame = max(support, key=lambda index: per_frame_score[index])
                suit_cards.append((card, {
                    "seat": seat,
                    "card": card,
                    "source": "VISUAL",
                    "frame_sha256": frame_sha256s[best_frame],
                    "timestamp_ms": int(timestamps_ms[best_frame]),
                    "confidence": round(per_frame_score[best_frame], 6),
                    "confidence_kind": "TEMPLATE_SIMILARITY_UNCALIBRATED",
                }))
            for card, evidence in suit_cards:
                hands[seat].append(card)
                observations.append(evidence)

    owners: dict[str, str] = {}
    conflicts = []
    for seat in rank_layout.SEATS:
        for card in hands[seat]:
            prior = owners.setdefault(card, seat)
            if prior != seat:
                conflicts.append({"card": card, "seats": sorted({prior, seat})})
    if conflicts:
        return {
            "status": "PARTIAL_VISUAL_CONFLICT",
            "hands": hands,
            "conflicts": conflicts,
            "observations": observations,
            "rejected_suits": rejected_suits,
        }
    return {
        "status": "PARTIAL_VISUAL_OBSERVATION" if owners else "NO_CARD_OBSERVATIONS",
        "hands": hands,
        "observations": observations,
        "rejected_suits": rejected_suits,
        "integrity": {
            "cards": sum(len(cards) for cards in hands.values()),
            "unique": len(owners),
            "seat_counts": {seat: len(hands[seat]) for seat in rank_layout.SEATS},
        },
    }


def _flatten_hands(result: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = result.get("hands") or {}
    hands: dict[str, list[str]] = {}
    for seat in rank_layout.SEATS:
        seat_raw = raw.get(seat) or {}
        cards = [rank + suit for suit in rank_layout.SUITS for rank in seat_raw.get(suit, [])]
        hands[seat] = cards
    return hands


def _accepted_primary_result(result: Mapping[str, Any]) -> bool:
    status = result.get("status")
    if status == "PARTIAL_VISUAL_OBSERVATION":
        integrity = result.get("integrity") or {}
        counts = {
            seat: int((integrity.get("seat_counts") or {}).get(seat, 0))
            for seat in rank_layout.SEATS
        }
        cards = int(integrity.get("cards") or 0)
        return (
            0 < cards == int(integrity.get("unique") or 0)
            and cards == sum(counts.values())
            and all(0 <= count <= 13 for count in counts.values())
            and not result.get("conflicts")
        )
    if status not in {
        "SHADOW_FULL_LAYOUT_CANDIDATE",
        "SHADOW_THREE_HAND_LAYOUT_CANDIDATE",
    }:
        return False
    integrity = result.get("integrity") or {}
    seat_counts = {
        seat: int((integrity.get("seat_counts") or {}).get(seat, 0))
        for seat in rank_layout.SEATS
    }
    derive = status == "SHADOW_THREE_HAND_LAYOUT_CANDIDATE"
    if derive:
        if integrity.get("cards") != 39 or integrity.get("unique") != 39:
            return False
        if sorted(seat_counts.values()) != [0, 13, 13, 13]:
            return False
        if result.get("hidden_hand_reconstruction_performed") is not True:
            return False
    else:
        if integrity.get("cards") != 52 or integrity.get("unique") != 52:
            return False
        if any(count != 13 for count in seat_counts.values()):
            return False
    if result.get("uncertainties"):
        return False
    evidence = result.get("evidence") or {}
    if evidence.get("per_frame_deal_agreement") is not True:
        return False
    if result.get("frame_assignment_issues"):
        return False
    if derive:
        canonicalize_video_deal(
            {"hands": _flatten_hands(result)}, derive_fourth_hand=True
        )
    else:
        canonicalize_video_deal(
            {"hands": _flatten_hands(result)}, derive_fourth_hand=False
        )
    return True


def recognize_video_primary(
    video_path: Path,
    *,
    reference_frame: Path,
    profile_path: Path,
    output_dir: Path,
    gambler_asset_root: Path | None = None,
    gambler_sprite_path: Path | None = None,
    gambler_sprite_sha256: str | None = None,
    verified_card_width_px: float = 109.0,
    verified_card_height_px: float = 147.0,
    scan_ms: int = DEFAULT_SCAN_MS,
    attempt_gap_ms: int = DEFAULT_ATTEMPT_GAP_MS,
    max_deals: int = 64,
) -> dict[str, Any]:
    if not 500 <= scan_ms <= 10_000:
        raise PrimaryVideoRecognitionError("scan_ms outside supported bounds")
    if not 1000 <= attempt_gap_ms <= 120_000:
        raise PrimaryVideoRecognitionError("attempt_gap_ms outside supported bounds")
    if not 1 <= max_deals <= 256:
        raise PrimaryVideoRecognitionError("max_deals outside supported bounds")
    output_dir.mkdir(parents=True, exist_ok=True)
    profile = rank_layout.load_profile(profile_path)
    if gambler_asset_root is not None:
        selected_variant, gambler_sprite_path, gambler_sprite_sha256 = (
            resolve_original_gambler_asset(
                gambler_asset_root,
                verified_card_width_px=verified_card_width_px,
                verified_card_height_px=verified_card_height_px,
            )
        )
    elif gambler_sprite_path is None or gambler_sprite_sha256 is None:
        raise PrimaryVideoRecognitionError(
            "Gambler asset root or explicit pinned sprite is required"
        )
    else:
        try:
            selected_variant = variant_for_pinned_sprite_sha256(gambler_sprite_sha256)
        except GamblerReferenceAuthorityError as exc:
            raise PrimaryVideoRecognitionError(
                "explicit Gambler sprite hash is not pinned"
            ) from exc
    assert gambler_sprite_path is not None
    assert gambler_sprite_sha256 is not None
    sprite = load_sprite(
        gambler_sprite_path,
        expected_sha256=gambler_sprite_sha256,
        expected_variant=selected_variant,
    )
    cv2, _ = rank_layout._pixel_runtime()
    reference = cv2.imread(str(reference_frame), cv2.IMREAD_COLOR)
    if reference is None:
        raise PrimaryVideoRecognitionError("reference frame cannot be decoded")
    if tuple(reference.shape[:2]) != (profile.height, profile.width):
        raise PrimaryVideoRecognitionError("reference dimensions do not match profile")
    derived_reference = derive_original_asset_reference(
        reference,
        profile,
        sprite,
        target_card_width_px=verified_card_width_px,
        target_card_height_px=verified_card_height_px,
    )
    bank = rank_layout._template_bank(derived_reference, profile)

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise PrimaryVideoRecognitionError("video decoder could not open source")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps <= 0 or frame_count <= 0:
        capture.release()
        raise PrimaryVideoRecognitionError("video metadata is invalid")
    if width != profile.width or not (
        profile.height <= height <= profile.height + MAX_VERTICAL_PADDING_PX
    ):
        capture.release()
        return {
            "version": _runtime_version(),
            "status": "LAYOUT_UNSUPPORTED",
            "deals": [],
            "source_size": {"width": width, "height": height},
        }
    duration_ms = round(frame_count * 1000 / fps)
    rejections: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    last_attempt_ms = -10**12
    event_regions = []
    for viewport_y in dict.fromkeys((0, max(0, height - profile.height))):
        for region in bridge_layout_regions(viewport_y):
            if region not in event_regions:
                event_regions.append(region)
    selector = EventFrameSelector(settle_ms=750, watchdog_ms=180_000)
    event_counts: Counter[str] = Counter()
    pending_by_geometry: dict[str, tuple[int, Any]] = {}
    timestamp_ms = 0
    try:
        while timestamp_ms < duration_ms and len(candidates) < max_deals * 8:
            first = _frame_at(capture, timestamp_ms)
            if first is None:
                rejections["decode"] += 1
                timestamp_ms += scan_ms
                continue
            try:
                event = selector.observe(frame_signature(first, event_regions), timestamp_ms)
            except Exception:
                rejections["event_signature_rejected"] += 1
                timestamp_ms += scan_ms
                continue
            if event is None:
                timestamp_ms += scan_ms
                continue
            event_counts[event.reason] += 1
            try:
                first_geometry = _full_geometry_gate(first, bank, profile)
                partial_mode = False
                if first_geometry is None and ALLOW_PARTIAL_OBSERVATIONS:
                    first_geometry = _partial_geometry_gate(first, bank, profile)
                    partial_mode = first_geometry is not None
            except Exception:
                rejections["geometry_exception"] += 1
                timestamp_ms += scan_ms
                continue
            if first_geometry is None:
                rejections[
                    "visible_geometry_not_proven"
                    if ALLOW_PARTIAL_OBSERVATIONS
                    else "full_geometry_not_proven"
                ] += 1
                timestamp_ms += scan_ms
                continue
            if timestamp_ms - last_attempt_ms < attempt_gap_ms:
                timestamp_ms += scan_ms
                continue
            geometry_key = json.dumps(first_geometry, sort_keys=True, separators=(",", ":"))
            pending_by_geometry = {
                key: value
                for key, value in pending_by_geometry.items()
                if 0 < timestamp_ms - value[0] <= MAX_TEMPORAL_PAIR_GAP_MS
            }
            prior = pending_by_geometry.pop(geometry_key, None)
            if prior is not None:
                first_timestamp, first = prior
                second_timestamp = timestamp_ms
                second = _frame_at(capture, timestamp_ms)
                if second is None:
                    rejections["temporal_union_decode"] += 1
                    timestamp_ms += scan_ms
                    continue
            else:
                first_timestamp = timestamp_ms
                second_timestamp = min(duration_ms - 1, timestamp_ms + 600)
                second = _frame_at(capture, second_timestamp)
                if second is None:
                    rejections["retry_decode"] += 1
                    timestamp_ms += scan_ms
                    continue
                try:
                    second_geometry = _full_geometry_gate(second, bank, profile)
                    second_partial_mode = False
                    if second_geometry is None and ALLOW_PARTIAL_OBSERVATIONS:
                        second_geometry = _partial_geometry_gate(second, bank, profile)
                        second_partial_mode = second_geometry is not None
                except Exception:
                    second_geometry = None
                    second_partial_mode = False
                if second_geometry != first_geometry or second_partial_mode != partial_mode:
                    # Visibility may legitimately change between frames. Keep a
                    # bounded observation so a later frame with the same visible
                    # geometry can supply independent per-card evidence.
                    pending_by_geometry[geometry_key] = (timestamp_ms, first.copy())
                    rejections["geometry_visibility_changed"] += 1
                    timestamp_ms += scan_ms
                    continue
            last_attempt_ms = timestamp_ms
            with tempfile.TemporaryDirectory(prefix="bridgit-primary-observation-") as tmp:
                tmp_root = Path(tmp)
                first_path = tmp_root / "frame-a.png"
                second_path = tmp_root / "frame-b.png"
                first_sha = _write_png(first_path, first)
                second_sha = _write_png(second_path, second)
                with native_gambler_geometry():
                    if partial_mode:
                        result = _recognize_partial_pair(
                            first,
                            second,
                            bank=bank,
                            profile=profile,
                            geometry=first_geometry,
                            frame_sha256s=[first_sha, second_sha],
                            timestamps_ms=[first_timestamp, second_timestamp],
                        )
                    else:
                        result = recognize_frames_with_original_gambler_deck(
                            reference_frame,
                            [first_path, second_path],
                            profile,
                            gambler_sprite_path=gambler_sprite_path,
                            gambler_sprite_sha256=gambler_sprite_sha256,
                            verified_card_width_px=verified_card_width_px,
                            verified_card_height_px=verified_card_height_px,
                            expected_frame_sha256s=[first_sha, second_sha],
                            observation_timestamps_ms=[first_timestamp, second_timestamp],
                            allow_fourth_hand_derivation=ALLOW_FOURTH_HAND_DERIVATION,
                        )
            if not _accepted_primary_result(result):
                rejections[str(result.get("status") or "primary_rejected")] += 1
                timestamp_ms += scan_ms
                continue
            is_partial = result.get("status") == "PARTIAL_VISUAL_OBSERVATION"
            hands = (
                {seat: list((result.get("hands") or {}).get(seat) or []) for seat in rank_layout.SEATS}
                if is_partial
                else _flatten_hands(result)
            )
            derive_fourth = result.get("status") == "SHADOW_THREE_HAND_LAYOUT_CANDIDATE"
            canonical = canonicalize_video_deal(
                {"hands": hands}, derive_fourth_hand=derive_fourth
            ).to_dict()
            complete_hands = {
                seat: list(canonical["hands"][seat]["cards"])
                for seat in rank_layout.SEATS
            }
            visual_seats = [seat for seat in rank_layout.SEATS if hands[seat]]
            inferred_seats = [
                str(item["seat"]) for item in canonical.get("derivations") or []
            ]
            layout_sha = hashlib.sha256(
                json.dumps(canonical["hands"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            screenshot = output_dir / f"primary_{first_timestamp:010d}.png"
            screenshot_sha = _write_png(screenshot, first)
            evidence = result.get("evidence") or {}
            candidates.append(
                {
                    "timestamp_ms": first_timestamp,
                    "status": (
                        "PRIMARY_PARTIAL_OBSERVATION"
                        if is_partial
                        else "PRIMARY_RECOGNIZER_CANDIDATE"
                    ),
                    "layout_sha256": layout_sha,
                    "hands": complete_hands,
                    "visual_hands": hands,
                    "visual_seats": visual_seats,
                    "inferred_seats": inferred_seats,
                    "hidden_hand_reconstruction_performed": derive_fourth,
                    "reconstruction_rule": (
                        "THREE_VISUAL_HANDS_PLUS_DECK_COMPLEMENT"
                        if derive_fourth
                        else "VISUAL_ONLY; NO_DECK_COMPLEMENT"
                    ),
                    "canonical_deal": canonical,
                    "screenshot": str(screenshot),
                    "screenshot_sha256": screenshot_sha,
                    "geometry": first_geometry,
                    "minimum_assigned_score": evidence.get("minimum_assigned_score"),
                    "median_assigned_score": evidence.get("median_assigned_score"),
                    "backend_status": result.get("status"),
                    "backend_version": result.get("successor_version") or _runtime_version(),
                    "template_source": result.get("template_source"),
                    "event_reason": event.reason,
                    "canonical_promotion_allowed": False,
                    "temporal_card_union": bool(
                        evidence.get("temporal_card_union")
                    ),
                    "temporal_card_support_rule": evidence.get(
                        "temporal_card_support_rule"
                    ),
                    "visual_observations": result.get("observations") or [],
                    "rejected_partial_suits": result.get("rejected_suits") or [],
                }
            )
            timestamp_ms += scan_ms
    finally:
        capture.release()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        grouped.setdefault(item["layout_sha256"], []).append(item)
    deals = []
    for values in sorted(grouped.values(), key=lambda group: group[0]["timestamp_ms"]):
        best = max(
            values,
            key=lambda item: (
                float(item.get("minimum_assigned_score") or -1.0),
                -int(item["timestamp_ms"]),
            ),
        )
        deal = dict(best)
        aggregated_visual_hands = {seat: set() for seat in rank_layout.SEATS}
        visibility_observations = []
        for item in values:
            visual_hands = item.get("visual_hands") or {}
            visible_seats = []
            for seat in rank_layout.SEATS:
                cards = set(visual_hands.get(seat) or [])
                if cards:
                    aggregated_visual_hands[seat].update(cards)
                    visible_seats.append(seat)
            visibility_observations.append(
                {
                    "timestamp_ms": item["timestamp_ms"],
                    "visible_seats": visible_seats,
                }
            )
        if any(len(cards) > 13 for cards in aggregated_visual_hands.values()):
            raise PrimaryVideoRecognitionError(
                "same-layout observations exceeded hand capacity"
            )
        owner: dict[str, str] = {}
        for seat, cards in aggregated_visual_hands.items():
            for card in cards:
                prior = owner.setdefault(card, seat)
                if prior != seat:
                    raise PrimaryVideoRecognitionError(
                        "same-layout observations assigned one card to two seats"
                    )
        aggregate_payload = {
            "hands": {
                seat: sorted(cards)
                for seat, cards in aggregated_visual_hands.items()
            }
        }
        aggregate_visible_seats = [
            seat for seat in rank_layout.SEATS if aggregated_visual_hands[seat]
        ]
        aggregate_derive = (
            len(aggregate_visible_seats) == 3
            and all(len(aggregated_visual_hands[seat]) == 13 for seat in aggregate_visible_seats)
        )
        aggregate_canonical = canonicalize_video_deal(
            aggregate_payload, derive_fourth_hand=aggregate_derive
        ).to_dict()
        if aggregate_canonical["hands"] != deal["canonical_deal"]["hands"]:
            raise PrimaryVideoRecognitionError(
                "same-layout observations disagree with canonical hands"
            )
        deal["visual_hands"] = aggregate_payload["hands"]
        deal["visual_seats"] = aggregate_visible_seats
        deal["inferred_seats"] = [
            str(item["seat"])
            for item in aggregate_canonical.get("derivations") or []
        ]
        deal["hidden_hand_reconstruction_performed"] = aggregate_derive
        deal["reconstruction_rule"] = (
            "THREE_VISUAL_HANDS_PLUS_DECK_COMPLEMENT"
            if aggregate_derive
            else "VISUAL_ONLY; NO_DECK_COMPLEMENT"
        )
        deal["canonical_deal"] = aggregate_canonical
        deal["visibility_observations"] = visibility_observations
        deal["server_confirmations"] = len(values)
        deal["confirmation_timestamps_ms"] = [item["timestamp_ms"] for item in values]
        deals.append(deal)
    keep = {item["screenshot"] for item in deals[:max_deals]}
    for path in output_dir.glob("primary_*.png"):
        if str(path) not in keep:
            path.unlink(missing_ok=True)
    return {
        "version": _runtime_version(),
        "status": (
            "PRIMARY_COMPLETE"
            if any(
                all(len((deal.get("hands") or {}).get(seat) or []) == 13 for seat in rank_layout.SEATS)
                for deal in deals
            )
            else "PARTIAL_OBSERVATIONS_RETAINED"
            if deals
            else "NO_CARD_OBSERVATIONS"
            if ALLOW_PARTIAL_OBSERVATIONS
            else "NO_FULL_LAYOUT_ACCEPTED"
        ),
        "source_size": {"width": width, "height": height},
        "gambler_variant": selected_variant,
        "gambler_sprite_sha256": gambler_sprite_sha256,
        "template_card_size": {
            "width": float(verified_card_width_px),
            "height": float(verified_card_height_px),
        },
        "template_resampled": (
            abs(float(verified_card_width_px) - sprite.card_width) > 1e-9
            or abs(float(verified_card_height_px) - sprite.card_height) > 1e-9
        ),
        "scan_ms": scan_ms,
        "attempt_gap_ms": attempt_gap_ms,
        "temporal_card_union_enabled": rank_layout.TEMPORAL_CARD_UNION_ENABLED,
        "fourth_hand_derivation_enabled": ALLOW_FOURTH_HAND_DERIVATION,
        "partial_observations_enabled": ALLOW_PARTIAL_OBSERVATIONS,
        "event_counts": dict(sorted(event_counts.items())),
        "rejections": dict(sorted(rejections.items())),
        "deals": deals[:max_deals],
        "canonical_promotion_allowed": False,
    }


__all__ = [
    "PRIMARY_VIDEO_VERSION",
    "PRIMARY_VIDEO_TEMPORAL_COMPLEMENT_VERSION",
    "PRIMARY_VIDEO_PARTIAL_OBSERVATION_VERSION",
    "PrimaryVideoRecognitionError",
    "recognize_video_primary",
    "resolve_original_gambler_asset",
]
