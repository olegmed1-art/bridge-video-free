import hashlib
import json
from pathlib import Path

import pytest

from bridge_vision import BridgeVisionEngine
from bridge_vision.profiled_challenger import (
    CARDS,
    MAX_PROFILE_BYTES,
    ProfiledCardChallenger,
    ProfiledChallengerError,
    build_teach_profile,
    derive_duplicate_board_metadata,
    parse_profile,
)
from tools.bridge_video_positions import process_job_frames


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def profile_raw(*, seat_positions=None, **gate_overrides):
    gates = {
        "min_registration_inliers": 20,
        "min_registration_inlier_ratio": 0.80,
        "min_deal_match_inliers": 20,
        "min_deal_match_inlier_ratio": 0.80,
        "min_rank_confidence": 0.90,
        "min_suit_confidence": 0.90,
        "min_reference_confidence": 0.90,
        "min_card_confidence": 0.90,
        "min_ambiguous_candidate_confidence": 0.70,
        "min_temporal_observations": 2,
        "seat_dead_zone": 0.08,
        **gate_overrides,
    }
    positions = seat_positions or {"top": "N", "right": "E", "bottom": "S", "left": "W"}
    axes = {
        logical_seat: "X_ASC" if position in {"top", "bottom"} else "Y_ASC"
        for position, logical_seat in positions.items()
    }
    return build_teach_profile(
        profile_id="lesson-ui-v1",
        reference_frame_sha256="a" * 64,
        reference_size={"width": 1000, "height": 1000},
        table_region={"x": 0, "y": 0, "w": 1000, "h": 1000},
        rank_templates={rank: digest("rank:" + rank) for rank in "AKQJT98765432"},
        suit_templates={suit: digest("suit:" + suit) for suit in "SHDC"},
        card_templates={card: digest("card:" + card) for card in CARDS},
        rank_suit_channel_id="glyph-rank-suit-v1",
        reference_channel_id="full-card-reference-v1",
        human_verified=True,
        verification={
            "method": "HUMAN_LABEL_REVIEW",
            "reviewer_id": "bridge-school-reviewer",
            "verified_at": "2026-08-28T23:59:00Z",
            "reference_frame_sha256": "a" * 64,
        },
        ordering_prior={
            "human_verified": True,
            "suit_order": ["H", "C", "D", "S"],
            "rank_order": list("AKQJT98765432"),
            "seat_axes": axes,
            "seat_positions": positions,
        },
        gates=gates,
    )


def frame_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observed_card(card="AS", *, x=490, y=50, rank_confidence=0.98, suit_confidence=0.97,
                  reference_card=None, reference_confidence=0.96):
    rank, suit = card[0], card[1]
    return {
        "box": {"x": x, "y": y, "w": 20, "h": 20},
        "rank": {"value": rank, "confidence": rank_confidence, "channel_id": "glyph-rank-suit-v1"},
        "suit": {"value": suit, "confidence": suit_confidence, "channel_id": "glyph-rank-suit-v1"},
        "reference_match": {
            "card": reference_card or card,
            "confidence": reference_confidence,
            "channel_id": "full-card-reference-v1",
        },
    }


def ambiguous_card(cards, *, x, y):
    return {
        "box": {"x": x, "y": y, "w": 20, "h": 20},
        "card_candidates": [
            {"card": card, "confidence": 0.85, "channel_id": "full-card-reference-v1"}
            for card in cards
        ],
    }


