#!/usr/bin/env python3
"""Evidence-gated anonymous-cluster -> participant -> role mapping for Bridge r29.

This module deliberately contains no real-person registry or identity evidence.
Those remain private runtime inputs.  Semantic role, file names, speaking duration,
and invitation membership are never identity evidence.
"""
from __future__ import annotations

import copy
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

IDENTITY_EVIDENCE = {"provider", "acoustic", "visual"}
EVIDENCE_WEIGHT = {"provider": 0.92, "acoustic": 0.90, "visual": 0.95, "interaction": 0.35}


@dataclass(frozen=True)
class MappingPolicy:
    confirmed_confidence: float = 0.85
    probable_confidence: float = 0.65
    confirmed_margin: float = 0.20
    probable_margin: float = 0.10
    min_confirmed_anchors: int = 3
    min_evidence_types: int = 2
    min_time_spread_seconds: float = 60.0
    segment_transfer_coverage: float = 0.60
    operational_participant_coverage: float = 0.70


def _duration(item: dict[str, Any]) -> float:
    return max(0.0, float(item.get("end", 0.0)) - float(item.get("start", 0.0)))


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    return max(0.0, min(float(a["end"]), float(b["end"])) - max(float(a["start"]), float(b["start"])))


def _combine(values: Iterable[float]) -> float:
    missing = 1.0
    for value in values:
        missing *= 1.0 - min(0.999, max(0.0, value))
    return 1.0 - missing


