#!/usr/bin/env python3
from __future__ import annotations

import copy

import bridge_runtime_hardening_r25_13_checkpoint as candidate


def test_checkpoint_preserves_failed_qc_and_targets_first_failed_block():
    qc = [
        {"block": 0, "ok": True, "similarity": 0.91},
        {
            "block": 11,
            "ok": False,
            "similarity": 0.0,
            "retry": True,
            "failureReasons": ["REPEATED_NONSPEECH_HALLUCINATION"],
            "qcEvidence": [{"text": "you you you you you you you you"}],
        },
        {"block": 22, "ok": True, "similarity": 0.88},
    ]
    before = copy.deepcopy(qc)
    checkpoint = candidate.build_asr_failure_checkpoint(
        qc,
        job_id="job",
        source_drive_id="source",
        duration_seconds=6900,
        candidate_passed=False,
    )
    assert qc == before
    assert checkpoint["status"] == "ASR_QC_FAILED"
    assert checkpoint["qcSummary"] == {"total": 3, "failed": 1, "failedBlocks": [11]}
    assert checkpoint["resume"]["resumeFromBlock"] == 11
    assert checkpoint["resume"]["fullQcRecalculationRequired"] is False
    assert checkpoint["meta_evidence_gate"]["hallucinationBlocks"] == [11]
    assert checkpoint["publicationAllowed"] is False
    assert checkpoint["reportDriveId"] is None


def test_targeted_diagnostic_hallucination_is_quarantined():
    result = candidate.classify_targeted_diagnostic(
        [
            {"passId": "medium:no-vad", "model": "medium", "text": "you " * 12},
            {"passId": "small:strict-vad", "model": "small", "text": ""},
        ],
        {"activeFrameRatio": 0.2},
    )
    assert result["status"] == "QUARANTINED_HALLUCINATION"
    assert result["publicationAllowed"] is False


def test_cross_model_convergence_is_only_a_candidate():
    result = candidate.classify_targeted_diagnostic(
        [
            {"passId": "medium:strict-vad", "model": "medium", "text": "контракт три без козыря первый ход"},
            {"passId": "small:strict-vad", "model": "small", "text": "контракт три без козыря и первый ход"},
        ],
        {"activeFrameRatio": 0.15},
    )
    assert result["status"] == "SPEECH_RECOVERABLE_CANDIDATE_REQUIRES_META"
    assert result["independentAssessmentRequired"] is True
    assert result["publicationAllowed"] is False


def test_empty_low_energy_is_not_declared_proven_silence():
    result = candidate.classify_targeted_diagnostic(
        [
            {"passId": "medium:strict-vad", "model": "medium", "text": ""},
            {"passId": "small:strict-vad", "model": "small", "text": ""},
        ],
        {"activeFrameRatio": 0.0},
    )
    assert result["status"] == "NO_SPEECH_CANDIDATE_REQUIRES_META"
    assert "NO_SPEECH_NOT_INDEPENDENTLY_CONFIRMED" in result["failureReasons"]


if __name__ == "__main__":
    test_checkpoint_preserves_failed_qc_and_targets_first_failed_block()
    test_targeted_diagnostic_hallucination_is_quarantined()
    test_cross_model_convergence_is_only_a_candidate()
    test_empty_low_energy_is_not_declared_proven_silence()
    print("R25_13_ASR_CHECKPOINT: PASS")

