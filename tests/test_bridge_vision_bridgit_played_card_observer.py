from __future__ import annotations

import hashlib
import json

import pytest

from bridge_vision.bridgit_played_card_observer import (
    build_suit_bank,
    build_table_geometry_bank,
    detect_table_geometry,
    observe_played_cards,
)
from bridge_vision.bridgit_visible_hand_observer import (
    PROFILE_SCHEMA,
    VisibleHandObserverError,
    build_rank_bank,
    parse_profile,
)

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

WIDTH = 1000
HEIGHT = 720
RANKS = "AKQJT98765432"
SUITS = "HCDS"


def canonical_hash(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def rank_patterns() -> dict[str, np.ndarray]:
    result = {}
    for index, rank in enumerate(RANKS):
        rng = np.random.default_rng(index + 301)
        pattern = (rng.random((8, 8)) > 0.45).astype(np.uint8)
        pattern[0, :] = 0
        pattern[:, 0] = 0
        result[rank] = pattern
    return result


def suit_patterns() -> dict[str, np.ndarray]:
    return {
        "H": np.array(
            [
                [0, 1, 1, 0, 1, 1, 0, 0],
                [1, 1, 1, 1, 1, 1, 1, 0],
                [1, 1, 1, 1, 1, 1, 1, 0],
                [0, 1, 1, 1, 1, 1, 0, 0],
                [0, 0, 1, 1, 1, 0, 0, 0],
                [0, 0, 0, 1, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        ),
        "C": np.array(
            [
                [0, 0, 1, 1, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 0, 0, 0],
                [1, 1, 1, 1, 1, 1, 0, 0],
                [0, 1, 1, 1, 1, 0, 0, 0],
                [0, 0, 1, 1, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 0, 0, 0],
                [0, 0, 1, 1, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        ),
        "D": np.array(
            [
                [0, 0, 0, 1, 0, 0, 0, 0],
                [0, 0, 1, 1, 1, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 0, 0],
                [1, 1, 1, 1, 1, 1, 1, 0],
                [0, 1, 1, 1, 1, 1, 0, 0],
                [0, 0, 1, 1, 1, 0, 0, 0],
                [0, 0, 0, 1, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        ),
        "S": np.array(
            [
                [0, 0, 0, 1, 0, 0, 0, 0],
                [0, 0, 1, 1, 1, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 0, 0],
                [1, 1, 1, 1, 1, 1, 1, 0],
                [0, 0, 0, 1, 0, 0, 0, 0],
                [0, 0, 1, 1, 1, 0, 0, 0],
                [0, 0, 0, 1, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        ),
    }


def paint_glyph(image, x: int, y: int, pattern, color) -> None:
    pixels = np.where(pattern[..., None] == 1, np.array(color), 255)
    image[y : y + 8, x : x + 8] = pixels


def paint_cardback_texture(image, x: int, y: int) -> None:
    """Paint a distinctive synthetic stand-in for a reviewed Bridgit back."""

    cv2.ellipse(image, (x + 25, y + 25), (14, 19), 0, 0, 360, (245, 245, 245), 3)
    cv2.line(image, (x + 14, y + 33), (x + 36, y + 17), (20, 20, 20), 3)
    cv2.circle(image, (x + 25, y + 25), 5, (245, 245, 245), -1)


def paint_seat_landmarks(image) -> None:
    blue = (200, 130, 80)
    image[210:410, 15:72] = blue
    image[210:410, 685:742] = blue
    paint_cardback_texture(image, 15, 210)
    paint_cardback_texture(image, 685, 210)
    for y, cards in ((100, "KQJT"), (500, "2356")):
        for x, rank, suit in zip((210, 320, 430, 540), cards, ("H", "C", "D", "S")):
            image[y : y + 80, x : x + 100] = 255
            paint_glyph(image, x + 8, y, rank_patterns()[rank], (0, 0, 0))
            color = (0, 0, 220) if suit in "HD" else (0, 0, 0)
            paint_glyph(image, x + 8, y + 12, suit_patterns()[suit], color)


def paint_reflowed_seat_landmarks(image) -> None:
    blue = (200, 130, 80)
    image[225:425, 75:132] = blue
    image[225:425, 625:682] = blue
    paint_cardback_texture(image, 75, 225)
    paint_cardback_texture(image, 625, 225)
    for y, cards in ((100, "KQJT"), (500, "2356")):
        for x, rank, suit in zip((160, 270, 380, 490), cards, ("H", "C", "D", "S")):
            image[y : y + 80, x : x + 100] = 255
            paint_glyph(image, x + 8, y, rank_patterns()[rank], (0, 0, 0))
            color = (0, 0, 220) if suit in "HD" else (0, 0, 0)
            paint_glyph(image, x + 8, y + 12, suit_patterns()[suit], color)


def paint_solid_seat_landmarks(image) -> None:
    blue = (200, 130, 80)
    image[55:105, 290:460] = blue
    image[210:410, 15:72] = blue
    image[210:410, 685:742] = blue
    for x in (210, 320, 430, 540):
        image[500:580, x : x + 100] = 255


def paint_generic_badge_seat_landmarks(image) -> None:
    """Adversarial blue UI blocks with non-cardback white/dark badges."""

    paint_solid_seat_landmarks(image)
    for x in (15, 685):
        image[220:255, x + 9 : x + 40] = 245
        image[231:242, x + 19 : x + 32] = 20


def verified_hand_cards(*, seat: str = "S", y: int = 500, x_start: int = 210):
    return [
        {
            "card": card,
            "seat": seat,
            "source": "HAND",
            "confidence": 1.0,
            "evidence_pixel_sha256": f"{index + 1:064x}",
            "region": {
                "x": x_start + index * 36,
                "y": y - 4,
                "width": 23,
                "height": 56,
            },
        }
        for index, card in enumerate(("AS", "KH", "QD"))
    ]


def verified_open_hands(
    *, north_y: int = 100, south_y: int = 500, south_x_start: int = 210
):
    return [
        *verified_hand_cards(seat="N", y=north_y),
        *verified_hand_cards(seat="S", y=south_y, x_start=south_x_start),
    ]


def reference_image():
    image = np.full((HEIGHT, WIDTH, 3), (20, 120, 20), dtype=np.uint8)
    starts = (60, 260, 460, 660)
    coordinates = {}
    per_suit = {suit: [] for suit in SUITS}
    for index, rank in enumerate(RANKS):
        per_suit[SUITS[index % 4]].append(rank)
    for suit, start in zip(SUITS, starts):
        image[100:152, start : start + 170] = 255
        for index, rank in enumerate(per_suit[suit]):
            x = start + 8 + index * 36
            coordinates[rank] = x
            paint_glyph(image, x, 100, rank_patterns()[rank], (0, 0, 0))
            color = (0, 0, 220) if suit in "HD" else (0, 0, 0)
            paint_glyph(image, x, 112, suit_patterns()[suit], color)
    blue = (200, 130, 80)
    image[210:410, 15:72] = blue
    image[210:410, 685:742] = blue
    paint_cardback_texture(image, 15, 210)
    paint_cardback_texture(image, 685, 210)
    return image, coordinates


def raw_profile(coordinates: dict[str, int]) -> dict:
    raw = {
        "schema": PROFILE_SCHEMA,
        "profile_id": "bridgit.played.synthetic.v1",
        "human_verified": True,
        "frame_size": {"width": WIDTH, "height": HEIGHT},
        "verification": {
            "method": "HUMAN_LABEL_REVIEW_IN_CHATGPT_WORK",
            "reviewer_id": "Oleg",
            "verified_at": "2026-09-11T03:34:43Z",
            "review_sheet_sha256": "a" * 64,
        },
        "pixel": {"binary_threshold": 180, "rank_width": 8, "rank_height": 8},
        "rows": {
            "N": {"y": 100, "x_min": 50, "x_max": 850},
            "S": {"y": 500, "x_min": 50, "x_max": 850},
        },
        "references": {"ref": {"path": "ref.png", "sha256": "b" * 64}},
        "rank_templates": {
            rank: [{"reference_id": "ref", "x": coordinates[rank], "y": 100}]
            for rank in RANKS
        },
        "gates": {
            "min_rank_score": 0.99,
            "min_rank_margin": 0.10,
            "min_run_width": 60,
            "min_suit_runs": 3,
            "min_rank_gap": 15,
            "red_dark_ratio": 0.18,
        },
    }
    raw["profile_sha256"] = canonical_hash(raw)
    return raw


def geometry_bank(profile, reference):
    return build_table_geometry_bank(profile, {"ref": reference})


def played_frame(
    rank: str,
    suit: str,
    *,
    x: int = 348,
    y: int = 190,
    seat_painter=paint_seat_landmarks,
):
    image = np.full((HEIGHT, WIDTH, 3), (20, 120, 20), dtype=np.uint8)
    seat_painter(image)
    image[y : y + 72, x : x + 54] = 255
    paint_glyph(image, x + 2, y + 2, rank_patterns()[rank], (0, 0, 0))
    color = (0, 0, 220) if suit in "HD" else (0, 0, 0)
    paint_glyph(image, x + 2, y + 14, suit_patterns()[suit], color)
    return image


@pytest.mark.parametrize(
    ("rank", "suit"), [("A", "H"), ("7", "C"), ("4", "D"), ("T", "S")]
)
def test_recognizes_played_card_rank_suit_and_owner(rank: str, suit: str) -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})

    result = observe_played_cards(
        played_frame(rank, suit),
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert result["status"] == "SHADOW_PLAYED_CARDS"
    assert [(item["card"], item["seat"]) for item in result["cards"]] == [
        (rank + suit, "N")
    ]
    assert result["cards"][0]["source"] == "PLAYED"
    assert result["cards"][0]["confidence"] >= 0.99
    assert result["cards"][0]["card_fill"] >= 0.95
    assert len(result["cards"][0]["layout_geometry_sha256"]) == 64
    assert len(result["cards"][0]["layout_transform_sha256"]) == 64
    assert len(result["cards"][0]["card_scale_policy_sha256"]) == 64
    assert len(result["cards"][0]["card_scale_measurement_sha256"]) == 64
    assert 0.895 <= result["cards"][0]["played_card_width_ratio"] <= 0.990
    assert len(result["cards"][0]["evidence_pixel_sha256"]) == 64


def test_table_geometry_uses_live_seat_axes_not_fixed_anchor_distance() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    image = played_frame("A", "H")

    geometry = detect_table_geometry(
        image,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert set(geometry["anchors"]) == {"N", "E", "S", "W"}
    assert geometry["anchors"]["N"]["source"] == "VERIFIED_VISIBLE_HAND_ROW_Y"
    assert geometry["anchors"]["S"]["source"] == "VERIFIED_VISIBLE_HAND_ROW_Y"
    assert len(geometry["geometry_sha256"]) == 64
    assert len(geometry["transform_sha256"]) == 64


def test_responsive_reflow_never_turns_north_card_into_east_claim() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})

    result = observe_played_cards(
        played_frame("A", "H", x=498, y=190),
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert not any(item["seat"] == "E" for item in result["cards"])
    assert result["status"] in {"REVIEW", "SHADOW_PLAYED_CARDS"}


@pytest.mark.parametrize(
    ("x", "expected_seat"),
    [
        (70, "W"),
        (625, "E"),
    ],
)
def test_bridgit_side_trick_cards_near_live_trays_are_retained(
    x: int, expected_seat: str
) -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})

    result = observe_played_cards(
        played_frame("A", "H", x=x, y=290),
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert [(item["card"], item["seat"]) for item in result["cards"]] == [
        ("AH", expected_seat)
    ]
    assert abs(result["cards"][0]["seat_coordinates"]["horizontal"]) > 0.78


def test_live_geometry_is_recomputed_after_same_resolution_table_reflow() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})
    before = played_frame("A", "H")
    after = played_frame("A", "H", seat_painter=paint_reflowed_seat_landmarks)

    before_result = observe_played_cards(
        before,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )
    after_result = observe_played_cards(
        after,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert [(item["card"], item["seat"]) for item in before_result["cards"]] == [
        ("AH", "N")
    ]
    assert [(item["card"], item["seat"]) for item in after_result["cards"]] == [
        ("AH", "N")
    ]
    assert (
        before_result["layout_geometry"]["transform_sha256"]
        != after_result["layout_geometry"]["transform_sha256"]
    )


def test_depleted_hand_width_cannot_rotate_played_card_ownership() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})
    frame = played_frame("A", "H", x=340, y=260)

    left_hand = observe_played_cards(
        frame,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(south_x_start=50),
    )
    right_hand = observe_played_cards(
        frame,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(south_x_start=500),
    )

    assert not any(item["seat"] == "E" for item in left_hand["cards"])
    assert not any(item["seat"] == "E" for item in right_hand["cards"])
    assert [(item["card"], item["seat"]) for item in left_hand["cards"]] == [
        (item["card"], item["seat"]) for item in right_hand["cards"]
    ]
    assert (
        left_hand["layout_geometry"]["transform_sha256"]
        == right_hand["layout_geometry"]["transform_sha256"]
    )


def test_exact_breakpoint_profile_recognizes_scaled_cards_and_table() -> None:
    scale = 1.5
    reference, coordinates = reference_image()
    scaled_reference = cv2.resize(
        reference,
        (round(WIDTH * scale), round(HEIGHT * scale)),
        interpolation=cv2.INTER_NEAREST,
    )
    raw = raw_profile(coordinates)
    raw["profile_id"] = "bridgit.played.synthetic.large.v1"
    raw["frame_size"] = {
        "width": round(WIDTH * scale),
        "height": round(HEIGHT * scale),
    }
    raw["pixel"]["rank_width"] = round(8 * scale)
    raw["pixel"]["rank_height"] = round(8 * scale)
    for row in raw["rows"].values():
        for field in ("y", "x_min", "x_max"):
            row[field] = round(row[field] * scale)
    for items in raw["rank_templates"].values():
        for item in items:
            item["x"] = round(item["x"] * scale)
            item["y"] = round(item["y"] * scale)
    raw["gates"]["min_run_width"] = round(raw["gates"]["min_run_width"] * scale)
    raw["gates"]["min_rank_gap"] = round(raw["gates"]["min_rank_gap"] * scale)
    raw.pop("profile_sha256")
    raw["profile_sha256"] = canonical_hash(raw)
    profile = parse_profile(raw)
    rank_bank = build_rank_bank(profile, {"ref": scaled_reference})
    suit_bank = build_suit_bank(profile, {"ref": scaled_reference})
    frame = cv2.resize(
        played_frame("A", "H"),
        (profile.width, profile.height),
        interpolation=cv2.INTER_NEAREST,
    )

    result = observe_played_cards(
        frame,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, scaled_reference),
        visible_hand_cards=verified_open_hands(
            north_y=raw["rows"]["N"]["y"],
            south_y=raw["rows"]["S"]["y"],
        ),
    )

    assert [(item["card"], item["seat"]) for item in result["cards"]] == [("AH", "N")]
    assert result["layout_geometry"]["coordinate_space"] == ("NORMALIZED_PROFILE_FRAME")


def test_missing_live_seat_landmark_rejects_played_claim() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})
    frame = played_frame("A", "H")
    frame[205:415, 680:740] = (20, 120, 20)

    result = observe_played_cards(
        frame,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert result["status"] == "REVIEW"
    assert result["cards"] == []
    assert result["layout_geometry"] is None
    assert result["rejected"][0]["reason"] == "SEAT_LAYOUT_UNPROVEN"


def test_ambiguous_live_seat_landmark_rejects_played_claim() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})
    frame = played_frame("A", "H")
    frame[210:410, 610:667] = (200, 130, 80)
    paint_cardback_texture(frame, 610, 210)

    result = observe_played_cards(
        frame,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert result["status"] == "REVIEW"
    assert result["cards"] == []
    assert result["layout_geometry"] is None
    assert result["rejected"][0]["reason"] == "SEAT_LAYOUT_UNPROVEN"


def test_solid_color_shapes_do_not_prove_bridgit_seat_geometry() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})
    frame = played_frame("A", "H", seat_painter=paint_solid_seat_landmarks)

    result = observe_played_cards(
        frame,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert result["status"] == "REVIEW"
    assert result["cards"] == []
    assert result["layout_geometry"] is None
    assert result["rejected"][0]["reason"] == "SEAT_LAYOUT_UNPROVEN"


def test_generic_white_dark_badges_do_not_spoof_reviewed_cardbacks() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})
    frame = played_frame("A", "H", seat_painter=paint_generic_badge_seat_landmarks)

    result = observe_played_cards(
        frame,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert result["status"] == "REVIEW"
    assert result["cards"] == []
    assert result["layout_geometry"] is None
    assert result["rejected"][0]["reason"] == "SEAT_LAYOUT_UNPROVEN"


def test_geometry_bank_from_another_profile_fails_closed() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    other_raw = raw_profile(coordinates)
    other_raw["profile_id"] = "bridgit.played.synthetic.other.v1"
    other_raw.pop("profile_sha256")
    other_raw["profile_sha256"] = canonical_hash(other_raw)
    other_profile = parse_profile(other_raw)

    result = observe_played_cards(
        played_frame("A", "H"),
        build_rank_bank(profile, {"ref": reference}),
        build_suit_bank(profile, {"ref": reference}),
        profile,
        geometry_bank=geometry_bank(other_profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert result["status"] == "REVIEW"
    assert result["cards"] == []
    assert result["rejected"][0]["reason"] == "SEAT_LAYOUT_UNPROVEN"


def test_geometry_bank_without_vertical_cardback_fails_closed() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    reference[200:420, 0:760] = (20, 120, 20)

    with pytest.raises(VisibleHandObserverError, match="vertical Bridgit card backs"):
        geometry_bank(profile, reference)


@pytest.mark.parametrize("scale", [0.90, 1.10, 1.25])
def test_unverified_independent_card_scale_emits_no_played_claim(
    scale: float,
) -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})
    original = played_frame("A", "H")
    frame = original.copy()
    frame[185:285, 340:420] = (20, 120, 20)
    scaled = cv2.resize(
        original[190:262, 348:402],
        (round(54 * scale), round(72 * scale)),
        interpolation=cv2.INTER_NEAREST,
    )
    height, width = scaled.shape[:2]
    frame[185 : 185 + height, 345 : 345 + width] = scaled

    result = observe_played_cards(
        frame,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert result["status"] == "REVIEW"
    assert result["cards"] == []


def test_non_card_white_region_does_not_emit_played_card() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})
    image = np.full((HEIGHT, WIDTH, 3), (20, 120, 20), dtype=np.uint8)
    paint_seat_landmarks(image)
    image[190:262, 348:402] = 255

    result = observe_played_cards(
        image,
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert result["status"] == "REVIEW"
    assert result["cards"] == []
    assert [(item["seat"], item["region"]) for item in result["played_regions"]] == [
        ("N", {"x": 348, "y": 190, "width": 54, "height": 72})
    ]
    assert result["rejected"][0]["reason"] == "RANK_AMBIGUOUS"


def test_player_tray_card_is_outside_played_table_geometry() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})

    result = observe_played_cards(
        played_frame("A", "H", x=5, y=500),
        rank_bank,
        suit_bank,
        profile,
        geometry_bank=geometry_bank(profile, reference),
        visible_hand_cards=verified_open_hands(),
    )

    assert result["status"] == "REVIEW"
    assert result["cards"] == []


def test_suit_bank_accepts_transferred_row_offset_within_rank_height() -> None:
    reference, coordinates = reference_image()
    # Reproduce transferred fan geometry where the live row has moved below
    # the immutable reviewed rank glyphs.  The reviewed row still contains
    # the complete rank and suit glyphs, but the shorter card background no
    # longer meets the white-run threshold when scanned ten pixels lower.
    reference[120:152, 50:230] = (20, 120, 20)
    raw = raw_profile(coordinates)
    raw["pixel"]["rank_height"] = 12
    raw["rows"]["N"]["y"] = 110
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    profile = parse_profile(raw)

    bank = build_suit_bank(profile, {"ref": reference})

    assert set(bank) == set(SUITS)
