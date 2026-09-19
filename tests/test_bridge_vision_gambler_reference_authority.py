from __future__ import annotations

import pytest

from bridge_vision.gambler_reference_authority import (
    PINNED_GAMBLER_CLASSIC_SPRITE_SHA256,
    GamblerReferenceAuthorityError,
    REFERENCE_AUTHORITY_VERSION,
    pinned_sprite_sha256,
    assert_pinned_sprite_binding,
    authority_provenance,
    variant_for_pinned_sprite_sha256,
)


def test_all_original_gambler_variants_are_bound_to_fixed_sha256() -> None:
    assert set(PINNED_GAMBLER_CLASSIC_SPRITE_SHA256) == set(range(1, 9))
    assert all(
        len(value) == 64 and value == value.lower()
        for value in PINNED_GAMBLER_CLASSIC_SPRITE_SHA256.values()
    )


def test_pinned_original_identity_passes_with_mechanical_provenance() -> None:
    sha = pinned_sprite_sha256(5)
    assert assert_pinned_sprite_binding(5, sha.upper()) == sha
    assert authority_provenance(5) == {
        "reference_authority": REFERENCE_AUTHORITY_VERSION,
        "identity_check": "FIXED_SHA256_ALLOWLIST",
        "structural_decode": "52_CARD_GRID_REQUIRED",
        "pinned_sprite_sha256": sha,
    }
    assert variant_for_pinned_sprite_sha256(sha.upper()) == 5


def test_nonpinned_asset_is_rejected_even_if_caller_supplies_a_valid_hash() -> None:
    with pytest.raises(GamblerReferenceAuthorityError, match="pinned original"):
        assert_pinned_sprite_binding(5, "0" * 64)
    with pytest.raises(GamblerReferenceAuthorityError, match="pinned allowlist"):
        variant_for_pinned_sprite_sha256("0" * 64)


def test_unknown_or_boolean_variant_is_rejected() -> None:
    with pytest.raises(GamblerReferenceAuthorityError):
        pinned_sprite_sha256(99)
    with pytest.raises(GamblerReferenceAuthorityError):
        pinned_sprite_sha256(True)