def payload(
    path: Path,
    cards,
    *,
    homography=None,
    registration_ratio=0.95,
    deal_identity=None,
    board_metadata=None,
):
    result = {
        "frame_sha256": frame_sha(path),
        "registration": {
            "reference_frame_sha256": "a" * 64,
            "inliers": 100,
            "inlier_ratio": registration_ratio,
            "homography": homography or [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        },
        "deal_identity": deal_identity or {"kind": "EXPLICIT_BOARD", "scope": "lesson-1", "value": "board-7"},
        "cards": cards,
    }
    if board_metadata is not None:
        result["board_metadata"] = board_metadata
    return result


def metadata_field(value, *, segment="frame#metadata"):
    return {
        "value": value,
        "confidence": 0.98,
        "source": "VISUAL_TEXT",
        "evidence_locator": segment,
    }


def make_frames(tmp_path: Path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first-frame")
    second.write_bytes(b"second-frame")
    return first, second


def test_profile_requires_human_verified_complete_teach_templates():
    raw = profile_raw()
    raw["human_verified"] = False
    with pytest.raises(ProfiledChallengerError, match="human verified"):
        parse_profile(raw)

    raw = profile_raw()
    raw["teach"]["rank_templates"].pop("A")
    with pytest.raises(ProfiledChallengerError, match="complete symbol set"):
        parse_profile(raw)

    with pytest.raises(ProfiledChallengerError, match="explicit human verification"):
        build_teach_profile(
            profile_id="lesson-ui-v1",
            reference_frame_sha256="a" * 64,
            reference_size={"width": 1000, "height": 1000},
            table_region={"x": 0, "y": 0, "w": 1000, "h": 1000},
            rank_templates={rank: digest("rank:" + rank) for rank in "AKQJT98765432"},
            suit_templates={suit: digest("suit:" + suit) for suit in "SHDC"},
            card_templates={card: digest("card:" + card) for card in CARDS},
            rank_suit_channel_id="glyph-rank-suit-v1",
            reference_channel_id="full-card-reference-v1",
            human_verified=False,
            verification={
                "method": "HUMAN_LABEL_REVIEW",
                "reviewer_id": "bridge-school-reviewer",
                "verified_at": "2026-08-28T23:59:00Z",
                "reference_frame_sha256": "a" * 64,
            },
            ordering_prior={
                "human_verified": True,
                "suit_order": ["H", "C", "D", "S"],
                "rank_order": list("AKQJT98765432"),
                "seat_axes": {"N": "X_ASC", "S": "X_ASC", "E": "Y_ASC", "W": "Y_ASC"},
            },
            gates=profile_raw()["gates"],
        )

    raw = profile_raw()
    raw["verification"]["reference_frame_sha256"] = "b" * 64
    with pytest.raises(ProfiledChallengerError, match="verification reference"):
        parse_profile(raw)

    raw = profile_raw()
    raw["verification"]["verified_at"] = "2026-19-39T23:59:00Z"
    with pytest.raises(ProfiledChallengerError, match="verification timestamp"):
        parse_profile(raw)


def test_profile_rejects_non_independent_channels_and_duplicate_templates():
    raw = profile_raw()
    raw["teach"]["channels"]["reference"] = raw["teach"]["channels"]["rank_suit"]
    with pytest.raises(ProfiledChallengerError, match="must be independent"):
        parse_profile(raw)

    raw = profile_raw()
    raw["teach"]["card_templates"]["AS"] = raw["teach"]["card_templates"]["AH"]
    with pytest.raises(ProfiledChallengerError, match="duplicate template hashes"):
        parse_profile(raw)

    raw = profile_raw()
    raw["teach"]["ordering_prior"]["suit_order"] = ["S", "H", "D", "C"]
    with pytest.raises(ProfiledChallengerError, match="suit order"):
        parse_profile(raw)


def test_profile_loader_bounds_size_and_duplicate_json_keys(tmp_path: Path):
    from bridge_vision.profiled_challenger import load_profile

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_PROFILE_BYTES + 1))
    with pytest.raises(ProfiledChallengerError, match="size limit"):
        load_profile(oversized)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
    with pytest.raises(ProfiledChallengerError, match="duplicate JSON keys"):
        load_profile(duplicate)


