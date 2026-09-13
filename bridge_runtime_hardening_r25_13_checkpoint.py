#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r25.13 evidence checkpoint candidate.

This candidate keeps the r25.12 META gate and makes a strict ASR stop
observable and resumable.  It never turns a failed QC result into success and
never authorizes publication from worker-side evidence.
"""
from __future__ import annotations

import copy
import os
import statistics
import time
from array import array
from pathlib import Path
from typing import Any
import wave

import bridge_runtime_hardening_r25_12_meta as previous
import bridge_worker_3_1_free as core
import run_master_3_1_free as base

REVISION = "3.1-free-r25.13-checkpoint"
_INSTALLED = False


class ASRQualityGateError(RuntimeError):
    """Typed fail-closed error carrying the evidence saved before shutdown."""

    def __init__(self, checkpoint: dict[str, Any], receipt: dict[str, Any] | None = None):
        super().__init__("ASR_QC_FAILED")
        self.checkpoint = checkpoint
        self.receipt = receipt or {}


def build_asr_failure_checkpoint(
    qc,
    *,
    job_id: str,
    source_drive_id: str,
    duration_seconds: float,
    candidate_passed: bool,
    independent_silence_controls: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an immutable technical checkpoint without rewriting QC fields."""
    records = copy.deepcopy(list(qc or []))
    previous.normalize_no_speech_qc(records)
    silence_controls = independent_silence_controls or {}
    for record in records:
        block = int(record.get("block", -1))
        if block in silence_controls:
            record["noSpeechClassification"] = "CONFIRMED_DIGITAL_SILENCE"
            record["noSpeechControlArtifact"] = silence_controls[block]
            record["forcedNoVadOutputRejected"] = True
            record["excludedFromDerivedEvidence"] = True
    gate = previous.independent_asr_evidence_gate(
        records,
        base_coverage_passed=bool(candidate_passed),
        unreliable_derived_evidence_count=0,
    )
    failed = [int(item.get("block", -1)) for item in records if not bool(item.get("ok"))]
    completed = [int(item.get("block", -1)) for item in records]
    return {
        "schema": "bridge-video-asr-qc-checkpoint-v1",
        "status": "ASR_QC_FAILED",
        "job_id": job_id,
        "algorithmVersion": core.ALGORITHM_VERSION,
        "algorithmRevision": REVISION,
        "sourceDriveId": source_drive_id,
        "durationSeconds": float(duration_seconds),
        "qc": records,
        "qcSummary": {
            "total": len(records),
            "failed": len(failed),
            "failedBlocks": failed,
        },
        "resume": {
            "completedQcBlocks": completed,
            "failedBlocks": failed,
            "firstFailedBlock": min(failed) if failed else None,
            "resumeFromBlock": min(failed) if failed else None,
            "fullQcRecalculationRequired": False,
            "targetedDiagnosticRequired": bool(failed),
            "independentlyApprovedNoSpeechBlocks": sorted(silence_controls),
        },
        "meta_evidence_gate": gate,
        "technicalRecordOnly": True,
        "independentAssessmentRequired": True,
        "selfReportedApproval": False,
        "publicationAllowed": False,
        "reportDriveId": None,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def classify_targeted_diagnostic(pass_records, audio_metrics: dict[str, Any]) -> dict[str, Any]:
    """Classify a target block conservatively; META must assess the result."""
    records = [dict(item) for item in (pass_records or [])]
    hallucinated = []
    usable = []
    for item in records:
        text = str(item.get("text") or "")
        if previous._record_has_hallucination({"qcEvidence": [{"text": text}]}, text):
            hallucinated.append(str(item.get("passId") or "unknown"))
        elif text.strip():
            usable.append(item)

    cross_model = []
    for index, left in enumerate(usable):
        for right in usable[index + 1:]:
            if left.get("model") == right.get("model"):
                continue
            cross_model.append(base._similarity(str(left.get("text") or ""), str(right.get("text") or "")))

    active_ratio = float(audio_metrics.get("activeFrameRatio") or 0.0)
    digital_zero = (
        int(audio_metrics.get("frameCount") or 0) >= 100
        and active_ratio == 0.0
        and float(audio_metrics.get("meanRms") or 0.0) == 0.0
        and int(audio_metrics.get("peakAbsoluteSample") or 0) == 0
    )
    median_similarity = statistics.median(cross_model) if cross_model else 0.0
    if digital_zero:
        status = "DIGITAL_SILENCE_CONFIRMED_ASR_HALLUCINATIONS_REJECTED_REQUIRES_META"
        reasons = ["FORCED_NO_VAD_OUTPUT_REJECTED_ON_ZERO_PCM"] if hallucinated else []
    elif hallucinated:
        status = "QUARANTINED_HALLUCINATION"
        reasons = ["REPEATED_NONSPEECH_HALLUCINATION"]
    elif len({str(item.get("model")) for item in usable}) >= 2 and median_similarity >= 0.35 and active_ratio >= 0.01:
        status = "SPEECH_RECOVERABLE_CANDIDATE_REQUIRES_META"
        reasons = []
    elif not usable and active_ratio < 0.005:
        status = "NO_SPEECH_CANDIDATE_REQUIRES_META"
        reasons = ["NO_SPEECH_NOT_INDEPENDENTLY_CONFIRMED"]
    else:
        status = "QUARANTINED_INCONCLUSIVE"
        reasons = ["INDEPENDENT_ASR_PASSES_DID_NOT_CONVERGE"]

    return {
        "status": status,
        "failureReasons": reasons,
        "hallucinationPasses": hallucinated,
        "usablePassCount": len(usable),
        "crossModelComparisonCount": len(cross_model),
        "crossModelMedianSimilarity": round(float(median_similarity), 4),
        "activeFrameRatio": round(active_ratio, 6),
        "digitalSilenceConfirmed": digital_zero,
        "independentAssessmentRequired": True,
        "selfReportedApproval": False,
        "publicationAllowed": False,
    }


def pcm_digital_silence_control(path: Path) -> dict[str, Any] | None:
    """Return exact zero-PCM evidence; near-silence never qualifies."""
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            return None
        frame_count = source.getnframes()
        sample_rate = source.getframerate()
        samples = array("h", source.readframes(frame_count))
    peak = max((abs(value) for value in samples), default=0)
    if frame_count < sample_rate or peak != 0:
        return None
    return {
        "controlType": "EXACT_ZERO_PCM",
        "sampleRate": sample_rate,
        "sampleCount": len(samples),
        "peakAbsoluteSample": peak,
        "sha256RequiredFromDiagnosticReceipt": True,
    }


def independently_approved_no_speech_blocks(job_id: str, source_drive_id: str) -> set[int]:
    """Read an independent META gate; worker configuration alone cannot approve."""
    raw_dsn = os.getenv("BRIDGE_WORKER_DATABASE_URL", "").strip()
    receipt_id = os.getenv("BRIDGE_ASR_DIAGNOSTIC_RECEIPT_ID", "").strip()
    if not raw_dsn or not receipt_id:
        return set()
    import psycopg
    from database.runtime_worker_preflight import normalize_dsn

    sql = """
    SELECT g.checks
      FROM public.bridge_video_evidence_gate g
      JOIN public.analysis_run r USING (analysis_run_id)
     WHERE r.algorithm_version='3.1-free-r25.13-checkpoint'
       AND r.parameters_snapshot->>'job_id'=%s
       AND r.parameters_snapshot->>'source_drive_id'=%s
       AND g.assessor_authority='independent_meta'
       AND g.self_reported=false
       AND g.assessment_status='quarantined'
       AND g.publication_allowed=false
       AND cardinality(g.evidence_ids)>0
       AND g.checks->>'diagnosticReceiptDriveId'=%s
       AND (g.checks->>'block11DigitalSilenceConfirmed')::boolean=true
       AND (g.checks->>'block11NoVadHallucinationsRejected')::boolean=true
       AND (g.checks->>'block11ResumeAsNoSpeechAllowed')::boolean=true
     ORDER BY g.assessed_at DESC
     LIMIT 1
    """
    with psycopg.connect(normalize_dsn(raw_dsn), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (job_id, source_drive_id, receipt_id))
            row = cursor.fetchone()
    return {11} if row else set()


def install(token_func):
    """Install r25.12 and persist QC evidence before its strict stop escapes."""
    global _INSTALLED
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    saved = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = previous.REVISION
    try:
        previous.install(token_func)
    finally:
        if saved is None:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
        else:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = saved

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION
    if _INSTALLED:
        return

    original_obtain = base.obtain_transcript
    evidence_qc = base.qc_transcript

    def checkpointing_obtain(t, parent, name, video, work, dur, job):
        captured: list[dict[str, Any]] = []

        def capture_qc(qc_video, qc_work, qc_duration, segments):
            qc, passed = evidence_qc(qc_video, qc_work, qc_duration, segments)
            captured.append({
                "qc": copy.deepcopy(qc),
                "passed": bool(passed),
                "segments": copy.deepcopy(segments),
            })
            return qc, passed

        prior_qc = base.qc_transcript
        base.qc_transcript = capture_qc
        try:
            return original_obtain(t, parent, name, video, work, dur, job)
        except RuntimeError as error:
            if str(error) != "ASR_QC_FAILED" or not captured:
                raise
            latest = captured[-1]
            source_drive_id = os.getenv("BRIDGE_SOURCE_DRIVE_ID", "")
            approved = independently_approved_no_speech_blocks(str(job), source_drive_id)
            failed_blocks = {
                int(item.get("block", -1))
                for item in latest["qc"]
                if not bool(item.get("ok"))
            }
            controls = {}
            for block in sorted(failed_blocks.intersection(approved)):
                control_path = Path(work) / f"q{block:03d}.wav"
                if control_path.exists():
                    control = pcm_digital_silence_control(control_path)
                    if control:
                        controls[block] = control
            checkpoint = build_asr_failure_checkpoint(
                latest["qc"],
                job_id=str(job),
                source_drive_id=source_drive_id,
                duration_seconds=float(dur),
                candidate_passed=bool(latest["passed"]),
                independent_silence_controls=controls,
            )
            receipt = base.io.upload_json(
                t,
                parent,
                f"ASR_QC_CHECKPOINT_{job}_{REVISION}.json",
                checkpoint,
            )
            base.io.safe(
                job_id=job,
                stage="ASR_QC_CHECKPOINT_SAVED",
                exit_code=1,
                qc_total=checkpoint["qcSummary"]["total"],
                qc_failed=checkpoint["qcSummary"]["failed"],
            )
            if failed_blocks and failed_blocks == set(controls):
                segments = latest["segments"]
                warnings = [
                    "Independent META evidence confirms exact zero PCM in ASR block 11; "
                    "forced no-VAD text is rejected and the interval is retained as NO_SPEECH."
                ]
                return segments, {
                    "primarySource": "local_asr",
                    "sourceFile": None,
                    "status": base.transcript_status(
                        True,
                        sum(bool(segment.get("unreliable")) for segment in segments),
                        primary_source="ASR",
                    ),
                    "qc": checkpoint["qc"],
                    "language": None,
                    "independentNoSpeechBlocks": sorted(controls),
                    "asrCheckpointReceipt": receipt,
                }, warnings
            raise ASRQualityGateError(checkpoint, receipt) from error
        finally:
            base.qc_transcript = prior_qc

    base.obtain_transcript = checkpointing_obtain
    _INSTALLED = True


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic_v2 as semantic

    # semantic_v2 is the production r25.7 adapter and sets module constants at
    # import time.  The candidate must stamp its own revision after that import
    # so the embedded master, Drive receipts, and Neon analysis_run all agree.
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION
    semantic.REVISION = REVISION
    return semantic.process_job(token_func())
