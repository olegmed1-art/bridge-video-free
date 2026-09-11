from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests import test_bridge_vision_bridgit_played_card_observer as pixels
from tools.bridge_vision_autonomous_video import (
    AutonomousVideoError,
    _merge_direct_cards,
    _validate_pbn,
    run,
)

cv2 = pytest.importorskip("cv2")


def seen(card: str, seat: str, confidence: float) -> dict:
    return {
        "card": card,
        "seat": seat,
        "source": "HAND",
        "confidence": confidence,
        "evidence_pixel_sha256": "a" * 64,
    }


def test_direct_merge_keeps_strongest_same_owner_and_rejects_two_owners() -> None:
    merged = _merge_direct_cards([seen("AS", "N", 0.96)], [seen("AS", "N", 0.99)])
    assert merged == [seen("AS", "N", 0.99)]

    with pytest.raises(AutonomousVideoError, match="two seats"):
        _merge_direct_cards([seen("AS", "N", 0.99)], [seen("AS", "E", 0.99)])


def test_complete_pbn_passes_independent_bridge_deal_model() -> None:
    pbn = "N:AKQJT98765432... .AKQJT98765432.. ..AKQJT98765432. ...AKQJT98765432"
    result = _validate_pbn(pbn)

    assert result["status"] == "PASS"
    assert result["pbn_sha256"] == hashlib.sha256(pbn.encode()).hexdigest()


def test_raw_video_pipeline_is_autonomous_and_writes_private_receipt(
    tmp_path: Path,
) -> None:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    reference, coordinates = pixels.reference_image()
    ok, encoded_reference = cv2.imencode(".png", reference)
    assert ok
    reference_path = profile_dir / "reference.png"
    reference_path.write_bytes(encoded_reference.tobytes())
    profile = pixels.raw_profile(coordinates)
    profile["references"]["ref"] = {
        "path": reference_path.name,
        "sha256": hashlib.sha256(encoded_reference.tobytes()).hexdigest(),
    }
    profile.pop("profile_sha256")
    profile["profile_sha256"] = pixels.canonical_hash(profile)
    profile_path = profile_dir / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    video_path = tmp_path / "input.avi"
    writer = cv2.VideoWriter(
        str(video_path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        10.0,
        (pixels.WIDTH, pixels.HEIGHT),
    )
    assert writer.isOpened()
    for index in range(4):
        image = pixels.played_frame("A", "H")
        image[250:254, 390 + index : 394 + index] = (245, 245, 245)
        image[142:166, 696:717] = 255
        if index < 2:
            image[144:162, 698:705] = 0
        else:
            image[144:162, 708:715] = 0
        writer.write(image)
    writer.release()

    output = tmp_path / "receipt.json"
    receipt = run(
        tmp_path,
        profile_path,
        video_path,
        output,
        sample_ms=100,
        max_sampled_frames=10,
    )

    assert receipt["uses_language_model"] is False
    assert receipt["requires_screenshot_review"] is False
    assert receipt["reconstruction"]["deal_count"] == 2
    assert receipt["reconstruction"]["status_counts"]["PARTIAL"] == 2
    assert all(
        deal["observed_card_count"] == 1 for deal in receipt["reconstruction"]["deals"]
    )
    assert os.stat(output).st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["receipt_sha256"] == receipt["receipt_sha256"]


def test_raw_video_pipeline_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(AutonomousVideoError, match="escapes job_root"):
        run(
            tmp_path,
            Path("../profile.json"),
            Path("input.mp4"),
            Path("receipt.json"),
        )
