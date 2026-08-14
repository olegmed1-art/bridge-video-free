#!/usr/bin/env python3
"""Runtime hardening for Bridge Video 3.1 FREE internal revision r6.

Keeps the user-facing algorithm name unchanged while adding fail-safe guards:
1. reject pathological repeated non-speech ASR markers such as [Аплодисменты];
2. reject the transcript if any autonomously checked ASR window remains failed after retry;
3. refresh Drive user OAuth tokens at late publication/permission boundaries.
"""
from __future__ import annotations

from collections import Counter
import re

import bridge_worker_3_1_free as core
import run_drive_3_1_free as io
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-master-analysis-r6"
_BRACKET_MARKER_RE = re.compile(r"\[\s*([^\]\n]{2,48})\s*\]", re.IGNORECASE)


def asr_hallucination_risk(text: str) -> bool:
    markers = [re.sub(r"\s+", " ", x.strip().lower()) for x in _BRACKET_MARKER_RE.findall(text or "")]
    if len(markers) < 6:
        return False
    top = Counter(markers).most_common(1)[0][1]
    return top >= 6 and top / len(markers) >= 0.60


def _fresh_call(fn, token_func, *args, **kwargs):
    return fn(token_func(), *args, **kwargs)


def install(token_func):
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION

    original_qc_match = base._qc_match
    original_qc_transcript = base.qc_transcript

    def hardened_qc_match(primary, check):
        if asr_hallucination_risk(primary) or asr_hallucination_risk(check):
            return False, 0.0
        return original_qc_match(primary, check)

    def fail_closed_qc_transcript(video, work, dur, segs):
        qc, passed = original_qc_transcript(video, work, dur, segs)
        # A checked window that still fails after retry means the transcript cannot be
        # trusted for semantic analysis. Mark the whole QC as failed instead of
        # allowing a percentage of failed windows through.
        if any(not item.get('ok', False) for item in qc):
            passed = False
            io.safe(stage='ASR_QC_FAIL_CLOSED', qc_failed=sum(not item.get('ok', False) for item in qc), qc_total=len(qc), exit_code=1)
        return qc, passed

    base._qc_match = hardened_qc_match
    base.qc_transcript = fail_closed_qc_transcript

    original_upload_file = io.upload_file
    original_perms = io.perms
    original_add_perm = io.add_perm

    def upload_file(_token, parent, path, mime):
        return _fresh_call(original_upload_file, token_func, parent, path, mime)

    def perms(_token, fid):
        return _fresh_call(original_perms, token_func, fid)

    def add_perm(_token, fid, permission):
        return _fresh_call(original_add_perm, token_func, fid, permission)

    io.upload_file = upload_file
    io.perms = perms
    io.add_perm = add_perm


def run(token_func):
    install(token_func)
    return semantic.process_job(token_func())
