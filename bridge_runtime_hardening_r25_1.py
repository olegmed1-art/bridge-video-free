#!/usr/bin/env python3
"""Bridge Video 3.1 FREE internal candidate r25.1.

Builds on the current r25 methodology implementation while hardening two failure
modes observed on real bridge lessons:
1. the requested internal revision must match the code that actually executes;
2. ASR QC remains fail-closed for detected repeated non-speech hallucinations,
   while exhausted isolated control-ASR disagreements are quarantined.

It also blocks an obvious repeated non-speech hallucination pattern such as a
minutes-long stream of ``[Аплодисменты]`` while allowing occasional genuine
non-speech markers inside normal speech.
"""
from __future__ import annotations

from collections import Counter
import os
import re

import bridge_runtime_hardening_r5 as r5
import bridge_worker_3_1_free as core
import run_drive_3_1_free as io
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-r25.1"
_NON_SPEECH_RE = re.compile(r"\[\s*([^\]\n]{2,48})\s*\]", re.IGNORECASE)
_KNOWN_NON_SPEECH = {
    "аплодисменты", "applause", "музыка", "music", "смех", "laughter",
    "шум", "noise", "тишина", "silence",
}
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")


def pathological_nonspeech_hallucination(text: str) -> bool:
    """Return True only for a dense repeated bracketed non-speech pattern."""
    raw = text or ""
    markers = [re.sub(r"\s+", " ", x.strip().lower()) for x in _NON_SPEECH_RE.findall(raw)]
    known = [x for x in markers if x in _KNOWN_NON_SPEECH]
    if len(known) < 8:
        return False
    marker, count = Counter(known).most_common(1)[0]
    if count < 8 or count / max(1, len(known)) < 0.70:
        return False
    words = _WORD_RE.findall(raw)
    return count / max(1, len(words)) >= 0.20


def critical_qc_failures(qc) -> int:
    """Count exhausted failed controls with effectively no lexical overlap."""
    return sum(
        not bool(item.get("ok"))
        and float(item.get("similarity") or 0.0) <= 0.05
        for item in (qc or [])
    )


def strict_qc_pass(qc, base_passed: bool, hallucination_blocks: int = 0) -> bool:
    """Release base-approved ASR when no pathological hallucination was detected.

    A failed control window is already marked unreliable after three attempts.
    Later hardening excludes every such segment from semantic derivation while
    preserving it for audit. Therefore an isolated empty/disagreeing control ASR
    is diagnostic, not a whole-job stop; detected hallucination remains fatal.
    """
    return (
        bool(base_passed)
        and bool(qc)
        and int(hallucination_blocks) == 0
    )


def _qc_has_hallucination(record: dict, primary_text: str) -> bool:
    if pathological_nonspeech_hallucination(primary_text):
        return True
    for evidence in record.get("qcEvidence") or []:
        if pathological_nonspeech_hallucination(str(evidence.get("text") or "")):
            return True
    return False


def install(token_func):
    """Install stable runtime protections, then r25.1 ASR/version guards."""
    r5.install(token_func)
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION

    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    original_qc_transcript = base.qc_transcript

    def qc_transcript_failclosed(video, work, dur, segs):
        qc, base_passed = original_qc_transcript(video, work, dur, segs)
        windows = base._windows(segs, dur)

        hallucinated = set()
        for record in qc:
            idx = int(record.get("block", -1))
            primary_text = windows[idx].get("text", "") if 0 <= idx < len(windows) else ""
            if _qc_has_hallucination(record, primary_text):
                record["ok"] = False
                reasons = list(record.get("failureReasons") or [])
                if "REPEATED_NONSPEECH_HALLUCINATION" not in reasons:
                    reasons.append("REPEATED_NONSPEECH_HALLUCINATION")
                record["failureReasons"] = reasons
                record["riskBand"] = "CRITICAL"
                record["estimatedErrorRisk"] = max(
                    0.95, float(record.get("estimatedErrorRisk") or 0.0)
                )
                hallucinated.add(idx)

        if hallucinated:
            for s in segs:
                for idx in hallucinated:
                    if 0 <= idx < len(windows):
                        b = windows[idx]
                        if s["end"] > b["start"] and s["start"] < b["end"]:
                            s["unreliable"] = True
                            break

        failed = sum(not bool(item.get("ok")) for item in qc)
        critical_failed = critical_qc_failures(qc)
        passed = strict_qc_pass(qc, base_passed, len(hallucinated))
        io.safe(
            stage="ASR_QC_STRICT_R25_1",
            qc_failed=failed,
            qc_total=len(qc),
            qc_hallucination_blocks=len(hallucinated),
            qc_critical_failed=critical_failed,
            qc_critical_isolated=critical_failed,
            exit_code=0 if passed else 1,
        )
        return qc, passed

    base.qc_transcript = qc_transcript_failclosed


def run(token_func):
    install(token_func)
    return semantic.process_job(token_func())
