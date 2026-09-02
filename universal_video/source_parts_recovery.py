"""Metadata-only recovery of a source-parts manifest.

The recovery request is an operator-supplied binding receipt.  This module
only reads local files, runs ``ffprobe``, and computes SHA-256 digests.  It
never invokes ffmpeg, ASR, Drive publication, or lifecycle APIs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from .source_parts_manifest import build_source_parts_manifest

SCHEMA = "universal-video-source-parts-recovery-v1"
ATTESTATION = "I attest that these ordered files were derived from this exact source."


class SourcePartsRecoveryError(ValueError):
    """Raised when metadata cannot establish the declared bounded binding."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_media(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise SourcePartsRecoveryError("media path must be a regular non-symlink file")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode:
        raise SourcePartsRecoveryError("ffprobe failed")
    try:
        duration_ms = round(float(json.loads(proc.stdout)["format"]["duration"]) * 1000)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SourcePartsRecoveryError("ffprobe duration is unavailable") from exc
    if duration_ms <= 0:
        raise SourcePartsRecoveryError("ffprobe duration is unavailable")
    return {"duration_ms": duration_ms, "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def recover_source_parts(
    request: Mapping[str, Any],
    *,
    probe: Callable[[Path], Mapping[str, Any]] = probe_media,
    duration_tolerance_ms: int = 1000,
) -> dict[str, Any]:
    """Verify an explicit binding receipt and return a canonical manifest."""

    if type(duration_tolerance_ms) is not int or not 0 <= duration_tolerance_ms <= 5000:
        raise SourcePartsRecoveryError("duration tolerance is outside the bounded range")
    if set(request) != {"schema", "operator_attestation", "source", "parts"} or request.get("schema") != SCHEMA:
        raise SourcePartsRecoveryError("request fields or schema are invalid")
    if request.get("operator_attestation") != ATTESTATION:
        raise SourcePartsRecoveryError("explicit operator attestation is required")
    source = request.get("source")
    parts = request.get("parts")
    source_fields = {"drive_id", "path", "size_bytes", "modified_time", "duration_ms", "sha256"}
    part_fields = {"drive_id", "path", "part_index", "source_start_ms", "source_end_ms", "size_bytes", "sha256"}
    if not isinstance(source, Mapping) or set(source) != source_fields:
        raise SourcePartsRecoveryError("source fields are invalid")
    if not isinstance(parts, list) or not parts:
        raise SourcePartsRecoveryError("parts must be a non-empty array")

    source_observed = dict(probe(Path(str(source["path"]))))
    for field in ("size_bytes", "duration_ms", "sha256"):
        if source_observed.get(field) != source.get(field):
            raise SourcePartsRecoveryError(f"source {field} mismatch")

    manifest_parts: list[dict[str, Any]] = []
    cursor = 0
    for expected_index, part in enumerate(parts):
        if not isinstance(part, Mapping) or set(part) != part_fields:
            raise SourcePartsRecoveryError("part fields are invalid")
        if part.get("part_index") != expected_index:
            raise SourcePartsRecoveryError("part order is invalid")
        start = part.get("source_start_ms")
        end = part.get("source_end_ms")
        if type(start) is not int or type(end) is not int or start != cursor or end <= start:
            raise SourcePartsRecoveryError("part intervals must be contiguous and ordered")
        observed = dict(probe(Path(str(part["path"]))))
        for field in ("size_bytes", "sha256"):
            if observed.get(field) != part.get(field):
                raise SourcePartsRecoveryError(f"part {expected_index} {field} mismatch")
        declared_duration = end - start
        if abs(observed.get("duration_ms", -duration_tolerance_ms - 1) - declared_duration) > duration_tolerance_ms:
            raise SourcePartsRecoveryError(f"part {expected_index} duration mismatch")
        manifest_parts.append({key: part[key] for key in part_fields - {"path"}})
        cursor = end

    manifest_source = {key: source[key] for key in source_fields - {"path"}}
    manifest = build_source_parts_manifest(manifest_source, manifest_parts)
    return {
        "schema": SCHEMA,
        "status": "RECOVERED",
        "method": "metadata-only-ffprobe-sha256-with-operator-attestation",
        "asr_started": False,
        "media_transcoded": False,
        "published": False,
        "source_parts_manifest": manifest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result = recover_source_parts(request)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
