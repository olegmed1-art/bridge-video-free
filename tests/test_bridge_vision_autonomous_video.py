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
    _new_server_play_events,
    _played_region_state,
    _registration_profile,
    _validate_pbn,
    run,
)

cv2 = pytest.importorskip("cv2")


def paint_interface_anchor(image) -> None:
    cv2.rectangle(image, (860, 16), (958, 86), (245, 245, 245), 2)
    cv2.circle(image, (908, 51), 23, (15, 15, 15), 3)
    cv2.line(image, (875, 51), (941, 51), (220, 220, 220), 2)
    cv2.line(image, (908, 25), (908, 77), (220, 220, 220), 2)


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


def test_server_play_event_preserves_unknown_card_and_does_not_duplicate_refinement() -> (
    None
):
    unknown = _played_region_state({"played_regions": [{"seat": "N"}], "cards": []})
    assert unknown == {"N": None}
    events = _new_server_play_events(
        {},
        unknown,
        timestamp_ms=100,
        source_frame_index=1,
        frame_sha256="a" * 64,
        input_decoded_pixel_sha256="b" * 64,
        observer_status="REVIEW",
        first_event_number=1,
    )
    assert [
        (event["seat"], event["card"], event["recognition_status"]) for event in events
    ] == [("N", None, "UNKNOWN_CARD")]
    assert (
        _new_server_play_events(
            unknown,
            {"N": "AH"},
            timestamp_ms=200,
            source_frame_index=2,
            frame_sha256="c" * 64,
            input_decoded_pixel_sha256="d" * 64,
            observer_status="SHADOW_PLAYED_CARDS",
            first_event_number=2,
        )
        == []
    )


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
    assert receipt["profile"]["frame_size"] == {
        "width": pixels.WIDTH,
        "height": pixels.HEIGHT,
    }
    assert receipt["profile"]["card_pixel_scale_source"] == (
        "EXACT_VERIFIED_PROFILE_PLUS_LIVE_REVIEWED_CARDBACK_WIDTH"
    )
    assert receipt["played_layout"]["coordinate_source"] == (
        "LIVE_TABLE_SEAT_LANDMARKS"
    )
    assert receipt["played_layout"]["proven_frame_count"] == 4
    assert receipt["played_layout"]["rejected_frame_count"] == 0
    assert receipt["played_layout"]["uses_fixed_screen_center"] is False
    assert receipt["played_layout"]["responsive_policy"] == (
        "RECOMPUTE_LIVE_SEAT_AXES_EVERY_FRAME"
    )
    assert receipt["played_layout"]["unproven_geometry_action"] == (
        "REVIEW_NO_PLAYED_CLAIM"
    )
    assert receipt["reconstruction"]["deal_count"] == 2
    assert receipt["reconstruction"]["status_counts"]["PARTIAL"] == 2
    assert any(
        deal["observed_card_count"] >= 1 for deal in receipt["reconstruction"]["deals"]
    )
    for deal in receipt["reconstruction"]["deals"]:
        claim = next(item for item in deal["accepted"] if item["card"] == "AH")
        assert (claim["card"], claim["seat"], claim["sources"]) == (
            "AH",
            "N",
            ["PLAYED"],
        )
        assert claim["layout_geometry_sha256s"]
        assert claim["layout_transform_sha256s"]
        assert claim["card_scale_policy_sha256s"]
        assert claim["card_scale_measurement_sha256s"]
        assert claim["played_card_width_ratio_range"] is not None
        assert deal["recognized_card_memory"]["forgets_disappeared_cards"] is False
        assert deal["played_card_events"][0]["snapshot_required"] is True
    snapshot_manifest = receipt["played_event_snapshots"]
    assert snapshot_manifest["status"] == "COMPLETE"
    assert snapshot_manifest["event_count"] == len(receipt["server_play_events"])
    assert all(event["snapshot_required"] for event in receipt["server_play_events"])
    assert snapshot_manifest["all_events_have_snapshot"] is True
    evidence_dir = tmp_path / snapshot_manifest["directory"]
    assert evidence_dir.is_dir()
    assert (evidence_dir / "manifest.json").is_file()
    for snapshot in snapshot_manifest["snapshots"]:
        assert (evidence_dir / snapshot["path"]).is_file()
    assert os.stat(output).st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["receipt_sha256"] == receipt["receipt_sha256"]