def test_two_channel_agreement_and_two_frames_are_required(tmp_path: Path):
    first, second = make_frames(tmp_path)
    observations = {
        first.name: payload(first, [observed_card()]),
        second.name: payload(second, [observed_card()]),
    }
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda frame, _: observations[frame.name])

    pending = detector(first)
    assert pending["status"] == "PENDING_TEMPORAL_CONSENSUS"
    assert pending["hands"] == {}
    assert pending["evidence"]["canonical_promotion_allowed"] is False

    accepted = detector(second)
    assert accepted["status"] == "PASS"
    assert accepted["hands"] == {"N": ["AS"]}
    consensus = accepted["evidence"]["consensus"][0]
    assert consensus["independent_frames"] == 2
    assert consensus["frame_sha256s"] == sorted([frame_sha(first), frame_sha(second)])
    assert accepted["evidence"]["canonical_promotion_allowed"] is False
    assert len(accepted["evidence"]["profile_verification_sha256"]) == 64


def test_rank_suit_must_agree_with_independent_reference_match(tmp_path: Path):
    first, _ = make_frames(tmp_path)
    raw = payload(first, [observed_card("AS", reference_card="AH")])
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda *_: raw)
    result = detector(first)
    assert result["hands"] == {}
    assert result["status"] == "REVIEW"
    assert result["evidence"]["reason"] == "NO_ACCEPTED_CARD_OBSERVATIONS"
    assert result["evidence"]["channel_rejections"][0]["reason"] == "CHANNEL_DISAGREEMENT"


def test_channel_identity_mismatch_cannot_enter_temporal_state(tmp_path: Path):
    first, second = make_frames(tmp_path)
    bad_card = observed_card()
    bad_card["reference_match"]["channel_id"] = "glyph-rank-suit-v1"
    observations = {
        first.name: payload(first, [bad_card]),
        second.name: payload(second, [observed_card()]),
    }
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda frame, _: observations[frame.name])
    rejected = detector(first)
    assert rejected["status"] == "REVIEW"
    assert rejected["evidence"]["reason"] == "NO_ACCEPTED_CARD_OBSERVATIONS"
    pending = detector(second)
    assert pending["status"] == "PENDING_TEMPORAL_CONSENSUS"
    assert pending["hands"] == {}


@pytest.mark.parametrize(
    ("known_before", "known_after", "ambiguous", "expected"),
    [
        (
            observed_card("AH", x=300, y=50),
            observed_card("QH", x=500, y=50),
            ambiguous_card(["KH", "2S"], x=400, y=50),
            "KH",
        ),
        (
            observed_card("2H", x=300, y=900),
            observed_card("QC", x=500, y=900),
            ambiguous_card(["AC", "AS"], x=400, y=900),
            "AC",
        ),
        (
            observed_card("AH", x=900, y=300),
            observed_card("QH", x=900, y=500),
            ambiguous_card(["KH", "2S"], x=900, y=400),
            "KH",
        ),
        (
            observed_card("AH", x=50, y=300),
            observed_card("QH", x=50, y=500),
            ambiguous_card(["KH", "2S"], x=50, y=400),
            "KH",
        ),
    ],
)
def test_layout_prior_suggests_unique_card_without_promoting_it(
    tmp_path: Path,
    known_before,
    known_after,
    ambiguous,
    expected,
):
    first, _ = make_frames(tmp_path)
    raw = payload(first, [known_before, ambiguous, known_after])
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda *_: raw)
    result = detector(first)
    assert result["hands"] == {}
    suggestion = result["evidence"]["layout_suggestions"][0]
    assert suggestion["resolution"] == "LAYOUT_UNIQUE_SUGGESTION"
    assert suggestion["suggested_card"] == expected
    assert suggestion["provenance_class"] == "LAYOUT_SUGGESTION"
    assert suggestion["accepted_as_observation"] is False
    assert result["evidence"]["canonical_promotion_allowed"] is False


