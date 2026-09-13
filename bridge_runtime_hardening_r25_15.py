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
    import transcript_stage_checkpoint_v1
    from bridge_speaker_diarization_v3 import diarize_transcript

    semantic_v2.diarize_transcript = diarize_transcript
    semantic_v2.REVISION = REVISION

    # There are two historical no-op gates in the inherited semantic stack.
    # Both must be scoped to this output generation, otherwise the inner r25
    # adapter can still accept an AI_DONE stored elsewhere on Drive.
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

    # Wrap the fully-installed transcript+speaker path, not the older r25.6 ASR
    # function. Reuse is allowed only after exact source/revision/SHA validation.
    transcript_stage_checkpoint_v1.install(base, REVISION)

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic_v2 as semantic_v2
    return semantic_v2.process_job(token_func())


__all__ = ["REVISION", "install", "run"]
