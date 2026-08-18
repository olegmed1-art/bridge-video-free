#!/usr/bin/env python3
"""Minimal execution monitor for Bridge Video jobs.

Writes tiny idempotent status receipts to the configured Drive work folder.
This is deliberately simple: it records observable start/final states and the
GitHub run identity so external monitoring can inspect a real worker run.
"""
from __future__ import annotations

import os
import time

import run_drive_3_1_free as io
from run_drive_3_1_free_oidc import user_oauth_token


def main() -> None:
    job_id = os.environ.get("BRIDGE_JOB_ID", "").strip()
    work_folder_id = os.environ.get("BRIDGE_WORK_FOLDER_ID", "").strip()
    status = os.environ.get("MONITOR_V1_STATUS", "WORKER_STARTED").strip() or "WORKER_STARTED"
    job_status = os.environ.get("MONITOR_V1_JOB_STATUS", "").strip() or None
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip() or None
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip() or None
    workflow = os.environ.get("GITHUB_WORKFLOW", "").strip() or None
    sha = os.environ.get("GITHUB_SHA", "").strip() or None

    if not job_id:
        raise SystemExit("MONITOR_V1_MISSING_JOB_ID")
    if not work_folder_id:
        print("MONITOR_V1_SKIP_NO_WORK_FOLDER")
        return

    token = user_oauth_token()
    if not token:
        raise SystemExit("MONITOR_V1_DRIVE_OAUTH_UNAVAILABLE")

    safe_run = run_id or "unknown"
    name = f"MONITOR_V1_{status}_{job_id}_run_{safe_run}.json"
    existing = io.search(
        token,
        f"'{work_folder_id}' in parents and trashed=false and name='{name}'",
    )
    if existing:
        print(f"MONITOR_V1_ALREADY_EXISTS {name}")
        return

    payload = {
        "schema": "bridge-video-monitor-v1",
        "job_id": job_id,
        "status": status,
        "job_status": job_status,
        "github_run_id": run_id,
        "github_run_attempt": run_attempt,
        "github_workflow": workflow,
        "github_sha": sha,
        "algorithm_revision": os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION"),
        "lesson_number": os.environ.get("BRIDGE_LESSON_NUMBER") or None,
        "master_read_only": True,
        "paid_cloud": os.environ.get("BRIDGE_PAID_CLOUD", "false"),
        "billing_fallback": os.environ.get("BRIDGE_BILLING_FALLBACK", "false"),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    item = io.upload_json(token, work_folder_id, name, payload)
    print(f"MONITOR_V1_RECEIPT {status} {item.get('id')} run={safe_run}")


if __name__ == "__main__":
    main()
