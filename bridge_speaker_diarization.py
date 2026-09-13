#!/usr/bin/env python3
"""Zero-cost, local, conservative two-speaker diarization for bridge lessons.

The module uses FFmpeg plus NumPy only.  It clusters short acoustic summaries
for transcript segments and then maps anonymous clusters to ``teacher`` or
``student`` only when lexical evidence is sufficiently asymmetric.  It never
claims a real-person identity and never persists raw audio or voice embeddings.

This is a fallback for recordings without Zoom speaker labels.  Existing
speaker labels are preserved and preferred.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import hashlib
import math
import os
from pathlib import Path
import subprocess
import wave
from typing import Any, Iterable, Mapping, Sequence

DIARIZATION_REVISION = "bridge-local-diarization-v1"


def _diagnostic_code(exc: Exception) -> str:
    """Map internal failures to bounded, non-sensitive field diagnostics."""
    if isinstance(exc, (ImportError, ModuleNotFoundError, FileNotFoundError)):
        return "OPTIONAL_RUNTIME_UNAVAILABLE"
    if isinstance(exc, subprocess.CalledProcessError):
        return "AUDIO_EXTRACTION_FAILED"
    detail = str(exc)
    if detail == "insufficient voiced segments":
        return "INSUFFICIENT_VOICED_SEGMENTS"
    if detail == "acoustic clusters not sufficiently separated":
        return "ACOUSTIC_CLUSTERS_NOT_SEPARATED"
    if detail == "unexpected diarization WAV format":
        return "AUDIO_FORMAT_UNSUPPORTED"
    return "DIARIZATION_ENGINE_FAILED"

TEACHER_CUES = (
    "диана",
    "как ты думаешь",
    "почему ты",
    "давай посмотрим",
    "давай посчитаем",
    "обрати внимание",
    "запомни",
    "правильно",
    "неправильно",
    "твоя задача",
    "тебе нужно",
)

STUDENT_CUES = (
    "я не знаю",
    "я не помню",
    "я думаю",
    "я вижу",
    "мне кажется",
    "я посчитала",
    "я посчитал",
    "я поняла",
    "я понял",
    "я пропустила",
    "я забыла",
)


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _role_scores(text: object) -> tuple[int, int]:
    low = _norm(text).casefold()
    return (
        sum(cue in low for cue in TEACHER_CUES),
        sum(cue in low for cue in STUDENT_CUES),
    )


def _extract_pcm(video_path: Path, wav_path: Path, sample_rate: int = 16000) -> None:
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video_path), "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", str(wav_path),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _read_clip(
    reader: wave.Wave_read,
    start_seconds: float,
    end_seconds: float,
    *,
    maximum_seconds: float = 6.0,
):
    import numpy as np

    rate = reader.getframerate()
    total = reader.getnframes()
    start = max(0.0, float(start_seconds or 0))
    end = max(start, float(end_seconds or start))
    if end - start > maximum_seconds:
        centre = (start + end) / 2.0
        start = max(0.0, centre - maximum_seconds / 2.0)
        end = centre + maximum_seconds / 2.0
    start_frame = min(total, int(start * rate))
    frame_count = max(0, min(total - start_frame, int((end - start) * rate)))
    if frame_count <= int(rate * 0.35):
        return None
    reader.setpos(start_frame)
    raw = reader.readframes(frame_count)
    if not raw:
        return None
    samples = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if samples.size <= int(rate * 0.35):
        return None
    return samples


def _feature_vector(samples, sample_rate: int):
    """Return a compact speaker-oriented vector, or ``None`` for silence."""
    import numpy as np

    if samples is None or samples.size < int(sample_rate * 0.35):
        return None
    samples = samples - float(np.mean(samples))
    peak = float(np.max(np.abs(samples)))
    rms_all = float(np.sqrt(np.mean(samples * samples) + 1e-12))
    if peak < 0.003 or rms_all < 0.001:
        return None

    frame_len = int(sample_rate * 0.032)
    hop = int(sample_rate * 0.016)
    if samples.size < frame_len:
        return None
    starts = np.arange(0, samples.size - frame_len + 1, hop, dtype=int)
    if starts.size > 240:
        starts = starts[np.linspace(0, starts.size - 1, 240).astype(int)]
    window = np.hanning(frame_len).astype("float32")
    freqs = np.fft.rfftfreq(frame_len, 1.0 / sample_rate)
    band_edges = (0, 250, 700, 1600, 3200, 8000)
    frame_features = []
    for start in starts:
        frame = samples[start:start + frame_len]
        rms = float(np.sqrt(np.mean(frame * frame) + 1e-12))
        if rms < 0.001:
            continue
        zcr = float(np.mean(np.signbit(frame[1:]) != np.signbit(frame[:-1])))
        spectrum = np.abs(np.fft.rfft(frame * window)) + 1e-9
        power = spectrum * spectrum
        power_sum = float(np.sum(power)) + 1e-12
        centroid = float(np.sum(freqs * power) / power_sum) / (sample_rate / 2.0)
        cumsum = np.cumsum(power)
        roll_idx = int(np.searchsorted(cumsum, cumsum[-1] * 0.85))
        rolloff = float(freqs[min(roll_idx, len(freqs) - 1)]) / (sample_rate / 2.0)
        flatness = float(np.exp(np.mean(np.log(spectrum))) / np.mean(spectrum))
        bands = []
        for low, high in zip(band_edges[:-1], band_edges[1:]):
            mask = (freqs >= low) & (freqs < high)
            bands.append(float(np.sum(power[mask]) / power_sum))
        # A coarse pitch/periodicity proxy; no identity claim is based on it.
        decimated = frame[::2]
        autocorr = np.correlate(decimated, decimated, mode="full")[len(decimated) - 1:]
        if autocorr[0] > 1e-8:
            autocorr = autocorr / autocorr[0]
            lo = max(1, int((sample_rate / 2) / 350))
            hi = min(len(autocorr), int((sample_rate / 2) / 75))
            periodicity = float(np.max(autocorr[lo:hi])) if hi > lo else 0.0
        else:
            periodicity = 0.0
        frame_features.append([
            math.log(rms + 1e-8), zcr, centroid, rolloff, flatness,
            *bands, periodicity,
        ])
    if len(frame_features) < 4:
        return None
    matrix = np.asarray(frame_features, dtype="float64")
    med = np.median(matrix, axis=0)
    spread = np.median(np.abs(matrix - med), axis=0)
    return np.concatenate([med, spread])


def _robust_scale(matrix):
    import numpy as np

    centre = np.median(matrix, axis=0)
    scale = np.median(np.abs(matrix - centre), axis=0)
    scale = np.where(scale < 1e-4, np.std(matrix, axis=0), scale)
    scale = np.where(scale < 1e-4, 1.0, scale)
    return (matrix - centre) / scale


def _deterministic_kmeans(matrix, iterations: int = 30):
    import numpy as np

    if len(matrix) < 4:
        raise ValueError("not enough feature rows")
    scaled = _robust_scale(matrix)
    mean = np.mean(scaled, axis=0)
    first = int(np.argmax(np.linalg.norm(scaled - mean, axis=1)))
    second = int(np.argmax(np.linalg.norm(scaled - scaled[first], axis=1)))
    centres = np.vstack([scaled[first], scaled[second]])
    labels = np.zeros(len(scaled), dtype=int)
    for _ in range(iterations):
        distances = np.linalg.norm(scaled[:, None, :] - centres[None, :, :], axis=2)
        new_labels = np.argmin(distances, axis=1)
        if np.array_equal(new_labels, labels) and _ > 0:
            break
        labels = new_labels
        new_centres = []
        for cluster in (0, 1):
            members = scaled[labels == cluster]
            if not len(members):
                new_centres.append(centres[cluster])
            else:
                new_centres.append(np.mean(members, axis=0))
        centres = np.vstack(new_centres)
    distances = np.linalg.norm(scaled[:, None, :] - centres[None, :, :], axis=2)
    own = distances[np.arange(len(labels)), labels]
    other = distances[np.arange(len(labels)), 1 - labels]
    confidence = (other - own) / (other + own + 1e-8)
    separation = float(np.linalg.norm(centres[0] - centres[1]))
    within = float(np.mean(own)) + 1e-8
    separation_ratio = separation / within
    return labels, confidence, separation_ratio


def _map_roles(segments: Sequence[Mapping[str, Any]], labels: Sequence[int]) -> tuple[dict[int, str], dict[str, Any]]:
    cluster_scores: dict[int, Counter[str]] = {0: Counter(), 1: Counter()}
    cluster_examples: dict[int, list[str]] = {0: [], 1: []}
    for segment, label in zip(segments, labels):
        teacher, student = _role_scores(segment.get("text") or segment.get("analysis_text"))
        cluster_scores[int(label)]["teacher"] += teacher
        cluster_scores[int(label)]["student"] += student
        if teacher or student:
            cluster_examples[int(label)].append(_norm(segment.get("text"))[:180])

    mapping: dict[int, str] = {0: "unknown", 1: "unknown"}
    # Require asymmetric evidence across the two clusters.  A single cue is not enough.
    teacher_cluster = max((0, 1), key=lambda value: cluster_scores[value]["teacher"] - cluster_scores[value]["student"])
    student_cluster = 1 - teacher_cluster
    teacher_margin = cluster_scores[teacher_cluster]["teacher"] - cluster_scores[teacher_cluster]["student"]
    student_margin = cluster_scores[student_cluster]["student"] - cluster_scores[student_cluster]["teacher"]
    if teacher_margin >= 3 and student_margin >= 2:
        mapping[teacher_cluster] = "teacher"
        mapping[student_cluster] = "student"
    return mapping, {
        "cluster_scores": {str(key): dict(value) for key, value in cluster_scores.items()},
        "role_mapping": {str(key): value for key, value in mapping.items()},
        "mapping_supported": set(mapping.values()) == {"teacher", "student"},
        "evidence_examples": {str(key): values[:5] for key, values in cluster_examples.items()},
    }


def _existing_label_report(segments: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    labels = [str(segment.get("speaker") or "").strip() for segment in segments]
    present = [label for label in labels if label]
    if len(present) < max(2, int(len(segments) * 0.5)):
        return None
    counts = Counter(present)
    return {
        "revision": DIARIZATION_REVISION,
        "status": "EXISTING_SPEAKER_LABELS_PRESERVED",
        "speaker_clusters": dict(counts),
        "segments_total": len(segments),
        "segments_labeled": len(present),
        "role_mapping_supported": False,
        "cost": {"paid_api": 0, "persistent_audio_created": False},
    }


def diarize_transcript(
    video_path: str | os.PathLike[str],
    segments: Sequence[Mapping[str, Any]],
    work_dir: str | os.PathLike[str],
    *,
    enabled: bool = True,
    minimum_segments_per_cluster: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return diarized segment copies and a conservative report.

    Failure is soft: the original segments are returned with an ``UNAVAILABLE``
    report so technical video processing can still finish, while methodology
    readiness remains partial.
    """
    copied = [copy.deepcopy(dict(segment)) for segment in segments]
    if not enabled:
        return copied, {
            "revision": DIARIZATION_REVISION,
            "status": "DISABLED",
            "segments_total": len(copied),
            "cost": {"paid_api": 0, "persistent_audio_created": False},
        }
    existing = _existing_label_report(copied)
    if existing is not None:
        return copied, existing
    if len(copied) < minimum_segments_per_cluster * 2:
        return copied, {
            "revision": DIARIZATION_REVISION,
            "status": "UNAVAILABLE_INSUFFICIENT_SEGMENTS",
            "segments_total": len(copied),
            "cost": {"paid_api": 0, "persistent_audio_created": False},
        }

    wav_path = Path(work_dir) / "speaker-diarization-16k.wav"
    try:
        import numpy as np
        _extract_pcm(Path(video_path), wav_path)
        usable_segments: list[dict[str, Any]] = []
        features = []
        original_indices = []
        with wave.open(str(wav_path), "rb") as reader:
            if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
                raise RuntimeError("unexpected diarization WAV format")
            sample_rate = reader.getframerate()
            for index, segment in enumerate(copied):
                try:
                    start = float(segment.get("start") or 0)
                    end = float(segment.get("end") or start)
                except (TypeError, ValueError):
                    continue
                clip = _read_clip(reader, start, end)
                vector = _feature_vector(clip, sample_rate)
                if vector is None:
                    continue
                features.append(vector)
                usable_segments.append(segment)
                original_indices.append(index)
        if len(features) < minimum_segments_per_cluster * 2:
            raise RuntimeError("insufficient voiced segments")
        matrix = np.vstack(features)
        labels, confidences, separation_ratio = _deterministic_kmeans(matrix)
        counts = Counter(int(value) for value in labels)
        if min(counts.values()) < minimum_segments_per_cluster or separation_ratio < 0.65:
            raise RuntimeError("acoustic clusters not sufficiently separated")
        role_mapping, role_report = _map_roles(usable_segments, labels)
        for segment, original_index, label, confidence in zip(usable_segments, original_indices, labels, confidences):
            cluster = int(label)
            cluster_label = "SPEAKER_A" if cluster == 0 else "SPEAKER_B"
            role = role_mapping.get(cluster, "unknown")
            copied[original_index]["speaker"] = cluster_label
            copied[original_index]["speaker_cluster"] = cluster_label
            copied[original_index]["speaker_confidence"] = round(max(0.0, min(1.0, float(confidence))), 4)
            copied[original_index]["speaker_role_candidate"] = role
            copied[original_index]["speaker_role_confidence"] = (
                round(max(0.0, min(0.9, float(confidence) * 0.9)), 4)
                if role != "unknown" else 0.0
            )
            copied[original_index]["speaker_assignment_revision"] = DIARIZATION_REVISION
        status = "DIARIZED_ROLE_MAPPED" if role_report["mapping_supported"] else "DIARIZED_UNMAPPED"
        report = {
            "revision": DIARIZATION_REVISION,
            "status": status,
            "segments_total": len(copied),
            "segments_with_acoustic_features": len(features),
            "segments_labeled": len(features),
            "speaker_clusters": {
                "SPEAKER_A": counts.get(0, 0),
                "SPEAKER_B": counts.get(1, 0),
            },
            "separation_ratio": round(separation_ratio, 4),
            "mean_assignment_confidence": round(float(np.mean(np.clip(confidences, 0, 1))), 4),
            "role_mapping_supported": role_report["mapping_supported"],
            **role_report,
            "privacy": {
                "real_person_identity_claimed": False,
                "raw_audio_persisted": False,
                "voice_embedding_persisted": False,
                "cross_lesson_voice_profile_persisted": False,
            },
            "cost": {
                "paid_api": 0,
                "paid_cloud": 0,
                "temporary_pcm_audio_created": True,
                "persistent_audio_created": False,
            },
        }
        return copied, report
    except Exception as exc:
        return copied, {
            "revision": DIARIZATION_REVISION,
            "status": "UNAVAILABLE",
            "reason": type(exc).__name__,
            "diagnostic_code": _diagnostic_code(exc),
            "detail": str(exc)[:300],
            "segments_total": len(copied),
            "cost": {"paid_api": 0, "persistent_audio_created": False},
        }
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["DIARIZATION_REVISION", "diarize_transcript"]
