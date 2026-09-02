from __future__ import annotations

import copy
from pathlib import Path

import pytest

from universal_video.source_parts_recovery import (
    ATTESTATION,
    SCHEMA,
    SourcePartsRecoveryError,
    recover_source_parts,
)


def _request():
    return {
        "schema": SCHEMA,
        "operator_attestation": ATTESTATION,
        "source": {
            "drive_id": "sourceVideo000001",
            "path": "/media/source.mp4",
            "size_bytes": 300,
            "modified_time": "2021-03-04T06:46:38.000Z",
            "duration_ms": 30_000,
            "sha256": "a" * 64,
        },
        "parts": [
            {"drive_id": "derivedPart000001", "path": "/media/part0.mp4", "part_index": 0,
             "source_start_ms": 0, "source_end_ms": 20_000, "size_bytes": 100, "sha256": "b" * 64},
            {"drive_id": "derivedPart000002", "path": "/media/part1.mp4", "part_index": 1,
             "source_start_ms": 20_000, "source_end_ms": 30_000, "size_bytes": 80, "sha256": "c" * 64},
        ],
    }


def _probe(path: Path):
    return {
        "/media/source.mp4": {"duration_ms": 30_000, "size_bytes": 300, "sha256": "a" * 64},
        "/media/part0.mp4": {"duration_ms": 20_001, "size_bytes": 100, "sha256": "b" * 64},
        "/media/part1.mp4": {"duration_ms": 9_999, "size_bytes": 80, "sha256": "c" * 64},
    }[str(path)]


def test_metadata_only_recovery_builds_strict_manifest():
    result = recover_source_parts(_request(), probe=_probe)
    assert result["status"] == "RECOVERED"
    assert result["asr_started"] is False
    assert result["media_transcoded"] is False
    assert result["published"] is False
    assert result["source_parts_manifest"]["parts"][1]["source_end_ms"] == 30_000


def test_recovery_rejects_unbounded_duration_tolerance():
    with pytest.raises(SourcePartsRecoveryError, match="bounded range"):
        recover_source_parts(_request(), probe=_probe, duration_tolerance_ms=10_000)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda value: value.update(operator_attestation=""), "attestation"),
        (lambda value: value["source"].update(sha256="d" * 64), "source sha256 mismatch"),
        (lambda value: value["parts"][1].update(source_start_ms=19_000), "contiguous"),
        (lambda value: value["parts"][0].update(source_end_ms=18_000), "duration mismatch"),
    ],
)
def test_recovery_fails_closed_on_missing_or_conflicting_evidence(mutate, message):
    request = copy.deepcopy(_request())
    mutate(request)
    with pytest.raises((SourcePartsRecoveryError, ValueError), match=message):
        recover_source_parts(request, probe=_probe)
