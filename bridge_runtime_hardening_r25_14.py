#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r25.14: neural local speaker diarization.

This revision deliberately inherits the proven r25.7 media/ASR/evidence path
rather than the later experimental ASR candidates. It changes only the speaker
separation layer: sherpa-onnx offline diarization is primary and r25.7's
conservative acoustic heuristic remains a fail-soft fallback.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_7 as previous
import bridge_worker_3_1_free as core
import run_master_3_1_free as base

REVISION = "3.1-free-r25.14"


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
    from bridge_speaker_diarization_v2_observable import diarize_transcript

    semantic_v2.diarize_transcript = diarize_transcript
    semantic_v2.REVISION = REVISION
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic_v2 as semantic_v2
    return semantic_v2.process_job(token_func())


__all__ = ["REVISION", "install", "run"]
