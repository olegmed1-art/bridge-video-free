#!/usr/bin/env python3
"""Zero-cost speaker diarization v2 for bridge lesson videos.

Primary engine: sherpa-onnx offline diarization with a pyannote segmentation
model converted to ONNX and NVIDIA NeMo TitaNet speaker embeddings. The engine
runs locally on CPU and needs no paid API or gated model token.

If the neural engine is unavailable, the proven conservative v1 acoustic
fallback is used. No raw audio, voice embedding, or cross-lesson voice profile
is persisted.
"""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request
import wave
from typing import Any, Mapping, Sequence

import bridge_speaker_diarization as fallback

DIARIZATION_REVISION = "bridge-sherpa-onnx-diarization-v2"
SHERPA_ONNX_VERSION = "1.13.4"

SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/"
    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/nemo_en_titanet_small.onnx"
)

TEACHER_CUES = (
    "диана", "как ты думаешь", "что ты думаешь", "почему ты",
    "давай посмотрим", "давай посчитаем", "давай подумаем",
    "обрати внимание", "запомни", "смотри", "посмотри", "подумай",
    "скажи", "посчитай", "покажи", "твоя задача", "тебе нужно",
    "что у тебя", "сколько у тебя", "какая у тебя", "какой у тебя",
    "правильно", "неправильно", "почему",
)
STUDENT_CUES = (
    "я не знаю", "я не помню", "не помню", "я думаю", "мне кажется",
    "я вижу", "я посчитала", "я посчитал", "я поняла", "я понял",
    "поняла", "понял", "я пропустила", "я пропустил", "я забыла",
    "я забыл", "у меня", "мне нужно", "я бы",
)


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 1024:
        return
    fd, tmp_name = tempfile.mkstemp(prefix=destination.name + ".", dir=destination.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "bridge-video-free-speaker-diarization-v2"}
        )
        with urllib.request.urlopen(request, timeout=180) as response, tmp.open("wb") as output:
            while True:
                block = response.read(8 * 1024 * 1024)
                if not block:
                    break
                output.write(block)
        if tmp.stat().st_size <= 1024:
            raise RuntimeError(f"downloaded model asset is unexpectedly small: {url}")
        tmp.replace(destination)
    finally:
        tmp.unlink(missing_ok=True)


