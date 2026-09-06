import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

import bridge_vision.bridgit_rank_layout as bridgit_rank_layout
from bridge_vision import BridgeVisionEngine
from bridge_vision.bridgit_rank_layout import (
    CARDS,
    JOB_TYPE,
    PROFILE_SCHEMA,
    BridgitRankLayoutError,
    atomic_write_receipt,
    canonical_hash,
    execute_shadow_job,
    find_chain_peaks,
    load_job,
    load_profile,
    ordered_assignments,
    parse_profile,
)


def profile_raw(reference_sha: str = "a" * 64) -> dict:
    slots = []
    for index, card in enumerate(
        rank + suit for rank in "AKQJT98765432" for suit in "HCDS"
    ):
        slots.append({"card": card, "x": 20 + index * 3, "y": 30 + index})
    anchors = {
        seat: {
            suit: {
                "x": 100 + suit_index * 40,
                "y": 100 + seat_index * 80 + suit_index * 12,
            }
            for suit_index, suit in enumerate("HCDS")
        }
        for seat_index, seat in enumerate("NESW")
    }
    raw = {
        "schema": PROFILE_SCHEMA,
        "profile_id": "bridgit.desktop.test.v1",
        "human_verified": True,
        "reference_frame_sha256": reference_sha,
        "verification": {
            "method": "HUMAN_LABEL_REVIEW",
            "reviewer_id": "bridge-school-reviewer",
            "verified_at": "2026-09-05T20:00:00Z",
            "reference_frame_sha256": reference_sha,
        },
        "frame_size": {"width": 1000, "height": 720},
        "ordering": {"suits": list("HCDS"), "ranks": list("AKQJT98765432")},
        "template_slots": slots,
        "geometry": {
            "anchors": anchors,
            "horizontal_search": {
                "N": {"x_min": 100, "x_max": 800, "y": 60},
                "S": {"x_min": 100, "x_max": 800, "y": 550},
            },
            "vertical_search": {
                "W": {"x_min": 10, "x_max": 145, "edge_x": 14},
                "E": {"x_min": 800, "x_max": 940, "edge_x": 930},
            },
        },
        "gates": {
            "glyph_width": 19,
            "glyph_height": 16,
            "local_registration_px": 2,
            "binary_threshold": 180,
            "min_template_score": 0.40,
            "min_peak_score": 0.72,
            "min_peak_prominence": 0.04,
            "min_rank_ink_fraction": 0.20,
            "min_assignment_margin": 0.12,
            "min_independent_frames": 2,
        },
    }
    raw["profile_sha256"] = canonical_hash(raw)
    return raw


def test_profile_is_human_reviewed_hash_bound_and_complete():
    profile = parse_profile(profile_raw())
    assert profile.profile_id == "bridgit.desktop.test.v1"
    assert {card for card, _, _ in profile.template_slots} == CARDS
    assert len(profile.template_slots) == 52

    raw = profile_raw()
    raw["ordering"]["suits"] = list("SHDC")
    with pytest.raises(BridgitRankLayoutError, match="suit order"):
        parse_profile(raw)

    raw = profile_raw()
    raw["template_slots"][1]["card"] = raw["template_slots"][0]["card"]
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="every card exactly once"):
        parse_profile(raw)

    raw = profile_raw()
    raw["geometry"]["vertical_search"]["W"]["x_max"] = 600
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="span exceeds scoring budget"):
        parse_profile(raw)

    raw = profile_raw()
    raw["geometry"]["anchors"]["S"]["S"]["y"] = (
        raw["frame_size"]["height"] - raw["gates"]["glyph_height"]
    )
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    assert parse_profile(raw).anchors["S"]["S"][1] == 704

    raw = profile_raw()
    raw["geometry"]["anchors"]["W"]["H"]["x"] = 1
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="crop leaves reference frame"):
        parse_profile(raw)

    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {"x": 0.72, "y": 0.02, "width": 0.08, "height": 0.10},
        "scales": [0.75, 1.0, 1.25],
        "minimum_score": 0.80,
        "minimum_margin": 0.03,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    assert parse_profile(raw).interface_anchor["type"] == "UPPER_RIGHT_TEMPLATE"

    raw["geometry"]["interface_anchor"]["reference_region"]["x"] = 0.1
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="right half"):
        parse_profile(raw)

    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {"x": 0.72, "y": 0.02, "width": 0.08, "height": 0.10},
        "scales": [1.0],
        "minimum_score": 0.80,
        "minimum_margin": 0,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="minimum_margin"):
        parse_profile(raw)

    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {"x": 0.72, "y": 0.02, "width": 0.08, "height": 0.10},
        "scales": [1.0],
        "minimum_score": 0,
        "minimum_margin": 0.03,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="minimum_score"):
        parse_profile(raw)

    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {
            "x": 0.98,
            "y": 0.02,
            "width": 0.008,
            "height": 0.012,
        },
        "scales": [0.25],
        "minimum_score": 0.80,
        "minimum_margin": 0.03,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="scaled interface anchor"):
        parse_profile(raw)

    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {
            "x": 0.999,
            "y": 0.02,
            "width": 0.001,
            "height": 0.02,
        },
        "scales": [1.0],
        "minimum_score": 0.80,
        "minimum_margin": 0.03,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="reference interface anchor"):
        parse_profile(raw)

    raw = profile_raw()
    raw["gates"]["min_assignment_margin"] = 0
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="min_assignment_margin"):
        parse_profile(raw)

    raw = profile_raw()
    raw["gates"]["min_rank_ink_fraction"] = 0
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="min_rank_ink_fraction"):
        parse_profile(raw)

    raw = profile_raw()
    raw["gates"]["min_peak_prominence"] = 0
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="min_peak_prominence"):
        parse_profile(raw)

    for field in ("min_template_score", "min_peak_score"):
        raw = profile_raw()
        raw["gates"][field] = 0
        raw["profile_sha256"] = canonical_hash(
            {key: value for key, value in raw.items() if key != "profile_sha256"}
        )
        with pytest.raises(BridgitRankLayoutError, match=field):
            parse_profile(raw)

    raw = profile_raw()
    raw["ordering"]["suits"] = 1
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="verified suit order"):
        parse_profile(raw)

    raw = profile_raw()
    raw["gates"]["min_template_score"] = 10**3999
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    with pytest.raises(BridgitRankLayoutError, match="min_template_score"):
        parse_profile(raw)


