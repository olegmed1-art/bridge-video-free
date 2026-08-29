#!/usr/bin/env python3
"""Validate and stage WORLD-META-001 exports without creating canon knowledge.

Input is a JSON object with sources/authors/bridgeclub_audit/material_queue arrays
exported from the named Drive tabs.  The script deliberately refuses aggregate-only
snapshots and never inserts knowledge_version, bidding.rule or any activation row.
"""
import argparse, hashlib, json
from pathlib import Path

EXPECTED = {"sources": 245, "authors": 42, "bridgeclub_audit": 95, "material_queue": 20}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--emit-manifest", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    payload = json.loads(raw)
    if set(payload) != set(EXPECTED):
        raise SystemExit("WORLD-META-001 requires exactly sources, authors, bridgeclub_audit, material_queue")
    counts = {key: len(payload[key]) for key in EXPECTED}
    if counts != EXPECTED:
        raise SystemExit(f"WORLD-META-001 count mismatch: expected {EXPECTED}, got {counts}")
    for key, rows in payload.items():
        if not all(isinstance(row, dict) for row in rows):
            raise SystemExit(f"WORLD-META-001 {key} contains a non-object row")
    manifest = {"batch_key":"WORLD-META-001", "authority_class":"external", "activation_allowed":False,
                "counts":counts, "input_sha256":hashlib.sha256(raw).hexdigest(),
                "guarantees":["metadata_evidence_only","no_canon_activation","no_bidding_rule_insert"]}
    args.emit_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
