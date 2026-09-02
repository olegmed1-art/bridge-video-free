#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from bridge_school_api.tournament_preanalysis_gate_v3 import build_preanalysis_gate


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Tournament Analyzer v3 pre-analysis gate")
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--received-at", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--algorithm-revision", required=True)
    args = parser.parse_args()

    raw = args.facts.read_bytes()
    source = json.loads(raw.decode("utf-8"))
    gate = build_preanalysis_gate(
        source,
        normalized_facts_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_facts_size_bytes=len(raw),
        normalized_facts_received_at=args.received_at,
        normalized_facts_commit=args.commit,
        algorithm_revision_id=args.algorithm_revision,
    )
    args.out.write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": gate["schema"],
        "run_id": gate["run_id"],
        "facts_only_ready": gate["facts_only_analysis_ready"],
        "full_causal_replay_ready": gate["full_causal_replay_ready"],
        "hard_stops": gate["hard_stop_conditions"],
    }, sort_keys=True))
    return 0 if gate["facts_only_analysis_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
