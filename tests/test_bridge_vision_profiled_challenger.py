import hashlib
import json
from pathlib import Path

import pytest

from bridge_vision import BridgeVisionEngine
from bridge_vision.profiled_challenger import (
    ProfiledCardChallenger,
    ProfiledChallengerError,
    build_teach_profile,
    parse_profile,
)
from tools.bridge_video_positions import process_job_frames


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def profile_raw(**gate_overrides):
    gates = {
        "min_registration_inliers": 20,
        "min_registration_inlier_ratio": 0.80,
        "min_deal_match_inliers": 20,
        "min_deal_match_inlier_ratio": 0.80,
        "min_rank_confidence": 0.90,
        "min_suit_confidence": 0.90,
        "min_reference_confidence": 0.90,
        "min_card_confidence": 0.90,
        "min_temporal_observations": 2,
        "seat_dead_zone": 0.08,
        **gate_overrides,
    }
    return build_teach_profile(
        profile_id="lesson-ui-v1",
        reference_frame_sha256="a" * 64,
        reference_size={"width": 1000, "height": 1000},
        table_region={"x": 0, "y": 0, "w": 1000, "h": 1000},
        rank_templates={rank: digest("rank:" + rank) for rank in "AKQJT98765432"},
        suit_templates={suit: digest("suit:" + suit) for suit in "SHDC"},
        gates=gates,
    )


def frame_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observed_card(card="AS", *, x=490, y=50, rank_confidence=0.98, suit_confidence=0.97,
                  reference_card=None, reference_confidence=0.96):
    rank, suit = card[0], card[1]
    return {
        "box": {"x": x, "y": y, "w": 20, "h": 20},
        "rank": {"value": rank, "confidence": rank_confidence},
        "suit": {"value": suit, "confidence": suit_confidence},
        "reference_match": {"card": reference_card or card, "confidence": reference_confidence},
    }


def payload(path: Path, cards, *, homography=None, registration_ratio=0.95, deal_identity=None):
    return {
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


def test_rank_suit_must_agree_with_independent_reference_match(tmp_path: Path):
    first, _ = make_frames(tmp_path)
    raw = payload(first, [observed_card("AS", reference_card="AH")])
    detector = ProfiledCardChallenger(parse_profile(profile_raw()), lambda *_: raw)
    result = detector(first)
    assert result["hands"] == {}
    assert result["status"] == "PENDING_TEMPORAL_CONSENSUS"
    assert result["evidence"]["channel_rejections"][0]["reason"] == "CHANNEL_DISAGREEMENT"


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
    assert summary["legacy_old_bbo_enabled"] is False
    assert summary["detectors"] == ["profiled-interface-challenger"]
    assert summary["recognized_frames"] == 1

    records = [json.loads(line) for line in (tmp_path / "bridge_positions.jsonl").read_text().splitlines()]
    assert records[0]["status"] == "UNAVAILABLE"
    assert records[0]["diagnostics"][0]["status"] == "PENDING_TEMPORAL_CONSENSUS"
    assert records[0]["diagnostics"][0]["evidence"]["pending"][0]["card"] == "AS"
    assert records[1]["deal"]["hands"]["N"]["cards"] == ["AS"]
    evidence = records[1]["candidates"][0]["evidence"]
    assert evidence["canonical_promotion_allowed"] is False

    with pytest.raises(ValueError, match="cannot be combined"):
        process_job_frames(
            tmp_path,
            profiled_challenger=detector,
            allow_legacy_old_bbo=True,
        )


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
