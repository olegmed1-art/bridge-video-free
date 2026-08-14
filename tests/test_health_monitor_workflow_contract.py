#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(".github/workflows/database-health-monitor.yml")


def main() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = [
        "cron: '17 * * * *'",
        "python tests/test_health_dsn_contract.py",
        "python tests/test_app_http_health_contract.py",
        "python tests/test_health_monitor_workflow_contract.py",
        "python database/runtime_app_http_preflight.py",
        "https://bridge-video-free.vercel.app/healthz",
        "secrets.BRIDGE_WORKER_DATABASE_URL",
        "python database/runtime_worker_preflight.py",
        "secrets.BRIDGE_HEALTH_DATABASE_URL",
        "python database/runtime_health_preflight.py",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise AssertionError(f"health monitor is missing required runtime checks: {missing}")

    print("HEALTH_MONITOR_WORKFLOW_CONTRACT: PASS")


if __name__ == "__main__":
    main()
