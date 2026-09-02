#!/usr/bin/env python3
"""STOP consumer contract for Oracle idle guard.

This module intentionally contains no OCI implementation and is not wired to
production.  It proves the only permitted transition: a STOP callable may be
invoked iff the tri-state classifier returns IDLE.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from oracle_idle_guard import Verdict, classify


class StopBlocked(RuntimeError):
    pass


def evaluate_stop(snapshot: Mapping[str, Any], *, now: float | None = None) -> Verdict:
    return classify(snapshot, now=now)


def maybe_stop(
    snapshot: Mapping[str, Any],
    stop_action: Callable[[], Any],
    *,
    now: float | None = None,
) -> Verdict:
    verdict = evaluate_stop(snapshot, now=now)
    if verdict.state != "IDLE" or not verdict.stop_allowed:
        raise StopBlocked(f"STOP_BLOCKED:{verdict.state}:{verdict.reason}")
    stop_action()
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Oracle STOP decision")
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("snapshot is not an object")
        verdict = evaluate_stop(payload)
    except Exception:
        verdict = Verdict("UNKNOWN", "snapshot_read_failed")
    print(json.dumps(verdict.as_dict(), sort_keys=True, separators=(",", ":")))
    print("ORACLE_STOP_DECISION=ALLOW" if verdict.stop_allowed else "ORACLE_STOP_DECISION=BLOCK")
    # No STOP action is implemented by this CLI. It is a decision boundary only.
    return 0 if verdict.stop_allowed else 3


if __name__ == "__main__":
    raise SystemExit(main())
