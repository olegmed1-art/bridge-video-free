"""Pure acoustic QC helpers for Bridge speaker diarization v3."""
from __future__ import annotations

from collections import Counter
import math
from typing import Any, Mapping, Sequence


def _turn_distribution(turns: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    duration = Counter()
    counts = Counter()
    transitions = 0
    previous_speaker: int | None = None
    ordered = sorted(turns, key=lambda item: (float(item["start"]), float(item["end"])))
    for item in ordered:
        speaker = int(item["speaker"])
        seconds = max(0.0, float(item["end"]) - float(item["start"]))
        duration[speaker] += seconds
        counts[speaker] += 1
        if previous_speaker is not None and previous_speaker != speaker:
            transitions += 1
        previous_speaker = speaker
    total_duration = float(sum(duration.values()))
    total_turns = int(sum(counts.values()))
    speakers = sorted(counts)
    minor_duration = min((duration[s] for s in speakers), default=0.0)
    minor_turns = min((counts[s] for s in speakers), default=0)
    return {
        "speaker_turn_counts": {str(s): int(counts[s]) for s in speakers},
        "speaker_turn_duration_seconds": {
            str(s): round(float(duration[s]), 3) for s in speakers
        },
        "minor_turn_ratio": round(minor_turns / max(1, total_turns), 4),
        "minor_duration_ratio": round(minor_duration / max(1e-9, total_duration), 4),
        "speaker_transitions": int(transitions),
        "speech_duration_seconds": round(total_duration, 3),
    }


def _assignment_distribution(assignments: Sequence[int | None]) -> dict[str, Any]:
    counts = Counter(int(value) for value in assignments if value is not None)
    labeled = int(sum(counts.values()))
    speakers = sorted(counts)
    minor = min((counts[s] for s in speakers), default=0)
    return {
        "speaker_segment_counts": {str(s): int(counts[s]) for s in speakers},
        "segments_labeled": labeled,
        "minor_segment_count": int(minor),
        "minor_segment_ratio": round(minor / max(1, labeled), 4),
    }


def _collapse_diagnostics(
    turns: Sequence[Mapping[str, Any]],
    assignments: Sequence[int | None],
    *,
    expected_speakers: int = 2,
    minimum_segments_per_cluster: int = 8,
) -> dict[str, Any]:
    """Detect only extreme near-empty-cluster failures, not normal imbalance."""
    turn = _turn_distribution(turns)
    segment = _assignment_distribution(assignments)
    active_turn_speakers = len(turn["speaker_turn_counts"])
    active_segment_speakers = len(segment["speaker_segment_counts"])
    minimum_segment_count = max(
        int(minimum_segments_per_cluster),
        int(math.ceil(segment["segments_labeled"] * 0.015)),
    )
    collapsed = False
    reasons: list[str] = []
    if expected_speakers > 1 and active_turn_speakers < expected_speakers:
        collapsed = True
        reasons.append("TOO_FEW_ACTIVE_TURN_CLUSTERS")
    if expected_speakers > 1 and active_segment_speakers < expected_speakers:
        collapsed = True
        reasons.append("TOO_FEW_ACTIVE_SEGMENT_CLUSTERS")
    if (
        segment["segments_labeled"] >= 100
        and segment["minor_segment_count"] < minimum_segment_count
    ):
        collapsed = True
        reasons.append("MINOR_SEGMENT_CLUSTER_NEAR_EMPTY")
    if (
        turn["speech_duration_seconds"] >= 300
        and turn["minor_duration_ratio"] < 0.01
        and turn["minor_turn_ratio"] < 0.02
    ):
        collapsed = True
        reasons.append("MINOR_SPEECH_CLUSTER_NEAR_EMPTY")
    return {
        **turn,
        **segment,
        "expected_speakers": int(expected_speakers),
        "minimum_segment_count_for_noncollapse": int(minimum_segment_count),
        "cluster_collapse_detected": bool(collapsed),
        "collapse_reasons": reasons,
    }


def _hypothesis_score(diagnostics: Mapping[str, Any]) -> float:
    """Rank acoustic hypotheses without transcript words or identity."""
    penalty = 10.0 if diagnostics.get("cluster_collapse_detected") else 0.0
    minor_seg = min(0.25, float(diagnostics.get("minor_segment_ratio") or 0.0))
    minor_dur = min(0.25, float(diagnostics.get("minor_duration_ratio") or 0.0))
    transitions = min(1.0, float(diagnostics.get("speaker_transitions") or 0) / 30.0)
    coverage = min(1.0, float(diagnostics.get("segments_labeled") or 0) / 100.0)
    return round(3.0 * minor_seg + 2.0 * minor_dur + transitions + coverage - penalty, 6)


def _normalize_rows(matrix):
    import numpy as np

    x = np.asarray(matrix, dtype="float32")
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    return x / norms


def _cluster_embeddings_two(embeddings) -> tuple[list[int], dict[str, Any]]:
    """Deterministic cosine 2-means used only as a collapse repair path."""
    import numpy as np

    x = _normalize_rows(embeddings)
    if x.ndim != 2 or x.shape[0] < 16:
        raise RuntimeError("insufficient speaker embeddings for repair")
    center = _normalize_rows([x.mean(axis=0)])[0]
    first = int(np.argmin(x @ center))
    second = int(np.argmin(x @ x[first]))
    if first == second:
        second = int((first + 1) % len(x))
    centroids = _normalize_rows([x[first], x[second]])
    labels = np.zeros(len(x), dtype=int)
    for iteration in range(20):
        similarities = x @ centroids.T
        new_labels = np.argmax(similarities, axis=1)
        if np.array_equal(labels, new_labels) and iteration > 0:
            break
        labels = new_labels
        if len(set(labels.tolist())) < 2:
            raise RuntimeError("speaker embedding repair collapsed to one cluster")
        centroids = _normalize_rows(
            [x[labels == cluster].mean(axis=0) for cluster in (0, 1)]
        )
    similarities = x @ centroids.T
    own = similarities[np.arange(len(x)), labels]
    other = similarities[np.arange(len(x)), 1 - labels]
    margins = own - other
    counts = Counter(int(v) for v in labels)
    centroid_similarity = float(centroids[0] @ centroids[1])
    median_margin = float(np.median(margins))
    q25_margin = float(np.quantile(margins, 0.25))
    min_count = min(counts.values())
    valid = (
        min_count >= 8
        and centroid_similarity <= 0.88
        and median_margin >= 0.06
        and q25_margin >= 0.015
    )
    return labels.tolist(), {
        "cluster_counts": {str(k): int(v) for k, v in sorted(counts.items())},
        "centroid_cosine_similarity": round(centroid_similarity, 4),
        "median_cosine_margin": round(median_margin, 4),
        "q25_cosine_margin": round(q25_margin, 4),
        "repair_validation_passed": bool(valid),
    }


def _cluster_embeddings_k(embeddings, clusters: int) -> tuple[list[int], dict[str, Any]]:
    """Deterministic cosine k-means used by the open-set count selector."""
    import numpy as np

    x = _normalize_rows(embeddings)
    if x.ndim != 2 or clusters < 1 or x.shape[0] < clusters * 8:
        raise RuntimeError("insufficient embeddings for candidate speaker count")
    center = _normalize_rows([x.mean(axis=0)])[0]
    seeds = [int(np.argmin(x @ center))]
    while len(seeds) < clusters:
        similarity = x @ x[seeds].T
        nearest = np.max(similarity, axis=1)
        for seed in seeds:
            nearest[seed] = 1.0
        seeds.append(int(np.argmin(nearest)))
    centroids = _normalize_rows(x[seeds])
    labels = np.full(len(x), -1, dtype=int)
    for _ in range(30):
        similarities = x @ centroids.T
        new_labels = np.argmax(similarities, axis=1)
        if np.array_equal(labels, new_labels):
            break
        labels = new_labels
        if len(set(labels.tolist())) != clusters:
            raise RuntimeError("candidate clustering contains an empty cluster")
        centroids = _normalize_rows(
            [x[labels == cluster].mean(axis=0) for cluster in range(clusters)]
        )
    similarities = x @ centroids.T
    own = similarities[np.arange(len(x)), labels]
    if clusters == 1:
        margins = np.ones(len(x), dtype="float32")
        max_centroid_similarity = None
    else:
        masked = similarities.copy()
        masked[np.arange(len(x)), labels] = -2.0
        margins = own - np.max(masked, axis=1)
        pairs = centroids @ centroids.T
        pairs[np.eye(clusters, dtype=bool)] = -2.0
        max_centroid_similarity = float(np.max(pairs))
    counts = Counter(int(value) for value in labels)
    minimum = min(counts.values())
    median_margin = float(np.median(margins))
    q25_margin = float(np.quantile(margins, 0.25))
    valid = clusters == 1 or (
        minimum >= 8
        and max_centroid_similarity is not None
        and max_centroid_similarity <= 0.88
        and median_margin >= 0.06
        and q25_margin >= 0.015
    )
    score = (
        0.0
        if clusters == 1
        else median_margin
        + q25_margin
        + 0.15 * minimum / len(x)
        - 0.035 * max(0, clusters - 2)
    )
    return labels.tolist(), {
        "candidate_count": clusters,
        "cluster_counts": {str(k): int(v) for k, v in sorted(counts.items())},
        "maximum_centroid_cosine_similarity": (
            None if max_centroid_similarity is None else round(max_centroid_similarity, 4)
        ),
        "median_cosine_margin": round(median_margin, 4),
        "q25_cosine_margin": round(q25_margin, 4),
        "minimum_cluster_segments": int(minimum),
        "candidate_valid": bool(valid),
        "selection_score": round(float(score), 6),
    }


def _cluster_embeddings_open_set(
    embeddings, *, max_speakers: int = 4
) -> tuple[list[int], dict[str, Any]]:
    """Compare 1..N acoustic hypotheses and prove a bounded speaker count."""
    import numpy as np

    x = _normalize_rows(embeddings)
    upper = min(int(max_speakers), int(x.shape[0] // 8))
    if upper < 2:
        raise RuntimeError("insufficient embeddings for open-set speaker count")
    candidates = []
    labels_by_count = {}
    for count in range(1, upper + 1):
        labels, report = _cluster_embeddings_k(x, count)
        candidates.append(report)
        labels_by_count[count] = labels
    valid = [item for item in candidates if item["candidate_count"] > 1 and item["candidate_valid"]]
    if not valid:
        raise RuntimeError("no separated open-set speaker-count hypothesis")
    ranked = sorted(valid, key=lambda item: (-item["selection_score"], item["candidate_count"]))
    selected = ranked[0]
    selected_count = int(selected["candidate_count"])
    runner_up = ranked[1] if len(ranked) > 1 else None
    margin = selected["selection_score"] - (runner_up["selection_score"] if runner_up else 0.0)
    # Higher-count solutions must win materially; this prevents one voice from
    # being split into several cosmetically tighter clusters.
    if runner_up and selected_count > int(runner_up["candidate_count"]) and margin < 0.08:
        selected = runner_up
        selected_count = int(selected["candidate_count"])
        margin = float(ranked[0]["selection_score"] - selected["selection_score"])
    proof = {
        "mode": "OPEN_SET",
        "candidate_counts": candidates,
        "selected_count": selected_count,
        "selection_margin": round(float(margin), 6),
        "collapse_check": "PASS",
        "fragmentation_check": "PASS",
        "mixing_check": "PASS",
    }
    return labels_by_count[selected_count], proof


__all__ = [
    "_collapse_diagnostics",
    "_cluster_embeddings_two",
    "_cluster_embeddings_k",
    "_cluster_embeddings_open_set",
    "_hypothesis_score",
    "_normalize_rows",
]
