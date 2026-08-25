#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from bridge_school_api.tournament_structural_validation_v3 import validate_tournament_structure


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate tournament structural facts without inference")
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    raw = args.facts.read_bytes()
    source = json.loads(raw.decode("utf-8"))
    report = validate_tournament_structure(source)
    report["source"] = {
        "path": str(args.facts),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "provider_native_key": source.get("tournament", {}).get("provider_native_key"),
        "policy_mode": source.get("policy", {}).get("mode"),
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"boards={report['board_count']} "
        f"leads={report['opening_leads_legal']}/{report['opening_leads_checked']} "
        f"pass={report['all_structural_checks_pass']}"
    )
    return 0 if report["all_structural_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