def test_profile_hash_and_duplicate_json_keys_fail_closed(tmp_path: Path):
    raw = profile_raw()
    raw["gates"]["min_template_score"] = 0.41
    with pytest.raises(BridgitRankLayoutError, match="profile hash mismatch"):
        parse_profile(raw)

    duplicate = tmp_path / "profile.json"
    duplicate.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
    with pytest.raises(BridgitRankLayoutError, match="duplicate JSON keys"):
        load_profile(duplicate)

    oversized_job = tmp_path / "oversized-job.json"
    oversized_job.write_bytes(b"{" + b" " * bridgit_rank_layout.MAX_JOB_BYTES)
    with pytest.raises(BridgitRankLayoutError, match="job exceeds size limit"):
        load_job(oversized_job)


def test_ordered_assignment_is_global_deterministic_and_retains_runner_up():
    lengths = {"N": 4, "E": 3, "S": 3, "W": 3}
    target = tuple("NNESWNESWNESW")
    offsets = {"N": 0, "E": 4, "S": 7, "W": 10}
    occurrences = {seat: 0 for seat in "NESW"}
    matrix = [[0.0] * 13 for _ in range(13)]
    for rank_index, seat in enumerate(target):
        row = offsets[seat] + occurrences[seat]
        occurrences[seat] += 1
        matrix[row][rank_index] = 10.0
    seats = tuple(seat for seat in "NESW" for _ in range(lengths[seat]))

    first = ordered_assignments(matrix, seats, lengths)
    second = ordered_assignments(matrix, seats, lengths)
    assert first == second
    assert first[0][1] == target
    assert len(first) == 2
    assert first[0][0] > first[1][0]


def test_ordered_assignment_rejects_missing_slots_and_non_finite_scores():
    with pytest.raises(BridgitRankLayoutError, match="exactly thirteen"):
        ordered_assignments([[0.0] * 13], ["N"], {"N": 1, "E": 0, "S": 0, "W": 0})

    matrix = [[0.0] * 13 for _ in range(13)]
    matrix[0][0] = math.nan
    with pytest.raises(BridgitRankLayoutError, match="non-finite"):
        ordered_assignments(matrix, ["N"] * 13, {"N": 13, "E": 0, "S": 0, "W": 0})


def test_peak_chain_is_edge_anchored_and_contiguous():
    values = [0.0] * 120
    for index, score in ((4, 0.93), (29, 0.88), (54, 0.91), (79, 0.86), (111, 0.95)):
        values[index] = score
    assert find_chain_peaks(
        values,
        origin=10,
        edge=14,
        direction=1,
        min_height=0.72,
        min_prominence=0.04,
    ) == [14, 39, 64, 89]

    assert (
        find_chain_peaks(
            values,
            origin=10,
            edge=20,
            direction=1,
            min_height=0.72,
            min_prominence=0.04,
        )
        == []
    )


def test_job_boundary_rejects_production_hidden_information_and_unknown_type():
    with pytest.raises(BridgitRankLayoutError, match="unknown job type"):
        execute_shadow_job({})
    with pytest.raises(BridgitRankLayoutError, match="production write"):
        execute_shadow_job(
            {
                "job_type": JOB_TYPE,
                "production_write": True,
                "allow_hidden_information": False,
            }
        )
    with pytest.raises(BridgitRankLayoutError, match="hidden information"):
        execute_shadow_job(
            {
                "job_type": JOB_TYPE,
                "production_write": False,
                "allow_hidden_information": True,
            }
        )
    with pytest.raises(BridgitRankLayoutError, match="too many teacher_pointer_events"):
        execute_shadow_job(
            {
                "job_type": JOB_TYPE,
                "production_write": False,
                "allow_hidden_information": False,
                "teacher_pointer_events": [{}] * 257,
            }
        )
    with pytest.raises(
        BridgitRankLayoutError, match="invalid teacher pointer evidence"
    ):
        execute_shadow_job(
            {
                "job_type": JOB_TYPE,
                "production_write": False,
                "allow_hidden_information": False,
                "teacher_pointer_events": [{}],
            }
        )


