#!/usr/bin/env python3
"""Observability wrapper for Bridge speaker diarization v2.

Uses sherpa-onnx's official progress callback while preserving the exact v2
model/config/assignment/role-mapping behavior. Progress is throttled to coarse
5% checkpoints so long two-hour lessons expose real liveness without flooding
GitHub Actions logs.
"""
from __future__ import annotations

from typing import Any, Mapping

import bridge_speaker_diarization_v2 as base

DIARIZATION_REVISION = base.DIARIZATION_REVISION


def _run_sherpa_observable(
    wav_path,
    models: Mapping[str, Any],
    *,
    num_speakers: int = 2,
):
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(models["segmentation"])
            )
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(models["embedding"])
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=int(num_speakers), threshold=0.5
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("sherpa-onnx diarization config validation failed")

    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    samples, sample_rate = base._read_pcm16_mono(wav_path)
    expected_rate = int(diarizer.sample_rate)
    if sample_rate != expected_rate:
        raise RuntimeError(
            f"speaker diarization sample-rate mismatch: {sample_rate} != {expected_rate}"
        )

    state = {"last_bucket": -1}

    def progress_callback(num_processed_chunk: int, num_total_chunks: int) -> int:
        total = max(1, int(num_total_chunks))
        done = max(0, min(total, int(num_processed_chunk)))
        percent = done * 100.0 / total
        bucket = min(20, int(percent // 5))
        if bucket > state["last_bucket"] or done == total:
            state["last_bucket"] = bucket
            print(
                "SPEAKER_DIARIZATION_V2_PROGRESS",
                f"processed={done}",
                f"total={total}",
                f"percent={percent:.1f}",
                flush=True,
            )
        return 0

    print(
        "SPEAKER_DIARIZATION_V2_START",
        f"samples={len(samples)}",
        f"sample_rate={sample_rate}",
        f"num_speakers={num_speakers}",
        flush=True,
    )
    result = diarizer.process(samples, callback=progress_callback)
    if hasattr(result, "sort_by_start_time"):
        result = result.sort_by_start_time()
    turns = [
        {"start": float(item.start), "end": float(item.end), "speaker": int(item.speaker)}
        for item in result
        if float(item.end) > float(item.start)
    ]
    speakers = sorted({item["speaker"] for item in turns})
    if len(speakers) < 2:
        raise RuntimeError("sherpa-onnx found fewer than two usable speakers")
    print(
        "SPEAKER_DIARIZATION_V2_COMPLETE",
        f"turns={len(turns)}",
        f"speakers={speakers}",
        flush=True,
    )
    return turns, {
        "engine": "sherpa-onnx",
        "engine_version": getattr(sherpa_onnx, "__version__", base.SHERPA_ONNX_VERSION),
        "speaker_turns": len(turns),
        "speaker_ids": speakers,
        "num_speakers_requested": int(num_speakers),
        "progress_callback": "official_sherpa_callback_5pct_log",
    }


# base.diarize_transcript resolves _run_sherpa from its own module globals at
# call time, so replacing that single internal hook preserves all v2 gates.
base._run_sherpa = _run_sherpa_observable

diarize_transcript = base.diarize_transcript

__all__ = ["DIARIZATION_REVISION", "diarize_transcript"]
