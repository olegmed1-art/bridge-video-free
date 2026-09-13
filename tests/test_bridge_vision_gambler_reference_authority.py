from __future__ import annotations

import pytest

from bridge_vision.gambler_reference_authority import (
    APPROVED_GAMBLER_CLASSIC_SPRITE_SHA256,
    GamblerReferenceAuthorityError,
    REFERENCE_AUTHORITY_VERSION,
    approved_sprite_sha256,
    assert_approved_sprite_binding,
    authority_provenance,
)


def test_all_original_gambler_variants_are_bound_to_fixed_sha256() -> None:
    assert set(APPROVED_GAMBLER_CLASSIC_SPRITE_SHA256) == set(range(1, 9))
    assert all(
        len(value) == 64 and value == value.lower()
        for value in APPROVED_GAMBLER_CLASSIC_SPRITE_SHA256.values()
    )


def test_approved_original_identity_passes_without_card_content_revalidation() -> None:
    sha = approved_sprite_sha256(5)
    assert assert_approved_sprite_binding(5, sha.upper()) == sha
    assert authority_provenance(5) == {
        "reference_authority": REFERENCE_AUTHORITY_VERSION,
        "human_approved_original": True,
        "template_content_revalidation_required": False,
        "approved_sprite_sha256": sha,
    }


def test_nonapproved_asset_is_rejected_even_if_caller_supplies_a_valid_hash() -> None:
    with pytest.raises(GamblerReferenceAuthorityError, match="human-approved original"):
        assert_approved_sprite_binding(5, "0" * 64)


def test_unknown_or_boolean_variant_is_rejected() -> None:
    with pytest.raises(GamblerReferenceAuthorityError):
        approved_sprite_sha256(99)
    with pytest.raises(GamblerReferenceAuthorityError):
        approved_sprite_sha256(True)
