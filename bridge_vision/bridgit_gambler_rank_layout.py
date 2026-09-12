"""Successor shadow adapter that replaces screenshot rank templates with Gambler assets.

The existing Bridgit rank-layout recognizer remains unchanged and available as
its historical baseline.  This opt-in successor derives a temporary reference
frame from the already verified UI reference, replacing only the 52 rank-glyph
crops with source-bound original Gambler classic artwork.  The base recognizer
then builds its ordinary rank bank from those original-client glyphs.

No Gambler asset bytes are committed.  The caller supplies a local sprite plus
its SHA-256 and a verified registered card scale.  No network access, cursor
input, hidden-hand completion, Canon write, or production activation occurs.
"""
from __future__ import annotations

import hashlib
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

import bridge_vision.bridgit_rank_layout as _base
from bridge_vision.gambler_classic_reference import (
    GamblerClassicReferenceError,
    GamblerClassicSprite,
    bank_provenance,
    decode_card_cells,
    load_sprite,
    select_variant_for_card_size,
)

SUCCESSOR_VERSION = "bridge-vision-bridgit-rank-layout-gambler-classic-v1"
_RANK_CROP_X_FRACTION = 4 / 109
_RANK_CROP_Y_FRACTION = 5 / 147


class BridgitGamblerRankLayoutError(ValueError):
    """Original-asset successor cannot continue without weakening a gate."""


def _interface_anchor_rect(profile: _base.BridgitRankLayoutProfile) -> tuple[int, int, int, int] | None:
    spec = profile.interface_anchor
    if not spec:
        return None
    region = spec.get("reference_region")
    if not isinstance(region, dict):
        return None
    try:
        x0 = round(float(region["x"]) * profile.width)
        y0 = round(float(region["y"]) * profile.height)
        x1 = round(float(region["x"] + region["width"]) * profile.width)
        y1 = round(float(region["y"] + region["height"]) * profile.height)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return x0, y0, x1, y1


def _overlaps(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(left[1], right[1])


def derive_original_asset_reference(
    verified_reference_image: Any,
    profile: _base.BridgitRankLayoutProfile,
    sprite: GamblerClassicSprite,
) -> Any:
    """Return a copy whose rank-template crops come from the original client sprite."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependent
        raise RuntimeError("opencv-python-headless and numpy are required") from exc

    if tuple(verified_reference_image.shape[:2]) != (profile.height, profile.width):
        raise BridgitGamblerRankLayoutError("verified reference dimensions do not match profile")
    derived = verified_reference_image.copy()
    cells = decode_card_cells(sprite)
    crop_x = max(1, round(sprite.card_width * _RANK_CROP_X_FRACTION))
    crop_y = max(1, round(sprite.card_height * _RANK_CROP_Y_FRACTION))
    if crop_x + profile.glyph_width > sprite.card_width or crop_y + profile.glyph_height > sprite.card_height:
        raise BridgitGamblerRankLayoutError("original-asset rank crop leaves native card cell")

    anchor_rect = _interface_anchor_rect(profile)
    for card, x, y in profile.template_slots:
        target = (x, y, x + profile.glyph_width, y + profile.glyph_height)
        if anchor_rect is not None and _overlaps(target, anchor_rect):
            raise BridgitGamblerRankLayoutError("template replacement overlaps interface anchor")
        cell = cells[card]
        if cell.ndim == 3 and cell.shape[2] == 4:
            source = cv2.cvtColor(cell, cv2.COLOR_BGRA2BGR)
        elif cell.ndim == 3 and cell.shape[2] == 3:
            source = cell
        elif cell.ndim == 2:
            source = cv2.cvtColor(cell, cv2.COLOR_GRAY2BGR)
        else:
            raise BridgitGamblerRankLayoutError("unsupported original-asset card raster")
        patch = source[
            crop_y : crop_y + profile.glyph_height,
            crop_x : crop_x + profile.glyph_width,
        ]
        if tuple(patch.shape[:2]) != (profile.glyph_height, profile.glyph_width):
            raise BridgitGamblerRankLayoutError("original-asset rank crop is incomplete")
        derived[y : y + profile.glyph_height, x : x + profile.glyph_width] = patch

    if not isinstance(derived, np.ndarray):
        raise BridgitGamblerRankLayoutError("derived reference is not a raster")
    return derived


def recognize_frames_with_original_gambler_deck(
    reference_frame: Path,
    frame_paths: Sequence[Path],
    profile: _base.BridgitRankLayoutProfile,
    *,
    gambler_sprite_path: Path,
    gambler_sprite_sha256: str,
    verified_card_width_px: float,
    verified_card_height_px: float,
    expected_frame_sha256s: Sequence[str] | None = None,
    observation_timestamps_ms: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Run the existing shadow recognizer with original Gambler rank templates.

    The native sprite variant is selected from the verified *registered card
    scale*, never from source-video resolution or window position.
    """
    try:
        expected_variant = select_variant_for_card_size(
            verified_card_width_px, verified_card_height_px
        )
        sprite = load_sprite(
            Path(gambler_sprite_path),
            expected_sha256=gambler_sprite_sha256,
            expected_variant=expected_variant,
        )
    except GamblerClassicReferenceError as exc:
        raise BridgitGamblerRankLayoutError(f"invalid Gambler classic reference: {exc}") from exc

    # Use the base decoder so the same byte/raster safety boundary and profile
    # dimensions apply to the human-reviewed UI reference.
    reference, reference_hash, _, _ = _base._read_frame(Path(reference_frame), profile)
    if reference_hash != profile.reference_frame_sha256:
        raise BridgitGamblerRankLayoutError("verified reference frame hash mismatch")
    derived = derive_original_asset_reference(reference, profile, sprite)

    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependent
        raise RuntimeError("opencv-python-headless is required") from exc
    encoded_ok, encoded = cv2.imencode(".png", derived)
    if not encoded_ok:
        raise BridgitGamblerRankLayoutError("cannot encode derived reference")
    derived_bytes = encoded.tobytes()
    derived_sha = hashlib.sha256(derived_bytes).hexdigest()
    derived_profile = replace(profile, reference_frame_sha256=derived_sha)

    with tempfile.TemporaryDirectory(prefix="bridgit-gambler-reference-") as directory:
        derived_path = Path(directory) / "derived-reference.png"
        derived_path.write_bytes(derived_bytes)
        result = _base.recognize_frames(
            derived_path,
            frame_paths,
            derived_profile,
            expected_frame_sha256s=expected_frame_sha256s,
            observation_timestamps_ms=observation_timestamps_ms,
        )

    result = dict(result)
    result["successor_version"] = SUCCESSOR_VERSION
    result["template_source"] = {
        **bank_provenance(sprite),
        "selection_basis": "VERIFIED_REGISTERED_CARD_SCALE",
        "verified_card_width_px": float(verified_card_width_px),
        "verified_card_height_px": float(verified_card_height_px),
        "legacy_reference_frame_sha256": profile.reference_frame_sha256,
        "derived_reference_frame_sha256": derived_sha,
        "replacement_scope": "RANK_TEMPLATE_CROPS_ONLY",
    }
    result["mouse_cursor_used"] = False
    result["hidden_hand_reconstruction_performed"] = False
    result["canonical_promotion_allowed"] = False
    return result
