"""Source-bound loader for original Gambler classic card sprites.

The actual Gambler PNG assets are intentionally not committed to this repository.
A caller supplies a local ``all.png`` plus its expected SHA-256.  The loader
validates the known 13x4 Gambler grid, exposes deterministic card cells and can
produce rank glyph samples for the shadow recognizer.

This module performs no network access, no hidden-hand inference and no
production/canonical writes.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RANKS = tuple("AKQJT98765432")
SUITS = tuple("CDHS")  # native Gambler sprite row order
CARDS = tuple(rank + suit for suit in SUITS for rank in RANKS)
MAX_SPRITE_BYTES = 8 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Native classic variants found in the installed Gambler client.
VARIANT_CARD_SIZE: dict[int, tuple[int, int]] = {
    1: (36, 49),
    2: (49, 66),
    3: (71, 96),
    4: (89, 120),
    5: (109, 147),
    6: (120, 180),
    7: (150, 225),
    8: (190, 285),
}
VARIANT_SPRITE_SIZE = {
    variant: (card_width * 13, card_height * 4)
    for variant, (card_width, card_height) in VARIANT_CARD_SIZE.items()
}


class GamblerClassicReferenceError(ValueError):
    """The supplied original-deck reference failed a closed validation gate."""


@dataclass(frozen=True)
class GamblerClassicSprite:
    variant: int
    sprite_sha256: str
    width: int
    height: int
    card_width: int
    card_height: int
    payload: bytes


def _read_regular_bounded(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise GamblerClassicReferenceError("sprite is unavailable") from exc
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode):
            raise GamblerClassicReferenceError("sprite must be a regular file")
        if meta.st_size <= 0 or meta.st_size > MAX_SPRITE_BYTES:
            raise GamblerClassicReferenceError("sprite size is outside the allowed bound")
        payload = os.read(fd, MAX_SPRITE_BYTES + 1)
    finally:
        os.close(fd)
    if len(payload) > MAX_SPRITE_BYTES:
        raise GamblerClassicReferenceError("sprite exceeds the byte bound")
    return payload


def png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 24 or not payload.startswith(PNG_SIGNATURE):
        raise GamblerClassicReferenceError("sprite must be PNG")
    if payload[12:16] != b"IHDR":
        raise GamblerClassicReferenceError("sprite PNG has no leading IHDR")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    if width <= 0 or height <= 0:
        raise GamblerClassicReferenceError("sprite has invalid dimensions")
    return width, height


def validate_sprite_bytes(
    payload: bytes,
    *,
    expected_sha256: str,
    expected_variant: int | None = None,
) -> GamblerClassicSprite:
    if not payload or len(payload) > MAX_SPRITE_BYTES:
        raise GamblerClassicReferenceError("sprite bytes are outside the allowed bound")
    expected_sha256 = str(expected_sha256 or "").lower()
    if not _SHA256.fullmatch(expected_sha256):
        raise GamblerClassicReferenceError("expected sprite SHA-256 is invalid")
    observed_sha = hashlib.sha256(payload).hexdigest()
    if observed_sha != expected_sha256:
        raise GamblerClassicReferenceError("sprite SHA-256 mismatch")
    width, height = png_dimensions(payload)
    matches = [variant for variant, size in VARIANT_SPRITE_SIZE.items() if size == (width, height)]
    if len(matches) != 1:
        raise GamblerClassicReferenceError("sprite dimensions are not a known classic variant")
    variant = matches[0]
    if expected_variant is not None and variant != int(expected_variant):
        raise GamblerClassicReferenceError("sprite variant does not match the expected variant")
    card_width, card_height = VARIANT_CARD_SIZE[variant]
    return GamblerClassicSprite(
        variant=variant,
        sprite_sha256=observed_sha,
        width=width,
        height=height,
        card_width=card_width,
        card_height=card_height,
        payload=payload,
    )


def load_sprite(path: Path, *, expected_sha256: str, expected_variant: int | None = None) -> GamblerClassicSprite:
    return validate_sprite_bytes(
        _read_regular_bounded(Path(path)),
        expected_sha256=expected_sha256,
        expected_variant=expected_variant,
    )


def card_box(variant: int, card: str) -> tuple[int, int, int, int]:
    try:
        card_width, card_height = VARIANT_CARD_SIZE[int(variant)]
    except (KeyError, TypeError, ValueError) as exc:
        raise GamblerClassicReferenceError("unknown classic variant") from exc
    normalized = str(card or "").upper().replace("10", "T")
    if len(normalized) != 2 or normalized[0] not in RANKS or normalized[1] not in SUITS:
        raise GamblerClassicReferenceError("invalid card identity")
    column = RANKS.index(normalized[0])
    row = SUITS.index(normalized[1])
    x0 = column * card_width
    y0 = row * card_height
    return x0, y0, x0 + card_width, y0 + card_height


def select_variant_for_card_size(
    card_width: float,
    card_height: float,
    *,
    maximum_relative_error: float = 0.08,
) -> int:
    """Select the single nearest native variant from verified registered card scale."""
    try:
        width = float(card_width)
        height = float(card_height)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GamblerClassicReferenceError("card scale must be numeric") from exc
    if width <= 0 or height <= 0 or not 0 < maximum_relative_error < 0.5:
        raise GamblerClassicReferenceError("invalid card-scale selection input")
    scored = []
    for variant, (native_width, native_height) in VARIANT_CARD_SIZE.items():
        error = max(abs(width - native_width) / native_width, abs(height - native_height) / native_height)
        scored.append((error, variant))
    scored.sort()
    best_error, best_variant = scored[0]
    if best_error > maximum_relative_error:
        raise GamblerClassicReferenceError("no classic variant matches the verified card scale")
    if len(scored) > 1 and abs(scored[1][0] - best_error) < 1e-9:
        raise GamblerClassicReferenceError("classic variant selection is ambiguous")
    return best_variant


def decode_card_cells(sprite: GamblerClassicSprite) -> dict[str, Any]:
    """Decode the source once and return exact native card-cell arrays.

    OpenCV/NumPy are optional runtime dependencies and are loaded lazily.
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on worker image
        raise RuntimeError("opencv-python-headless and numpy are required") from exc
    encoded = np.frombuffer(sprite.payload, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if image is None or tuple(image.shape[:2]) != (sprite.height, sprite.width):
        raise GamblerClassicReferenceError("sprite decode does not match validated dimensions")
    result: dict[str, Any] = {}
    for card in CARDS:
        x0, y0, x1, y1 = card_box(sprite.variant, card)
        result[card] = image[y0:y1, x0:x1].copy()
    if len(result) != 52 or any(cell.shape[:2] != (sprite.card_height, sprite.card_width) for cell in result.values()):
        raise GamblerClassicReferenceError("sprite did not produce exactly 52 native card cells")
    return result


def provenance(sprite: GamblerClassicSprite, card: str) -> dict[str, Any]:
    x0, y0, x1, y1 = card_box(sprite.variant, card)
    return {
        "kind": "GAMBLER_CLASSIC_ORIGINAL_ASSET",
        "variant": sprite.variant,
        "sprite_sha256": sprite.sprite_sha256,
        "card": str(card).upper().replace("10", "T"),
        "cell": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
        "network_access_used": False,
    }
