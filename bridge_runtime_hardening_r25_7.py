#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r25.7 — final zero-cost idempotency guard.

Preserves r25.6 analysis/QC/PDF behavior and adds a Drive-wide terminal-receipt
check before any large video download or ASR work. If CLEANUP_ACK_<job>.json
already exists, the run is a safe no-op. This prevents duplicate PDFs after a
repeated dispatch, stale queue marker, workflow retry, or recovery run.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_6 as stable
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
import run_drive_3_1_free as io

REVISION = "3.1-free-r25.7"


def install(token_func):
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    had_requested = "BRIDGE_REQUESTED_ALGORITHM_REVISION" in os.environ
    saved_requested = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = stable.REVISION
    try:
        stable.install(token_func)
    finally:
        if had_requested:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = saved_requested or ""
        else:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION


def _already_completed(token: str, job_id: str) -> bool:
    name = f"CLEANUP_ACK_{job_id}.json"
    escaped = name.replace("'", "\\'")
    found = io.search(token, f"trashed=false and name='{escaped}'")
    return bool(found)


def run(token_func):
    install(token_func)
    token = token_func()
    job = os.environ["BRIDGE_JOB_ID"]

    if _already_completed(token, job):
        io.safe(
            job_id=job,
            stage="ALREADY_COMPLETED",
            exit_code=0,
            terminal_receipt="CLEANUP_ACK",
        )
        return 0

    import run_master_3_1_free_semantic as semantic
    return semantic.process_job(token)
