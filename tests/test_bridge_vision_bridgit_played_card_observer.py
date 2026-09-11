from __future__ import annotations

import hashlib
import json

import pytest

from bridge_vision.bridgit_played_card_observer import (
    build_suit_bank,
    observe_played_cards,
)
from bridge_vision.bridgit_visible_hand_observer import (
    PROFILE_SCHEMA,
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


def played_frame(rank: str, suit: str, *, x: int = 348, y: int = 190):
    image = np.full((HEIGHT, WIDTH, 3), (20, 120, 20), dtype=np.uint8)
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
        played_frame(rank, suit), rank_bank, suit_bank, profile
    )

    assert result["status"] == "SHADOW_PLAYED_CARDS"
    assert [(item["card"], item["seat"]) for item in result["cards"]] == [
        (rank + suit, "N")
    ]
    assert result["cards"][0]["source"] == "PLAYED"
    assert result["cards"][0]["confidence"] >= 0.99
    assert result["cards"][0]["card_fill"] >= 0.95
    assert len(result["cards"][0]["evidence_pixel_sha256"]) == 64


def test_non_card_white_region_does_not_emit_played_card() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})
    image = np.full((HEIGHT, WIDTH, 3), (20, 120, 20), dtype=np.uint8)
    image[190:262, 348:402] = 255

    result = observe_played_cards(image, rank_bank, suit_bank, profile)

    assert result["status"] == "REVIEW"
    assert result["cards"] == []
    assert result["rejected"][0]["reason"] == "RANK_AMBIGUOUS"


def test_player_tray_card_is_outside_played_table_geometry() -> None:
    reference, coordinates = reference_image()
    profile = parse_profile(raw_profile(coordinates))
    rank_bank = build_rank_bank(profile, {"ref": reference})
    suit_bank = build_suit_bank(profile, {"ref": reference})

    result = observe_played_cards(
        played_frame("A", "H", x=5, y=500), rank_bank, suit_bank, profile
    )

    assert result["status"] == "REVIEW"
    assert result["cards"] == []


def test_suit_bank_accepts_transferred_row_offset_within_rank_height() -> None:
    reference, coordinates = reference_image()
    raw = raw_profile(coordinates)
    raw["pixel"]["rank_height"] = 12
    raw["rows"]["N"]["y"] = 110
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    profile = parse_profile(raw)

    bank = build_suit_bank(profile, {"ref": reference})

    assert set(bank) == set(SUITS)
