"""Synthetic vertical partial-card path; no real-video accuracy claim."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from bridge_vision.bridgit_adjacent_candidate_profile import PROFILE
from bridge_vision.bridgit_partial_evidence import (
    PartialEvidenceError, SamplingContract, accumulate_detected_frames,
    accumulate_partial_frames,
)
from bridge_vision.native_cards import NativeFourSeatCardDetector


def _contract(*, attempt_gap_ms: int = 3000) -> SamplingContract:
    return SamplingContract(PROFILE.version, 1000, attempt_gap_ms, PROFILE.max_gap_ms, 0.80)


def _card(card: str, seat: str, confidence: float = 0.95) -> dict:
    y = 5 if seat == "N" else 85 if seat == "S" else 45
    x = 85 if seat == "E" else 5 if seat == "W" else 45
    return {"card": card, "confidence": confidence, "box": {"x": x, "y": y, "w": 10, "h": 10}}


def _samples(tmp_path: Path) -> tuple[list[dict], dict[str, dict]]:
    samples = []
    for index, timestamp in enumerate((1000, 4000)):
        path = tmp_path / f"synthetic-frame-{index}.png"
        payload = f"synthetic-pixels-{index}".encode()
        path.write_bytes(payload)
        read = {"status": "READ", "number": 12}
        samples.append({
            "frame_path": path, "frame_sha256": sha256(payload).hexdigest(),
            "source_video_sha256": "a" * 64,
            "episode_id": "synthetic-episode-a", "board_epoch": 1, "board_number": 12,
            "board_number_read": dict(read), "board_number_confirming_read": dict(read),
            "timestamp_ms": timestamp, "observation_index": index,
            "raw_frame_size": {"width": 1920, "height": 1010},
            "reference_profile_id": "synthetic-gambler-stub-v1",
            "data_class": "SYNTHETIC_REGRESSION",
        })
    payloads = {
        samples[0]["frame_path"].name: {"table_region": {"x": 0, "y": 0, "w": 100, "h": 100},
                                        "cards": [_card("AS", "N")]},
        samples[1]["frame_path"].name: {"table_region": {"x": 0, "y": 0, "w": 100, "h": 100},
                                        "cards": [_card("KS", "N"), _card("AH", "S"),
                                                  _card("QD", "CENTER")]},
    }
    return samples, payloads


def _run(samples: list[dict], payloads: dict[str, dict], *, contract: SamplingContract | None = None) -> dict:
    detector = NativeFourSeatCardDetector(lambda path: payloads[path.name], min_card_confidence=0.80)
    return accumulate_detected_frames(samples, detector=detector, contract=contract or _contract())


def _append_third_sample(samples: list[dict], payloads: dict[str, dict], tmp_path: Path,
                         cards: list[dict]) -> None:
    path = tmp_path / "synthetic-frame-2.png"
    pixels = b"synthetic-pixels-2"
    path.write_bytes(pixels)
    third = dict(samples[1], frame_path=path, frame_sha256=sha256(pixels).hexdigest(),
                 timestamp_ms=7000, observation_index=2)
    samples.append(third)
    payloads[path.name] = {"table_region": {"x": 0, "y": 0, "w": 100, "h": 100},
                           "cards": cards}


def test_two_partial_frames_add_distinct_cards_without_52_card_prerequisite(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    result = _run(samples, payloads)
    assert len(result["tracks"]) == 1
    track = result["tracks"][0]
    assert track["status"] == "REVIEW_PARTIAL"
    assert track["frame_sha256s"] == [sample["frame_sha256"] for sample in samples]
    assert track["observed_card_count"] == 3
    assert track["deal"]["hands"]["N"]["cards"] == ["AS", "KS"]
    assert track["deal"]["hands"]["S"]["cards"] == ["AH"]
    assert track["deal"]["hands"]["N"]["unknown_count"] == 11
    assert any(item["reason"] == "AMBIGUOUS_SEAT" for item in track["review_observations"])
    assert track["canonical_promotion_allowed"] is False
    assert result["real_video_accuracy_evaluated"] is False


@pytest.mark.parametrize("change", ["board", "episode", "source", "occurrence", "size", "far_time", "too_close"])
def test_identity_or_sampling_boundary_starts_a_new_partial_track(tmp_path: Path, change: str):
    samples, payloads = _samples(tmp_path)
    if change == "board":
        samples[1]["board_number"] = 13
        samples[1]["board_number_read"]["number"] = 13
        samples[1]["board_number_confirming_read"]["number"] = 13
    elif change == "episode":
        samples[1]["episode_id"] = "synthetic-episode-b"
    elif change == "source":
        samples[1]["source_video_sha256"] = "b" * 64
    elif change == "occurrence":
        samples[1]["board_epoch"] = 2
    elif change == "size":
        samples[1]["raw_frame_size"]["height"] = 1050
    elif change == "far_time":
        samples[1]["timestamp_ms"] = 1000 + PROFILE.max_gap_ms + 1
    elif change == "too_close":
        samples[1]["timestamp_ms"] = 1600
    result = _run(samples, payloads)
    assert len(result["tracks"]) == 2
    assert [track["observed_card_count"] for track in result["tracks"]] == [1, 2]
    assert all(track["status"] == "REVIEW_SINGLE_FRAME" for track in result["tracks"])


def test_cross_frame_card_conflict_is_retained_and_not_guessed(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    payloads[samples[1]["frame_path"].name]["cards"] = [_card("AS", "S"), _card("KS", "N")]
    track = _run(samples, payloads)["tracks"][0]
    assert track["status"] == "REVIEW_CONFLICT"
    assert track["conflicts"][0]["card"] == "AS"
    assert track["conflicts"][0]["seats"] == ["N", "S"]
    assert "AS" not in track["deal"]["hands"]["N"]["cards"]
    assert "AS" not in track["deal"]["hands"]["S"]["cards"]
    assert track["deal"]["hands"]["N"]["cards"] == ["KS"]


def test_third_frame_does_not_resolve_cross_seat_conflict_by_majority(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    payloads[samples[1]["frame_path"].name]["cards"] = [_card("AS", "S")]
    _append_third_sample(samples, payloads, tmp_path, [_card("AS", "N")])
    tracks = _run(samples, payloads)["tracks"]
    assert len(tracks) == 1
    track = tracks[0]
    assert track["status"] == "REVIEW_CONFLICT"
    assert track["observed_card_count"] == 0
    assert track["conflicts"][0]["seats"] == ["N", "S"]
    assert [(item["seat"], item["frame_sha256"])
            for item in track["conflicts"][0]["evidence"]] == [
                ("N", samples[0]["frame_sha256"]), ("N", samples[2]["frame_sha256"]),
                ("S", samples[1]["frame_sha256"])]
    assert all("AS" not in track["deal"]["hands"][seat]["cards"] for seat in ("N", "S"))


def test_repeated_same_seat_card_has_one_slot_with_two_supports(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    payloads[samples[1]["frame_path"].name]["cards"] = [_card("AS", "N")]
    track = _run(samples, payloads)["tracks"][0]
    assert track["status"] == "REVIEW_PARTIAL"
    assert track["observed_card_count"] == 1
    assert track["deal"]["hands"]["N"]["cards"] == ["AS"]
    assert [item["frame_sha256"] for item in track["support"]["AS"]["N"]] == [
        sample["frame_sha256"] for sample in samples]


def test_nonadjacent_replay_of_first_frame_hash_fails_closed(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    _append_third_sample(samples, payloads, tmp_path, [_card("AS", "N")])
    samples[2]["frame_path"].write_bytes(samples[0]["frame_path"].read_bytes())
    samples[2]["frame_sha256"] = samples[0]["frame_sha256"]
    with pytest.raises(PartialEvidenceError, match="FRAME_HASH_REPLAY"):
        _run(samples, payloads)


def test_rejected_observation_cannot_replace_its_frame_hash(tmp_path: Path):
    samples, _ = _samples(tmp_path)
    frame = dict(samples[0], recognizer_version="synthetic-detector-v1", observations=[],
                 rejected_observations=[{"reason": "LOW_CONFIDENCE", "card": "AS",
                                         "frame_sha256": "f" * 64}])
    frame.pop("frame_path")
    with pytest.raises(PartialEvidenceError, match="REJECTED_FRAME_BINDING_MISMATCH"):
        accumulate_partial_frames([frame], contract=_contract())
    frame["rejected_observations"][0]["frame_sha256"] = frame["frame_sha256"]
    review = accumulate_partial_frames([frame], contract=_contract())["tracks"][0]["review_observations"]
    assert review[0]["frame_sha256"] == samples[0]["frame_sha256"]


def test_unknown_board_read_fails_closed(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    samples[1]["board_number_confirming_read"] = {"status": "UNREADABLE", "number": None}
    with pytest.raises(PartialEvidenceError, match="FRAME_IDENTITY_UNPROVEN"):
        _run(samples, payloads)


def test_unknown_episode_fails_closed(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    samples[1]["episode_id"] = None
    with pytest.raises(PartialEvidenceError, match="FRAME_IDENTITY_UNPROVEN"):
        _run(samples, payloads)


def test_low_confidence_card_remains_review_evidence(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    payloads[samples[1]["frame_path"].name]["cards"].append(_card("JC", "E", 0.70))
    track = _run(samples, payloads)["tracks"][0]
    assert "JC" not in track["deal"]["hands"]["E"]["cards"]
    assert any(item.get("card") == "JC" and item["reason"] == "LOW_CONFIDENCE"
               for item in track["review_observations"])


def test_sampling_interval_cannot_exceed_neighbor_profile(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    with pytest.raises(PartialEvidenceError, match="SAMPLING_CANNOT_PRODUCE_NEIGHBORS"):
        _run(samples, payloads, contract=_contract(attempt_gap_ms=15_000))


def test_frame_bytes_must_match_manifest(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    samples[1]["frame_sha256"] = "0" * 64
    with pytest.raises(PartialEvidenceError, match="FRAME_HASH_MISMATCH"):
        _run(samples, payloads)


def test_detector_observes_verified_snapshot_if_source_is_replaced(tmp_path: Path):
    samples, payloads = _samples(tmp_path)
    sample = samples[0]
    original = sample["frame_path"].read_bytes()

    def detector(snapshot: Path):
        assert snapshot != sample["frame_path"]
        sample["frame_path"].write_bytes(b"replacement-after-hash")
        assert snapshot.read_bytes() == original
        return NativeFourSeatCardDetector(lambda path: payloads[path.name])(snapshot)

    track = accumulate_detected_frames([sample], detector=detector,
                                       contract=_contract())["tracks"][0]
    assert track["frame_sha256s"] == [sample["frame_sha256"]]
    assert track["deal"]["hands"]["N"]["cards"] == ["AS"]


def test_detector_cannot_mutate_verified_snapshot_without_rejection(tmp_path: Path):
    samples, payloads = _samples(tmp_path)

    def detector(snapshot: Path):
        result = NativeFourSeatCardDetector(lambda path: payloads[path.name])(snapshot)
        snapshot.write_bytes(b"mutated-during-detection")
        return result

    with pytest.raises(PartialEvidenceError, match="FRAME_SNAPSHOT_CHANGED"):
        accumulate_detected_frames([samples[0]], detector=detector, contract=_contract())
