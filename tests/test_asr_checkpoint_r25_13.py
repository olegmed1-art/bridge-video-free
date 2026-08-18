#!/usr/bin/env python3
from __future__ import annotations

import copy
from pathlib import Path

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
        {"frameCount": 1000, "activeFrameRatio": 0.2, "meanRms": 100, "peakAbsoluteSample": 1000},
    )
    assert result["status"] == "QUARANTINED_HALLUCINATION"
    assert result["publicationAllowed"] is False


def test_cross_model_convergence_is_only_a_candidate():
    result = candidate.classify_targeted_diagnostic(
        [
            {"passId": "medium:strict-vad", "model": "medium", "text": "контракт три без козыря первый ход"},
            {"passId": "small:strict-vad", "model": "small", "text": "контракт три без козыря и первый ход"},
        ],
        {"frameCount": 1000, "activeFrameRatio": 0.15, "meanRms": 100, "peakAbsoluteSample": 1000},
    )
    assert result["status"] == "SPEECH_RECOVERABLE_CANDIDATE_REQUIRES_META"
    assert result["independentAssessmentRequired"] is True
    assert result["publicationAllowed"] is False


def test_exact_zero_pcm_rejects_forced_asr_hallucinations_without_publication():
    result = candidate.classify_targeted_diagnostic(
        [
            {"passId": "medium:strict-vad", "model": "medium", "text": ""},
            {"passId": "medium:no-vad", "model": "medium", "text": "you " * 12},
            {"passId": "small:strict-vad", "model": "small", "text": ""},
            {"passId": "small:no-vad", "model": "small", "text": "you " * 10},
        ],
        {"frameCount": 10000, "activeFrameRatio": 0.0, "meanRms": 0.0, "peakAbsoluteSample": 0},
    )
    assert result["status"] == "DIGITAL_SILENCE_CONFIRMED_ASR_HALLUCINATIONS_REJECTED_REQUIRES_META"
    assert result["digitalSilenceConfirmed"] is True
    assert result["hallucinationPasses"] == ["medium:no-vad", "small:no-vad"]
    assert result["publicationAllowed"] is False


def test_r25_13_full_result_is_staging_only_until_meta_pass():
    source = (Path(__file__).parents[1] / "database" / "video_result_persistence.py").read_text(
        encoding="utf-8"
    )
    assert "candidate_requires_meta = algorithm_revision in {" in source
    assert '"3.1-free-r25.12-meta"' in source
    assert '"3.1-free-r25.13-checkpoint"' in source


def test_r25_13_workflow_recovers_stalled_runtime_install_without_parallel_data_work():
    workflow = (
        Path(__file__).parents[1]
        / ".github"
        / "workflows"
        / "video-r25-13-production-candidate.yml"
    ).read_text(encoding="utf-8")
    assert "cancel-in-progress: true" in workflow
    assert "candidate_requests/2026-08-18-logic-bridge-r25-13-full-retry3.txt" in workflow
    assert "timeout-minutes: 20" in workflow
    assert "timeout 180s apt-get update -qq" in workflow
    assert "APT_RUNTIME_INSTALL_FAILED_AFTER_RETRIES" in workflow
    assert "--timeout 60 --retries 8 -r requirements-worker.txt" in workflow
    assert "BRIDGE_REPOSITORY_PRIVATE: ${{ github.event.repository.private }}" in workflow
    assert 'BRIDGE_RUNNER_LABEL: "ubuntu-24.04"' in workflow
    assert 'BRIDGE_LARGER_RUNNER: "false"' in workflow
    assert 'BRIDGE_PAID_CLOUD: "false"' in workflow
    assert 'BRIDGE_BILLING_FALLBACK: "false"' in workflow


def test_run_restamps_candidate_revision_after_semantic_adapter_import(monkeypatch):
    import run_master_3_1_free_semantic_v2 as semantic

    monkeypatch.setattr(candidate, "install", lambda token_func: None)
    seen = {}

    def fake_process(token):
        seen["token"] = token
        seen["core_revision"] = candidate.core.ALGORITHM_REVISION
        seen["base_revision"] = candidate.base.ALGORITHM_REVISION
        seen["semantic_revision"] = semantic.REVISION
        return seen

    monkeypatch.setattr(semantic, "process_job", fake_process)
    candidate.core.ALGORITHM_REVISION = "3.1-free-r25.7"
    candidate.base.ALGORITHM_REVISION = "3.1-free-r25.7"
    semantic.REVISION = "3.1-free-r25.7"

    result = candidate.run(lambda: "drive-token")

    assert result["token"] == "drive-token"
    assert result["core_revision"] == candidate.REVISION
    assert result["base_revision"] == candidate.REVISION
    assert result["semantic_revision"] == candidate.REVISION


if __name__ == "__main__":
    test_checkpoint_preserves_failed_qc_and_targets_first_failed_block()
    test_targeted_diagnostic_hallucination_is_quarantined()
    test_cross_model_convergence_is_only_a_candidate()
    test_exact_zero_pcm_rejects_forced_asr_hallucinations_without_publication()
    test_r25_13_full_result_is_staging_only_until_meta_pass()
    test_r25_13_workflow_recovers_stalled_runtime_install_without_parallel_data_work()
    # pytest supplies monkeypatch for the import-order regression test.
    print("R25_13_ASR_CHECKPOINT: PASS")