@pytest.mark.parametrize(
    ("before", "ambiguous", "after"),
    [
        (observed_card("2H", x=300, y=50), ambiguous_card(["AC", "AS"], x=400, y=50), observed_card("QD", x=500, y=50)),
        (observed_card("2H", x=300, y=900), ambiguous_card(["AC", "AS"], x=400, y=900), observed_card("QD", x=500, y=900)),
        (observed_card("2H", x=900, y=300), ambiguous_card(["AC", "AS"], x=900, y=400), observed_card("QD", x=900, y=500)),
        (observed_card("2H", x=50, y=300), ambiguous_card(["AC", "AS"], x=50, y=400), observed_card("QD", x=50, y=500)),
    ],
)
def test_verified_hcds_suit_order_applies_horizontally_and_vertically_without_promotion(
    tmp_path: Path,
    before,
    ambiguous,
    after,
):
    first, _ = make_frames(tmp_path)
    detector = ProfiledCardChallenger(
        parse_profile(profile_raw()),
        lambda *_: payload(first, [before, ambiguous, after]),
    )

    result = detector(first)

    suggestion = result["evidence"]["layout_suggestions"][0]
    assert suggestion["suggested_card"] == "AC"
    assert suggestion["accepted_as_observation"] is False
    assert detector.profile.ordering_prior["suit_order"] == list("HCDS")
    assert detector.profile.ordering_prior["rank_order"] == list("AKQJT98765432")


@pytest.mark.parametrize(
    "positions",
    [
        {"top": "N", "right": "E", "bottom": "S", "left": "W"},
        {"top": "W", "right": "N", "bottom": "E", "left": "S"},
        {"top": "S", "right": "W", "bottom": "N", "left": "E"},
        {"top": "E", "right": "S", "bottom": "W", "left": "N"},
    ],
)
def test_verified_quarter_turn_rotation_maps_screen_positions_to_logical_seats(
    tmp_path: Path,
    positions,
):
    first, second = make_frames(tmp_path)
    screen_cards = {
        "top": observed_card("AS", x=490, y=50),
        "right": observed_card("KH", x=900, y=490),
        "bottom": observed_card("QD", x=490, y=900),
        "left": observed_card("JC", x=50, y=490),
    }
    observations = {
        first.name: payload(first, list(screen_cards.values())),
        second.name: payload(second, list(screen_cards.values())),
    }
    detector = ProfiledCardChallenger(
        parse_profile(profile_raw(seat_positions=positions)),
        lambda frame, _: observations[frame.name],
    )

    detector(first)
    accepted = detector(second)

    expected = {
        positions[screen_position]: [card["reference_match"]["card"]]
        for screen_position, card in screen_cards.items()
    }
    assert accepted["hands"] == expected
    assert detector.profile.ordering_prior["rotation_degrees_clockwise"] in {0, 90, 180, 270}


def test_non_cyclic_compass_mapping_is_rejected():
    raw = profile_raw()
    raw["teach"]["ordering_prior"]["seat_positions"] = {
        "top": "N",
        "right": "S",
        "bottom": "E",
        "left": "W",
    }
    with pytest.raises(ProfiledChallengerError, match="0/90/180/270"):
        parse_profile(raw)


def test_standard_board_number_derives_dealer_and_vulnerability_cycle():
    assert [derive_duplicate_board_metadata(board)[0] for board in range(1, 9)] == list("NESWNESW")
    assert derive_duplicate_board_metadata(1) == ("N", "NONE")
    assert derive_duplicate_board_metadata(2) == ("E", "NS")
    assert derive_duplicate_board_metadata(16) == ("W", "EW")
    assert derive_duplicate_board_metadata(17) == ("N", "NONE")


def test_board_metadata_requires_two_frames_and_preserves_rotated_compass(tmp_path: Path):
    first, second = make_frames(tmp_path)
    metadata = {"board_number": metadata_field(16)}
    observations = {
        first.name: payload(first, [observed_card()], board_metadata=metadata),
        second.name: payload(second, [observed_card()], board_metadata=metadata),
    }
    positions = {"top": "W", "right": "N", "bottom": "E", "left": "S"}
    detector = ProfiledCardChallenger(
        parse_profile(profile_raw(seat_positions=positions)),
        lambda frame, _: observations[frame.name],
    )

    pending = detector(first)["evidence"]["board_metadata"]
    confirmed = detector(second)["evidence"]["board_metadata"]

    assert pending["status"] == "PENDING_TEMPORAL_CONSENSUS"
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["board_number"] == 16
    assert confirmed["dealer"] == "W"
    assert confirmed["vulnerability"] == "EW"
    assert confirmed["provenance"]["dealer"]["class"] == "DERIVED_FROM_BOARD_NUMBER"
    assert confirmed["seat_positions"] == positions
    assert confirmed["rotation_degrees_clockwise"] == 90