def test_receipt_write_is_atomic_and_default_engine_remains_empty(tmp_path: Path):
    receipt = {
        "receipt_type": "test",
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
    }
    receipt["receipt_sha256"] = canonical_hash(receipt)
    path = tmp_path / "receipt.json"
    atomic_write_receipt(path, receipt)
    assert json.loads(path.read_text(encoding="utf-8")) == receipt
    assert BridgeVisionEngine().detector_names == ()


def test_valid_shadow_job_is_hash_bound_deterministic_and_never_promotable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    reference = tmp_path / "reference.jpg"
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    reference.write_bytes(b"reference-frame")
    first.write_bytes(b"first-frame")
    second.write_bytes(b"second-frame")
    reference_sha = bridgit_rank_layout.sha256_file(reference)
    raw = profile_raw(reference_sha)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(raw), encoding="utf-8")

    def fake_recognize(
        reference_path, frame_paths, profile, *, expected_frame_sha256s=None
    ):
        assert reference_path == reference
        assert frame_paths == [first, second]
        assert profile.profile_id == raw["profile_id"]
        frame_hashes = [
            bridgit_rank_layout.sha256_file(first),
            bridgit_rank_layout.sha256_file(second),
        ]
        assert expected_frame_sha256s == frame_hashes
        return {
            "status": "SHADOW_FULL_LAYOUT_CANDIDATE",
            "result_scope": "SHADOW_ONLY",
            "canonical_promotion_allowed": False,
            "school_canon_write_performed": False,
            "hidden_hand_reconstruction_performed": False,
            "input_hashes": {
                "reference_frame_sha256": reference_sha,
                "frame_sha256s": frame_hashes,
            },
            "_visual_observations": [
                {
                    "seat": "N",
                    "suit": "H",
                    "rank": "A",
                    "source": "VISUAL",
                    "frame_sha256": frame_sha,
                    "region": {
                        "coordinate_space": "NORMALIZED_FRAME",
                        "x": 0.1,
                        "y": 0.1,
                        "width": 0.02,
                        "height": 0.03,
                    },
                    "confidence": 0.91,
                    "recognizer_version": bridgit_rank_layout.BACKEND_VERSION,
                }
                for frame_sha in frame_hashes
            ],
        }

    monkeypatch.setattr(bridgit_rank_layout, "recognize_frames", fake_recognize)
    job = {
        "job_type": JOB_TYPE,
        "input_root": str(tmp_path),
        "profile_id": raw["profile_id"],
        "profile_ref": {
            "path": str(profile_path),
            "sha256": bridgit_rank_layout.sha256_file(profile_path),
        },
        "reference_frame_ref": {"path": str(reference), "sha256": reference_sha},
        "frame_refs": [
            {
                "path": str(first),
                "sha256": bridgit_rank_layout.sha256_file(first),
                "timestamp_ms": 1000,
            },
            {
                "path": str(second),
                "sha256": bridgit_rank_layout.sha256_file(second),
                "timestamp_ms": 2000,
            },
        ],
        "allow_hidden_information": False,
        "production_write": False,
        "teacher_pointer_events": [
            {
                "source": "TEACHER_POINTER",
                "frame_sha256": bridgit_rank_layout.sha256_file(second),
                "timestamp_ms": 2000,
                "point": {
                    "coordinate_space": "NORMALIZED_FRAME",
                    "x": 0.11,
                    "y": 0.11,
                },
                "confidence": 0.95,
                "claimed_card": "AH",
                "claimed_seat": "N",
            }
        ],
    }
    receipt = execute_shadow_job(job)
    assert receipt == execute_shadow_job(job)
    assert receipt["result"]["result_scope"] == "SHADOW_ONLY"
    assert receipt["result"]["canonical_promotion_allowed"] is False
    assert receipt["production_write_performed"] is False
    assert receipt["school_canon_write_performed"] is False
    report = receipt["result"]["deal_evidence_report"]
    assert report["status"] == "PARTIAL"
    observed = next(item for item in report["card_records"] if item["rank"] == "A")
    assert observed["source"] == "TEMPORAL_CONSENSUS"
    assert [item["timestamp_ms"] for item in observed["evidence"][:2]] == [1000, 2000]
    assert observed["evidence"][-1]["source"] == "TEACHER_POINTER"
    assert observed["evidence"][-1]["accepted_as_visual_observation"] is False
    invalid_pointer_job = {**job, "teacher_pointer_events": None}
    with pytest.raises(BridgitRankLayoutError, match="must be an array"):
        execute_shadow_job(invalid_pointer_job)
    claimed = receipt.pop("receipt_sha256")
    assert claimed == canonical_hash(receipt)

    class FakeCv2Error(Exception):
        pass

    class FakeCv2:
        error = FakeCv2Error

    monkeypatch.setattr(
        bridgit_rank_layout,
        "_pixel_runtime",
        lambda: (FakeCv2(), object()),
    )
    monkeypatch.setattr(
        bridgit_rank_layout,
        "recognize_frames",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FakeCv2Error()),
    )
    with pytest.raises(BridgitRankLayoutError, match="OpenCV pixel operation failed"):
        execute_shadow_job(job)


