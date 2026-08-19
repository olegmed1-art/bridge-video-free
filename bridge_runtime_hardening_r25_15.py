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
from bridge_output_scoped_idempotency import existing_same_revision_done

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
    # A completed result in another output folder is evidence, not permission to
    # suppress an explicitly requested fresh verification generation.
    semantic_v2._existing_same_revision_done = (
        lambda token, job_id: existing_same_revision_done(
            semantic_v2, token, job_id, REVISION
        )
    )
    # The inherited r25 semantic adapter has its own older global job+revision
    # ALREADY_DONE check.  Once r25.15's generation-scoped check has decided the
    # requested output generation is fresh, continue through the already-patched
    # base heavy processor directly; otherwise the legacy guard can still reuse a
    # completed candidate generation from another Drive folder.
    semantic_v2.previous.process_job = base.process_job
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic_v2 as semantic_v2
    return semantic_v2.process_job(token_func())


__all__ = ["REVISION", "install", "run"]
