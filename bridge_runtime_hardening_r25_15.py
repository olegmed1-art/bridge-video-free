#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r25.15: collapse-aware local speaker diarization.

This candidate inherits the proven r25.14 -> r25.7 -> r25.6 media/ASR/evidence
route and changes only the speaker-separation layer. The v3 diarizer detects
near-empty two-speaker clusters, tries an independent public embedding model,
and may perform an in-memory acoustic re-cluster repair. Named identity is
never inferred here; r29 identity evidence remains a separate gate.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_14 as previous
import bridge_worker_3_1_free as core
import run_master_3_1_free as base

REVISION = "3.1-free-r25.15"


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
    from bridge_speaker_diarization_v3 import diarize_transcript

    semantic_v2.diarize_transcript = diarize_transcript
    semantic_v2.REVISION = REVISION
    # r25's legacy semantic adapter owns a global job+revision idempotency guard.
    # r25.15 performs its own output-generation-scoped terminal check first, so
    # continuing through the inherited path must call the base heavy processor
    # directly or a prior candidate generation can suppress a fresh production
    # generation of the same source/revision.
    semantic_v2.previous.process_job = base.process_job
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic_v2 as semantic_v2
    return semantic_v2.process_job(token_func())


__all__ = ["REVISION", "install", "run"]