def test_shadow_job_rejects_replayed_bytes_and_template_as_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    reference = tmp_path / "reference.jpg"
    frame = tmp_path / "frame.jpg"
    duplicate = tmp_path / "duplicate.jpg"
    reference.write_bytes(b"reference-frame")
    frame.write_bytes(b"same-frame")
    duplicate.write_bytes(b"same-frame")
    reference_sha = bridgit_rank_layout.sha256_file(reference)
    raw = profile_raw(reference_sha)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(raw), encoding="utf-8")
    base = {
        "job_type": JOB_TYPE,
        "input_root": str(tmp_path),
        "profile_id": raw["profile_id"],
        "profile_ref": {
            "path": str(profile_path),
            "sha256": bridgit_rank_layout.sha256_file(profile_path),
        },
        "reference_frame_ref": {"path": str(reference), "sha256": reference_sha},
        "allow_hidden_information": False,
        "production_write": False,
    }
    monkeypatch.setattr(
        bridgit_rank_layout,
        "recognize_frames",
        lambda *_: pytest.fail("must fail before recognition"),
    )

    bounded = tmp_path / "bounded"
    bounded.mkdir()
    escape = dict(base)
    escape["input_root"] = str(bounded)
    with pytest.raises(BridgitRankLayoutError, match="profile_ref escapes input_root"):
        execute_shadow_job(escape)

    replay = dict(base)
    replay["frame_refs"] = [
        {
            "path": str(frame),
            "sha256": bridgit_rank_layout.sha256_file(frame),
            "timestamp_ms": 1000,
        },
        {
            "path": str(duplicate),
            "sha256": bridgit_rank_layout.sha256_file(duplicate),
            "timestamp_ms": 2000,
        },
    ]
    with pytest.raises(BridgitRankLayoutError, match="duplicate frame bytes"):
        execute_shadow_job(replay)

    template_reuse = dict(base)
    template_reuse["frame_refs"] = [
        {"path": str(reference), "sha256": reference_sha, "timestamp_ms": 1000},
    ]
    with pytest.raises(BridgitRankLayoutError, match="template frame"):
        execute_shadow_job(template_reuse)


def test_decoded_pixel_identity_rejects_reencoding_and_pixel_replay():
    reference_bytes = "a" * 64
    reference_pixels = "b" * 64
    with pytest.raises(BridgitRankLayoutError, match="template pixels"):
        bridgit_rank_layout._validate_temporal_identities(
            reference_bytes,
            reference_pixels,
            ["c" * 64],
            [reference_pixels],
        )

    with pytest.raises(BridgitRankLayoutError, match="duplicate decoded frame pixels"):
        bridgit_rank_layout._validate_temporal_identities(
            reference_bytes,
            reference_pixels,
            ["c" * 64, "d" * 64],
            ["e" * 64, "e" * 64],
        )


