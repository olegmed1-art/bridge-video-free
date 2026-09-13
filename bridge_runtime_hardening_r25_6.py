#!/usr/bin/env python3
"""Bridge Video 3.1 FREE internal candidate r25.6.

r25.6 preserves the confirmed r25.5 ASR policy: a non-hallucinatory isolated
control-ASR disagreement may be quarantined when base coverage passes, while
repeated non-speech hallucinations remain a hard stop. Unreliable transcript
segments remain excluded from every derived conclusion.

This revision changes PDF presentation only: repeated empty actor cycles are
collapsed when speaker labels are unavailable, canon links are deduplicated for
print, and technical QC is rendered as readable diagnostics. The complete
master_analysis.json remains embedded unchanged. The public product name remains
exactly 3.1 FREE.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_1 as asr
import bridge_runtime_hardening_r25_4 as stable
import bridge_worker_3_1_free as core
import run_master_3_1_free as base

REVISION = "3.1-free-r25.6"


def isolated_qc_pass(qc, base_passed: bool, hallucination_blocks: int = 0) -> bool:
    """Allow isolated control disagreement, but never detected hallucination."""
    return (
        bool(base_passed)
        and bool(qc)
        and int(hallucination_blocks) == 0
    )


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

    # Make the confirmed job-86 policy explicit in r25.6 itself. This is
    # intentionally applied after the inherited runtime is installed so merging
    # the candidate onto a main branch that still contains the r25.4 hard-zero
    # gate cannot silently restore the obsolete whole-job stop.
    asr.strict_qc_pass = isolated_qc_pass

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic as semantic
    return semantic.process_job(token_func())
