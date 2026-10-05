"""Review-only v2 successor: bounded observation queue and local duplicate rejection.

The primary path reuses the geometry/ordered-DP Bridgit recognizer with the
hash-bound original Gambler classic deck as rank authority.  This module
only emits recognizer candidates; it never writes SCHOOL CANON and never uses
hidden-hand/deck-complement inference.

The candidate owns its selector type, so historical r26's global retry patch
cannot affect it. It is installed only by an explicit review runtime wrapper;
no default runtime route selects it.
"""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections import Counter, deque
from pathlib import Path
from typing import Any

from bridge_contracts.video_deal import canonicalize_video_deal
from bridge_vision.auction_observer import observer_from_profile
from bridge_vision import bridgit_rank_layout as rank_layout
from bridge_vision.bridgit_gambler_rank_layout import (
    derive_original_asset_reference,
    recognize_frames_with_original_gambler_deck,
)
from bridge_vision.gambler_classic_reference import (
    load_sprite,
)
from bridge_vision.bridgit_event_frame_selector import bridge_layout_regions, frame_signature, signature_distance
from bridge_vision.bridgit_candidate_frame_selector import CandidateFrameSelector as EventFrameSelector
from bridge_vision.gambler_reference_authority import (
    GamblerReferenceAuthorityError,
    variant_for_pinned_sprite_sha256,
)
from bridge_vision.bridgit_primary_compat import MAX_VERTICAL_PADDING_PX, native_gambler_geometry
from bridge_vision.bridgit_primary_video import (
    PrimaryVideoRecognitionError, _accepted_primary_result, _flatten_hands,
    _frame_at, _full_geometry_gate, _write_png, resolve_original_gambler_asset,
)

PRIMARY_VIDEO_VERSION = "bridgit-primary-video-gambler-v2-auction-candidate3"
MAX_PENDING_PAIRS = 4
DUPLICATE_OBSERVATION_ERRORS = frozenset({
    "duplicate frame bytes do not provide independent evidence",
    "duplicate decoded frame pixels do not provide independent evidence",
    "duplicate decoded frame pixels",
})
DEFAULT_SCAN_MS = 3000
DEFAULT_ATTEMPT_GAP_MS = 15000


