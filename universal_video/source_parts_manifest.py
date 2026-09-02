"""Strict provenance contract for media parts derived from one source video.

Generic names such as ``AI_PART_000.mp4`` are never evidence of provenance.
Every consumer must validate this manifest before a derived part can support
review, reuse, or evidence promotion.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

SCHEMA = "universal-video-source-parts-v1"
DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_FIELDS = frozenset({"drive_id", "size_bytes", "modified_time", "duration_ms", "sha256"})
PART_FIELDS = frozenset({"drive_id", "part_index", "source_start_ms", "source_end_ms", "size_bytes", "sha256"})
MANIFEST_FIELDS = frozenset({"schema", "source", "parts", "manifest_sha256"})


class SourcePartsManifestError(ValueError):
    """Raised when derived media cannot be bound uniquely to its source."""


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _drive_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not DRIVE_ID_RE.fullmatch(text):
        raise SourcePartsManifestError(f"invalid {field}")
    return text


def _sha256(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not HEX64_RE.fullmatch(text):
        raise SourcePartsManifestError(f"invalid {field}")
    return text


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise SourcePartsManifestError(f"invalid {field}")
    return value


def _timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", text):
        raise SourcePartsManifestError("invalid source.modified_time")
    return text


def validate_source_parts_manifest(value: Any) -> dict[str, Any]:
    """Validate exact fields, source binding, coverage, ordering, and digest."""

    if not isinstance(value, Mapping) or set(value) != MANIFEST_FIELDS:
        raise SourcePartsManifestError("manifest fields must match the bounded contract")
    if value.get("schema") != SCHEMA:
        raise SourcePartsManifestError("unsupported source-parts schema")
    source_raw = value.get("source")
    if not isinstance(source_raw, Mapping) or set(source_raw) != SOURCE_FIELDS:
        raise SourcePartsManifestError("source fields must match the bounded contract")
    source = {
        "drive_id": _drive_id(source_raw.get("drive_id"), "source.drive_id"),
        "size_bytes": _integer(source_raw.get("size_bytes"), "source.size_bytes", minimum=1),
        "modified_time": _timestamp(source_raw.get("modified_time")),
        "duration_ms": _integer(source_raw.get("duration_ms"), "source.duration_ms", minimum=1),
        "sha256": _sha256(source_raw.get("sha256"), "source.sha256"),
    }
    parts_raw = value.get("parts")
    if not isinstance(parts_raw, Sequence) or isinstance(parts_raw, (str, bytes)) or not parts_raw:
        raise SourcePartsManifestError("parts must be a non-empty array")
    parts: list[dict[str, Any]] = []
    drive_ids: set[str] = set()
    cursor = 0
    for expected_index, raw in enumerate(parts_raw):
        if not isinstance(raw, Mapping) or set(raw) != PART_FIELDS:
            raise SourcePartsManifestError("part fields must match the bounded contract")
        part = {
            "drive_id": _drive_id(raw.get("drive_id"), "part.drive_id"),
            "part_index": _integer(raw.get("part_index"), "part.part_index"),
            "source_start_ms": _integer(raw.get("source_start_ms"), "part.source_start_ms"),
            "source_end_ms": _integer(raw.get("source_end_ms"), "part.source_end_ms", minimum=1),
            "size_bytes": _integer(raw.get("size_bytes"), "part.size_bytes", minimum=1),
            "sha256": _sha256(raw.get("sha256"), "part.sha256"),
        }
        if part["part_index"] != expected_index:
            raise SourcePartsManifestError("part indices must be contiguous and ordered")
        if part["drive_id"] in drive_ids:
            raise SourcePartsManifestError("duplicate part Drive id")
        if part["source_start_ms"] != cursor or part["source_end_ms"] <= cursor:
            raise SourcePartsManifestError("part intervals must provide contiguous source coverage")
        drive_ids.add(part["drive_id"])
        cursor = part["source_end_ms"]
        parts.append(part)
    if cursor != source["duration_ms"]:
        raise SourcePartsManifestError("part coverage does not end at source duration")
    normalized = {"schema": SCHEMA, "source": source, "parts": parts}
    expected_digest = hashlib.sha256(_canonical(normalized)).hexdigest()
    if _sha256(value.get("manifest_sha256"), "manifest_sha256") != expected_digest:
        raise SourcePartsManifestError("manifest digest mismatch")
    return {**normalized, "manifest_sha256": expected_digest}


def build_source_parts_manifest(source: Mapping[str, Any], parts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build and self-validate a canonical manifest from measured metadata."""

    unsigned = {"schema": SCHEMA, "source": dict(source), "parts": [dict(part) for part in parts]}
    candidate = {**unsigned, "manifest_sha256": hashlib.sha256(_canonical(unsigned)).hexdigest()}
    return validate_source_parts_manifest(candidate)