def test_encoded_dimensions_and_decoded_memory_are_bounded_before_decode():
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + (1920).to_bytes(4, "big")
        + (1080).to_bytes(4, "big")
    )
    assert bridgit_rank_layout._encoded_image_dimensions(png) == (1920, 1080)

    jpeg = (
        b"\xff\xd8\xff\xc0\x00\x11\x08"
        + (720).to_bytes(2, "big")
        + (1686).to_bytes(2, "big")
        + b"\x03"
        + b"\x00" * 9
    )
    assert bridgit_rank_layout._encoded_image_dimensions(jpeg) == (1686, 720)
    with pytest.raises(BridgitRankLayoutError, match="JPEG frame has no"):
        bridgit_rank_layout._encoded_image_dimensions(b"\xff\xd8\xff\xd9")
    with pytest.raises(BridgitRankLayoutError, match="JPEG or PNG"):
        bridgit_rank_layout._encoded_image_dimensions(b"GIF89a")

    bridgit_rank_layout._validate_decoded_budget(4096, 4096, observation_count=1)
    with pytest.raises(BridgitRankLayoutError, match="raster memory budget"):
        bridgit_rank_layout._validate_decoded_budget(8192, 8192, observation_count=0)
    with pytest.raises(BridgitRankLayoutError, match="job memory budget"):
        bridgit_rank_layout._validate_decoded_budget(4096, 4096, observation_count=5)

    with pytest.raises(BridgitRankLayoutError, match="frame decode"):
        bridgit_rank_layout._validate_decode_peak_budget(
            retained_decoded_bytes=bridgit_rank_layout.MAX_DECODED_JOB_BYTES - 200,
            decoded_frame_bytes=100,
            encoded_payload_bytes=1,
        )
    bridgit_rank_layout._validate_decode_peak_budget(
        retained_decoded_bytes=bridgit_rank_layout.MAX_DECODED_JOB_BYTES - 201,
        decoded_frame_bytes=100,
        encoded_payload_bytes=1,
    )

    profile = parse_profile(profile_raw())
    registered_bytes = profile.width * profile.height * 3 * 2
    bridgit_rank_layout._validate_registration_retention_budget(
        profile,
        source_decoded_bytes=(
            bridgit_rank_layout.MAX_DECODED_JOB_BYTES - registered_bytes
        ),
        observation_count=2,
        matcher_scratch_bytes=0,
    )
    with pytest.raises(BridgitRankLayoutError, match="source and registered frames"):
        bridgit_rank_layout._validate_registration_retention_budget(
            profile,
            source_decoded_bytes=(
                bridgit_rank_layout.MAX_DECODED_JOB_BYTES - registered_bytes + 1
            ),
            observation_count=2,
            matcher_scratch_bytes=0,
        )

    with pytest.raises(BridgitRankLayoutError, match="source and registered frames"):
        bridgit_rank_layout._validate_registration_retention_budget(
            profile,
            source_decoded_bytes=(
                bridgit_rank_layout.MAX_DECODED_JOB_BYTES - registered_bytes
            ),
            observation_count=2,
            matcher_scratch_bytes=1,
        )

    bridgit_rank_layout._validate_scoring_budget(profile, observation_count=2)
    large_profile = replace(
        profile,
        width=4096,
        height=4096,
        glyph_width=64,
        glyph_height=64,
        local_registration_px=4,
    )
    bridgit_rank_layout._validate_decoded_budget(
        large_profile.width, large_profile.height, observation_count=3
    )
    with pytest.raises(BridgitRankLayoutError, match="recognition workspace"):
        bridgit_rank_layout._validate_recognition_memory_budget(
            large_profile, observation_count=3
        )
    with pytest.raises(BridgitRankLayoutError, match="scoring-operation budget"):
        bridgit_rank_layout.recognize_frames(
            Path("unread-reference.jpg"),
            [Path(f"unread-frame-{index}.jpg") for index in range(16)],
            profile,
        )


def test_frame_payload_read_is_bounded_by_remaining_memory(
    monkeypatch: pytest.MonkeyPatch,
):
    profile = parse_profile(profile_raw())
    observed_limits = []

    def bounded_read(_path, max_bytes, _kind):
        observed_limits.append(max_bytes)
        raise BridgitRankLayoutError("bounded read stopped")

    monkeypatch.setattr(bridgit_rank_layout, "_read_bounded_bytes", bounded_read)
    with pytest.raises(BridgitRankLayoutError, match="bounded read stopped"):
        bridgit_rank_layout._read_frame(
            Path("frame.png"),
            profile,
            decoded_job_bytes_so_far=bridgit_rank_layout.MAX_DECODED_JOB_BYTES - 10,
        )
    assert observed_limits == [9]


def test_anchor_work_is_bounded_across_job_before_decode(monkeypatch):
    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {
            "x": 0.72,
            "y": 0.02,
            "width": 0.08,
            "height": 0.10,
        },
        "scales": [1.0, 1.25, 1.5],
        "minimum_score": 0.80,
        "minimum_margin": 0.03,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    profile = parse_profile(raw)
    assert (
        bridgit_rank_layout.estimate_anchor_peak_scratch_bytes(
            (profile.width, profile.height),
            [(8, 8)],
            profile.interface_anchor,
        )
        == 80 * 72 + 8 * 8
    )
    monkeypatch.setattr(
        bridgit_rank_layout,
        "_validate_input_raster_budget",
        lambda paths: [
            (1000, 720),
            (1920, 1080),
            (1920, 1080),
            (1920, 1080),
        ],
    )
    monkeypatch.setattr(
        bridgit_rank_layout,
        "_read_frame",
        lambda *_args, **_kwargs: pytest.fail("must reject before decode"),
    )

    with pytest.raises(BridgitRankLayoutError, match="anchor job exceeds work budget"):
        bridgit_rank_layout.recognize_frames(
            Path("reference.png"),
            [Path(f"frame-{index}.png") for index in range(3)],
            profile,
        )


def test_anchor_job_budget_is_rechecked_on_payloads_actually_decoded(monkeypatch):
    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {
            "x": 0.72,
            "y": 0.02,
            "width": 0.08,
            "height": 0.10,
        },
        "scales": [1.0, 1.25, 1.5],
        "minimum_score": 0.80,
        "minimum_margin": 0.03,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    profile = parse_profile(raw)

    class Raster:
        def __init__(self, height, width):
            self.shape = (height, width, 3)

    reads = iter(
        [(Raster(720, 1000), "a" * 64, "b" * 64, None)]
        + [
            (Raster(1080, 1920), str(index) * 64, str(index + 3) * 64, None)
            for index in range(3)
        ]
    )
    monkeypatch.setattr(
        bridgit_rank_layout,
        "_validate_input_raster_budget",
        lambda paths: [(1000, 720)] * len(paths),
    )
    monkeypatch.setattr(
        bridgit_rank_layout, "_read_frame", lambda *_args, **_kwargs: next(reads)
    )
    monkeypatch.setattr(
        bridgit_rank_layout,
        "register_from_upper_right_anchor",
        lambda *_args, **_kwargs: pytest.fail("must reject before registration"),
    )

    with pytest.raises(BridgitRankLayoutError, match="anchor job exceeds work budget"):
        bridgit_rank_layout.recognize_frames(
            Path("reference.png"),
            [Path(f"frame-{index}.png") for index in range(3)],
            profile,
        )


