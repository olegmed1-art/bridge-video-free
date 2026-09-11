from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from bridge_vision.bridgit_visible_hand_observer import (
    PROFILE_SCHEMA,
    VisibleHandObserverError,
    build_rank_bank,
    decode_frame,
    observe_frame,
    parse_profile,
)
from tools.bridge_vision_visible_hand_observer import JOB_SCHEMA, run


WIDTH = 1000
HEIGHT = 720
RANKS = "AKQJT98765432"


def canonical_hash(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def rehash(raw: dict) -> None:
    raw.pop("profile_sha256", None)
    raw["profile_sha256"] = canonical_hash(raw)


def patterns() -> dict[str, np.ndarray]:
    result = {}
    for index, rank in enumerate(RANKS):
        rng = np.random.default_rng(index + 31)
        pattern = (rng.random((8, 8)) > 0.45).astype(np.uint8)
        pattern[0, :] = 0
        pattern[:, 0] = 0
        result[rank] = pattern
    return result


def reference_image() -> np.ndarray:
    image = np.full((HEIGHT, WIDTH, 3), (20, 120, 20), dtype=np.uint8)
    for index, rank in enumerate(RANKS):
        x = 20 + index * 20
        image[20:28, x : x + 8] = np.where(patterns()[rank][..., None] == 1, 0, 255)
    return image


def profile_raw() -> dict:
    raw = {
        "schema": PROFILE_SCHEMA,
        "profile_id": "bridgit.visible.synthetic.v1",
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
            "N": {"y": 100, "x_min": 80, "x_max": 520},
            "S": {"y": 500, "x_min": 80, "x_max": 520},
        },
        "references": {"ref": {"path": "ref.png", "sha256": "b" * 64}},
        "rank_templates": {
            rank: [{"reference_id": "ref", "x": 20 + index * 20, "y": 20}]
            for index, rank in enumerate(RANKS)
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


def draw_hand(image: np.ndarray, y: int, ranks: str) -> None:
    for index, (x, rank) in enumerate(zip((100, 200, 300, 400), ranks)):
        image[y : y + 34, x : x + 70] = 255
        glyph = patterns()[rank]
        image[y : y + 8, x + 2 : x + 10] = np.where(glyph[..., None] == 1, 0, 255)
        if index in (0, 2):
            image[y + 12 : y + 22, x + 30 : x + 40] = (0, 0, 220)
        else:
            image[y + 12 : y + 22, x + 30 : x + 40] = 0


def observed_frame() -> np.ndarray:
    image = np.full((HEIGHT, WIDTH, 3), (20, 120, 20), dtype=np.uint8)
    draw_hand(image, 100, "AKQJ")
    draw_hand(image, 500, "T987")
    return image


def test_observes_only_visible_horizontal_hands() -> None:
    profile = parse_profile(profile_raw())
    bank = build_rank_bank(profile, {"ref": reference_image()})
    result = observe_frame(observed_frame(), bank, profile)

    assert result["status"] == "SHADOW_VISIBLE_HANDS"
    assert [item["card"] for item in result["cards"]] == [
        "AH",
        "KC",
        "QD",
        "JS",
        "TH",
        "9C",
        "8D",
        "7S",
    ]
    assert {item["source"] for item in result["cards"]} == {"HAND"}
    assert all(len(item["evidence_pixel_sha256"]) == 64 for item in result["cards"])


def test_two_suit_runs_are_ambiguous_and_not_observed() -> None:
    profile = parse_profile(profile_raw())
    bank = build_rank_bank(profile, {"ref": reference_image()})
    image = observed_frame()
    image[100:134, 300:470] = (20, 120, 20)
    result = observe_frame(image, bank, profile)

    assert result["hands"]["N"]["status"] == "REVIEW"
    assert not any(item["seat"] == "N" for item in result["cards"])
    assert result["rejected"][0]["reason"] == "SUIT_RUN_GEOMETRY_AMBIGUOUS"


def test_profile_requires_human_verification_and_exact_hash() -> None:
    raw = profile_raw()
    raw["human_verified"] = False
    rehash(raw)
    with pytest.raises(VisibleHandObserverError, match="human verified"):
        parse_profile(raw)

    raw = profile_raw()
    raw["gates"]["min_rank_score"] = 0.98
    with pytest.raises(VisibleHandObserverError, match="profile hash mismatch"):
        parse_profile(raw)


def test_decodes_bytes_without_filesystem_image_reader() -> None:
    profile = parse_profile(profile_raw())
    ok, encoded = cv2.imencode(".png", observed_frame())
    assert ok
    decoded = decode_frame(encoded.tobytes(), profile)
    assert decoded.shape == (HEIGHT, WIDTH, 3)


def test_template_cannot_be_relabelled() -> None:
    raw = profile_raw()
    raw["rank_templates"]["K"] = copy.deepcopy(raw["rank_templates"]["A"])
    rehash(raw)
    profile = parse_profile(raw)
    with pytest.raises(VisibleHandObserverError, match="labelled as two ranks"):
        build_rank_bank(profile, {"ref": reference_image()})


def test_cli_writes_private_hash_bound_shadow_receipt(tmp_path: Path) -> None:
    ok, reference = cv2.imencode(".png", reference_image())
    assert ok
    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(reference.tobytes())
    ok, frame = cv2.imencode(".png", observed_frame())
    assert ok
    frame_path = tmp_path / "frame.png"
    frame_path.write_bytes(frame.tobytes())

    raw_profile = profile_raw()
    raw_profile["references"]["ref"] = {
        "path": reference_path.name,
        "sha256": hashlib.sha256(reference.tobytes()).hexdigest(),
    }
    rehash(raw_profile)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(raw_profile), encoding="utf-8")
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "schema": JOB_SCHEMA,
                "frames": [
                    {
                        "path": frame_path.name,
                        "frame_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                        "timestamp_ms": 1000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "receipt.json"
    receipt = run(tmp_path, profile_path, job_path, output)

    assert receipt["result_scope"] == "SHADOW_ONLY"
    assert len(receipt["frames"][0]["cards"]) == 8
    assert receipt["canonical_promotion_allowed"] is False
    assert os.stat(output).st_mode & 0o777 == 0o600


def test_cli_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(VisibleHandObserverError, match="escapes job_root"):
        run(tmp_path, Path("../profile.json"), Path("job.json"), Path("out.json"))
