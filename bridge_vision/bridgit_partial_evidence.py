"""Offline, shadow-only accumulation of partial visual card observations.

The caller must supply independently decoded, source-bound frame observations.
This module does not classify pixels, infer missing cards, or promote facts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Mapping, Sequence

from bridge_contracts.video_deal import FULL_DECK, SEATS, canonicalize_video_deal
from bridge_vision.bridgit_adjacent_candidate_profile import PROFILE

PARTIAL_EVIDENCE_VERSION = "bridgit-partial-evidence-v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")


class PartialEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class SamplingContract:
    profile_version: str
    scan_ms: int
    attempt_gap_ms: int
    max_neighbor_gap_ms: int
    minimum_card_confidence: float

    def validate(self) -> None:
        if self.profile_version != PROFILE.version or self.max_neighbor_gap_ms != PROFILE.max_gap_ms:
            raise PartialEvidenceError("ADJACENCY_PROFILE_MISMATCH")
        if (type(self.scan_ms) is not int or type(self.attempt_gap_ms) is not int
                or not 500 <= self.scan_ms <= 10_000
                or not 1000 <= self.attempt_gap_ms <= self.max_neighbor_gap_ms):
            raise PartialEvidenceError("SAMPLING_CANNOT_PRODUCE_NEIGHBORS")
        score = self.minimum_card_confidence
        if type(score) not in (int, float) or not 0.0 <= score <= 1.0 or not math.isfinite(score):
            raise PartialEvidenceError("CARD_CONFIDENCE_GATE_INVALID")


def _valid_read(read: Any, number: int) -> bool:
    return (isinstance(read, Mapping) and read.get("status") == "READ"
            and type(read.get("number")) is int and read["number"] == number)


def _frame_identity(frame: Mapping[str, Any]) -> tuple[Any, ...]:
    source = frame.get("source_video_sha256")
    episode = frame.get("episode_id")
    epoch = frame.get("board_epoch")
    number = frame.get("board_number")
    size = frame.get("raw_frame_size")
    frame_sha = frame.get("frame_sha256")
    timestamp = frame.get("timestamp_ms")
    index = frame.get("observation_index")
    recognizer = frame.get("recognizer_version")
    reference = frame.get("reference_profile_id")
    data_class = frame.get("data_class")
    if (not isinstance(source, str) or _SHA.fullmatch(source) is None
            or not isinstance(frame_sha, str) or _SHA.fullmatch(frame_sha) is None
            or not isinstance(episode, str) or not episode.strip()
            or type(epoch) is not int or epoch < 1
            or type(number) is not int or number < 1
            or not _valid_read(frame.get("board_number_read"), number)
            or not _valid_read(frame.get("board_number_confirming_read"), number)
            or type(timestamp) is not int or timestamp < 0
            or type(index) is not int or index < 0
            or not isinstance(size, Mapping)
            or any(type(size.get(axis)) is not int or size[axis] < 1 for axis in ("width", "height"))
            or not isinstance(recognizer, str) or not recognizer.strip()
            or not isinstance(reference, str) or not reference.strip()
            or data_class not in ("SYNTHETIC_REGRESSION", "REAL_SHADOW")):
        raise PartialEvidenceError("FRAME_IDENTITY_UNPROVEN")
    return (source, episode, epoch, number, size["width"], size["height"],
            recognizer, reference, data_class)


def _visual_observations(frame: Mapping[str, Any], minimum: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw = frame.get("observations")
    if not isinstance(raw, list) or len(raw) > 52:
        raise PartialEvidenceError("OBSERVATIONS_INVALID")
    accepted: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise PartialEvidenceError("OBSERVATION_INVALID")
        seat, card, score = item.get("seat"), item.get("card"), item.get("confidence")
        reason = None
        if item.get("status") != "ACCEPTED_VISUAL" or item.get("source") != "VISUAL":
            reason = "NOT_ACCEPTED_VISUAL"
        elif seat not in SEATS or not isinstance(card, str) or card not in FULL_DECK:
            reason = "CARD_OR_SEAT_UNKNOWN"
        elif type(score) not in (int, float) or not 0.0 <= score <= 1.0 or not math.isfinite(score):
            reason = "CONFIDENCE_INVALID"
        elif score < minimum:
            reason = "LOW_CONFIDENCE"
        if reason is not None:
            review.append({"observation_index": index, "reason": reason,
                           "frame_sha256": frame["frame_sha256"], "card": card, "seat": seat})
            continue
        accepted.append({"seat": seat, "card": card, "confidence": score,
                         "frame_sha256": frame["frame_sha256"], "timestamp_ms": frame["timestamp_ms"]})
    return accepted, review


def _track_output(frames: list[Mapping[str, Any]], contract: SamplingContract) -> dict[str, Any]:
    support: dict[str, dict[str, list[dict[str, Any]]]] = {}
    review: list[dict[str, Any]] = []
    for frame in frames:
        accepted, rejected = _visual_observations(frame, contract.minimum_card_confidence)
        review.extend(rejected)
        extra_rejected = frame.get("rejected_observations") or []
        if (not isinstance(extra_rejected, list) or len(extra_rejected) > 52
                or any(not isinstance(item, Mapping) for item in extra_rejected)):
            raise PartialEvidenceError("REJECTED_OBSERVATIONS_INVALID")
        for item in extra_rejected:
            if "frame_sha256" in item and item["frame_sha256"] != frame["frame_sha256"]:
                raise PartialEvidenceError("REJECTED_FRAME_BINDING_MISMATCH")
            review.append({**item, "frame_sha256": frame["frame_sha256"]})
        for item in accepted:
            support.setdefault(item["card"], {}).setdefault(item["seat"], []).append(item)
    conflicts = [
        {"card": card, "seats": sorted(by_seat),
         "evidence": [item for seat in sorted(by_seat) for item in by_seat[seat]]}
        for card, by_seat in sorted(support.items()) if len(by_seat) > 1
    ]
    hands = {seat: [] for seat in SEATS}
    for card, by_seat in support.items():
        if len(by_seat) == 1:
            seat = next(iter(by_seat))
            hands[seat].append(card)
    overfull = any(len(cards) > 13 for cards in hands.values())
    deal = None if overfull else canonicalize_video_deal({"hands": hands}, derive_fourth_hand=False).to_dict()
    status = ("REVIEW_CONFLICT" if conflicts else "REVIEW_OVERFULL" if overfull
              else "REVIEW_PARTIAL" if len(frames) > 1 else "REVIEW_SINGLE_FRAME")
    return {
        "version": PARTIAL_EVIDENCE_VERSION, "status": status,
        "identity": {"source_video_sha256": frames[0]["source_video_sha256"],
                     "episode_id": frames[0]["episode_id"], "board_epoch": frames[0]["board_epoch"],
                     "board_number": frames[0]["board_number"]},
        "frame_sha256s": [frame["frame_sha256"] for frame in frames],
        "timestamps_ms": [frame["timestamp_ms"] for frame in frames],
        "sampling_profile_version": contract.profile_version,
        "observed_card_count": sum(map(len, hands.values())),
        "deal": deal, "support": support, "conflicts": conflicts,
        "review_observations": review,
        "canonical_promotion_allowed": False, "real_video_accuracy_evaluated": False,
    }


def accumulate_partial_frames(
    frames: Sequence[Mapping[str, Any]], *, contract: SamplingContract,
) -> dict[str, Any]:
    """Group adjacent scoped observations; retain uncertainty without guessing."""
    contract.validate()
    tracks: list[list[Mapping[str, Any]]] = []
    identities: list[tuple[Any, ...]] = []
    seen_hashes: set[str] = set()
    for frame in frames:
        if not isinstance(frame, Mapping):
            raise PartialEvidenceError("FRAME_INVALID")
        identity = _frame_identity(frame)
        if frame["frame_sha256"] in seen_hashes:
            raise PartialEvidenceError("FRAME_HASH_REPLAY")
        seen_hashes.add(frame["frame_sha256"])
        if tracks:
            previous = tracks[-1][-1]
            gap = frame["timestamp_ms"] - previous["timestamp_ms"]
            adjacent = (identity == identities[-1]
                        and frame["observation_index"] == previous["observation_index"] + 1
                        and frame["frame_sha256"] != previous["frame_sha256"]
                        and contract.attempt_gap_ms <= gap <= contract.max_neighbor_gap_ms)
        else:
            adjacent = False
        if adjacent:
            tracks[-1].append(frame)
        else:
            tracks.append([frame])
            identities.append(identity)
    outputs = [_track_output(track, contract) for track in tracks]
    return {
        "version": PARTIAL_EVIDENCE_VERSION,
        "sampling_profile_version": contract.profile_version,
        "tracks": outputs,
        "canonical_promotion_allowed": False,
        "real_video_accuracy_evaluated": False,
    }


def accumulate_detected_frames(
    samples: Sequence[Mapping[str, Any]], *,
    detector: Callable[[Path], Mapping[str, Any]],
    contract: SamplingContract,
) -> dict[str, Any]:
    """Bind local frame bytes to a card detector, then accumulate shadow evidence.

    The detector receives a private byte-for-byte snapshot with the original
    basename. Source-path replacement cannot change the pixels it observes.
    The detector must be independently authorized for real pixels; this entry
    point performs no activation or reference verification on its behalf.
    """
    contract.validate()
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="bridgit-partial-snapshot-") as directory:
        for index, sample in enumerate(samples):
            if not isinstance(sample, Mapping):
                raise PartialEvidenceError("FRAME_INVALID")
            path = sample.get("frame_path")
            if not isinstance(path, Path) or not path.is_file():
                raise PartialEvidenceError("FRAME_FILE_UNAVAILABLE")
            raw = path.read_bytes()
            raw_hash = hashlib.sha256(raw).hexdigest()
            if raw_hash != sample.get("frame_sha256"):
                raise PartialEvidenceError("FRAME_HASH_MISMATCH")
            snapshot_dir = Path(directory) / f"{index:08d}"
            snapshot_dir.mkdir()
            snapshot = snapshot_dir / path.name
            snapshot.write_bytes(raw)
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != raw_hash:
                raise PartialEvidenceError("FRAME_SNAPSHOT_MISMATCH")
            detected = detector(snapshot)
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != raw_hash:
                raise PartialEvidenceError("FRAME_SNAPSHOT_CHANGED")
            evidence = detected.get("evidence") if isinstance(detected, Mapping) else None
            if not isinstance(evidence, Mapping) or not isinstance(evidence.get("accepted"), list):
                raise PartialEvidenceError("DETECTOR_EVIDENCE_INVALID")
            observations = [
                {"status": "ACCEPTED_VISUAL", "source": "VISUAL", "seat": item.get("seat"),
                 "card": item.get("card"), "confidence": item.get("confidence")}
                for item in evidence["accepted"] if isinstance(item, Mapping)
            ]
            record = dict(sample)
            record.pop("frame_path")
            record["recognizer_version"] = evidence.get("detector_version")
            record["observations"] = observations
            record["rejected_observations"] = evidence.get("rejected") or []
            records.append(record)
    return accumulate_partial_frames(records, contract=contract)


__all__ = ["PARTIAL_EVIDENCE_VERSION", "PartialEvidenceError", "SamplingContract",
           "accumulate_partial_frames", "accumulate_detected_frames"]