def test_decoded_observation_hash_is_checked_before_registration(monkeypatch):
    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {
            "x": 0.72,
            "y": 0.02,
            "width": 0.08,
            "height": 0.10,
        },
        "scales": [1.0],
        "minimum_score": 0.80,
        "minimum_margin": 0.03,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    profile = parse_profile(raw)

    class Raster:
        shape = (720, 1000, 3)

    reads = iter(
        [
            (Raster(), "a" * 64, "b" * 64, None),
            (Raster(), "c" * 64, "d" * 64, None),
        ]
    )
    read_count = 0

    def read_once_before_rejection(*_args, **_kwargs):
        nonlocal read_count
        read_count += 1
        if read_count > 2:
            pytest.fail("must not decode the next observation after hash mismatch")
        return next(reads)

    monkeypatch.setattr(
        bridgit_rank_layout,
        "_validate_input_raster_budget",
        lambda paths: [(1000, 720)] * len(paths),
    )
    monkeypatch.setattr(bridgit_rank_layout, "_read_frame", read_once_before_rejection)
    monkeypatch.setattr(
        bridgit_rank_layout,
        "register_from_upper_right_anchor",
        lambda *_args, **_kwargs: pytest.fail("must reject before registration"),
    )

    with pytest.raises(BridgitRankLayoutError, match="changed before recognition"):
        bridgit_rank_layout.recognize_frames(
            Path("reference.png"),
            [Path("frame.png"), Path("unread-next-frame.png")],
            profile,
            expected_frame_sha256s=["e" * 64, "f" * 64],
        )
    assert read_count == 2


def test_anchor_source_raster_is_released_before_recognition(monkeypatch):
    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {
            "x": 0.72,
            "y": 0.02,
            "width": 0.08,
            "height": 0.10,
        },
        "scales": [1.0],
        "minimum_score": 0.80,
        "minimum_margin": 0.03,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    profile = parse_profile(raw)
    released = []

    class Raster(bytearray):
        shape = (720, 1000, 3)

    class SourceRaster(Raster):
        def __del__(self):
            released.append(True)

    read_count = 0

    def read_frame(*_args, **_kwargs):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return Raster(b"reference"), "a" * 64, "b" * 64, None
        return SourceRaster(b"source"), "c" * 64, "d" * 64, None

    monkeypatch.setattr(
        bridgit_rank_layout,
        "_validate_input_raster_budget",
        lambda _paths: [(1000, 720), (1000, 720)],
    )
    monkeypatch.setattr(
        bridgit_rank_layout,
        "_read_frame",
        read_frame,
    )
    monkeypatch.setattr(
        bridgit_rank_layout,
        "register_from_upper_right_anchor",
        lambda *_args, **_kwargs: (Raster(b"registered"), {}),
    )

    def assert_source_released(*_args, **_kwargs):
        assert released == [True]
        raise BridgitRankLayoutError("stop after release check")

    monkeypatch.setattr(
        bridgit_rank_layout,
        "_validate_temporal_identities",
        assert_source_released,
    )
    with pytest.raises(BridgitRankLayoutError, match="stop after release check"):
        bridgit_rank_layout.recognize_frames(
            Path("reference.png"),
            [Path("frame.png")],
            profile,
        )


def test_registered_pixel_replay_stops_before_next_anchor_match(monkeypatch):
    raw = profile_raw()
    raw["geometry"]["interface_anchor"] = {
        "type": "UPPER_RIGHT_TEMPLATE",
        "reference_region": {"x": 0.72, "y": 0.02, "width": 0.08, "height": 0.10},
        "scales": [1.0],
        "minimum_score": 0.80,
        "minimum_margin": 0.03,
    }
    raw["profile_sha256"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "profile_sha256"}
    )
    profile = parse_profile(raw)

    class Raster(bytearray):
        shape = (720, 1000, 3)

    read_count = 0

    def read_frame(*_args, **_kwargs):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return Raster(b"reference"), "a" * 64, "b" * 64, None
        marker = str(read_count)
        return Raster(marker.encode()), marker * 64, chr(96 + read_count) * 64, None

    registration_count = 0

    def register(*_args, **_kwargs):
        nonlocal registration_count
        registration_count += 1
        if registration_count > 2:
            pytest.fail("must reject replay before matching the next frame")
        return Raster(b"same-registered-pixels"), {}

    monkeypatch.setattr(
        bridgit_rank_layout,
        "_validate_input_raster_budget",
        lambda _paths: [(1000, 720)] * 4,
    )
    monkeypatch.setattr(bridgit_rank_layout, "_read_frame", read_frame)
    monkeypatch.setattr(
        bridgit_rank_layout,
        "register_from_upper_right_anchor",
        register,
    )
    with pytest.raises(BridgitRankLayoutError, match="duplicate decoded frame pixels"):
        bridgit_rank_layout.recognize_frames(
            Path("reference.png"),
            [Path(f"frame-{index}.png") for index in range(3)],
            profile,
        )
    assert registration_count == 2