def test_observed_dealer_conflicting_with_board_number_fails_frame_gate(tmp_path: Path):
    first, _ = make_frames(tmp_path)
    metadata = {
        "board_number": metadata_field(16),
        "dealer": metadata_field("N"),
    }
    detector = ProfiledCardChallenger(
        parse_profile(profile_raw()),
        lambda frame, _: payload(frame, [observed_card()], board_metadata=metadata),
    )

    result = detector(first)

    assert result["status"] == "REVIEW"
    assert result["evidence"]["reason"] == "FRAME_GATE_REJECTED"
    assert "dealer conflicts" in result["evidence"]["detail"]


def test_board_number_disagreement_within_one_deal_is_hard_conflict(tmp_path: Path):
    first, second = make_frames(tmp_path)
    observations = {
        first.name: payload(
            first,
            [observed_card()],
            board_metadata={"board_number": metadata_field(7)},
        ),
        second.name: payload(
            second,
            [observed_card()],
            board_metadata={"board_number": metadata_field(8)},
        ),
    }
    detector = ProfiledCardChallenger(
        parse_profile(profile_raw()),
        lambda frame, _: observations[frame.name],
    )

    detector(first)
    conflict = detector(second)

    assert conflict["status"] == "CONFLICT"
    assert conflict["conflicts"][0]["reason"] == "BOARD_METADATA_DISAGREEMENT"


def test_registration_gate_and_homography_precede_seat_assignment(tmp_path: Path):
    first, second = make_frames(tmp_path)
    low = payload(first, [observed_card()], registration_ratio=0.20)
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda *_: low)
    rejected = detector(first)
    assert rejected["status"] == "REVIEW"
    assert rejected["evidence"]["reason"] == "FRAME_GATE_REJECTED"

    # The raw card centre is left of North. Registration translates it into the
    # North seat region before geometry is evaluated.
    translate = [[1, 0, 100], [0, 1, 0], [0, 0, 1]]
    observations = {
        first.name: payload(first, [observed_card(x=390)], homography=translate),
        second.name: payload(second, [observed_card(x=390)], homography=translate),
    }
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda frame, _: observations[frame.name])
    detector(first)
    accepted = detector(second)
    assert accepted["hands"] == {"N": ["AS"]}

    mirrored = payload(first, [observed_card()], homography=[[-1, 0, 1000], [0, 1, 0], [0, 0, 1]])
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda *_: mirrored)
    rejected = detector(first)
    assert rejected["status"] == "REVIEW"
    assert "mirrored" in rejected["evidence"]["detail"]


def test_cross_frame_cross_seat_disagreement_is_a_hard_conflict(tmp_path: Path):
    first, second = make_frames(tmp_path)
    observations = {
        first.name: payload(first, [observed_card(x=490, y=50)]),
        second.name: payload(second, [observed_card(x=490, y=900)]),
    }
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda frame, _: observations[frame.name])
    engine = BridgeVisionEngine({"profiled": detector})
    assert engine.analyze_frame(first).status == "UNAVAILABLE"
    conflict = engine.analyze_frame(second).to_dict()
    assert conflict["status"] == "CONFLICT"
    assert conflict["deal"] is None
    assert conflict["conflicts"][0]["reason"] == "TEMPORAL_CROSS_SEAT_DISAGREEMENT"


