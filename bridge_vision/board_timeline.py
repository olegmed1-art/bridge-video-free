"""Contiguous board-instance confirmation without cross-deal frame mixing."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from bridge_vision.bridgit_compass import ROTATIONS, SEATS, VULNERABILITY_CYCLE

BOARD_TIMELINE_SCHEMA = "bridge-board-timeline-v1"
MIN_BOARD_SUPPORT = 2
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BoardTimelineError(ValueError):
    pass


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def confirm_board_timeline(
    observations: Sequence[Mapping[str, Any]], *, min_support: int = MIN_BOARD_SUPPORT,
) -> dict[str, Any]:
    if min_support < MIN_BOARD_SUPPORT:
        raise BoardTimelineError("board support cannot be lowered below two frames")
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        raise BoardTimelineError("board observations must be an array")
    if not observations:
        return {
            "schema": BOARD_TIMELINE_SCHEMA, "status": "INCONCLUSIVE", "reason": "NO_BOARD_OBSERVATIONS",
            "segments": [], "production_activation_allowed": False,
        }
    segments: list[dict[str, Any]] = []
    closed_instances: set[tuple[str, str, int, str]] = set()
    seen_frames: set[str] = set()
    source_id: str | None = None
    prior_timestamp = -1
    active: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal active
        if active is None:
            return
        identity = active["identity"]
        anchor_present = identity["anchor_frame_sha256"] in active["frame_sha256s"]
        reasons = list(active["review_reasons"])
        if len(active["frame_sha256s"]) < min_support:
            reasons.append("INSUFFICIENT_TEMPORAL_SUPPORT")
        if not anchor_present:
            reasons.append("ANCHOR_FRAME_MISSING")
        status = "CONFIRMED" if not reasons else "REVIEW"
        segments.append({
            "segment_id": "board-segment-" + _fingerprint(identity)[:16],
            "status": status, "review_reasons": sorted(set(reasons)),
            "source_id": active["source_id"], "deal_identity": identity,
            "board_metadata": {
                "status": "CONFIRMED_TEMPORAL" if status == "CONFIRMED" else "REVIEW",
                **active["board_values"],
                "independent_frames": len(active["frame_sha256s"]),
                "frame_evidence": list(active["metadata_evidence"]),
            },
            "seat_positions": active["seat_positions"],
            "rotation_degrees_clockwise": active["rotation_degrees_clockwise"],
            "start_timestamp_ms": active["timestamps"][0], "end_timestamp_ms": active["timestamps"][-1],
            "frame_sha256s": list(active["frame_sha256s"]),
            "production_activation_allowed": False,
        })
        closed_instances.add((identity["scope"], identity["instance_id"], identity["board_number"], identity["anchor_frame_sha256"]))
        active = None

    for raw in observations:
        if not isinstance(raw, Mapping) or raw.get("schema") != "bridge-source-bound-board-context-v1":
            raise BoardTimelineError("unexpected board observation schema")
        timestamp = raw.get("timestamp_ms")
        if not isinstance(timestamp, int) or timestamp <= prior_timestamp:
            raise BoardTimelineError("board observations must be strictly chronological")
        prior_timestamp = timestamp
        frame_sha = str(raw.get("frame_sha256") or "")
        if not _SHA256.fullmatch(frame_sha):
            raise BoardTimelineError("invalid frame hash")
        if frame_sha in seen_frames:
            raise BoardTimelineError("duplicate frame evidence")
        seen_frames.add(frame_sha)
        current_source = str(raw.get("source_id") or "")
        if not current_source:
            raise BoardTimelineError("source identity is required")
        if source_id is None:
            source_id = current_source
        elif current_source != source_id:
            raise BoardTimelineError("one timeline cannot mix video sources")
        identity_raw = raw.get("deal_identity")
        metadata = raw.get("board_metadata")
        positions = raw.get("seat_positions")
        if not isinstance(identity_raw, Mapping) or not isinstance(metadata, Mapping) or not isinstance(positions, Mapping):
            raise BoardTimelineError("board identity, metadata, and seats are required")
        if identity_raw.get("kind") != "SOURCE_BOUND_BOARD_INSTANCE":
            raise BoardTimelineError("board instance identity is not source-bound")
        try:
            board_number = int(identity_raw.get("board_number"))
        except (TypeError, ValueError) as exc:
            raise BoardTimelineError("invalid board number") from exc
        if isinstance(identity_raw.get("board_number"), bool) or board_number < 1:
            raise BoardTimelineError("invalid board number")
        identity = {
            "scope": str(identity_raw.get("scope") or ""),
            "instance_id": str(identity_raw.get("instance_id") or ""),
            "board_number": board_number,
            "anchor_frame_sha256": str(identity_raw.get("anchor_frame_sha256") or ""),
        }
        if not identity["scope"] or not identity["instance_id"] or not _SHA256.fullmatch(identity["anchor_frame_sha256"]):
            raise BoardTimelineError("incomplete board instance identity")
        expected_dealer = SEATS[(board_number - 1) % 4]
        expected_vulnerability = VULNERABILITY_CYCLE[(board_number - 1) % 16]
        if (
            metadata.get("status") != "OBSERVED_SINGLE_FRAME"
            or metadata.get("board_number") != board_number
            or metadata.get("dealer") != expected_dealer
            or metadata.get("vulnerability") != expected_vulnerability
        ):
            raise BoardTimelineError("board metadata conflicts with duplicate-board mechanics")
        provenance = metadata.get("provenance")
        if not isinstance(provenance, Mapping) or set(provenance) != {"board_number", "dealer", "vulnerability"}:
            raise BoardTimelineError("complete board metadata provenance is required")
        if any(not isinstance(item, Mapping) or item.get("frame_sha256") != frame_sha for item in provenance.values()):
            raise BoardTimelineError("board metadata provenance is not frame-bound")
        position_cycle = tuple(str(positions.get(position) or "") for position in ("top", "right", "bottom", "left"))
        if position_cycle not in ROTATIONS or raw.get("rotation_degrees_clockwise") != 90 * ROTATIONS.index(position_cycle):
            raise BoardTimelineError("seat orientation is invalid or inconsistent")
        identity_key = (identity["scope"], identity["instance_id"], identity["board_number"], identity["anchor_frame_sha256"])
        if active is None or active["identity_key"] != identity_key:
            finish()
            if identity_key in closed_instances:
                active = {
                    "identity_key": identity_key, "identity": identity, "source_id": current_source,
                    "board_values": {key: metadata.get(key) for key in ("board_number", "dealer", "vulnerability")},
                    "metadata_evidence": [], "seat_positions": dict(positions),
                    "rotation_degrees_clockwise": raw.get("rotation_degrees_clockwise"),
                    "timestamps": [], "frame_sha256s": [],
                    "review_reasons": ["NON_CONTIGUOUS_INSTANCE_REAPPEARANCE"],
                }
            else:
                active = {
                    "identity_key": identity_key, "identity": identity, "source_id": current_source,
                    "board_values": {key: metadata.get(key) for key in ("board_number", "dealer", "vulnerability")},
                    "metadata_evidence": [], "seat_positions": dict(positions),
                    "rotation_degrees_clockwise": raw.get("rotation_degrees_clockwise"),
                    "timestamps": [], "frame_sha256s": [], "review_reasons": [],
                }
        context = {
            "board_values": {key: metadata.get(key) for key in ("board_number", "dealer", "vulnerability")},
            "seat_positions": dict(positions),
            "rotation_degrees_clockwise": raw.get("rotation_degrees_clockwise"),
        }
        baseline = {
            "board_values": active["board_values"], "seat_positions": active["seat_positions"],
            "rotation_degrees_clockwise": active["rotation_degrees_clockwise"],
        }
        if context != baseline:
            active["review_reasons"].append("BOARD_CONTEXT_DISAGREEMENT")
        active["timestamps"].append(timestamp)
        active["frame_sha256s"].append(frame_sha)
        active["metadata_evidence"].append({
            "frame_sha256": frame_sha,
            "provenance": dict(metadata.get("provenance") or {}),
        })
    finish()
    confirmed = sum(segment["status"] == "CONFIRMED" for segment in segments)
    return {
        "schema": BOARD_TIMELINE_SCHEMA,
        "status": "PASS" if segments and confirmed == len(segments) else "REVIEW",
        "source_id": source_id, "segment_count": len(segments), "confirmed_segment_count": confirmed,
        "segments": segments, "production_activation_allowed": False,
    }


__all__ = ["BOARD_TIMELINE_SCHEMA", "BoardTimelineError", "MIN_BOARD_SUPPORT", "confirm_board_timeline"]