class PrimaryVideoInputError(PrimaryVideoRecognitionError):
    """The source decoder cannot provide valid video input."""


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
    auction_profile_path: Path | None = None,
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

    auction_observer = (observer_from_profile(
        profile_path, reference, video_path=video_path, output_dir=output_dir,
        auction_profile_path=auction_profile_path,
    ) if auction_profile_path is not None or Path(profile_path).is_file() else None)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise PrimaryVideoInputError("video decoder could not open source")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
            raise ValueError("invalid metadata")
    except (TypeError, ValueError, OverflowError) as exc:
        capture.release()
        raise PrimaryVideoInputError("video metadata is invalid") from exc
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
    # Keep pixels from the event itself, not whatever board is on screen when
    # the expensive recognizer's cooldown expires. Four pairs bound RAM use.
    pending = deque()
    timestamp_ms = 0

    def retry_current_state(observation, current_signature, now_ms):
        if (observation["retry_allowed"] and current_signature is not None
                and signature_distance(observation["signature"], current_signature) <= selector.stable_threshold):
            selector.schedule_retry(now_ms, delay_ms=1_500)

    def process_observation(observation):
        first, second = observation["first"], observation["second"]
        first_timestamp, second_timestamp = observation["timestamps"]
        with tempfile.TemporaryDirectory(prefix="bridgit-candidate-observation-") as tmp:
            first_path, second_path = Path(tmp) / "frame-a.png", Path(tmp) / "frame-b.png"
            first_sha = _write_png(first_path, first)
            second_sha = _write_png(second_path, second)
            try:
                with native_gambler_geometry():
                    result = recognize_frames_with_original_gambler_deck(
                        reference_frame, [first_path, second_path], profile,
                        gambler_sprite_path=gambler_sprite_path,
                        gambler_sprite_sha256=gambler_sprite_sha256,
                        verified_card_width_px=verified_card_width_px,
                        verified_card_height_px=verified_card_height_px,
                        expected_frame_sha256s=[first_sha, second_sha],
                        observation_timestamps_ms=[first_timestamp, second_timestamp],
                    )
            except rank_layout.BridgitRankLayoutError as exc:
                # Only a known lack of independent evidence is an ordinary
                # observation rejection. Integrity/program errors still escape.
                if str(exc) not in DUPLICATE_OBSERVATION_ERRORS:
                    raise
                rejections["duplicate_observation"] += 1
                return False
        if not _accepted_primary_result(result):
            rejections[str(result.get("status") or "primary_rejected")] += 1
            return False
        hands = _flatten_hands(result)
        canonical = canonicalize_video_deal({"hands": hands}, derive_fourth_hand=False).to_dict()
        layout_sha = hashlib.sha256(
            json.dumps(canonical["hands"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        screenshot = output_dir / f"primary_{first_timestamp:010d}.png"
        screenshot_sha = _write_png(screenshot, first)
        evidence = result.get("evidence") or {}
        candidates.append({
            "timestamp_ms": first_timestamp,
            "status": "PRIMARY_RECOGNIZER_CANDIDATE",
            "layout_sha256": layout_sha,
            "hands": hands,
            "canonical_deal": canonical,
            "screenshot": str(screenshot),
            "screenshot_sha256": screenshot_sha,
            "geometry": observation["geometry"],
            "minimum_assigned_score": evidence.get("minimum_assigned_score"),
            "median_assigned_score": evidence.get("median_assigned_score"),
            "backend_status": result.get("status"),
            "backend_version": result.get("successor_version"),
            "template_source": result.get("template_source"),
            "event_reason": observation["reason"],
            "canonical_promotion_allowed": False,
        })
        return True

    try:
        while timestamp_ms < duration_ms and len(candidates) < max_deals * 8:
            first = _frame_at(capture, timestamp_ms)
            if auction_observer is not None:
                auction_observer.observe(first, timestamp_ms)
            signature = None
            if first is None:
                rejections["decode"] += 1
            else:
                try:
                    signature = frame_signature(first, event_regions)
                except ValueError:
                    rejections["event_signature_rejected"] += 1
            if signature is not None:
                event = selector.observe(signature, timestamp_ms)
                if event is not None:
                    event_counts[event.reason] += 1
                    observation = {
                        "signature": signature.copy(),
                        "reason": event.reason,
                        "retry_allowed": event.reason != "WATCHDOG_STABLE_STATE",
                    }
                    with native_gambler_geometry():
                        first_geometry = _full_geometry_gate(first, bank, profile)
                    if first_geometry is None:
                        rejections["full_geometry_not_proven"] += 1
                        retry_current_state(observation, signature, timestamp_ms)
                    else:
                        second_timestamp = min(duration_ms - 1, timestamp_ms + 600)
                        second = _frame_at(capture, second_timestamp)
                        if second is None:
                            rejections["retry_decode"] += 1
                            retry_current_state(observation, signature, timestamp_ms)
                        else:
                            with native_gambler_geometry():
                                second_geometry = _full_geometry_gate(second, bank, profile)
                            if second_timestamp <= timestamp_ms or second_geometry != first_geometry:
                                rejections["geometry_not_stable"] += 1
                                retry_current_state(observation, signature, timestamp_ms)
                            else:
                                observation.update({
                                    "first": first.copy(), "second": second.copy(),
                                    "timestamps": (timestamp_ms, second_timestamp),
                                    "geometry": first_geometry,
                                })
                                if len(pending) == MAX_PENDING_PAIRS:
                                    # Explicit coverage loss, never an invented board.
                                    pending.popleft()
                                    rejections["observation_queue_overflow"] += 1
                                pending.append(observation)
            if pending and timestamp_ms - last_attempt_ms >= attempt_gap_ms:
                observation = pending.popleft()
                last_attempt_ms = timestamp_ms
                if not process_observation(observation):
                    retry_current_state(observation, signature, timestamp_ms)
            timestamp_ms += scan_ms
        if auction_observer is not None and timestamp_ms < duration_ms:
            auction_observer.truncated = True
            auction_observer.coverage_issues.add("CARD_CANDIDATE_SCAN_LIMIT")
        # A final short-lived board must not disappear just because the input
        # ended before the next cooldown slot. Drain at most MAX_PENDING_PAIRS.
        while pending and len(candidates) < max_deals * 8:
            process_observation(pending.popleft())
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
        "status": ("PRIMARY_PARTIAL_COVERAGE" if rejections["observation_queue_overflow"] else "PRIMARY_COMPLETE") if deals else "NO_FULL_LAYOUT_ACCEPTED",
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
        "max_pending_pairs": MAX_PENDING_PAIRS,
        "event_counts": dict(sorted(event_counts.items())),
        "rejections": dict(sorted(rejections.items())),
        "deals": deals[:max_deals],
        "auction_recognition": auction_observer.result() if auction_observer is not None else {"status": "UNAVAILABLE", "reason": "NO_AUCTION_PROFILE", "auctions": []},
        "canonical_promotion_allowed": False,
    }


__all__ = ["PRIMARY_VIDEO_VERSION", "PrimaryVideoRecognitionError", "PrimaryVideoInputError", "recognize_video_primary", "resolve_original_gambler_asset"]