def test_each_frame_must_independently_support_the_fused_deal():
    profile = parse_profile(profile_raw())
    agreed = {suit: tuple("N" * 13) for suit in "HCDS"}
    changed = dict(agreed)
    changed["S"] = tuple("E" + "N" * 12)
    hashes = ["a" * 64, "b" * 64]

    assert (
        bridgit_rank_layout._frame_assignment_issues(
            hashes,
            agreed,
            [agreed, agreed],
            [0.8, 0.8],
            [0.5, 0.5],
            [0.3, 0.3],
            profile,
        )
        == []
    )
    issues = bridgit_rank_layout._frame_assignment_issues(
        hashes,
        agreed,
        [agreed, changed],
        [0.8, 0.39],
        [0.5, 0.11],
        [0.3, 0.19],
        profile,
    )
    assert issues == [
        {
            "frame_sha256": "b" * 64,
            "reasons": [
                "deal_assignment_disagrees",
                "assigned_rank_score_below_threshold",
                "assignment_margin_below_threshold",
                "rank_ink_below_threshold",
            ],
            "minimum_assigned_score": 0.39,
            "minimum_assignment_margin": 0.11,
            "minimum_rank_ink_fraction": 0.19,
        }
    ]


def test_rank_ink_uses_only_the_configured_glyph_crop(
    monkeypatch: pytest.MonkeyPatch,
):
    profile = replace(
        parse_profile(profile_raw()),
        glyph_width=8,
        glyph_height=8,
        local_registration_px=0,
    )
    frame_slices = []
    gray_slices = []

    class FakeFrame:
        def __getitem__(self, key):
            frame_slices.append(key)
            return object()

    class FakeMask:
        def __getitem__(self, key):
            gray_slices.append(key)
            return self

        def __lt__(self, _threshold):
            return self

        def mean(self):
            return 0.25

    class FakeCv2:
        COLOR_BGR2GRAY = 1

        @staticmethod
        def cvtColor(_crop, _conversion):
            return FakeMask()

    monkeypatch.setattr(
        bridgit_rank_layout,
        "_pixel_runtime",
        lambda: (FakeCv2(), object()),
    )
    assert bridgit_rank_layout._rank_ink_fractions(
        [FakeFrame()], (10, 10), profile
    ) == [0.25]
    assert frame_slices == [(slice(10, 18), slice(10, 18))]
    assert gray_slices == [(slice(1, 7), slice(1, 7))]


def test_rank_holes_use_only_the_configured_glyph_crop(
    monkeypatch: pytest.MonkeyPatch,
):
    profile = replace(
        parse_profile(profile_raw()),
        glyph_width=8,
        glyph_height=8,
        local_registration_px=0,
    )
    frame_slices = []
    thresholds = []

    class FakeFrame:
        def __getitem__(self, key):
            frame_slices.append(key)
            return object()

    class FakeBinary:
        def astype(self, _dtype):
            return self

        def __mul__(self, _value):
            return self

    class FakeGray:
        def __lt__(self, threshold):
            thresholds.append(threshold)
            return FakeBinary()

    class FakeCv2:
        COLOR_BGR2GRAY = 1
        RETR_CCOMP = 2
        CHAIN_APPROX_SIMPLE = 3

        @staticmethod
        def cvtColor(_crop, _conversion):
            return FakeGray()

        @staticmethod
        def findContours(_binary, _mode, _method):
            return (), None

    monkeypatch.setattr(
        bridgit_rank_layout,
        "_pixel_runtime",
        lambda: (FakeCv2(), object()),
    )
    assert bridgit_rank_layout._rank_hole_counts([FakeFrame()], (10, 10), profile) == [
        0
    ]
    assert frame_slices == [(slice(10, 18), slice(10, 18))]
    assert thresholds == [profile.binary_threshold]


def test_hole_prior_does_not_raise_raw_template_evidence(monkeypatch):
    np = pytest.importorskip("numpy")
    profile = replace(parse_profile(profile_raw()), local_registration_px=0)
    monkeypatch.setattr(
        bridgit_rank_layout,
        "_pixel_runtime",
        lambda: (object(), np),
    )
    monkeypatch.setattr(bridgit_rank_layout, "_glyph", lambda *_args: object())
    monkeypatch.setattr(bridgit_rank_layout, "_similarity", lambda *_args: 0.20)
    monkeypatch.setattr(bridgit_rank_layout, "_rank_hole_counts", lambda *_args: [2])

    assignment_scores, raw_scores = bridgit_rank_layout._slot_score_components(
        [object()],
        {rank: object() for rank in bridgit_rank_layout.RANKS},
        (10, 10),
        profile,
    )

    assert raw_scores.tolist() == pytest.approx([0.20] * 13)
    assert assignment_scores[bridgit_rank_layout.RANKS.index("8")] == pytest.approx(
        0.50
    )
    assert raw_scores[bridgit_rank_layout.RANKS.index("8")] < profile.min_template_score


