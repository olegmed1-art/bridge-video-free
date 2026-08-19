#!/usr/bin/env python3
"""Zero-cost speaker diarization v3 with collapse detection and acoustic repair.

The revision keeps r25.14 privacy/authority rules while fixing a field failure
where a nominal two-speaker result collapsed almost all speech into one cluster.
It never assigns a real-person identity. Anonymous role candidates are considered
only after the acoustic separation gate passes.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import tarfile
from typing import Any, Mapping, Sequence

import bridge_speaker_diarization as fallback
import bridge_speaker_diarization_v2 as previous
from bridge_speaker_diarization_v3_core import (
    _collapse_diagnostics,
    _cluster_embeddings_two,
    _hypothesis_score,
)
from bridge_speaker_diarization_v3_repair import repair_with_segment_embeddings

DIARIZATION_REVISION = "bridge-sherpa-onnx-diarization-v3"
SHERPA_ONNX_VERSION = previous.SHERPA_ONNX_VERSION
SEGMENTATION_URL = previous.SEGMENTATION_URL
NEMO_EMBEDDING_URL = previous.EMBEDDING_URL
THREED_EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)
EMBEDDING_URL = THREED_EMBEDDING_URL


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _ensure_segmentation(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    segmentation = cache_dir / "pyannote-segmentation-3.0.onnx"
    if segmentation.exists() and segmentation.stat().st_size > 1024:
        return segmentation
    archive = cache_dir / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
    previous._download(SEGMENTATION_URL, archive)
    with tarfile.open(archive, "r:bz2") as bundle:
        member = next(
            (item for item in bundle.getmembers() if item.isfile() and item.name.endswith("/model.onnx")),
            None,
        )
        if member is None:
            raise RuntimeError("speaker segmentation archive has no model.onnx")
        source = bundle.extractfile(member)
        if source is None:
            raise RuntimeError("speaker segmentation model could not be extracted")
        tmp = segmentation.with_suffix(".onnx.tmp")
        with tmp.open("wb") as output:
            while True:
                block = source.read(8 * 1024 * 1024)
                if not block:
                    break
                output.write(block)
        if tmp.stat().st_size <= 1024:
            raise RuntimeError("speaker segmentation model is unexpectedly small")
        tmp.replace(segmentation)
    archive.unlink(missing_ok=True)
    return segmentation


def _ensure_embedding(cache_dir: Path, kind: str) -> Path:
    if kind == "3dspeaker":
        target = cache_dir / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
        url = THREED_EMBEDDING_URL
    elif kind == "nemo":
        target = cache_dir / "nemo_en_titanet_small.onnx"
        url = NEMO_EMBEDDING_URL
    else:
        raise ValueError(f"unknown embedding kind: {kind}")
    previous._download(url, target)
    return target


def _run_sherpa_model(
    wav_path: Path,
    segmentation: Path,
    embedding: Path,
    *,
    model_id: str,
    num_speakers: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(segmentation)
            )
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding)),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=int(num_speakers), threshold=0.5
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError(f"sherpa-onnx config validation failed for {model_id}")
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    samples, sample_rate = previous._read_pcm16_mono(wav_path)
    if sample_rate != int(diarizer.sample_rate):
        raise RuntimeError("speaker diarization sample-rate mismatch")
    result = diarizer.process(samples)
    if hasattr(result, "sort_by_start_time"):
        result = result.sort_by_start_time()
    turns = [
        {"start": float(item.start), "end": float(item.end), "speaker": int(item.speaker)}
        for item in result
        if float(item.end) > float(item.start)
    ]
    speakers = sorted({int(item["speaker"]) for item in turns})
    if len(speakers) < 2:
        raise RuntimeError(f"{model_id} found fewer than two usable speakers")
    return turns, {
        "engine": "sherpa-onnx",
        "engine_version": getattr(sherpa_onnx, "__version__", SHERPA_ONNX_VERSION),
        "model_id": model_id,
        "speaker_turns": len(turns),
        "speaker_ids": speakers,
        "num_speakers_requested": int(num_speakers),
    }


def _assign_v3(segments, turns):
    enriched, report, assignments = previous._assign_speakers_from_turns(segments, turns)
    for item in enriched:
        if item.get("speaker"):
            item["speaker_assignment_revision"] = DIARIZATION_REVISION
    return enriched, report, assignments


def _build_report(
    copied,
    turns,
    engine,
    *,
    hypotheses,
    minimum_segments_per_cluster,
    models,
):
    enriched, assignment_report, assignments = _assign_v3(copied, turns)
    collapse = _collapse_diagnostics(
        turns,
        assignments,
        expected_speakers=2,
        minimum_segments_per_cluster=minimum_segments_per_cluster,
    )
    if assignment_report["segments_labeled"] < minimum_segments_per_cluster * 2:
        raise RuntimeError("too few transcript segments received speaker labels")
    role_mapping, role_report = previous._map_roles_v2(enriched, assignments)
    if collapse["cluster_collapse_detected"]:
        role_mapping = {0: "unknown", 1: "unknown"}
        role_report = dict(role_report)
        role_report.update(
            {
                "mapping_supported": False,
                "role_mapping": {"0": "unknown", "1": "unknown"},
                "mapping_blocker": "ACOUSTIC_CLUSTER_COLLAPSE_RISK",
            }
        )
    role_labeled = 0
    for segment, assignment in zip(enriched, assignments):
        if assignment not in (0, 1):
            continue
        role = role_mapping.get(int(assignment), "unknown")
        segment["speaker_role_candidate"] = role
        if role != "unknown":
            segment["speaker_role_confidence"] = round(
                min(0.95, float(segment.get("speaker_confidence") or 0) * 0.95), 4
            )
            role_labeled += 1
        else:
            segment["speaker_role_confidence"] = 0.0
    if collapse["cluster_collapse_detected"]:
        status = "DIARIZED_COLLAPSE_RISK"
    else:
        status = "DIARIZED_ROLE_MAPPED" if role_report.get("mapping_supported") else "DIARIZED_UNMAPPED"
    report = {
        "revision": DIARIZATION_REVISION,
        "status": status,
        **dict(engine),
        **assignment_report,
        **collapse,
        "role_labeled_segments": role_labeled,
        "role_labeled_ratio": round(role_labeled / max(1, len(enriched)), 4),
        **role_report,
        "hypotheses": hypotheses,
        "models": dict(models),
        "privacy": {
            "real_person_identity_claimed": False,
            "raw_audio_persisted": False,
            "voice_embedding_persisted": False,
            "cross_lesson_voice_profile_persisted": False,
        },
        "cost": {
            "paid_api": 0,
            "paid_cloud": 0,
            "gated_model_token_required": False,
            "temporary_pcm_audio_created": True,
            "temporary_voice_embeddings_created": engine.get("engine") == "segment-embedding-recluster",
            "persistent_audio_created": False,
            "persistent_voice_embeddings_created": False,
        },
    }
    return enriched, report


def diarize_transcript(
    video_path: str | os.PathLike[str],
    segments: Sequence[Mapping[str, Any]],
    work_dir: str | os.PathLike[str],
    *,
    enabled: bool = True,
    minimum_segments_per_cluster: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    copied = [copy.deepcopy(dict(segment)) for segment in segments]
    if not enabled:
        return copied, {
            "revision": DIARIZATION_REVISION,
            "status": "DISABLED",
            "segments_total": len(copied),
            "cost": {"paid_api": 0, "paid_cloud": 0},
        }
    existing = fallback._existing_label_report(copied)
    if existing is not None:
        existing = dict(existing)
        existing["requested_revision"] = DIARIZATION_REVISION
        return copied, existing
    if len(copied) < minimum_segments_per_cluster * 2:
        return copied, {
            "revision": DIARIZATION_REVISION,
            "status": "UNAVAILABLE_INSUFFICIENT_SEGMENTS",
            "segments_total": len(copied),
            "cost": {"paid_api": 0, "paid_cloud": 0},
        }

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    wav_path = work / "speaker-diarization-v3-16k.wav"
    cache_dir = Path(
        os.getenv("BRIDGE_SPEAKER_MODEL_CACHE", "").strip()
        or str(work / "speaker-model-cache-v3")
    )
    hypotheses: list[dict[str, Any]] = []
    try:
        previous._extract_pcm(Path(video_path), wav_path)
        segmentation = _ensure_segmentation(cache_dir)
        primary_embedding = _ensure_embedding(cache_dir, "3dspeaker")
        candidates = []

        def evaluate(model_id: str, embedding_path: Path):
            turns, engine = _run_sherpa_model(
                wav_path, segmentation, embedding_path, model_id=model_id, num_speakers=2
            )
            _, _, assignments = _assign_v3(copied, turns)
            diagnostics = _collapse_diagnostics(
                turns,
                assignments,
                expected_speakers=2,
                minimum_segments_per_cluster=minimum_segments_per_cluster,
            )
            score = _hypothesis_score(diagnostics)
            hypotheses.append({"model_id": model_id, "score": score, **diagnostics})
            candidates.append((score, turns, engine, diagnostics))
            return diagnostics

        primary_diag = evaluate("pyannote+3dspeaker", primary_embedding)
        if primary_diag["cluster_collapse_detected"]:
            evaluate("pyannote+nemo", _ensure_embedding(cache_dir, "nemo"))

        candidates.sort(key=lambda item: item[0], reverse=True)
        chosen_score, chosen_turns, chosen_engine, chosen_diag = candidates[0]

        if chosen_diag["cluster_collapse_detected"]:
            try:
                repaired_turns, repair_engine = repair_with_segment_embeddings(
                    wav_path,
                    copied,
                    primary_embedding,
                    read_pcm16_mono=previous._read_pcm16_mono,
                    sherpa_version=SHERPA_ONNX_VERSION,
                )
                _, _, repair_assignments = _assign_v3(copied, repaired_turns)
                repair_diag = _collapse_diagnostics(
                    repaired_turns,
                    repair_assignments,
                    expected_speakers=2,
                    minimum_segments_per_cluster=minimum_segments_per_cluster,
                )
                repair_score = _hypothesis_score(repair_diag)
                hypotheses.append(
                    {
                        "model_id": "3dspeaker-segment-recluster",
                        "score": repair_score,
                        **repair_diag,
                        "repair_qc": {
                            key: value
                            for key, value in repair_engine.items()
                            if key in {
                                "cluster_counts",
                                "centroid_cosine_similarity",
                                "median_cosine_margin",
                                "q25_cosine_margin",
                                "repair_validation_passed",
                                "embedding_segments_total",
                                "embedding_segments_accepted",
                                "embedding_segments_ambiguous",
                            }
                        },
                    }
                )
                if not repair_diag["cluster_collapse_detected"] and repair_score > chosen_score:
                    chosen_score, chosen_turns, chosen_engine = repair_score, repaired_turns, repair_engine
            except Exception as repair_exc:
                hypotheses.append(
                    {
                        "model_id": "3dspeaker-segment-recluster",
                        "status": "FAILED_SOFT",
                        "error": type(repair_exc).__name__,
                        "detail": str(repair_exc)[:400],
                    }
                )

        nemo_path = cache_dir / "nemo_en_titanet_small.onnx"
        model_report = {
            "segmentation": "sherpa-onnx-pyannote-segmentation-3-0",
            "segmentation_sha256": _sha256(segmentation),
            "primary_embedding": "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
            "primary_embedding_sha256": _sha256(primary_embedding),
            "compatibility_embedding": "nemo_en_titanet_small.onnx" if nemo_path.exists() else None,
            "compatibility_embedding_sha256": _sha256(nemo_path) if nemo_path.exists() else None,
        }
        chosen_engine = dict(chosen_engine)
        chosen_engine.update(
            {
                "hypothesis_score": chosen_score,
                "collapse_repair_attempted": any(
                    item.get("model_id") == "3dspeaker-segment-recluster"
                    for item in hypotheses
                ),
                "selected_hypothesis": chosen_engine.get("model_id"),
            }
        )
        return _build_report(
            copied,
            chosen_turns,
            chosen_engine,
            hypotheses=hypotheses,
            minimum_segments_per_cluster=minimum_segments_per_cluster,
            models=model_report,
        )
    except Exception as exc:
        fallback_segments, fallback_report = fallback.diarize_transcript(
            video_path,
            copied,
            work_dir,
            enabled=True,
            minimum_segments_per_cluster=minimum_segments_per_cluster,
        )
        fallback_report = dict(fallback_report)
        fallback_report.update(
            {
                "requested_revision": DIARIZATION_REVISION,
                "primary_engine_status": "FAILED_SOFT",
                "primary_engine": "sherpa-onnx-multihypothesis-v3",
                "primary_engine_error": type(exc).__name__,
                "primary_engine_detail": str(exc)[:400],
                "hypotheses": hypotheses,
                "fallback_used": True,
            }
        )
        return fallback_segments, fallback_report
    finally:
        wav_path.unlink(missing_ok=True)


__all__ = [
    "DIARIZATION_REVISION",
    "SHERPA_ONNX_VERSION",
    "SEGMENTATION_URL",
    "EMBEDDING_URL",
    "NEMO_EMBEDDING_URL",
    "THREED_EMBEDDING_URL",
    "_collapse_diagnostics",
    "_cluster_embeddings_two",
    "_hypothesis_score",
    "diarize_transcript",
]
