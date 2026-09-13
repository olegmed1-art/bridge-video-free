#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from bridge_school_api.tournament_input_manifest_v3 import build_input_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a reproducible Tournament Analyzer v3 input manifest")
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--received-at", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--algorithm-revision", required=True)
    args = parser.parse_args()

    raw = args.facts.read_bytes()
    source = json.loads(raw.decode("utf-8"))
    manifest = build_input_manifest(
        source,
        normalized_facts_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_facts_size_bytes=len(raw),
        normalized_facts_received_at=args.received_at,
        normalized_facts_commit=args.commit,
        algorithm_revision_id=args.algorithm_revision,
    )
    args.out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": manifest["schema"],
        "run_id": manifest["run_id"],
        "boards": manifest["tournament"]["board_count"],
        "source_conflict_gate_pass": manifest["source_conflict_gate_pass"],
        "provenance_limitations": manifest["provenance_limitations"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["source_conflict_gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
