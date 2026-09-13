from __future__ import annotations

import hashlib
import struct
import zlib

import pytest

from bridge_vision.gambler_classic_reference import (
    GamblerClassicReferenceError,
    RANKS,
    VARIANT_CARD_SIZE,
    VARIANT_SPRITE_SIZE,
    bank_provenance,
    build_rank_template_bank,
    card_box,
    png_dimensions,
    select_variant_for_card_size,
    validate_sprite_bytes,
)


def _png(width: int, height: int) -> bytes:
    # Minimal deterministic RGBA PNG; sufficient for header/identity validation.
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\xff\xff\xff" * width for _ in range(height))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_known_native_variant_sizes_are_exact_grid() -> None:
    for variant, (cw, ch) in VARIANT_CARD_SIZE.items():
        assert VARIANT_SPRITE_SIZE[variant] == (cw * 13, ch * 4)


def test_png_dimensions_reads_ihdr() -> None:
    payload = _png(1417, 588)
    assert png_dimensions(payload) == (1417, 588)


def test_validate_variant_5_by_sha_and_dimensions() -> None:
    payload = _png(1417, 588)
    sha = hashlib.sha256(payload).hexdigest()
    sprite = validate_sprite_bytes(payload, expected_sha256=sha, expected_variant=5)
    assert sprite.variant == 5
    assert (sprite.card_width, sprite.card_height) == (109, 147)


def test_validate_fails_closed_on_wrong_sha() -> None:
    payload = _png(1417, 588)
    with pytest.raises(GamblerClassicReferenceError, match="SHA-256 mismatch"):
        validate_sprite_bytes(payload, expected_sha256="0" * 64, expected_variant=5)


def test_validate_rejects_unknown_dimensions() -> None:
    payload = _png(1400, 588)
    sha = hashlib.sha256(payload).hexdigest()
    with pytest.raises(GamblerClassicReferenceError, match="not a known classic variant"):
        validate_sprite_bytes(payload, expected_sha256=sha)


def test_card_grid_order_matches_original_client() -> None:
    assert card_box(5, "AC") == (0, 0, 109, 147)
    assert card_box(5, "2C") == (1308, 0, 1417, 147)
    assert card_box(5, "AD") == (0, 147, 109, 294)
    assert card_box(5, "AH") == (0, 294, 109, 441)
    assert card_box(5, "AS") == (0, 441, 109, 588)
    assert card_box(5, "2S") == (1308, 441, 1417, 588)


def test_scale_selects_variant_5_for_verified_native_size() -> None:
    assert select_variant_for_card_size(109, 147) == 5
    assert select_variant_for_card_size(110, 146) == 5


def test_scale_rejects_unmatched_card_size() -> None:
    with pytest.raises(GamblerClassicReferenceError, match="no classic variant"):
        select_variant_for_card_size(75, 160)


def test_original_asset_rank_bank_is_complete_and_deterministic() -> None:
    pytest.importorskip("cv2")
    pytest.importorskip("numpy")
    payload = _png(1417, 588)
    sprite = validate_sprite_bytes(
        payload,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_variant=5,
    )
    first = build_rank_template_bank(
        sprite,
        glyph_width=19,
        glyph_height=16,
        binary_threshold=180,
        local_registration_px=2,
    )
    second = build_rank_template_bank(
        sprite,
        glyph_width=19,
        glyph_height=16,
        binary_threshold=180,
        local_registration_px=2,
    )
    assert set(first) == set(RANKS)
    assert all(first[rank].shape == (100, 19 * 16) for rank in RANKS)
    assert all((first[rank] == second[rank]).all() for rank in RANKS)


def test_original_asset_bank_provenance_is_source_bound() -> None:
    payload = _png(1417, 588)
    sha = hashlib.sha256(payload).hexdigest()
    sprite = validate_sprite_bytes(payload, expected_sha256=sha, expected_variant=5)
    evidence = bank_provenance(sprite)
    assert evidence == {
        "kind": "GAMBLER_CLASSIC_ORIGINAL_ASSET",
        "variant": 5,
        "sprite_sha256": sha,
        "card_width": 109,
        "card_height": 147,
        "rank_order": list(RANKS),
        "suit_row_order": list("CDHS"),
        "network_access_used": False,
    }
