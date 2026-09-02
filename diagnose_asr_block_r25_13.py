#!/usr/bin/env python3
"""Targeted, non-publishing ASR diagnosis for one five-minute source block."""
from __future__ import annotations

from array import array
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import wave

from faster_whisper import WhisperModel

import bridge_runtime_hardening_r25_13_checkpoint as candidate
import run_drive_3_1_free as io
import run_master_3_1_free as base
from run_drive_3_1_free_oidc import drive_token


def audio_metrics(path: Path) -> dict:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        rate = source.getframerate()
        samples = array("h", source.readframes(source.getnframes()))
    if channels != 1 or sample_width != 2:
        raise RuntimeError("DIAGNOSTIC_PCM_FORMAT_UNEXPECTED")
    frame = max(1, int(rate * 0.03))
    rms_values = []
    for offset in range(0, len(samples), frame):
        chunk = samples[offset:offset + frame]
        if not chunk:
            continue
        rms_values.append((sum(value * value for value in chunk) / len(chunk)) ** 0.5)
    active = sum(value >= 500.0 for value in rms_values)
    return {
        "sampleRate": rate,
        "frameMilliseconds": 30,
        "frameCount": len(rms_values),
        "activeFrameThresholdRms": 500.0,
        "activeFrameRatio": active / max(1, len(rms_values)),
        "meanRms": sum(rms_values) / max(1, len(rms_values)),
        "peakAbsoluteSample": max((abs(value) for value in samples), default=0),
    }


def transcribe_pass(path: Path, model, model_name: str, mode: str) -> dict:
    strict = mode == "strict-vad"
    kwargs = {
        "language": None,
        "condition_on_previous_text": False,
        "initial_prompt": base.PROMPT,
        "beam_size": 3 if strict else 5,
        "vad_filter": strict,
    }
    if strict:
        kwargs["vad_parameters"] = {
            "threshold": 0.65,
            "min_speech_duration_ms": 300,
            "min_silence_duration_ms": 800,
        }
    segments, info = model.transcribe(str(path), **kwargs)
    text = " ".join((segment.text or "").strip() for segment in segments if (segment.text or "").strip())
    return {
        "passId": f"{model_name}:{mode}",
        "model": model_name,
        "mode": mode,
        "language": getattr(info, "language", None),
        "text": text,
        "textSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "wordCount": len(base._words(text)),
    }


def main() -> dict:
    job_id = os.environ["BRIDGE_JOB_ID"]
    source_id = os.environ["BRIDGE_SOURCE_DRIVE_ID"]
    parent_id = os.environ["BRIDGE_OUTPUT_FOLDER_ID"]
    expected_sha = os.environ["BRIDGE_EXPECTED_SOURCE_SHA256"]
    block = int(os.getenv("BRIDGE_DIAGNOSTIC_BLOCK", "11"))
    start = float(os.getenv("BRIDGE_DIAGNOSTIC_START", "3300"))
    duration = float(os.getenv("BRIDGE_DIAGNOSTIC_DURATION", "300"))
    models = [name.strip() for name in os.getenv("BRIDGE_DIAGNOSTIC_MODELS", "medium,small").split(",") if name.strip()]
    token = drive_token()
    before = io.meta(token, source_id)

    with tempfile.TemporaryDirectory(prefix="bridge-r25-13-diagnostic-") as directory:
        work = Path(directory)
        video = work / "source.video"
        block_audio = work / f"block-{block:03d}.wav"
        io.download(token, source_id, video)
        actual_sha = io.sha(video)
        if actual_sha != expected_sha:
            raise RuntimeError("ORIGINAL_SHA256_MISMATCH")
        source_duration = io.duration(video)
        if start < 0 or duration <= 0 or start + duration > source_duration + 1:
            raise RuntimeError("DIAGNOSTIC_RANGE_INVALID")
        io.wav(video, block_audio, start, duration)
        metrics = audio_metrics(block_audio)
        passes = []
        for model_name in models:
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            for mode in ("strict-vad", "no-vad"):
                passes.append(transcribe_pass(block_audio, model, model_name, mode))
        classification = candidate.classify_targeted_diagnostic(passes, metrics)
        after = io.meta(token, source_id)
        source_unchanged = {
            "id": before.get("id") == after.get("id") == source_id,
            "name": before.get("name") == after.get("name"),
            "size": before.get("size") == after.get("size"),
            "modifiedTime": before.get("modifiedTime") == after.get("modifiedTime"),
            "sha256": actual_sha == expected_sha,
        }
        if not all(source_unchanged.values()):
            raise RuntimeError("ORIGINAL_INTEGRITY_REVERIFY_FAILED")
        receipt = {
            "schema": "bridge-video-targeted-asr-diagnostic-v1",
            "status": classification["status"],
            "job_id": job_id,
            "algorithmVersion": "3.1 FREE",
            "algorithmRevision": candidate.REVISION,
            "source": {
                "driveId": source_id,
                "name": before.get("name"),
                "sizeBytes": int(before.get("size") or 0),
                "sha256": actual_sha,
                "immutable": True,
                "integrityReverified": source_unchanged,
            },
            "target": {"block": block, "start": start, "end": start + duration},
            "audioMetrics": metrics,
            "asrPasses": passes,
            "classification": classification,
            "technicalRecordOnly": True,
            "independentAssessmentRequired": True,
            "publicationAllowed": False,
            "reportDriveId": None,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        uploaded = io.upload_json(
            token,
            parent_id,
            f"ASR_DIAGNOSTIC_{job_id}_{candidate.REVISION}_block_{block}.json",
            receipt,
        )
    io.safe(
        job_id=job_id,
        stage="TARGETED_ASR_DIAGNOSTIC_SAVED",
        exit_code=0,
        qc_block=block,
        diagnostic_status=classification["status"],
        receipt_drive_id=uploaded.get("id"),
    )
    return {"receipt": uploaded, "classification": classification}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, sort_keys=True))