def _ensure_models(cache_dir: Path) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    segmentation = cache_dir / "pyannote-segmentation-3.0.onnx"
    embedding = cache_dir / "nemo_en_titanet_small.onnx"
    if not segmentation.exists():
        archive = cache_dir / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
        _download(SEGMENTATION_URL, archive)
        with tarfile.open(archive, "r:bz2") as bundle:
            member = next(
                (item for item in bundle.getmembers()
                 if item.isfile() and item.name.endswith("/model.onnx")),
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
    _download(EMBEDDING_URL, embedding)
    return {
        "segmentation": segmentation,
        "embedding": embedding,
        "segmentation_sha256": _sha256(segmentation),
        "embedding_sha256": _sha256(embedding),
    }


def _extract_pcm(video_path: Path, wav_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(wav_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _read_pcm16_mono(wav_path: Path):
    import numpy as np
    with wave.open(str(wav_path), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise RuntimeError("unexpected diarization WAV format")
        sample_rate = reader.getframerate()
        raw = reader.readframes(reader.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    return samples, sample_rate


def _run_sherpa(
    wav_path: Path,
    models: Mapping[str, Any],
    *,
    num_speakers: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    samples, sample_rate = _read_pcm16_mono(wav_path)
    expected_rate = int(diarizer.sample_rate)
    if sample_rate != expected_rate:
        raise RuntimeError(
            f"speaker diarization sample-rate mismatch: {sample_rate} != {expected_rate}"
        )
    result = diarizer.process(samples)
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
    return turns, {
        "engine": "sherpa-onnx",
        "engine_version": getattr(sherpa_onnx, "__version__", SHERPA_ONNX_VERSION),
        "speaker_turns": len(turns),
        "speaker_ids": speakers,
        "num_speakers_requested": int(num_speakers),
    }


def _speaker_label(index: int) -> str:
    return f"SPEAKER_{chr(ord('A') + index)}" if 0 <= index < 26 else f"SPEAKER_{index}"


def _assign_speakers_from_turns(
    segments: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[int | None]]:
    copied = [copy.deepcopy(dict(segment)) for segment in segments]
    speaker_values = sorted({int(turn["speaker"]) for turn in turns})
    canonical = {speaker: index for index, speaker in enumerate(speaker_values)}
    assignments: list[int | None] = []
    mapped = 0
    ambiguous = 0
    confidence_values: list[float] = []
    ordered_turns = sorted(turns, key=lambda item: (float(item["start"]), float(item["end"])))
    pointer = 0
    for segment in copied:
        try:
            start = float(segment.get("start") or 0)
            end = float(segment.get("end") or start)
        except (TypeError, ValueError):
            assignments.append(None)
            continue
        if end <= start:
            assignments.append(None)
            continue
        while pointer < len(ordered_turns) and float(ordered_turns[pointer]["end"]) <= start:
            pointer += 1
        overlaps: Counter[int] = Counter()
        j = pointer
        while j < len(ordered_turns) and float(ordered_turns[j]["start"]) < end:
            turn = ordered_turns[j]
            overlap = max(
                0.0,
                min(end, float(turn["end"])) - max(start, float(turn["start"])),
            )
            if overlap > 0:
                overlaps[int(turn["speaker"])] += overlap
            j += 1
        if not overlaps:
            assignments.append(None)
            continue
        ranked = overlaps.most_common()
        best_speaker, best_overlap = ranked[0]
        total_overlap = sum(overlaps.values())
        duration = max(0.05, end - start)
        dominance = best_overlap / max(0.05, total_overlap)
        coverage = min(1.0, total_overlap / duration)
        confidence = max(0.0, min(1.0, 0.65 * dominance + 0.35 * coverage))
        canonical_index = canonical[best_speaker]
        if best_overlap < 0.18 or dominance < 0.62 or confidence < 0.58:
            assignments.append(None)
            ambiguous += 1
            continue
        label = _speaker_label(canonical_index)
        segment["speaker"] = label
        segment["speaker_cluster"] = label
        segment["speaker_confidence"] = round(confidence, 4)
        segment["speaker_assignment_revision"] = DIARIZATION_REVISION
        segment["speaker_overlap_seconds"] = round(best_overlap, 3)
        segment["speaker_overlap_dominance"] = round(dominance, 4)
        assignments.append(canonical_index)
        confidence_values.append(confidence)
        mapped += 1
    return copied, {
        "segments_total": len(copied),
        "segments_labeled": mapped,
        "segments_ambiguous": ambiguous,
        "speaker_labeled_ratio": round(mapped / max(1, len(copied)), 4),
        "mean_assignment_confidence": (
            round(sum(confidence_values) / len(confidence_values), 4)
            if confidence_values else 0.0
        ),
    }, assignments


def _role_scores(text: object) -> tuple[float, float]:
    low = _norm(text).casefold()
    teacher = float(sum(cue in low for cue in TEACHER_CUES))
    student = float(sum(cue in low for cue in STUDENT_CUES))
    teacher += 1.5 * low.count("диана")
    if "?" in str(text or ""):
        teacher += 0.5
    if low.startswith(("да", "нет", "не знаю", "думаю", "наверное", "возможно")):
        student += 0.35
    return teacher, student


def _map_roles_v2(
    segments: Sequence[Mapping[str, Any]], assignments: Sequence[int | None]
) -> tuple[dict[int, str], dict[str, Any]]:
    scores: dict[int, Counter[str]] = {0: Counter(), 1: Counter()}
    examples = {
        0: {"teacher": [], "student": []},
        1: {"teacher": [], "student": []},
    }
    word_counts = Counter()
    observed = Counter()
    for segment, assignment in zip(segments, assignments):
        if assignment not in (0, 1):
            continue
        assignment = int(assignment)
        observed[assignment] += 1
        text = _norm(segment.get("text") or segment.get("analysis_text"))
        word_counts[assignment] += len(text.split())
        teacher, student = _role_scores(text)
        scores[assignment]["teacher"] += teacher
        scores[assignment]["student"] += student
        if teacher > student and teacher > 0:
            examples[assignment]["teacher"].append(text[:180])
        if student > teacher and student > 0:
            examples[assignment]["student"].append(text[:180])
    mapping = {0: "unknown", 1: "unknown"}
    deltas = {
        cluster: float(scores[cluster]["teacher"] - scores[cluster]["student"])
        for cluster in (0, 1)
    }
    teacher_cluster = max((0, 1), key=lambda cluster: deltas[cluster])
    student_cluster = 1 - teacher_cluster
    teacher_margin = deltas[teacher_cluster]
    student_margin = float(
        scores[student_cluster]["student"] - scores[student_cluster]["teacher"]
    )
    teacher_evidence = float(scores[teacher_cluster]["teacher"])
    student_evidence = float(scores[student_cluster]["student"])
    supported = (
        observed[teacher_cluster] >= 8
        and observed[student_cluster] >= 8
        and teacher_margin >= 3.0
        and student_margin >= 1.5
        and teacher_evidence >= 4.0
        and student_evidence >= 2.0
    )
    if not supported:
        supported = (
            observed[teacher_cluster] >= 12
            and observed[student_cluster] >= 8
            and teacher_margin >= 5.0
            and teacher_evidence >= 6.0
            and student_evidence >= 1.5
            and word_counts[teacher_cluster] >= max(1, int(word_counts[student_cluster] * 1.2))
        )
    if supported:
        mapping[teacher_cluster] = "teacher"
        mapping[student_cluster] = "student"
    return mapping, {
        "mapping_supported": supported,
        "role_mapping": {str(key): value for key, value in mapping.items()},
        "cluster_scores": {
            str(key): {
                "teacher": round(float(scores[key]["teacher"]), 3),
                "student": round(float(scores[key]["student"]), 3),
                "delta_teacher_minus_student": round(deltas[key], 3),
                "segments": int(observed[key]),
                "words": int(word_counts[key]),
            }
            for key in (0, 1)
        },
        "evidence_examples": {
            str(cluster): {role: values[:5] for role, values in role_examples.items()}
            for cluster, role_examples in examples.items()
        },
    }


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
    wav_path = work / "speaker-diarization-v2-16k.wav"
    cache_dir = Path(
        os.getenv("BRIDGE_SPEAKER_MODEL_CACHE", "").strip()
        or str(work / "speaker-model-cache-v2")
    )
    try:
        _extract_pcm(Path(video_path), wav_path)
        models = _ensure_models(cache_dir)
        turns, engine = _run_sherpa(wav_path, models, num_speakers=2)
        enriched, assignment_report, assignments = _assign_speakers_from_turns(copied, turns)
        if assignment_report["segments_labeled"] < minimum_segments_per_cluster * 2:
            raise RuntimeError("too few transcript segments received speaker labels")
        role_mapping, role_report = _map_roles_v2(enriched, assignments)
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
        status = "DIARIZED_ROLE_MAPPED" if role_report["mapping_supported"] else "DIARIZED_UNMAPPED"
        report = {
            "revision": DIARIZATION_REVISION,
            "status": status,
            **engine,
            **assignment_report,
            "role_labeled_segments": role_labeled,
            "role_labeled_ratio": round(role_labeled / max(1, len(enriched)), 4),
            **role_report,
            "models": {
                "segmentation": "sherpa-onnx-pyannote-segmentation-3-0",
                "segmentation_sha256": models["segmentation_sha256"],
                "embedding": "nemo_en_titanet_small.onnx",
                "embedding_sha256": models["embedding_sha256"],
            },
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
                "persistent_audio_created": False,
            },
        }
        return enriched, report
    except Exception as exc:
        fallback_segments, fallback_report = fallback.diarize_transcript(
            video_path,
            copied,
            work_dir,
            enabled=True,
            minimum_segments_per_cluster=minimum_segments_per_cluster,
        )
        fallback_report = dict(fallback_report)
        fallback_report.update({
            "requested_revision": DIARIZATION_REVISION,
            "primary_engine_status": "FAILED_SOFT",
            "primary_engine": "sherpa-onnx",
            "primary_engine_error": type(exc).__name__,
            "primary_engine_detail": str(exc)[:400],
            "fallback_used": True,
        })
        return fallback_segments, fallback_report
    finally:
        wav_path.unlink(missing_ok=True)


__all__ = [
    "DIARIZATION_REVISION", "SHERPA_ONNX_VERSION", "SEGMENTATION_URL",
    "EMBEDDING_URL", "diarize_transcript",
]
