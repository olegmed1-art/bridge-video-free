import json
import math
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

    def fake_recognize(reference_path, frame_paths, profile):
        assert reference_path == reference
        assert frame_paths == [first, second]
        assert profile.profile_id == raw["profile_id"]
        return {
            "status": "SHADOW_FULL_LAYOUT_CANDIDATE",
            "result_scope": "SHADOW_ONLY",
            "canonical_promotion_allowed": False,
            "school_canon_write_performed": False,
            "hidden_hand_reconstruction_performed": False,
            "input_hashes": {
                "reference_frame_sha256": reference_sha,
                "frame_sha256s": [
                    bridgit_rank_layout.sha256_file(first),
                    bridgit_rank_layout.sha256_file(second),
                ],
            },
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
    }
    receipt = execute_shadow_job(job)
    assert receipt == execute_shadow_job(job)
    assert receipt["result"]["result_scope"] == "SHADOW_ONLY"
    assert receipt["result"]["canonical_promotion_allowed"] is False
    assert receipt["production_write_performed"] is False
    assert receipt["school_canon_write_performed"] is False
    claimed = receipt.pop("receipt_sha256")
    assert claimed == canonical_hash(receipt)


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

    profile = parse_profile(profile_raw())
    bridgit_rank_layout._validate_scoring_budget(profile, observation_count=2)
    with pytest.raises(BridgitRankLayoutError, match="scoring-operation budget"):
        bridgit_rank_layout.recognize_frames(
            Path("unread-reference.jpg"),
            [Path(f"unread-frame-{index}.jpg") for index in range(16)],
            profile,
        )


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


def test_cli_rejection_is_retained_as_fail_closed_receipt(tmp_path: Path):
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