def test_shadow_detector_mode_cannot_be_renamed_or_mixed_away():
    detector = ProfiledCardChallenger(
        parse_profile(profile_raw()),
        lambda current, _: payload(current, [observed_card()]),
    )
    renamed = BridgeVisionEngine({"arbitrary-name": detector})
    assert renamed.shadow_only is True

    with pytest.raises(ValueError, match="cannot be mixed"):
        BridgeVisionEngine({
            "shadow": detector,
            "canonical": lambda _: {"hands": {"N": ["AS"]}, "confidence": 1.0},
        })


def test_profiled_challenger_is_explicit_opt_in_for_video_positions(tmp_path: Path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    first = frames_dir / "first.jpg"
    second = frames_dir / "second.jpg"
    first.write_bytes(b"first-frame")
    second.write_bytes(b"second-frame")
    (tmp_path / "manifest.json").write_text(
        json.dumps({
            "job_id": "profiled-shadow-1",
            "source_fingerprint": "source-1",
            "frames": [
                {"time": 10.0, "file": first.name, "sha256": frame_sha(first)},
                {"time": 40.0, "file": second.name, "sha256": frame_sha(second)},
            ],
        }),
        encoding="utf-8",
    )
    observations = {
        first.name: payload(first, [observed_card()]),
        second.name: payload(second, [observed_card()]),
    }
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda frame, _: observations[frame.name])
    summary = process_job_frames(tmp_path, profiled_challenger=detector)
    assert summary["profiled_challenger_enabled"] is True
    assert summary["status"] == "SHADOW_COMPLETED"
    assert summary["result_scope"] == "SHADOW_ONLY"
    assert summary["canonical_promotion_allowed"] is False
    assert summary["legacy_old_bbo_enabled"] is False
    assert summary["detectors"] == ["profiled-interface-challenger"]
    assert summary["recognized_frames"] == 1

    assert not (tmp_path / "bridge_positions.jsonl").exists()
    shadow_path = tmp_path / "bridge_positions_profiled_shadow.jsonl"
    records = [json.loads(line) for line in shadow_path.read_text().splitlines()]
    assert records[0]["status"] == "UNAVAILABLE"
    assert records[0]["diagnostics"][0]["status"] == "PENDING_TEMPORAL_CONSENSUS"
    assert records[0]["diagnostics"][0]["evidence"]["pending"][0]["card"] == "AS"
    assert records[1]["deal"]["hands"]["N"]["cards"] == ["AS"]
    evidence = records[1]["candidates"][0]["evidence"]
    assert evidence["canonical_promotion_allowed"] is False
    pbn = (tmp_path / "bridge_positions_profiled_shadow.pbn").read_text(encoding="utf-8")
    assert '% X-ResultScope: SHADOW_ONLY' in pbn
    assert '[X-Observed-N "A.-.-.-"]' in pbn
    assert '[X-UnknownCount-N "12"]' in pbn
    assert '[Deal "' not in pbn
    assert summary["pbn_output"] == "bridge_positions_profiled_shadow.pbn"

    with pytest.raises(ValueError, match="cannot be combined"):
        process_job_frames(
            tmp_path,
            profiled_challenger=detector,
            allow_legacy_old_bbo=True,
        )