def test_generated_glyph_coordinates_must_be_unique_across_suits():
    profile = parse_profile(profile_raw())
    anchors = {seat: dict(values) for seat, values in profile.anchors.items()}
    anchors["W"]["C"] = anchors["W"]["H"]
    lengths = {
        seat: {suit: int(seat == "W" and suit in {"H", "C"}) for suit in "HCDS"}
        for seat in "NESW"
    }

    assert not bridgit_rank_layout._generated_glyph_coords_are_unique(
        lengths, anchors, {}
    )


def test_side_fan_coordinate_preflight_includes_registration_radius():
    profile = parse_profile(profile_raw())
    assert bridgit_rank_layout._glyph_coords_fit_frame([(2, 100), (20, 100)], profile)
    assert not bridgit_rank_layout._glyph_coords_fit_frame(
        [(1, 100), (20, 100)], profile
    )
    assert not bridgit_rank_layout._glyph_coords_fit_frame([(980, 100)], profile)


def test_single_good_frame_is_pending_not_weak_evidence():
    assert bridgit_rank_layout._temporal_support_gates(
        observed_frames=1,
        required_frames=2,
        minimum_ink_support=1,
    ) == (True, False)
    assert bridgit_rank_layout._temporal_support_gates(
        observed_frames=2,
        required_frames=2,
        minimum_ink_support=2,
    ) == (True, True)
    assert bridgit_rank_layout._temporal_support_gates(
        observed_frames=2,
        required_frames=2,
        minimum_ink_support=1,
    ) == (False, True)


def test_cli_rejection_is_retained_as_fail_closed_receipt(tmp_path: Path, monkeypatch):
    job_path = tmp_path / "job.json"
    output_path = tmp_path / "receipt.json"
    job_path.write_text('{"job_type":"one","job_type":"two"}', encoding="utf-8")
    with pytest.raises(BridgitRankLayoutError, match="duplicate JSON keys"):
        load_job(job_path)

    job_path.write_text("{}", encoding="utf-8")
    assert (
        bridgit_rank_layout.main(["--job", str(job_path), "--output", str(output_path)])
        == 2
    )
    receipt = json.loads(output_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "REJECTED"
    assert receipt["result_scope"] == "SHADOW_ONLY"
    assert receipt["canonical_promotion_allowed"] is False
    assert receipt["production_write_performed"] is False
    assert receipt["school_canon_write_performed"] is False

    job_path.write_text(
        json.dumps(
            {
                "job_type": JOB_TYPE,
                "input_root": str(tmp_path) + "\u0000",
                "production_write": False,
                "allow_hidden_information": False,
                "teacher_pointer_events": [],
            }
        ),
        encoding="utf-8",
    )
    assert (
        bridgit_rank_layout.main(["--job", str(job_path), "--output", str(output_path)])
        == 2
    )
    receipt = json.loads(output_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "REJECTED"
    assert receipt["reason"] == "input_root is unavailable"

    job_path.write_text('{"value":' + "1" * 5000 + "}", encoding="utf-8")
    assert (
        bridgit_rank_layout.main(["--job", str(job_path), "--output", str(output_path)])
        == 2
    )
    receipt = json.loads(output_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "REJECTED"
    assert receipt["reason"] == "job is not valid UTF-8 JSON"

    with monkeypatch.context() as patcher:

        def recursion_failure(*_args, **_kwargs):
            raise RecursionError("parser nesting limit")

        patcher.setattr(bridgit_rank_layout.json, "loads", recursion_failure)
        with pytest.raises(BridgitRankLayoutError, match="not valid UTF-8 JSON"):
            bridgit_rank_layout._json_object(b"{}", "job")

    job_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        bridgit_rank_layout,
        "execute_shadow_job",
        lambda _job: (_ for _ in ()).throw(MemoryError()),
    )
    assert (
        bridgit_rank_layout.main(["--job", str(job_path), "--output", str(output_path)])
        == 2
    )
    receipt = json.loads(output_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "REJECTED"
    assert receipt["reason"] == "MemoryError"


def test_path_resolution_runtime_errors_are_translated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    candidate = tmp_path / "profile.json"
    candidate.write_text("{}", encoding="utf-8")

    def fail_resolve(_path, *, strict=False):
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(BridgitRankLayoutError, match="profile_ref escapes input_root"):
        bridgit_rank_layout._validated_ref(
            {"path": str(candidate), "sha256": "a" * 64},
            "profile_ref",
            max_bytes=1024,
            input_root=tmp_path,
        )


def test_normalized_region_rounding_preserves_frame_boundary():
    start, size = bridgit_rank_layout._rounded_normalized_axis(
        0.999999994,
        0.000000006,
    )
    assert start == 0.99999999
    assert size == 0.00000001
    assert start + size <= 1.0
