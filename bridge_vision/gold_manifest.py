"""Immutable human-labelled frame/crop manifest for Bridge Vision."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

GOLD_MANIFEST_VERSION = "bridge-vision-human-gold-v2"
_KINDS = {"rank", "suit", "card"}
_PARTITIONS = {"TEMPLATE", "TRAIN", "HOLDOUT"}
_VISIBILITY = {"VISIBLE", "PARTIAL", "HIDDEN", "UNKNOWN"}
_SEATS = {"N", "E", "S", "W"}
_RANKS = set("AKQJT98765432")
_SUITS = {"S", "H", "D", "C"}


def canonical_gold_manifest(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)) or not entries:
        raise ValueError("gold entries must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    seen_crops: set[tuple[Any, ...]] = set()
    frame_identity: dict[str, tuple[str, int, str]] = {}
    visible_cards: dict[tuple[str, str], str] = {}
    for raw in entries:
        if not isinstance(raw, Mapping) or raw.get("human_verified") is not True:
            raise ValueError("gold entry must be an explicitly human-verified object")
        frame_sha = str(raw.get("frame_sha256") or "").lower()
        if len(frame_sha) != 64 or any(c not in "0123456789abcdef" for c in frame_sha):
            raise ValueError("frame_sha256 must be a lowercase sha256 digest")
        source_id = str(raw.get("source_id") or "").strip()
        reviewer = str(raw.get("reviewer") or "").strip()
        annotator = str(raw.get("annotator") or "").strip()
        if not source_id or not annotator or not reviewer or reviewer == annotator:
            raise ValueError("source, annotator, and an independent reviewer are required")
        timestamp_ms = raw.get("timestamp_ms")
        if not isinstance(timestamp_ms, int) or timestamp_ms < 0:
            raise ValueError("timestamp_ms must be a non-negative integer")
        partition = str(raw.get("partition") or "").strip().upper()
        if partition not in _PARTITIONS:
            raise ValueError("partition must be TEMPLATE, TRAIN, or HOLDOUT")
        identity = (source_id, timestamp_ms, partition)
        previous_identity = frame_identity.setdefault(frame_sha, identity)
        if previous_identity != identity:
            raise ValueError("one frame must have one source, timestamp, and partition")
        kind = str(raw.get("kind") or "").strip().lower()
        visibility = str(raw.get("visibility") or "").strip().upper()
        seat = str(raw.get("seat") or "").strip().upper()
        if kind not in _KINDS or visibility not in _VISIBILITY or seat not in _SEATS:
            raise ValueError("kind, visibility, or seat is invalid")
        try:
            x, y, w, h = (int(raw[key]) for key in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("gold crop geometry is invalid") from exc
        if min(x, y) < 0 or min(w, h) <= 0:
            raise ValueError("gold crop geometry is invalid")
        label = str(raw.get("label") or "").strip().upper() or None
        if visibility == "VISIBLE" and label is None:
            raise ValueError("visible gold label must be explicit")
        if visibility in {"HIDDEN", "UNKNOWN"} and label is not None:
            raise ValueError("hidden or unknown evidence cannot carry an inferred label")
        if label is not None:
            valid = (
                kind == "rank" and label in _RANKS
                or kind == "suit" and label in _SUITS
                or kind == "card" and len(label) == 2 and label[0] in _RANKS and label[1] in _SUITS
            )
            if not valid:
                raise ValueError("gold label is invalid for its independent channel")
        if kind == "card" and visibility == "VISIBLE" and label is not None:
            previous_seat = visible_cards.setdefault((frame_sha, label), seat)
            if previous_seat != seat:
                raise ValueError("one visible card cannot belong to two seats in one frame")
        crop_key = (frame_sha, kind, x, y, w, h)
        if crop_key in seen_crops:
            raise ValueError("duplicate gold crop")
        seen_crops.add(crop_key)
        normalized.append({
            "frame_sha256": frame_sha, "timestamp_ms": timestamp_ms, "source_id": source_id,
            "partition": partition, "kind": kind, "label": label, "seat": seat,
            "visibility": visibility, "x": x, "y": y, "w": w, "h": h,
            "human_verified": True, "annotator": annotator, "reviewer": reviewer,
        })
    normalized.sort(key=lambda row: (
        row["frame_sha256"], row["timestamp_ms"], row["kind"], row["seat"], row["y"], row["x"]
    ))
    payload = {"schema": GOLD_MANIFEST_VERSION, "entries": normalized}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


__all__ = ["GOLD_MANIFEST_VERSION", "canonical_gold_manifest"]
