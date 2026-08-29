"""End-to-end SHADOW deal-evidence production for Video 3.1 FREE.

This module closes the former gap between extracted video frames and the
source-bound ``bridge-3.1-free-deal-evidence/v1`` contract.  It remains a
universal orchestrator: interface pixels are interpreted only by an immutable,
human-verified profile bundle, and bridge mechanics stay in shared contracts.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bridge_vision.auction_observer import aggregate_auction_observations
from bridge_vision.deal_evidence import (
    RESULT_SCOPE,
    SCHEMA,
    evidence_payload_sha256,
)
from bridge_vision.profiled_challenger import ProfiledCardChallenger
from bridge_vision.template_pixel_producer import (
    PRODUCER_REVISION,
    LoadedTemplatePixelProducer,
)
from bridge_vision.transcript_card_observer import observe_transcript_cards

FRAME_PLAN_SCHEMA = "bridge-video-dense-frame-plan/v1"
PRODUCER_SCHEMA = "bridge-video-deal-evidence-producer/v1"
DEFAULT_MAX_FRAMES = 20_000
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_SEATS = ("N", "E", "S", "W")


class VideoDealProducerError(RuntimeError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dense_frame_timestamps(duration_seconds: float, interval_seconds: float) -> list[float]:
    try:
        duration = float(duration_seconds)
        interval = float(interval_seconds)
    except (TypeError, ValueError) as exc:
        raise VideoDealProducerError("invalid dense frame plan") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise VideoDealProducerError("video duration must be positive")
    if not math.isfinite(interval) or not 1.0 <= interval <= 30.0:
        raise VideoDealProducerError("dense interval outside [1,30]")
    count = int(math.floor(math.nextafter(duration, -math.inf) / interval))
    points = [index * interval for index in range(count + 1)]
    if not points or abs(points[-1] - duration) > 0.0005:
        points.append(duration)
    return [round(value, 3) for value in points]


def extract_dense_frames(
    video: Path,
    output_dir: Path,
    *,
    duration_seconds: float,
    interval_seconds: float,
    source_key: str,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if video.is_symlink() or not video.is_file():
        raise VideoDealProducerError("source video is unavailable")
    points = dense_frame_timestamps(duration_seconds, interval_seconds)
    if not 1 <= len(points) <= int(max_frames):
        raise VideoDealProducerError("dense frame count exceeds its explicit limit")
    if not 1 <= int(max_total_bytes) <= 16 * 1024 * 1024 * 1024:
        raise VideoDealProducerError("dense byte limit is invalid")
    try:
        import cv2
    except ImportError as exc:
        raise VideoDealProducerError("OpenCV frame extractor is unavailable") from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise VideoDealProducerError("source video cannot be decoded")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    safe_last = max(0.0, float(duration_seconds) - (1.0 / fps if fps > 0 else 0.04))
    shots: list[dict[str, Any]] = []
    total = 0
    try:
        for index, logical_time in enumerate(points):
            seek_time = min(logical_time, safe_last)
            capture.set(cv2.CAP_PROP_POS_MSEC, seek_time * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise VideoDealProducerError(f"dense frame extraction failed at index {index}")
            path = (output_dir / f"dense-{index:06d}-{int(round(logical_time * 1000)):010d}.jpg").resolve()
            if not cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95]):
                raise VideoDealProducerError("dense frame encoding failed")
            size = path.stat().st_size
            total += size
            if size <= 0 or total > int(max_total_bytes):
                raise VideoDealProducerError("dense frame byte budget exceeded")
            digest = _sha256(path)
            evidence_id = "dense-" + hashlib.sha256(
                f"{source_key}|{logical_time:.3f}|{digest}".encode("utf-8")
            ).hexdigest()[:32]
            shots.append({
                "evidence_id": evidence_id,
                "time": logical_time,
                "path": str(path),
                "sha256": digest,
                "source": "dense_profiled_video_frame",
            })
    finally:
        capture.release()
    if len(shots) != len(points):
        raise VideoDealProducerError("dense frame timeline is incomplete")
    return shots, {
        "schema": FRAME_PLAN_SCHEMA,
        "interval_seconds": interval_seconds,
        "expected_frame_count": len(points),
        "extracted_frame_count": len(shots),
        "total_bytes": total,
        "first_timestamp": points[0],
        "last_timestamp": points[-1],
        "timeline_complete": True,
    }


def _frame_ref(shot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": str(shot["evidence_id"]),
        "sha256": str(shot["sha256"]),
        "time": float(shot["time"]),
    }


def _identity_key(identity: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes({
        "kind": identity.get("kind"),
        "scope": identity.get("scope"),
        "value": identity.get("value"),
        "anchor_frame_sha256": identity.get("anchor_frame_sha256"),
    })).hexdigest()


def _records(
    shots: Sequence[Mapping[str, Any]],
    loaded: LoadedTemplatePixelProducer,
) -> list[dict[str, Any]]:
    challenger = ProfiledCardChallenger(loaded.profile, loaded.recognizer)
    records: list[dict[str, Any]] = []
    for shot in sorted(shots, key=lambda item: (float(item["time"]), str(item["sha256"]))):
        path = Path(str(shot.get("path") or ""))
        result = challenger(path)
        candidate = result.get("status") == "PASS"
        records.append({
            "time": float(shot["time"]),
            "frame_file": str(path),
            "frame_sha256": str(shot["sha256"]),
            "evidence_id": str(shot["evidence_id"]),
            "status": result.get("status"),
            "candidates": [result] if candidate else [],
            "diagnostics": [] if candidate else [result],
        })
    return records


def _candidate_sources(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for key in ("candidates", "diagnostics"):
        raw = record.get(key) or []
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            result.extend(item for item in raw if isinstance(item, Mapping))
    return result


def _deals_from_records(
    records: Sequence[Mapping[str, Any]],
    shots: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    shots_by_sha: dict[str, Mapping[str, Any]] = {}
    for shot in shots:
        shots_by_sha.setdefault(str(shot["sha256"]), shot)
    groups: dict[str, dict[str, Any]] = {}
    rejected_conflicts = 0
    for record in records:
        for source in _candidate_sources(record):
            evidence = source.get("evidence") or {}
            if not isinstance(evidence, Mapping):
                continue
            identity = evidence.get("deal_identity") or {}
            metadata = evidence.get("board_metadata") or {}
            if not isinstance(identity, Mapping) or not isinstance(metadata, Mapping):
                continue
            if metadata.get("status") != "CONFIRMED" or identity.get("kind") != "EXPLICIT_BOARD":
                continue
            key = _identity_key(identity)
            state = groups.setdefault(key, {
                "identity": dict(identity),
                "board_number": int(metadata["board_number"]),
                "dealer": str(metadata["dealer"]),
                "cards": {},
                "auctions": [],
                "records": 0,
            })
            if (state["board_number"], state["dealer"]) != (int(metadata["board_number"]), str(metadata["dealer"])):
                rejected_conflicts += 1
                state["conflict"] = True
                continue
            state["records"] += 1
            for item in evidence.get("consensus") or []:
                if not isinstance(item, Mapping):
                    continue
                seat = str(item.get("seat") or "").upper()
                card = str(item.get("card") or "").upper()
                if seat not in _SEATS or not card:
                    continue
                previous = state["cards"].get(card)
                candidate = {"seat": seat, **dict(item)}
                if previous is not None and previous["seat"] != seat:
                    state["conflict"] = True; rejected_conflicts += 1; continue
                if previous is None or len(candidate.get("frame_sha256s") or []) > len(previous.get("frame_sha256s") or []):
                    state["cards"][card] = candidate
            auction = evidence.get("auction_observation")
            if isinstance(auction, Mapping):
                state["auctions"].append(dict(auction))

    deals: list[dict[str, Any]] = []
    accepted_cards = complete_auctions = 0
    for key, state in sorted(groups.items(), key=lambda item: (item[1]["board_number"], item[0])):
        if state.get("conflict"):
            continue
        observations: list[dict[str, Any]] = []
        for card, item in sorted(state["cards"].items(), key=lambda pair: (pair[1]["seat"], pair[0])):
            frame_shas = []
            for digest in item.get("frame_sha256s") or []:
                if digest in shots_by_sha and digest not in frame_shas:
                    frame_shas.append(digest)
            if len(frame_shas) < 2:
                continue
            channels = item.get("channels") or []
            if not isinstance(channels, Sequence) or isinstance(channels, (str, bytes)) or not channels:
                continue
            ranks = [entry.get("rank") for entry in channels if isinstance(entry, Mapping) and isinstance(entry.get("rank"), Mapping)]
            suits = [entry.get("suit") for entry in channels if isinstance(entry, Mapping) and isinstance(entry.get("suit"), Mapping)]
            references = [entry.get("reference_match") for entry in channels if isinstance(entry, Mapping) and isinstance(entry.get("reference_match"), Mapping)]
            if not ranks or not suits or not references:
                continue
            rank_base = str(ranks[0].get("channel_id") or "")
            suit_base = str(suits[0].get("channel_id") or "")
            reference_id = str(references[0].get("channel_id") or "")
            channel_ids = {
                "rank": rank_base + ":rank",
                "suit": suit_base + ":suit",
                "reference": reference_id,
            }
            if len(set(channel_ids.values())) != 3:
                continue
            observations.append({
                "seat": item["seat"],
                "card": card,
                "evidence_class": "OBSERVED_MACHINE",
                "confidence": {
                    "rank": min(float(value["confidence"]) for value in ranks),
                    "suit": min(float(value["confidence"]) for value in suits),
                    "reference": min(float(value["confidence"]) for value in references),
                },
                "channels": channel_ids,
                "frames": [_frame_ref(shots_by_sha[digest]) for digest in frame_shas[:8]],
            })
        auction_evidence = None
        aggregate = aggregate_auction_observations(state["auctions"])
        if aggregate.get("status") != "UNAVAILABLE":
            calls = aggregate.get("calls") or []
            supporting = []
            for observation in state["auctions"]:
                digest = str(observation.get("frame_sha256") or "")
                if (
                    observation.get("accepted_as_observation") is True
                    and list(observation.get("calls") or []) == list(calls)
                    and digest in shots_by_sha
                    and digest not in supporting
                ):
                    supporting.append(digest)
            if calls and supporting:
                complete = aggregate.get("accepted_as_standard_pbn") is True and len(supporting) >= 2
                auction_evidence = {
                    "status": "COMPLETE_CONFIRMED" if complete else "PARTIAL_REVIEW",
                    "dealer": state["dealer"],
                    "calls": list(calls),
                    "frames": [_frame_ref(shots_by_sha[digest]) for digest in supporting[:8]],
                }
                complete_auctions += int(complete)
        if not observations and auction_evidence is None:
            continue
        accepted_cards += len(observations)
        deals.append({
            "deal_id": "board-" + str(state["board_number"]) + "-" + key[:16],
            "board_number": state["board_number"],
            "dealer": state["dealer"],
            "card_observations": observations,
            "auction": auction_evidence,
            "verification": None,
        })
    return deals, {
        "confirmed_track_count": len(groups),
        "emitted_deal_count": len(deals),
        "emitted_machine_card_count": accepted_cards,
        "emitted_complete_auction_count": complete_auctions,
        "rejected_conflict_count": rejected_conflicts,
    }


def produce_deal_evidence_bundle(
    *,
    source: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
    loaded: LoadedTemplatePixelProducer,
    transcript_rows: Sequence[Mapping[str, Any]] = (),
    frame_plan: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not shots:
        raise VideoDealProducerError("profiled producer requires evidence frames")
    records = _records(shots, loaded)
    deals, summary = _deals_from_records(records, shots)
    multimodal = observe_transcript_cards(transcript_rows, records) if transcript_rows else {
        "status": "UNAVAILABLE", "accepted_observations": 0, "observations": [],
        "canonical_promotion_allowed": False,
    }
    bundle: dict[str, Any] = {
        "schema": SCHEMA,
        "result_scope": RESULT_SCOPE,
        "canonical_promotion_allowed": False,
        "production_activation_allowed": False,
        "source": {
            "driveId": str(source.get("driveId") or ""),
            "sha256": str(source.get("sha256") or "").lower(),
            "sizeBytes": int(source.get("sizeBytes") or 0),
        },
        "producer": {
            "kind": "PROFILED_PIXEL_BACKEND",
            "revision": PRODUCER_REVISION,
            "backend_sha256": loaded.backend_sha256,
            "profile_sha256": loaded.profile_sha256,
            "config_sha256": loaded.config_sha256,
        },
        "deals": deals,
        "diagnostics": {
            "schema": PRODUCER_SCHEMA,
            "frame_plan": dict(frame_plan or {}),
            "records_processed": len(records),
            "frame_status_counts": {
                status: sum(record.get("status") == status for record in records)
                for status in ("PASS", "PENDING_TEMPORAL_CONSENSUS", "REVIEW", "CONFLICT")
            },
            "deal_summary": summary,
            "transcript_card_observer": multimodal,
            "canonical_promotion_allowed": False,
            "production_activation_allowed": False,
        },
    }
    bundle["payload_sha256"] = evidence_payload_sha256(bundle)
    return bundle, {**summary, "records_processed": len(records), "multimodal": multimodal}


__all__ = [
    "DEFAULT_MAX_FRAMES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "FRAME_PLAN_SCHEMA",
    "PRODUCER_SCHEMA",
    "VideoDealProducerError",
    "dense_frame_timestamps",
    "extract_dense_frames",
    "produce_deal_evidence_bundle",
]
