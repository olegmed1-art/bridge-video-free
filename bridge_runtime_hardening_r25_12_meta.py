#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r25.12 META-gated candidate.

This module is intentionally *not* the production entrypoint.  It preserves the
confirmed r25.6 runtime and adds a fail-closed ASR evidence classifier for the
next independently assessed candidate.  A worker-side result is preliminary;
only the database META gate may authorize publication.
"""
from __future__ import annotations

from collections import Counter
import os
import re

import bridge_runtime_hardening_r25_1 as asr
import bridge_runtime_hardening_r25_6 as stable
import bridge_worker_3_1_free as core
import run_master_3_1_free as base

REVISION = "3.1-free-r25.12-meta"
_INSTALLED = False
_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")


def _dense_repetition(text: str) -> bool:
    words = [word.casefold() for word in _WORD_RE.findall(str(text or ""))]
    if len(words) < 8:
        return False
    _, count = Counter(words).most_common(1)[0]
    return count >= 8 and count / len(words) >= 0.70


def _record_has_hallucination(record: dict, primary_text: str = "") -> bool:
    reasons = {str(value) for value in (record.get("failureReasons") or [])}
    if "REPEATED_NONSPEECH_HALLUCINATION" in reasons:
        return True
    texts = [str(primary_text or "")]
    texts.extend(str(item.get("text") or "") for item in (record.get("qcEvidence") or []))
    return any(asr.pathological_nonspeech_hallucination(text) or _dense_repetition(text)
               for text in texts)


def normalize_no_speech_qc(qc) -> int:
    """Annotate plausible silence without erasing the source QC failure.

    The r25.10/r25.11 defect replaced similarity, failure reasons and risk with a
    fabricated successful ``NO_SPEECH`` result.  This candidate never changes
    ``ok``, ``similarity``, ``failureReasons``, ``estimatedErrorRisk`` or
    ``riskBand``.  Repetition/hallucination can never be classified as silence.
    """
    annotated = 0
    for record in qc or []:
        if record.get("primaryTextEmpty") is not True:
            continue
        if _record_has_hallucination(record):
            record["noSpeechClassification"] = "REJECTED_HALLUCINATION"
            continue
        record["noSpeechClassification"] = "CANDIDATE_ONLY"
        record["noSpeechControlArtifact"] = True
        annotated += 1
    return annotated


def independent_asr_evidence_gate(
    qc,
    *,
    base_coverage_passed: bool,
    unreliable_derived_evidence_count: int,
) -> dict:
    """Produce worker evidence for META; this is not an independent approval."""
    records = list(qc or [])
    hallucinated = []
    isolated_zero = []
    for record in records:
        block = int(record.get("block", -1))
        if _record_has_hallucination(record):
            hallucinated.append(block)
            record["metaIsolationStatus"] = "HARD_STOP_HALLUCINATION"
            continue
        similarity = record.get("similarity")
        if not bool(record.get("ok")) and similarity is not None and float(similarity) <= 0.05:
            isolated_zero.append(block)
            record["metaIsolationStatus"] = "ISOLATED_UNRELIABLE"
            record["excludedFromDerivedEvidence"] = True

    reasons = []
    if not base_coverage_passed:
        reasons.append("BASE_COVERAGE_FAILED")
    if hallucinated:
        reasons.append("REPEATED_NONSPEECH_HALLUCINATION")
    if int(unreliable_derived_evidence_count or 0) != 0:
        reasons.append("UNRELIABLE_DERIVED_EVIDENCE")
    if not records:
        reasons.append("ASR_QC_EMPTY")

    return {
        "workerEvidenceStatus": "PASS_CANDIDATE" if not reasons else "FAIL",
        "independentAssessmentRequired": True,
        "selfReportedApproval": False,
        "publicationAllowed": False,
        "baseCoveragePassed": bool(base_coverage_passed),
        "hallucinationBlocks": hallucinated,
        "isolatedZeroBlocks": isolated_zero,
        "unreliableDerivedEvidenceCount": int(unreliable_derived_evidence_count or 0),
        "failureReasons": reasons,
    }


def install(token_func):
    global _INSTALLED
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    previous = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = stable.REVISION
    try:
        stable.install(token_func)
    finally:
        if previous is None:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
        else:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = previous

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION
    if _INSTALLED:
        return

    original_qc = base.qc_transcript

    def evidence_preserving_qc(video, work, duration, segments):
        qc, base_passed = original_qc(video, work, duration, segments)
        normalize_no_speech_qc(qc)
        preliminary = independent_asr_evidence_gate(
            qc,
            base_coverage_passed=base_passed,
            unreliable_derived_evidence_count=0,
        )
        passed = preliminary["workerEvidenceStatus"] == "PASS_CANDIDATE"
        return qc, passed

    base.qc_transcript = evidence_preserving_qc

    original_payload = base.master_analysis_payload

    def payload_with_meta_candidate(**kwargs):
        master = original_payload(**kwargs)
        gate = dict((master.get("content_quality") or {}).get("r24Gate") or {})
        transcript_qc = ((master.get("technical_qc") or {}).get("transcript") or {})
        transcript_status = str(transcript_qc.get("status") or "").upper()
        preliminary = independent_asr_evidence_gate(
            transcript_qc.get("qc") or [],
            base_coverage_passed=transcript_status in {
                "OK",
                "PASS",
                "PASSED",
                "ISOLATED_UNRELIABLE",
            },
            unreliable_derived_evidence_count=int(
                gate.get("unreliableDerivedEvidenceCount") or 0
            ),
        )
        master["meta_evidence_gate"] = preliminary
        master.setdefault("principles", {})[
            "worker_self_report_cannot_authorize_publication"
        ] = True
        return master

    base.master_analysis_payload = payload_with_meta_candidate
    _INSTALLED = True


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic as semantic
    return semantic.process_job(token_func())