def test_raw_video_pipeline_registers_larger_offset_frame_by_anchor(
    tmp_path: Path,
) -> None:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    reference, coordinates = pixels.reference_image()
    paint_interface_anchor(reference)
    ok, encoded_reference = cv2.imencode(".png", reference)
    assert ok
    reference_path = profile_dir / "reference.png"
    reference_path.write_bytes(encoded_reference.tobytes())
    profile = pixels.raw_profile(coordinates)
    profile["references"]["ref"] = {
        "path": reference_path.name,
        "sha256": hashlib.sha256(encoded_reference.tobytes()).hexdigest(),
    }
    profile["registration"] = {
        "mode": "UPPER_RIGHT_ANCHOR",
        "reference_id": "ref",
        "interface_anchor": {
            "type": "UPPER_RIGHT_TEMPLATE",
            "reference_region": {
                "x": 0.86,
                "y": 0.02,
                "width": 0.10,
                "height": 0.10,
            },
            "scales": [1.0],
            "minimum_score": 0.75,
            "minimum_margin": 0.05,
        },
    }
    profile.pop("profile_sha256")
    profile["profile_sha256"] = pixels.canonical_hash(profile)
    profile_path = profile_dir / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    video_path = tmp_path / "offset.avi"
    writer = cv2.VideoWriter(
        str(video_path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        10.0,
        (1250, 900),
    )
    assert writer.isOpened()
    for index in range(4):
        game = pixels.played_frame("A", "H")
        paint_interface_anchor(game)
        game[142:166, 696:717] = 255
        game[
            144:162, 698 + (10 if index >= 2 else 0) : 705 + (10 if index >= 2 else 0)
        ] = 0
        canvas = pixels.np.full((900, 1250, 3), 120, dtype=pixels.np.uint8)
        canvas[80:800, 120:1120] = game
        writer.write(canvas)
    writer.release()

    output = tmp_path / "offset-receipt.json"
    receipt = run(
        tmp_path,
        profile_path,
        video_path,
        output,
        sample_ms=100,
        max_sampled_frames=10,
    )

    assert receipt["registration"]["mode"] == "UPPER_RIGHT_ANCHOR"
    assert receipt["registration"]["search_count"] == 1
    assert receipt["registration"]["locked_frame_count"] == 3
    assert receipt["registration"]["rejected_frame_count"] == 0
    assert receipt["registration"]["input_sizes"] == [{"width": 1250, "height": 900}]
    assert receipt["registration"]["transforms"][0]["scale"] == 1.0
    assert receipt["played_layout"]["proven_frame_count"] >= 3
    assert receipt["played_layout"]["rejected_frame_count"] <= 1
    assert receipt["played_layout"]["uses_fixed_screen_center"] is False
    assert receipt["reconstruction"]["status_counts"]["PARTIAL"] >= 1
    assert receipt["reconstruction"]["status_counts"]["CONFLICT"] == 0


def test_registration_profile_requires_known_reference() -> None:
    with pytest.raises(AutonomousVideoError, match="reference is unknown"):
        _registration_profile(
            {
                "registration": {
                    "mode": "UPPER_RIGHT_ANCHOR",
                    "reference_id": "missing",
                    "interface_anchor": {
                        "type": "UPPER_RIGHT_TEMPLATE",
                        "reference_region": {
                            "x": 0.8,
                            "y": 0.05,
                            "width": 0.1,
                            "height": 0.1,
                        },
                        "scales": [1.0],
                        "minimum_score": 0.8,
                        "minimum_margin": 0.1,
                    },
                }
            },
            {"ref"},
        )


def test_raw_video_pipeline_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(AutonomousVideoError, match="escapes job_root"):
        run(
            tmp_path,
            Path("../profile.json"),
            Path("input.mp4"),
            Path("receipt.json"),
        )
