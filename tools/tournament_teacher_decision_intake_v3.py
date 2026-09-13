#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.tournament_teacher_decision_intake_v3 import (
    apply_teacher_decision_intake,
    build_teacher_decision_template,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build/apply explicit Tournament Analyzer teacher-decision intake")
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template")
    template.add_argument("--bundle", type=Path, required=True)
    template.add_argument("--out", type=Path, required=True)

    apply = sub.add_parser("apply")
    apply.add_argument("--bundle", type=Path, required=True)
    apply.add_argument("--intake", type=Path, required=True)
    apply.add_argument("--out", type=Path, required=True)

    args = parser.parse_args()
    bundle = _load(args.bundle)
    if args.command == "template":
        payload = build_teacher_decision_template(bundle)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "schema": payload["schema"],
            "rows": len(payload["decisions"]),
            "preselected": sum(row["status"] is not None for row in payload["decisions"]),
            "bundle_id": payload["bundle_id"],
        }, sort_keys=True))
        return 0

    payload = apply_teacher_decision_intake(bundle, _load(args.intake))
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": payload["schema"],
        "decided": payload["decided_count"],
        "pending": payload["pending_count"],
        "bundle_id": payload["bundle_id"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
