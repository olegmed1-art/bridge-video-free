from __future__ import annotations

import copy

import pytest

from universal_video.source_parts_manifest import (
    SourcePartsManifestError,
    build_source_parts_manifest,
    validate_source_parts_manifest,
)


def _manifest():
    return build_source_parts_manifest(
        {
            "drive_id": "sourceVideo000001",
            "size_bytes": 623_948_644,
            "modified_time": "2021-03-04T06:46:38.000Z",
            "duration_ms": 652_620,
            "sha256": "a" * 64,
        },
        [
            {
                "drive_id": "derivedPart000001",
                "part_index": 0,
                "source_start_ms": 0,
                "source_end_ms": 600_000,
                "size_bytes": 90_000_000,
                "sha256": "b" * 64,
            },
            {
                "drive_id": "derivedPart000002",
                "part_index": 1,
                "source_start_ms": 600_000,
                "source_end_ms": 652_620,
                "size_bytes": 8_000_000,
                "sha256": "c" * 64,
            },
        ],
    )


def test_canonical_manifest_binds_source_parts_offsets_and_hashes():
    manifest = _manifest()
    assert validate_source_parts_manifest(manifest) == manifest
    assert manifest["parts"][-1]["source_end_ms"] == manifest["source"]["duration_ms"]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value["parts"][1].update(part_index=7), "indices"),
        (lambda value: value["parts"][1].update(source_start_ms=599_000), "contiguous"),
        (lambda value: value["parts"][1].update(source_end_ms=652_000), "source duration"),
        (lambda value: value["parts"][1].update(drive_id=value["parts"][0]["drive_id"]), "duplicate"),
        (lambda value: value["parts"][0].update(sha256="unknown"), "sha256"),
    ],
)
def test_manifest_fails_closed_on_ambiguous_or_incomplete_provenance(mutate, message):
    manifest = copy.deepcopy(_manifest())
    mutate(manifest)
    with pytest.raises(SourcePartsManifestError, match=message):
        validate_source_parts_manifest(manifest)


def test_generic_part_names_are_not_a_provenance_substitute():
    manifest = copy.deepcopy(_manifest())
    manifest["parts"][0]["name"] = "AI_PART_000.mp4"
    with pytest.raises(SourcePartsManifestError, match="part fields"):
        validate_source_parts_manifest(manifest)


def test_manifest_digest_detects_tampering_after_creation():
    manifest = copy.deepcopy(_manifest())
    manifest["parts"][0]["size_bytes"] += 1
    with pytest.raises(SourcePartsManifestError, match="digest mismatch"):
        validate_source_parts_manifest(manifest)

