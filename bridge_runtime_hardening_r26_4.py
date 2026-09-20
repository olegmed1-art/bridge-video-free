#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r26.4: temporal card union and strict fourth-hand complement."""
from __future__ import annotations

import os

import bridge_runtime_hardening_r26 as previous
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
from bridge_output_scoped_idempotency import existing_same_revision_done
from bridge_vision import bridgit_primary_video as primary_video
from bridge_vision import bridgit_rank_layout as rank_layout

REVISION = "3.1-free-r26.4"


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

    primary_video.ALLOW_FOURTH_HAND_DERIVATION = True
    rank_layout.TEMPORAL_CARD_UNION_ENABLED = True
    import run_master_3_1_free_semantic_v2 as semantic_v2

    semantic_v2.REVISION = REVISION
    semantic_v2._existing_same_revision_done = lambda token, job_id: existing_same_revision_done(
        semantic_v2, token, job_id, REVISION
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
