"""Independent, fail-closed verifier for anonymous speakers and semantic roles.

The verifier consumes only persisted result artifacts.  It does not trust the
producer's aggregate coverage, speaker count, or ``DIARIZED_ROLE_MAPPED``
status: every value below is recomputed from transcript rows.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "universal-video-speaker-role-proof-v1"
ALLOWED_ROLES = frozenset({"teacher", "student"})


def _duration(row: Mapping[str, Any]) -> float:
    try:
        start, end = float(row.get("start")), float(row.get("end"))
    except (TypeError, ValueError):
        return 0.0
    return end - start if math.isfinite(start) and math.isfinite(end) and end > start else 0.0


def _probability(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def verify_speaker_roles(
    rows: Sequence[Mapping[str, Any]],
    producer_report: Mapping[str, Any],
    *,
    expected_speakers: int = 2,
    minimum_coverage: float = 0.80,
    minimum_role_confidence: float = 0.65,
    minimum_cluster_segments: int = 2,
    minimum_cluster_duration_share: float = 0.03,
) -> dict[str, Any]:
    """Recompute coverage, separation blockers, and role-map status."""
    if not rows:
        raise ValueError("transcript rows are required")
    if expected_speakers < 1:
        raise ValueError("expected_speakers must be positive")

    total_duration = sum(_duration(row) for row in rows)
    labels = [str(row.get("speaker") or "").strip() for row in rows]
    clusters = sorted({label for label in labels if label})
    labeled_segments = sum(bool(label) for label in labels)
    labeled_duration = sum(_duration(row) for row, label in zip(rows, labels) if label)
    segment_coverage = labeled_segments / len(rows)
    duration_coverage = labeled_duration / total_duration if total_duration else 0.0

    cluster_segments = Counter(label for label in labels if label)
    cluster_duration: dict[str, float] = defaultdict(float)
    role_segments: dict[str, Counter[str]] = defaultdict(Counter)
    role_duration: dict[str, Counter[str]] = defaultdict(Counter)
    role_refs: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    invalid_annotations: list[str] = []
    for index, (row, label) in enumerate(zip(rows, labels)):
        if not label:
            continue
        duration = _duration(row)
        cluster_duration[label] += duration
        role = str(row.get("speaker_role_candidate") or "unknown").strip().lower()
        confidence = _probability(row.get("speaker_role_confidence", 0.0))
        if confidence is None or role not in ALLOWED_ROLES | {"unknown"}:
            invalid_annotations.append(f"segment-{index:05d}")
            continue
        if role in ALLOWED_ROLES and confidence >= minimum_role_confidence:
            role_segments[label][role] += 1
            role_duration[label][role] += duration
            role_refs[label][role].append(str(row.get("segment_id") or f"segment-{index:05d}"))

    blockers: list[str] = []
    if segment_coverage < minimum_coverage:
        blockers.append("SEGMENT_COVERAGE_BELOW_0_80")
    if duration_coverage < minimum_coverage:
        blockers.append("DURATION_COVERAGE_BELOW_0_80")
    if len(clusters) < expected_speakers:
        blockers.append("SPEAKER_COLLAPSE")
    if len(clusters) > expected_speakers:
        blockers.append("EXCESSIVE_FRAGMENTATION")
    for cluster in clusters:
        share = cluster_duration[cluster] / labeled_duration if labeled_duration else 0.0
        if cluster_segments[cluster] < minimum_cluster_segments or share < minimum_cluster_duration_share:
            blockers.append(f"MINORITY_CLUSTER_TOO_SMALL:{cluster}")
    if invalid_annotations:
        blockers.append("INVALID_ROLE_ANNOTATION")

    role_map: dict[str, dict[str, Any]] = {}
    for cluster in clusters:
        counts = role_segments[cluster]
        durations = role_duration[cluster]
        ranked = sorted(ALLOWED_ROLES, key=lambda role: (-counts[role], role))
        best = ranked[0]
        confident_count = sum(counts.values())
        confident_duration = sum(durations.values())
        segment_agreement = counts[best] / confident_count if confident_count else 0.0
        duration_agreement = durations[best] / confident_duration if confident_duration else 0.0
        opposing = next(role for role in ALLOWED_ROLES if role != best)
        mapped = (
            confident_count >= minimum_cluster_segments
            and segment_agreement >= minimum_coverage
            and duration_agreement >= minimum_coverage
            and counts[opposing] == 0
        )
        role_map[cluster] = {
            "status": "MAPPED" if mapped else "UNMAPPED",
            "role": best if mapped else None,
            "evidence": {
                "segment_refs": role_refs[cluster][best] if mapped else [],
                "confident_segments": confident_count,
                "confident_duration_seconds": round(confident_duration, 3),
                "segment_agreement": round(segment_agreement, 4),
                "duration_agreement": round(duration_agreement, 4),
            },
            "conflicts": ([f"CROSS_ROLE_CONFLICT:{opposing}"] if counts[opposing] else []),
        }

    mapped_roles = [entry["role"] for entry in role_map.values() if entry["status"] == "MAPPED"]
    if any(entry["status"] != "MAPPED" for entry in role_map.values()):
        blockers.append("UNMAPPED_ACTIVE_CLUSTER")
    if sorted(mapped_roles) != ["student", "teacher"]:
        blockers.append("TEACHER_STUDENT_ROLES_NOT_SEPARATELY_PROVED")
    if not bool(producer_report.get("role_mapping_supported")):
        blockers.append("PRODUCER_ROLE_EVIDENCE_UNAVAILABLE")
    if str(producer_report.get("status") or "") != "DIARIZED_ROLE_MAPPED":
        blockers.append("PRODUCER_STATUS_NOT_MAPPED")

    count_evidence = producer_report.get("speaker_count_evidence")
    count_proved = bool(
        isinstance(count_evidence, Mapping)
        and count_evidence.get("mode") == "OPEN_SET"
        and count_evidence.get("selected_count") == len(clusters)
        and count_evidence.get("collapse_check") == "PASS"
        and count_evidence.get("fragmentation_check") == "PASS"
        and count_evidence.get("mixing_check") == "PASS"
    )
    if not count_proved:
        blockers.append("REAL_SPEAKER_COUNT_AND_MIXING_UNPROVED")
    blockers = sorted(set(blockers))
    unmapped_segments = sum(
        cluster_segments[cluster]
        for cluster, entry in role_map.items()
        if entry["status"] == "UNMAPPED"
    ) + (len(rows) - labeled_segments)
    return {
        "schema": SCHEMA,
        "result_scope": "SHADOW_ONLY",
        "status": "PASS" if not blockers else "INCONCLUSIVE",
        "observed_speaker_count": len(clusters),
        "expected_speaker_count": expected_speakers,
        "speaker_count_status": "PROVED" if count_proved else "UNPROVED",
        "speaker_count_evidence": count_evidence if count_proved else None,
        "coverage": {
            "segments_total": len(rows),
            "segments_labeled": labeled_segments,
            "by_segments": round(segment_coverage, 4),
            "speech_duration_seconds": round(total_duration, 3),
            "labeled_speech_duration_seconds": round(labeled_duration, 3),
            "by_speech_duration": round(duration_coverage, 4),
            "minimum": minimum_coverage,
        },
        "clusters": {
            cluster: {
                "segments": cluster_segments[cluster],
                "speech_duration_seconds": round(cluster_duration[cluster], 3),
                "labeled_duration_share": round(
                    cluster_duration[cluster] / labeled_duration if labeled_duration else 0.0, 4
                ),
            }
            for cluster in clusters
        },
        "role_map": role_map,
        "unmapped_share_by_segments": round(unmapped_segments / len(rows), 4),
        "blockers": blockers,
        "canonical_promotion_allowed": False,
        "production_activation_allowed": False,
        "next_video_auto_start_allowed": False,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    proof = verify_speaker_roles(
        _read_jsonl(args.job_dir / "transcript.jsonl"),
        json.loads((args.job_dir / "speaker_diarization.json").read_text(encoding="utf-8")),
    )
    rendered = json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if proof["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
