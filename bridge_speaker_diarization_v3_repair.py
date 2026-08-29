"""Temporary in-memory speaker-embedding repair for diarization v3."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from bridge_speaker_diarization_v3_core import (
    _cluster_embeddings_open_set,
    _cluster_embeddings_two,
    _normalize_rows,
)


def _speaker_embedding(extractor, samples, sample_rate: int):
    import numpy as np

    stream = extractor.create_stream()
    stream.accept_waveform(
        sample_rate=int(sample_rate),
        waveform=np.ascontiguousarray(samples, dtype="float32"),
    )
    stream.input_finished()
    if not extractor.is_ready(stream):
        return None
    value = np.asarray(extractor.compute(stream), dtype="float32")
    norm = float(np.linalg.norm(value))
    if not np.isfinite(value).all() or norm < 1e-9:
        return None
    return value / norm


def repair_with_segment_embeddings(
    wav_path,
    segments: Sequence[Mapping[str, Any]],
    embedding_model,
    *,
    read_pcm16_mono,
    sherpa_version: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import numpy as np
    import sherpa_onnx

    samples, sample_rate = read_pcm16_mono(wav_path)
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(embedding_model), num_threads=2, debug=False, provider="cpu"
    )
    if not config.validate():
        raise RuntimeError("speaker embedding repair config validation failed")
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    rows = []
    refs = []
    for index, segment in enumerate(segments):
        if bool(segment.get("unreliable")):
            continue
        try:
            start = max(0.0, float(segment.get("start") or 0.0))
            end = max(start, float(segment.get("end") or start))
        except (TypeError, ValueError):
            continue
        duration = end - start
        if duration < 1.1:
            continue
        if duration > 10.0:
            middle = (start + end) / 2.0
            start = max(start, middle - 4.0)
            end = min(end, middle + 4.0)
        a = int(start * sample_rate)
        b = min(len(samples), int(end * sample_rate))
        if b - a < int(1.0 * sample_rate):
            continue
        embedding = _speaker_embedding(extractor, samples[a:b], sample_rate)
        if embedding is None:
            continue
        rows.append(embedding)
        refs.append((index, start, end))
    if len(rows) < 16:
        raise RuntimeError("too few clean transcript segments for embedding repair")
    labels, diagnostics = _cluster_embeddings_two(np.vstack(rows))
    if not diagnostics["repair_validation_passed"]:
        raise RuntimeError(
            "speaker embedding repair failed separation QC: " + str(diagnostics)
        )
    matrix = _normalize_rows(np.vstack(rows))
    label_array = np.asarray(labels, dtype=int)
    centroids = _normalize_rows(
        [matrix[label_array == cluster].mean(axis=0) for cluster in (0, 1)]
    )
    turns = []
    accepted = 0
    ambiguous = 0
    for embedding, (_, start, end), label in zip(matrix, refs, labels, strict=True):
        sims = embedding @ centroids.T
        own = float(sims[label])
        other = float(sims[1 - label])
        margin = own - other
        if margin < 0.035 or own < 0.35:
            ambiguous += 1
            continue
        turns.append({"start": float(start), "end": float(end), "speaker": int(label)})
        accepted += 1
    if accepted < 16 or len({item["speaker"] for item in turns}) < 2:
        raise RuntimeError("too few confident repaired speaker turns")
    diagnostics.update(
        {
            "engine": "segment-embedding-recluster",
            "engine_version": getattr(sherpa_onnx, "__version__", sherpa_version),
            "model_id": "3dspeaker-segment-recluster",
            "embedding_segments_total": len(rows),
            "embedding_segments_accepted": accepted,
            "embedding_segments_ambiguous": ambiguous,
            "speaker_turns": len(turns),
            "speaker_ids": [0, 1],
            "num_speakers_requested": 2,
        }
    )
    return turns, diagnostics


def diarize_with_open_set_embeddings(
    wav_path,
    segments: Sequence[Mapping[str, Any]],
    embedding_model,
    *,
    read_pcm16_mono,
    sherpa_version: str,
    max_speakers: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Estimate speaker count from temporary segment embeddings, then discard them."""
    import numpy as np
    import sherpa_onnx

    samples, sample_rate = read_pcm16_mono(wav_path)
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(embedding_model), num_threads=2, debug=False, provider="cpu"
    )
    if not config.validate():
        raise RuntimeError("open-set speaker embedding config validation failed")
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    rows = []
    refs = []
    for index, segment in enumerate(segments):
        if bool(segment.get("unreliable")):
            continue
        try:
            start = max(0.0, float(segment.get("start") or 0.0))
            end = max(start, float(segment.get("end") or start))
        except (TypeError, ValueError):
            continue
        if end - start < 1.1:
            continue
        if end - start > 10.0:
            middle = (start + end) / 2.0
            start, end = max(start, middle - 4.0), min(end, middle + 4.0)
        a, b = int(start * sample_rate), min(len(samples), int(end * sample_rate))
        if b - a < sample_rate:
            continue
        embedding = _speaker_embedding(extractor, samples[a:b], sample_rate)
        if embedding is not None:
            rows.append(embedding)
            refs.append((index, start, end))
    if len(rows) < 16:
        raise RuntimeError("too few clean segments for open-set speaker count")
    matrix = _normalize_rows(np.vstack(rows))
    labels, count_evidence = _cluster_embeddings_open_set(
        matrix, max_speakers=max_speakers
    )
    label_array = np.asarray(labels, dtype=int)
    count = int(count_evidence["selected_count"])
    centroids = _normalize_rows(
        [matrix[label_array == cluster].mean(axis=0) for cluster in range(count)]
    )
    turns = []
    ambiguous = 0
    for embedding, (_, start, end), label in zip(matrix, refs, labels, strict=True):
        similarities = embedding @ centroids.T
        own = float(similarities[label])
        other = max(
            (float(value) for index, value in enumerate(similarities) if index != label),
            default=-1.0,
        )
        if own < 0.35 or own - other < 0.035:
            ambiguous += 1
            continue
        turns.append({"start": float(start), "end": float(end), "speaker": int(label)})
    if len(turns) < 16 or len({item["speaker"] for item in turns}) != count:
        raise RuntimeError("open-set result lost an active speaker cluster")
    return turns, {
        "engine": "segment-embedding-open-set",
        "engine_version": getattr(sherpa_onnx, "__version__", sherpa_version),
        "model_id": "3dspeaker-segment-open-set",
        "embedding_segments_total": len(rows),
        "embedding_segments_accepted": len(turns),
        "embedding_segments_ambiguous": ambiguous,
        "speaker_turns": len(turns),
        "speaker_ids": list(range(count)),
        "num_speakers_requested": None,
        "speaker_count_evidence": count_evidence,
    }


__all__ = ["diarize_with_open_set_embeddings", "repair_with_segment_embeddings"]
