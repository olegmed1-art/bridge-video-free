#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r25.18: autonomous profiled deal extraction.

This revision inherits r25.17 and activates no unverified recognizer.  When the
source folder contains exactly one hash-valid ``BRIDGE_VISION_PROFILE.zip``,
the runtime extracts a complete dense timeline, runs the human-verified
profile, creates source-bound SHADOW deal evidence, and publishes the existing
PBN/PDF surfaces.  Without that bundle, r25.17 behaviour is unchanged.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_17 as previous
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
from bridge_output_scoped_idempotency import existing_same_revision_done

REVISION = "3.1-free-r25.18"


def install(token_func):
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    had_requested = "BRIDGE_REQUESTED_ALGORITHM_REVISION" in os.environ
    saved = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = previous.REVISION
    try:
        previous.install(token_func)
    finally:
        if had_requested:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = saved or ""
        else:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)

    import run_master_3_1_free_semantic_v2 as semantic_v2

    semantic_v2.REVISION = REVISION
    semantic_v2._existing_same_revision_done = (
        lambda token, job_id: existing_same_revision_done(
            semantic_v2, token, job_id, REVISION
        )
    )
    semantic_v2.previous._existing_same_revision_done = (
        lambda token, job_id: existing_same_revision_done(
            semantic_v2.previous, token, job_id, REVISION
        )
    )
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic_v2 as semantic_v2

    return semantic_v2.process_job(token_func())


__all__ = ["REVISION", "install", "run"]
