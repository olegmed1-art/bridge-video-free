#!/usr/bin/env python3
"""Runtime hardening for Bridge Video 3.1 FREE internal revision r5.

Keeps the user-facing algorithm name unchanged while adding two fail-safe guards:
1. reject pathological repeated non-speech ASR markers such as [Аплодисменты];
2. refresh the Drive user OAuth token at late publication/permission boundaries so
   long CPU ASR runs do not fail with an expired one-hour access token.
"""
from __future__ import annotations

from collections import Counter
import re

import bridge_worker_3_1_free as core
import run_drive_3_1_free as io
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-master-analysis-r5"
_BRACKET_MARKER_RE = re.compile(r"\[\s*([^\]\n]{2,48})\s*\]", re.IGNORECASE)


def asr_hallucination_risk(text: str) -> bool:
    """Conservative detector for repeated bracketed non-speech hallucinations.

    Real transcripts may legitimately contain an occasional [music]/[applause] marker.
    We only block when one short bracket marker is repeated at least six times and
    dominates the bracket markers, which catches the known Diana 9 failure without
    penalising ordinary bridge speech.
    """
    markers = [re.sub(r"\s+", " ", x.strip().lower()) for x in _BRACKET_MARKER_RE.findall(text or "")]
    if len(markers) < 6:
        return False
    top = Counter(markers).most_common(1)[0][1]
    return top >= 6 and top / len(markers) >= 0.60


def _fresh_call(fn, token_func, *args, **kwargs):
    """Call a Drive function with a newly refreshed OAuth token."""
    return fn(token_func(), *args, **kwargs)


def install(token_func):
    """Install r5 guards into the already imported 3.1 FREE runtime."""
    # Internal revision only; user-visible version stays exactly "3.1 FREE".
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION

    original_qc_match = base._qc_match

    def hardened_qc_match(primary, check):
        if asr_hallucination_risk(primary) or asr_hallucination_risk(check):
            return False, 0.0
        return original_qc_match(primary, check)

    base._qc_match = hardened_qc_match

    # Refresh only at late mutable Drive operations. Reads during the long analysis
    # keep using the initial token, while publication and permission propagation get
    # a fresh access token and therefore cannot expire merely because ASR took >1 h.
    original_upload_file = io.upload_file
    original_perms = io.perms
    original_add_perm = io.add_perm
    original_download = io.download

    def upload_file(_token, parent, path, mime):
        return _fresh_call(original_upload_file, token_func, parent, path, mime)

    def perms(_token, fid):
        return _fresh_call(original_perms, token_func, fid)

    def add_perm(_token, fid, permission):
        return _fresh_call(original_add_perm, token_func, fid, permission)

    def download(_token, fid, output):
        return _fresh_call(original_download, token_func, fid, output)

    io.upload_file = upload_file
    io.perms = perms
    io.add_perm = add_perm
    io.download = download


def run(token_func):
    install(token_func)
    # Start with a fresh token. Late publication refreshes again through patched I/O.
    return semantic.process_job(token_func())
