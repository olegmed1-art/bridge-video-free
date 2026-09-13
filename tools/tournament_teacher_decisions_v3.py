#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.tournament_teacher_decisions_v3 import (
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    ledger = build_pending_teacher_decision_ledger(queue)
    payload = serialize_teacher_decision_ledger(ledger)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": payload["schema"],
        "queue_sha256": payload["queue_sha256"],
        "decisions": len(payload["decisions"]),
        "pending": sum(x["status"] == "PENDING" for x in payload["decisions"]),
        "automatic_decisions_allowed": payload["automatic_decisions_allowed"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