def test_profiled_shadow_fuses_attributed_student_speech_with_layout_without_promotion(
    tmp_path: Path,
):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frame = frames_dir / "speech-layout.jpg"
    frame.write_bytes(b"speech-layout-frame")
    (tmp_path / "manifest.json").write_text(
        json.dumps({
            "job_id": "profiled-speech-layout-1",
            "source_fingerprint": "source-1",
            "frames": [{"time": 10.0, "file": frame.name, "sha256": frame_sha(frame)}],
        }),
        encoding="utf-8",
    )
    observations = [
        observed_card("AH", x=300, y=50),
        ambiguous_card(["KH", "2S"], x=400, y=50),
        observed_card("QH", x=500, y=50),
    ]
    detector = ProfiledCardChallenger(
        parse_profile(profile_raw()),
        lambda current, _: payload(current, observations),
    )
    speech = [{
        "card": "KH",
        "seat": "N",
        "confidence": 0.98,
        "speaker_role": "STUDENT",
        "speaker_id": "student-1",
        "speaker_identity_verified": True,
        "speaker_assignment_confidence": 0.97,
        "evidence_locator": "transcript.jsonl#segment=7",
        "start": 9.0,
        "end": 11.0,
        "frame_sha256": frame_sha(frame),
    }]

    summary = process_job_frames(
        tmp_path,
        profiled_challenger=detector,
        speech_declarations=speech,
    )

    assert summary["status"] == "SHADOW_REVIEW"
    assert summary["speech_fusion_records"] == 1
    assert summary["speech_review_frames"] == 1
    assert summary["speech_declarations_input"] == 1
    assert summary["speech_declarations_matched"] == 1
    assert summary["speech_unmatched_declarations"] == 0
    record = json.loads(
        (tmp_path / "bridge_positions_profiled_shadow.jsonl").read_text().splitlines()[0]
    )
    assert record["deal"] is None
    assert record["fused_deal"]["hands"]["N"]["cards"] == []
    suggestion = record["speech_fusion"]["student_speech_suggestions"][0]
    assert suggestion["resolution"] == "CORROBORATES_LAYOUT_SUGGESTION"
    assert suggestion["accepted_as_observation"] is False
    assert record["speech_fusion"]["canonical_promotion_allowed"] is False


def test_speech_fusion_is_rejected_outside_profiled_shadow(tmp_path: Path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frame = frames_dir / "frame.jpg"
    frame.write_bytes(b"frame")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"frames": [{"time": 1.0, "file": frame.name, "sha256": frame_sha(frame)}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="limited to the profiled shadow"):
        process_job_frames(
            tmp_path,
            parser=lambda _: {"status": "PASS", "hands": {"N": ["AS"]}},
            speech_declarations=[{}],
        )


def test_profiled_shadow_rejects_manifest_frame_hash_mismatch(tmp_path: Path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frame = frames_dir / "frame.jpg"
    frame.write_bytes(b"real-frame")
    (tmp_path / "manifest.json").write_text(
        json.dumps({
            "job_id": "profiled-shadow-bad-hash",
            "source_fingerprint": "source-1",
            "frames": [{"time": 10.0, "file": frame.name, "sha256": "0" * 64}],
        }),
        encoding="utf-8",
    )
    detector = ProfiledCardChallenger(
        parse_profile(profile_raw()),
        lambda current, _: payload(current, [observed_card()]),
    )
    with pytest.raises(ValueError, match="frame hash mismatch"):
        process_job_frames(tmp_path, profiled_challenger=detector)
    assert not (tmp_path / "bridge_positions_profiled_shadow.jsonl").exists()


def test_39_to_13_derivation_waits_for_per_card_temporal_consensus(tmp_path: Path):
    first, second = make_frames(tmp_path)
    ranks = "AKQJT98765432"
    cards = []
    for index, rank in enumerate(ranks):
        cards.append(observed_card(rank + "S", x=300 + index * 30, y=50))
        cards.append(observed_card(rank + "H", x=900, y=300 + index * 30))
        cards.append(observed_card(rank + "D", x=300 + index * 30, y=900))
    observations = {
        first.name: payload(first, cards),
        second.name: payload(second, cards),
    }
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda frame, _: observations[frame.name])
    engine = BridgeVisionEngine({"profiled": detector})

    pending = engine.analyze_frame(first).to_dict()
    assert pending["status"] == "UNAVAILABLE"
    assert pending["deal"] is None

    accepted = engine.analyze_frame(second).to_dict()
    assert accepted["status"] == "PARTIAL_BOARD_OBSERVATION"
    assert len(accepted["deal"]["hands"]["W"]["cards"]) == 13
    derivation = accepted["deal"]["derivations"][0]
    assert derivation["provenance_class"] == "DERIVED"
    assert derivation["evidence_basis"] == "39_unique_cards_in_three_complete_observed_hands"
    assert accepted["candidates"][0]["evidence"]["canonical_promotion_allowed"] is False
