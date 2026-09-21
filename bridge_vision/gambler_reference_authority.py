"""Mechanical identity authority for original Gambler classic card artwork.

The hashes in this module identify the original card sprites supplied from the
installed Gambler client. Runtime accepts only a fixed SHA-256 allowlist and the
sprite loader separately enforces the expected dimensions and 52-card grid.
"""
from __future__ import annotations

import re

REFERENCE_AUTHORITY_VERSION = "gambler-classic-original-hash-bound-v2"

PINNED_GAMBLER_CLASSIC_SPRITE_SHA256: dict[int, str] = {
    1: "627cc3170c39d19304d81b54e92144714bddb7b3917e21c66bd221c31bd82390",
    2: "53b9a93fb25c7f4efd367b5219ae664c8b1b7b2e1243df5383f37df779ef2c90",
    3: "0eee20f34a8fe380c06231f93129c5ff32acd2a31d790797e06f12b436dd2d8f",
    4: "c59dd0cafb91506fdbd5513bf217061f816ea4d3fb956982f79a8859022c9c57",
    5: "52eb9c97c9685758f48beb6e84013e9289bdb97f8a31b4ae8e46a0ecee3a37fb",
    6: "eb47ec363bc2824587c5ab0f3897ae52c5d98e26a8403d9605353c3cbdf99517",
    7: "c653e77694592f2059196f17c26f817a7e0c5a2ad6162014cc5ca5a5577c2695",
    8: "22955238debcaefb4ccd797f5a1a3fb4590b3646e6061c314ba122cfa19689d0",
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GamblerReferenceAuthorityError(ValueError):
    """The supplied sprite is not bound to the pinned original reference."""


def pinned_sprite_sha256(variant: int) -> str:
    if isinstance(variant, bool):
        raise GamblerReferenceAuthorityError("invalid Gambler classic variant")
    try:
        return PINNED_GAMBLER_CLASSIC_SPRITE_SHA256[int(variant)]
    except (KeyError, TypeError, ValueError) as exc:
        raise GamblerReferenceAuthorityError("unknown Gambler classic variant") from exc


def assert_pinned_sprite_binding(variant: int, supplied_sha256: str) -> str:
    """Return the canonical pinned hash or fail closed on a different asset."""
    normalized = str(supplied_sha256 or "").lower()
    if _SHA256.fullmatch(normalized) is None:
        raise GamblerReferenceAuthorityError("invalid Gambler sprite SHA-256")
    pinned = pinned_sprite_sha256(variant)
    if normalized != pinned:
        raise GamblerReferenceAuthorityError(
            "Gambler sprite does not match the pinned original asset for this variant"
        )
    return pinned


def variant_for_pinned_sprite_sha256(supplied_sha256: str) -> int:
    normalized = str(supplied_sha256 or "").lower()
    if _SHA256.fullmatch(normalized) is None:
        raise GamblerReferenceAuthorityError("invalid Gambler sprite SHA-256")
    matches = [
        variant
        for variant, pinned in PINNED_GAMBLER_CLASSIC_SPRITE_SHA256.items()
        if pinned == normalized
    ]
    if len(matches) != 1:
        raise GamblerReferenceAuthorityError("Gambler sprite is not in the pinned allowlist")
    return matches[0]


def authority_provenance(variant: int) -> dict[str, object]:
    return {
        "reference_authority": REFERENCE_AUTHORITY_VERSION,
        "identity_check": "FIXED_SHA256_ALLOWLIST",
        "structural_decode": "52_CARD_GRID_REQUIRED",
        "pinned_sprite_sha256": pinned_sprite_sha256(variant),
    }
