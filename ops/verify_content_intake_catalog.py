"""Verify retained catalog text bytes; this never authorizes canon promotion."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from pathlib import Path


def verify_catalog_snapshot(data: dict, export: bytes) -> dict[str, int]:
    identity = data["catalog_snapshot_identity"]
    digest = identity.get("immutable_export_sha256")
    revision = identity.get("immutable_export_revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError("CATALOG_REVISION_REQUIRED")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("CATALOG_DIGEST_REQUIRED")
    if hashlib.sha256(export).hexdigest() != digest:
        raise ValueError("CATALOG_BYTES_MISMATCH")
    if identity.get("export_format") != "connector-csv-sections-utf8-v1":
        raise ValueError("CATALOG_EXPORT_FORMAT_UNSUPPORTED")
    expected_binding = {
        "drive_spreadsheet_id": identity["drive_spreadsheet_id"],
        "revision": revision,
        "sha256": digest,
    }
    dependent = {"LEGACY_L1_DRIVE", "LEARNING_CONTENT", "WORLD_EXTERNAL"}
    if set(identity["dependent_lanes"]) != dependent:
        raise ValueError("CATALOG_LANE_SET_MISMATCH")
    lanes = {lane["lane_id"]: lane for lane in data["lanes"]}
    for name in dependent:
        if lanes[name].get("catalog_snapshot_binding") != expected_binding:
            raise ValueError("CATALOG_LANE_BINDING_MISMATCH")
    parts = export.decode("utf-8").split("\f")
    sections = identity["sections"]
    if len(parts) != len(sections):
        raise ValueError("CATALOG_SECTION_COUNT_MISMATCH")
    counts = {}
    for part, section in zip(parts, sections):
        rows = list(csv.reader(io.StringIO(part)))
        if not rows or not rows[0] or rows[0][0] != "":
            raise ValueError("CATALOG_HEADER_INVALID")
        header_hash = hashlib.sha256(json.dumps(rows[0], ensure_ascii=False,
            separators=(",", ":")).encode()).hexdigest()
        if header_hash != section["header_sha256"]:
            raise ValueError("CATALOG_SECTION_ORDER_MISMATCH")
        # The first CSV column is the connector's row index, not source data.
        counts[section["sheet"]] = sum(any(cell.strip() for cell in row[1:]) for row in rows[1:])
    if counts != data["drive_catalog_sheet_rows"]:
        raise ValueError("CATALOG_ROW_COUNTS_MISMATCH")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("export", type=Path)
    args = parser.parse_args()
    counts = verify_catalog_snapshot(json.loads(args.manifest.read_text()), args.export.read_bytes())
    print(json.dumps({"status": "PASS", "sheets": len(counts),
                      "staging_or_activation_authorized": False}))


if __name__ == "__main__":
    main()