class SpeakerMappingEngine:
    revision = "3.1-free-r29"
    schema_version = "4.5"

    def __init__(self, policy: MappingPolicy | None = None) -> None:
        self.policy = policy or MappingPolicy()

    def _cluster_scores(
        self,
        cluster_id: str,
        evidence: list[dict[str, Any]],
    ) -> tuple[dict[str, float], dict[str, set[str]], dict[str, list[dict[str, Any]]]]:
        by_person_type: dict[str, dict[str, float]] = defaultdict(dict)
        refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in evidence:
            if item.get("cluster_id") != cluster_id or not item.get("participant_ref"):
                continue
            kind = str(item.get("evidence_type", "interaction"))
            if kind == "interaction":
                # Semantic function cannot create a named identity candidate.
                continue
            confidence = float(item.get("confidence", 0.0)) * EVIDENCE_WEIGHT.get(kind, 0.0)
            person = str(item["participant_ref"])
            by_person_type[person][kind] = max(by_person_type[person].get(kind, 0.0), confidence)
            refs[person].append(item)
        scores = {person: _combine(types.values()) for person, types in by_person_type.items()}
        kinds = {person: set(types) for person, types in by_person_type.items()}
        return scores, kinds, refs

    def map_clusters(
        self,
        intervals: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        participant_registry: dict[str, dict[str, Any]],
        *,
        allow_exclusion: bool = True,
    ) -> dict[str, dict[str, Any]]:
        clusters = sorted({str(i["acoustic_cluster_id"]) for i in intervals if i.get("acoustic_cluster_id")})
        result: dict[str, dict[str, Any]] = {}
        for cluster in clusters:
            scores, kinds, refs = self._cluster_scores(cluster, evidence)
            ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
            best_person, best_score = ranked[0] if ranked else (None, 0.0)
            second = ranked[1][1] if len(ranked) > 1 else 0.0
            margin = best_score - second
            chosen_refs = refs.get(best_person, []) if best_person else []
            clean = [
                r
                for r in chosen_refs
                if r.get("clean_anchor", True)
                and r.get("evidence_type") in IDENTITY_EVIDENCE
                and not r.get("overlap", False)
            ]
            times = sorted(float(r.get("start", 0.0)) for r in clean)
            spread = (times[-1] - times[0]) if len(times) >= 2 else 0.0
            conflict = any(person != best_person and score >= 0.65 for person, score in ranked[1:])
            type_ok = bool(best_person and len(kinds.get(best_person, set())) >= self.policy.min_evidence_types)
            modality_ok = bool(best_person and kinds.get(best_person, set()) & {"acoustic", "visual"})
            confirmed = (
                best_score >= self.policy.confirmed_confidence
                and margin >= self.policy.confirmed_margin
                and len(clean) >= self.policy.min_confirmed_anchors
                and spread >= self.policy.min_time_spread_seconds
                and type_ok
                and modality_ok
                and not conflict
            )
            probable = best_score >= self.policy.probable_confidence and margin >= self.policy.probable_margin and not conflict
            status = "PERSON_CONFIRMED" if confirmed else "PERSON_PROBABLE" if probable else "UNKNOWN_PARTICIPANT"
            participant = best_person if status != "UNKNOWN_PARTICIPANT" else None
            registry = participant_registry.get(participant or "", {})
            role = None
            if status == "PERSON_CONFIRMED" and registry.get("confirmed") is True:
                role = registry.get("role")
            elif status == "PERSON_PROBABLE" and registry.get("role"):
                role = f"{registry['role']}_likely"
            result[cluster] = {
                "cluster_id": cluster,
                "participant_ref": participant,
                "participant_status": status,
                "role": role or "speaker_unknown",
                "mapping_confidence": round(best_score, 4),
                "margin": round(margin, 4),
                "confirmed_anchor_count": len(clean),
                "anchor_time_spread": round(spread, 3),
                "evidence_types": sorted(kinds.get(best_person, set())) if best_person else [],
                "evidence_refs": [str(r.get("evidence_id")) for r in chosen_refs if r.get("evidence_id")],
                "alternatives": [
                    {"participant_ref": person, "confidence": round(score, 4)} for person, score in ranked[1:]
                ],
                "conflicts": ["ACTOR_ATTRIBUTION_CONFLICT"] if conflict else [],
                "profile_write_allowed": status == "PERSON_CONFIRMED" and registry.get("confirmed") is True,
            }

        if allow_exclusion:
            self._apply_two_person_exclusion(result, intervals, participant_registry)
        return result

    def _apply_two_person_exclusion(
        self,
        mappings: dict[str, dict[str, Any]],
        intervals: list[dict[str, Any]],
        registry: dict[str, dict[str, Any]],
    ) -> None:
        active_people = [p for p, v in registry.items() if v.get("active") is True and v.get("confirmed") is True]
        confirmed = [m for m in mappings.values() if m["participant_status"] == "PERSON_CONFIRMED"]
        if len(active_people) != 2 or len(mappings) != 2 or len(confirmed) != 1:
            return
        if any(i.get("background_voice") or i.get("third_speaker") for i in intervals):
            return
        unknown = [m for m in mappings.values() if m["participant_status"] != "PERSON_CONFIRMED"]
        if len(unknown) != 1:
            return
        cluster = unknown[0]["cluster_id"]
        clean = [
            i
            for i in intervals
            if i.get("acoustic_cluster_id") == cluster
            and i.get("anchor_eligible") is True
            and float(i.get("acoustic_confidence", 0.0)) >= 0.80
            and i.get("overlap_status", "SINGLE_SPEAKER") == "SINGLE_SPEAKER"
        ]
        times = sorted(float(i["start"]) for i in clean)
        spread = times[-1] - times[0] if len(times) >= 2 else 0.0
        if len(clean) < self.policy.min_confirmed_anchors or spread < self.policy.min_time_spread_seconds:
            return
        used = confirmed[0]["participant_ref"]
        remaining = [p for p in active_people if p != used]
        if len(remaining) != 1:
            return
        person = remaining[0]
        unknown[0].update(
            {
                "participant_ref": person,
                "participant_status": "PERSON_CONFIRMED",
                "role": registry[person]["role"],
                "mapping_confidence": 0.85,
                "margin": 0.20,
                "confirmed_anchor_count": len(clean),
                "anchor_time_spread": round(spread, 3),
                "evidence_types": ["acoustic", "two_person_registry_exclusion"],
                "evidence_refs": ["TWO_PERSON_EXCLUSION_RULE"],
                "profile_write_allowed": True,
            }
        )

    def build_overlay(
        self,
        transcript_segments: list[dict[str, Any]],
        intervals: list[dict[str, Any]],
        mappings: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        raw = copy.deepcopy(transcript_segments)
        overlay: list[dict[str, Any]] = []
        for index, segment in enumerate(transcript_segments):
            duration = max(_duration(segment), 1e-9)
            hits = [(i, _overlap(segment, i)) for i in intervals]
            hits = [(i, overlap) for i, overlap in hits if overlap > 0]
            unresolved = any(i.get("overlap_status") != "SINGLE_SPEAKER" for i, _ in hits)
            by_cluster: dict[str, float] = defaultdict(float)
            for interval, amount in hits:
                if interval.get("acoustic_cluster_id"):
                    by_cluster[str(interval["acoustic_cluster_id"])] += amount
            ranked = sorted(by_cluster.items(), key=lambda x: (-x[1], x[0]))
            cluster = ranked[0][0] if ranked and ranked[0][1] / duration >= self.policy.segment_transfer_coverage else None
            mixed = unresolved or (len(ranked) > 1 and ranked[1][1] / duration >= 0.20)
            mapping = mappings.get(cluster or "", {}) if not mixed else {}
            status = "MIXED" if mixed else mapping.get("participant_status", "UNKNOWN_PARTICIPANT")
            overlay.append(
                {
                    "interval_ref": segment.get("segment_id", f"segment-{index:05d}"),
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                    "cluster_id": None if mixed else cluster,
                    "participant_ref": mapping.get("participant_ref"),
                    "participant_status": status,
                    "role": mapping.get("role", "speaker_unknown"),
                    "confidence": mapping.get("mapping_confidence", 0.0),
                    "evidence_refs": mapping.get("evidence_refs", []),
                    "alternatives": mapping.get("alternatives", []),
                    "conflicts": mapping.get("conflicts", []) + (["OVERLAP_UNRESOLVED"] if mixed else []),
                    "profile_write_allowed": bool(mapping.get("profile_write_allowed", False) and not mixed),
                }
            )
        if transcript_segments != raw:
            raise AssertionError("r29 overlay mutated raw ASR")
        return raw, overlay

    def qc(
        self,
        intervals: list[dict[str, Any]],
        mappings: dict[str, dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        speech = sum(_duration(i) for i in intervals)
        speaker = sum(_duration(i) for i in intervals if i.get("acoustic_cluster_id"))
        participant = 0.0
        overlap_duration = 0.0
        unknown = 0.0
        conflict = 0.0
        per: dict[str, float] = defaultdict(float)
        for interval in intervals:
            d = _duration(interval)
            if interval.get("overlap_status") != "SINGLE_SPEAKER":
                overlap_duration += d
            mapping = mappings.get(str(interval.get("acoustic_cluster_id")), {})
            if mapping.get("participant_status") == "PERSON_CONFIRMED" and interval.get("overlap_status") == "SINGLE_SPEAKER":
                participant += d
                per[str(mapping["participant_ref"])] += d
            else:
                unknown += d
            if mapping.get("conflicts"):
                conflict += d

        def agreement(a: str, b: str) -> float | None:
            pairs: dict[tuple[str, float, float], dict[str, str]] = defaultdict(dict)
            for item in evidence:
                if item.get("evidence_type") in {a, b} and item.get("participant_ref"):
                    key = (str(item.get("cluster_id")), float(item.get("start", 0.0)), float(item.get("end", 0.0)))
                    pairs[key][str(item["evidence_type"])] = str(item["participant_ref"])
            comparable = [v for v in pairs.values() if a in v and b in v]
            return None if not comparable else sum(v[a] == v[b] for v in comparable) / len(comparable)

        failure: list[str] = []
        coverage = participant / speech if speech else 0.0
        if coverage < self.policy.operational_participant_coverage:
            failure.append("PARTICIPANT_COVERAGE_BELOW_0_70")
        if any(m["participant_status"] != "PERSON_CONFIRMED" for m in mappings.values()):
            failure.append("UNCONFIRMED_ACTIVE_CLUSTER")
        if conflict > 0:
            failure.append("ACTOR_ATTRIBUTION_CONFLICT")
        return {
            "speaker_coverage_by_speech_duration": round(speaker / speech if speech else 0.0, 4),
            "participant_coverage_by_speech_duration": round(coverage, 4),
            "coverage_per_participant": {p: round(d / speech if speech else 0.0, 4) for p, d in sorted(per.items())},
            "confirmed_anchor_count": {c: m["confirmed_anchor_count"] for c, m in sorted(mappings.items())},
            "anchor_time_spread": {c: m["anchor_time_spread"] for c, m in sorted(mappings.items())},
            "overlap_duration": round(overlap_duration, 3),
            "unknown_duration": round(unknown, 3),
            "conflict_duration": round(conflict, 3),
            "provider_acoustic_agreement": agreement("provider", "acoustic"),
            "visual_acoustic_agreement": agreement("visual", "acoustic"),
            "mapping_confidence": {c: m["mapping_confidence"] for c, m in sorted(mappings.items())},
            "alternatives": {c: m["alternatives"] for c, m in sorted(mappings.items())},
            "failure_reasons": sorted(set(failure)),
        }

    def operational_gate(
        self,
        qc: dict[str, Any],
        mappings: dict[str, dict[str, Any]],
        target_participants: Iterable[str],
        key_interactions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        blockers = list(qc.get("failure_reasons", []))
        for person in target_participants:
            hits = [m for m in mappings.values() if m.get("participant_ref") == person]
            if not hits or any(m["participant_status"] != "PERSON_CONFIRMED" for m in hits):
                blockers.append(f"PERSON_NOT_CONFIRMED:{person}")
            elif sum(int(m["confirmed_anchor_count"]) for m in hits) < self.policy.min_confirmed_anchors:
                blockers.append(f"INSUFFICIENT_ANCHORS:{person}")
        for item in key_interactions or []:
            if not item.get("student_actor_ref") or not item.get("teacher_actor_ref"):
                blockers.append(f"UNCONFIRMED_INTERACTION_ACTORS:{item.get('interaction_id', 'unknown')}")
        blockers = sorted(set(blockers))
        return {"operational": not blockers, "blockers": blockers}

    def build_speaker_map(
        self,
        transcript_segments: list[dict[str, Any]],
        intervals: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        participant_registry: dict[str, dict[str, Any]],
        *,
        supersedes: str | None = None,
        key_interactions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        mappings = self.map_clusters(intervals, evidence, participant_registry)
        _, overlay = self.build_overlay(transcript_segments, intervals, mappings)
        qc = self.qc(intervals, mappings, evidence)
        gate = self.operational_gate(qc, mappings, participant_registry.keys(), key_interactions)
        return {
            "schema": "bridge.speaker_map",
            "schemaVersion": self.schema_version,
            "algorithmRevision": self.revision,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "supersedes": supersedes,
            "clusterMappings": mappings,
            "intervals": overlay,
            "speaker_mapping_qc": qc,
            "operationalGate": gate,
            "privacy": {
                "speaker_embeddings_persisted": False,
                "temporary_audio_anchors_persisted": False,
                "durable_fields": ["mapping evidence refs", "QC metrics", "interval labels"],
            },
        }

    @staticmethod
    def write_speaker_map(path: str | Path, payload: dict[str, Any]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
