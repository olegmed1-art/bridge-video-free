#!/usr/bin/env python3
"""Run lifecycle cleanup against the same canonical production Neon endpoint as preflight."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.runtime_worker_preflight import normalize_dsn  # noqa: E402


def main() -> None:
    raw = os.environ.get("BRIDGE_WORKER_DATABASE_URL", "")
    canonical = normalize_dsn(raw)
    if not canonical:
        raise SystemExit("CANONICAL_CLEANUP_DSN: FAIL: BRIDGE_WORKER_DATABASE_URL is not configured")
    os.environ["BRIDGE_WORKER_DATABASE_URL"] = canonical
    cleanup = ROOT / "tools" / "github_actions_artifact_cleanup.py"
    os.execv(sys.executable, [sys.executable, str(cleanup), *sys.argv[1:]])


if __name__ == "__main__":
    main()
