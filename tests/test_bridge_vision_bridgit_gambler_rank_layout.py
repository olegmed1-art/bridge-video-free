from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import bridge_vision.bridgit_gambler_rank_layout as successor
from bridge_vision.bridgit_rank_layout import BridgitRankLayoutProfile
from bridge_vision.gambler_classic_reference import RANKS, SUITS, validate_sprite_bytes


def _profile(reference_sha: str) -> BridgitRankLayoutProfile:
    slots = []
    for row, suit in enumerate("HCDS"):
        for column, rank in enumerate(RANKS):
            slots.append((rank + suit, 10 + column * 24, 10 + row * 24))
    anchors = {
        seat: {suit: (400 + index * 30, 300 + index * 10) for index, suit in enumerate("HCDS")}
        for seat in "NESW"
    }
    return BridgitRankLayoutProfile(
        profile_id="bridgit.desktop.original-asset-test.v1",
        reference_frame_sha256=reference_sha,
        verification_sha256="1" * 64,
        width=640,
        height=480,
        template_slots=tuple(slots),
        anchors=anchors,
        horizontal_search={"N": (10, 500, 60), "S": (10, 500, 400)},
        vertical_search={"W": (10, 140, 10), "E": (500, 630, 629)},
        interface_anchor=None,
        glyph_width=19,
        glyph_height=16,
        local_registration_px=2,
        binary_threshold=180,
        min_template_score=0.4,
        min_peak_score=0.7,
        min_peak_prominence=0.04,
        min_rank_ink_fraction=0.2,
        min_assignment_margin=0.12,
        min_independent_frames=2,
        profile_sha256="2" * 64,
    )


def _sprite_payload():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    image = np.full((588, 1417, 4), 255, dtype=np.uint8)
    image[:, :, 3] = 255
    cw, ch = 109, 147
    # Put a deterministic rank-specific pattern exactly in the native rank crop.
    for suit_index, suit in enumerate(SUITS):
        for rank_index, rank in enumerate(RANKS):
            x = rank_index * cw + 4
            y = suit_index * ch + 5
            image[y : y + 16, x : x + 19, :3] = 255
            image[y + 2 : y + 14, x + 2 : x + 4 + rank_index % 10, :3] = 0
            image[y + 4 : y + 6, x + 3 : x + 16, :3] = 0
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def test_derived_reference_replaces_only_template_crops() -> None:
    np = pytest.importorskip("numpy")
    payload = _sprite_payload()
    sha = hashlib.sha256(payload).hexdigest()
    sprite = validate_sprite_bytes(payload, expected_sha256=sha, expected_variant=5)
    reference = np.full((480, 640, 3), 127, dtype=np.uint8)
    profile = _profile("a" * 64)

    derived = successor.derive_original_asset_reference(reference, profile, sprite)
    assert (derived[0:5, 0:5] == 127).all()
    for card, x, y in profile.template_slots:
        assert tuple(derived[y : y + 16, x : x + 19].shape[:2]) == (16, 19)
        assert not (derived[y : y + 16, x : x + 19] == 127).all()


def test_successor_selects_sprite_by_registered_card_scale_and_reports_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    payload = _sprite_payload()
    sha = hashlib.sha256(payload).hexdigest()
    sprite_path = tmp_path / "all.png"
    sprite_path.write_bytes(payload)
    reference = np.full((480, 640, 3), 127, dtype=np.uint8)
    reference_sha = "a" * 64
    profile = _profile(reference_sha)
    captured = {}

    monkeypatch.setattr(
        successor._base,
        "_read_frame",
        lambda path, parsed_profile: (reference.copy(), reference_sha, "b" * 64, None),
    )

    def fake_recognize(reference_path, frame_paths, derived_profile, **kwargs):
        data = Path(reference_path).read_bytes()
        captured["sha"] = hashlib.sha256(data).hexdigest()
        captured["profile_sha"] = derived_profile.reference_frame_sha256
        return {
            "backend_version": "legacy-shadow",
            "status": "PENDING_TEMPORAL_CONSENSUS",
            "result_scope": "SHADOW_ONLY",
        }

    monkeypatch.setattr(successor._base, "recognize_frames", fake_recognize)
    result = successor.recognize_frames_with_original_gambler_deck(
        tmp_path / "reference.png",
        [tmp_path / "frame.png"],
        profile,
        gambler_sprite_path=sprite_path,
        gambler_sprite_sha256=sha,
        verified_card_width_px=109,
        verified_card_height_px=147,
    )

    assert captured["sha"] == captured["profile_sha"]
    assert result["successor_version"] == successor.SUCCESSOR_VERSION
    assert result["template_source"]["kind"] == "GAMBLER_CLASSIC_ORIGINAL_ASSET"
    assert result["template_source"]["variant"] == 5
    assert result["template_source"]["sprite_sha256"] == sha
    assert result["template_source"]["selection_basis"] == "VERIFIED_REGISTERED_CARD_SCALE"
    assert result["mouse_cursor_used"] is False
    assert result["hidden_hand_reconstruction_performed"] is False
    assert result["canonical_promotion_allowed"] is False
