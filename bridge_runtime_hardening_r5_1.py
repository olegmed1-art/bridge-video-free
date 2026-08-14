#!/usr/bin/env python3
"""Bridge Video 3.1 FREE internal hardening revision r5.1.

Builds on r5 and makes ASR QC fail closed: after retries, every checked block
must pass before semantic analysis or PDF publication may continue.
"""
from __future__ import annotations

import bridge_runtime_hardening_r5 as r5
import bridge_worker_3_1_free as core
import run_drive_3_1_free as io
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-master-analysis-r5.1"


def strict_qc_pass(qc, base_passed: bool) -> bool:
    """Return True only when the base QC passed and every checked block passed."""
    return bool(base_passed) and bool(qc) and all(bool(item.get("ok")) for item in qc)


def install(token_func):
    """Install r5 protections, then tighten the final ASR QC acceptance rule."""
    r5.install(token_func)
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION

    original_qc_transcript = base.qc_transcript

    def qc_transcript_zero_fail(video, work, dur, segs):
        qc, base_passed = original_qc_transcript(video, work, dur, segs)
        passed = strict_qc_pass(qc, base_passed)
        failed = sum(not bool(item.get("ok")) for item in qc)
        io.safe(
            stage="ASR_QC_STRICT",
            qc_failed=failed,
            qc_total=len(qc),
            algorithmRevision=REVISION,
            exit_code=0 if passed else 1,
        )
        return qc, passed

    base.qc_transcript = qc_transcript_zero_fail


def run(token_func):
    install(token_func)
    return semantic.process_job(token_func())
