#!/usr/bin/env python3
"""Bridge Video 3.1 FREE internal candidate r25.3.

r25.3 preserves the r25.2 ASR and semantic-confidence gates and fixes a
production failure found on the known-good Sunday lesson: after a long CPU ASR
run the initial Google Drive OAuth access token could expire before the final
Neon persistence pass.  The PDF and completion markers were already written,
then persistence failed while searching Drive with the stale token.

The public product name remains exactly ``3.1 FREE``.  Only the internal
``algorithmRevision`` changes.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_2 as r25_2
import bridge_worker_3_1_free as core
import run_drive_3_1_free as io
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-r25.3"


def fresh_persistence_call(persist_fn, token_func, _stale_token):
    """Run final Drive->Neon persistence with a newly refreshed Drive token.

    The argument supplied by ``semantic.process_job`` may be more than an hour old.
    Never reuse it at this late boundary.  A missing freshly acquired token is a
    hard failure because the production database contract is fail-closed.
    """
    fresh_token = token_func()
    if not fresh_token:
        raise RuntimeError("BLOCKED_ACCESS: late Drive OAuth refresh unavailable")
    io.safe(stage="LATE_DRIVE_OAUTH_REFRESH_R25_3", exit_code=0)
    return persist_fn(fresh_token)


def install(token_func):
    """Install r25.2 protections, then the r25.3 late-auth boundary."""
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    # The inherited installer validates its own internal revision.  Present r25.2
    # only while installing the already-tested hooks, then restore the caller's
    # r25.3 request and revision identity.
    had_requested = "BRIDGE_REQUESTED_ALGORITHM_REVISION" in os.environ
    saved_requested = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = r25_2.REVISION
    try:
        r25_2.install(token_func)
    finally:
        if had_requested:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = saved_requested or ""
        else:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION

    original_persist = semantic.persist_completed_drive_job

    def persist_with_fresh_drive_token(stale_token):
        return fresh_persistence_call(original_persist, token_func, stale_token)

    semantic.persist_completed_drive_job = persist_with_fresh_drive_token


def run(token_func):
    install(token_func)
    # Use a fresh token at the beginning too; the final persistence boundary will
    # refresh independently after long ASR/visual/PDF work.
    return semantic.process_job(token_func())
