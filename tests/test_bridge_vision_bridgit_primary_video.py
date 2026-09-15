from pathlib import Path

import pytest

from bridge_vision.bridgit_primary_video import (
    PrimaryVideoRecognitionError,
    resolve_original_gambler_asset,
)
from bridge_vision.gambler_reference_authority import approved_sprite_sha256


def test_resolver_selects_native_variant_5_from_verified_card_scale(tmp_path: Path):
    source = Path("/home/ubuntu/bridge-school-private-media/gambler/all-v5.png")
    if not source.is_file():
        pytest.skip("private approved Gambler v5 fixture unavailable")
    target = tmp_path / "all-v5.png"
    target.write_bytes(source.read_bytes())
    variant, path, sha = resolve_original_gambler_asset(
        tmp_path,
        verified_card_width_px=109.0,
        verified_card_height_px=147.0,
    )
    assert variant == 5
    assert path == target
    assert sha == approved_sprite_sha256(5)


def test_resolver_fails_closed_when_selected_variant_is_missing(tmp_path: Path):
    with pytest.raises(
        PrimaryVideoRecognitionError,
        match="approved Gambler classic variant 6 is unavailable",
    ):
        resolve_original_gambler_asset(
            tmp_path,
            verified_card_width_px=120.0,
            verified_card_height_px=180.0,
        )


def test_resolver_accepts_client_directory_layout(tmp_path: Path):
    source = Path("/home/ubuntu/bridge-school-private-media/gambler/all-v5.png")
    if not source.is_file():
        pytest.skip("private approved Gambler v5 fixture unavailable")
    target = tmp_path / "5" / "all.png"
    target.parent.mkdir()
    target.write_bytes(source.read_bytes())
    variant, path, sha = resolve_original_gambler_asset(
        tmp_path,
        verified_card_width_px=109.0,
        verified_card_height_px=147.0,
    )
    assert (variant, path, sha) == (5, target, approved_sprite_sha256(5))
