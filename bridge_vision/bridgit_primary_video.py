"""Production adapter: old rank-layout recognizer first, Gambler recovery second.

The primary path is the proven geometry/ordered-DP Bridgit recognizer with the
hash-bound original Gambler classic deck as rank authority.  This module
only emits recognizer candidates; it never writes SCHOOL CANON and never uses
hidden-hand/deck-complement inference.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from bridge_contracts.video_deal import canonicalize_video_deal
from bridge_vision import bridgit_rank_layout as rank_layout
from bridge_vision.bridgit_gambler_rank_layout import (
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
DEFAULT_SCAN_MS = 3000
DEFAULT_ATTEMPT_GAP_MS = 15000


class PrimaryVideoRecognitionError(ValueError):
    """The bounded primary-video pass cannot continue safely."""


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
        if not anchors or sum(lengths[seat].values()) != 13:
            return None
    side = rank_layout._side_lengths([registered], bank, profile)
    lengths["W"], lengths["E"] = side["W"], side["E"]
    if any(sum(lengths[seat].values()) != 13 for seat in rank_layout.SEATS):
        return None
    if any(sum(lengths[seat][suit] for seat in rank_layout.SEATS) != 13 for suit in rank_layout.SUITS):
        return None
    return lengths


def _flatten_hands(result: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = result.get("hands") or {}
    hands: dict[str, list[str]] = {}
    for seat in rank_layout.SEATS:
        seat_raw = raw.get(seat) or {}
        cards = [rank + suit for suit in rank_layout.SUITS for rank in seat_raw.get(suit, [])]
        hands[seat] = cards
    return hands


def _accepted_primary_result(result: Mapping[str, Any]) -> bool:
    if result.get("status") != "SHADOW_FULL_LAYOUT_CANDIDATE":
        return False
    integrity = result.get("integrity") or {}
    if integrity.get("cards") != 52 or integrity.get("unique") != 52:
        return False
    if any(int((integrity.get("seat_counts") or {}).get(seat, 0)) != 13 for seat in rank_layout.SEATS):
        return False
    if result.get("uncertainties"):
        return False
    evidence = result.get("evidence") or {}
    if evidence.get("per_frame_deal_agreement") is not True:
        return False
    if result.get("frame_assignment_issues"):
        return False
    canonicalize_video_deal({"hands": _flatten_hands(result)}, derive_fourth_hand=False)
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
            "version": PRIMARY_VIDEO_VERSION,
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
            except Exception:
                rejections["geometry_exception"] += 1
                timestamp_ms += scan_ms
                continue
            if first_geometry is None:
                rejections["full_geometry_not_proven"] += 1
                timestamp_ms += scan_ms
                continue
            if timestamp_ms - last_attempt_ms < attempt_gap_ms:
                timestamp_ms += scan_ms
                continue
            second_timestamp = min(duration_ms - 1, timestamp_ms + 600)
            second = _frame_at(capture, second_timestamp)
            if second is None:
                rejections["retry_decode"] += 1
                timestamp_ms += scan_ms
                continue
            try:
                second_geometry = _full_geometry_gate(second, bank, profile)
            except Exception:
                second_geometry = None
            if second_geometry != first_geometry:
                rejections["geometry_not_stable"] += 1
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
                    result = recognize_frames_with_original_gambler_deck(
                        reference_frame,
                        [first_path, second_path],
                        profile,
                        gambler_sprite_path=gambler_sprite_path,
                        gambler_sprite_sha256=gambler_sprite_sha256,
                        verified_card_width_px=verified_card_width_px,
                        verified_card_height_px=verified_card_height_px,
                        expected_frame_sha256s=[first_sha, second_sha],
                        observation_timestamps_ms=[timestamp_ms, second_timestamp],
                    )
            if not _accepted_primary_result(result):
                rejections[str(result.get("status") or "primary_rejected")] += 1
                timestamp_ms += scan_ms
                continue
            hands = _flatten_hands(result)
            canonical = canonicalize_video_deal({"hands": hands}, derive_fourth_hand=False).to_dict()
            layout_sha = hashlib.sha256(
                json.dumps(canonical["hands"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            screenshot = output_dir / f"primary_{timestamp_ms:010d}.png"
            screenshot_sha = _write_png(screenshot, first)
            evidence = result.get("evidence") or {}
            candidates.append(
                {
                    "timestamp_ms": timestamp_ms,
                    "status": "PRIMARY_RECOGNIZER_CANDIDATE",
                    "layout_sha256": layout_sha,
                    "hands": hands,
                    "canonical_deal": canonical,
                    "screenshot": str(screenshot),
                    "screenshot_sha256": screenshot_sha,
                    "geometry": first_geometry,
                    "minimum_assigned_score": evidence.get("minimum_assigned_score"),
                    "median_assigned_score": evidence.get("median_assigned_score"),
                    "backend_status": result.get("status"),
                    "backend_version": result.get("successor_version"),
                    "template_source": result.get("template_source"),
                    "event_reason": event.reason,
                    "canonical_promotion_allowed": False,
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
        deal["server_confirmations"] = len(values)
        deal["confirmation_timestamps_ms"] = [item["timestamp_ms"] for item in values]
        deals.append(deal)
    keep = {item["screenshot"] for item in deals[:max_deals]}
    for path in output_dir.glob("primary_*.png"):
        if str(path) not in keep:
            path.unlink(missing_ok=True)
    return {
        "version": PRIMARY_VIDEO_VERSION,
        "status": "PRIMARY_COMPLETE" if deals else "NO_FULL_LAYOUT_ACCEPTED",
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
        "event_counts": dict(sorted(event_counts.items())),
        "rejections": dict(sorted(rejections.items())),
        "deals": deals[:max_deals],
        "canonical_promotion_allowed": False,
    }


__all__ = ["PRIMARY_VIDEO_VERSION", "PrimaryVideoRecognitionError", "recognize_video_primary", "resolve_original_gambler_asset"]
