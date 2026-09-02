#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest

from bridge_speaker_mapping_r29 import SpeakerMappingEngine


REGISTRY = {
    "participant-A": {"role": "teacher", "confirmed": True, "active": True},
    "participant-B": {"role": "learner", "confirmed": True, "active": True},
}


def interval(start: float, cluster: str | None, **extra):
    value = {
        "start": start,
        "end": start + 20.0,
        "acoustic_cluster_id": cluster,
        "overlap_status": "SINGLE_SPEAKER",
        "acoustic_confidence": 0.93,
        "anchor_eligible": bool(cluster),
    }
    value.update(extra)
    return value


def identity_evidence(cluster: str, person: str, prefix: str = "a"):
    out = []
    for index, start in enumerate((0.0, 90.0, 180.0)):
        out.extend(
            [
                {
                    "evidence_id": f"{prefix}-ac-{index}",
                    "evidence_type": "acoustic",
                    "cluster_id": cluster,
                    "participant_ref": person,
                    "confidence": 0.92,
                    "start": start,
                    "end": start + 12,
                    "clean_anchor": True,
                },
                {
                    "evidence_id": f"{prefix}-vis-{index}",
                    "evidence_type": "visual",
                    "cluster_id": cluster,
                    "participant_ref": person,
                    "confidence": 0.93,
                    "start": start,
                    "end": start + 12,
                    "clean_anchor": True,
                },
            ]
        )
    return out


class R29IdentityMappingTests(unittest.TestCase):
    def setUp(self):
        self.engine = SpeakerMappingEngine()

    def test_semantic_role_never_creates_named_identity(self):
        evidence = [{
            "evidence_id": "semantic-only",
            "evidence_type": "interaction",
            "cluster_id": "SPEAKER_A",
            "participant_ref": "participant-B",
            "confidence": 0.99,
            "start": 0,
            "end": 20,
        }]
        mapping = self.engine.map_clusters([interval(0, "SPEAKER_A")], evidence, REGISTRY)["SPEAKER_A"]
        self.assertEqual(mapping["participant_status"], "UNKNOWN_PARTICIPANT")
        self.assertIsNone(mapping["participant_ref"])
        self.assertFalse(mapping["profile_write_allowed"])

    def test_visual_and_acoustic_anchors_confirm_identity(self):
        evidence = identity_evidence("SPEAKER_A", "participant-A")
        mapping = self.engine.map_clusters(
            [interval(x, "SPEAKER_A") for x in (0, 90, 180)], evidence, REGISTRY
        )["SPEAKER_A"]
        self.assertEqual(mapping["participant_status"], "PERSON_CONFIRMED")
        self.assertEqual(mapping["participant_ref"], "participant-A")
        self.assertEqual(mapping["role"], "teacher")
        self.assertGreaterEqual(mapping["confirmed_anchor_count"], 3)
        self.assertGreaterEqual(mapping["anchor_time_spread"], 60)

    def test_identity_conflict_fails_closed(self):
        evidence = identity_evidence("SPEAKER_A", "participant-A")
        evidence.extend(identity_evidence("SPEAKER_A", "participant-B", prefix="conflict"))
        mapping = self.engine.map_clusters(
            [interval(x, "SPEAKER_A") for x in (0, 90, 180)], evidence, REGISTRY
        )["SPEAKER_A"]
        self.assertIn("ACTOR_ATTRIBUTION_CONFLICT", mapping["conflicts"])
        self.assertNotEqual(mapping["participant_status"], "PERSON_CONFIRMED")

    def test_probable_identity_cannot_write_profile(self):
        evidence = [{
            "evidence_id": "one-acoustic-anchor",
            "evidence_type": "acoustic",
            "cluster_id": "SPEAKER_A",
            "participant_ref": "participant-A",
            "confidence": 0.80,
            "start": 0,
            "end": 12,
        }]
        mapping = self.engine.map_clusters([interval(0, "SPEAKER_A")], evidence, REGISTRY)["SPEAKER_A"]
        self.assertEqual(mapping["participant_status"], "PERSON_PROBABLE")
        self.assertFalse(mapping["profile_write_allowed"])

    def test_overlap_is_not_forced_to_one_person(self):
        intervals = [interval(
            0,
            None,
            multiple_speaker_refs=["SPEAKER_A", "SPEAKER_B"],
            overlap_status="OVERLAP_UNRESOLVED",
        )]
        transcript = [{"segment_id": "s0", "start": 0, "end": 20, "text": "simultaneous"}]
        _, overlay = self.engine.build_overlay(transcript, intervals, {})
        self.assertEqual(overlay[0]["participant_status"], "MIXED")
        self.assertIsNone(overlay[0]["participant_ref"])

    def test_overlay_does_not_mutate_raw_asr(self):
        transcript = [{"segment_id": "s0", "start": 1.25, "end": 3.75, "text": "raw words"}]
        before = copy.deepcopy(transcript)
        self.engine.build_overlay(transcript, [interval(0, "SPEAKER_A")], {})
        self.assertEqual(transcript, before)

    def test_qc_emits_full_required_field_set(self):
        intervals = [
            *[interval(x, "SPEAKER_A") for x in (0, 90, 180)],
            *[interval(x + 30, "SPEAKER_B") for x in (0, 90, 180)],
        ]
        evidence = identity_evidence("SPEAKER_A", "participant-A") + identity_evidence(
            "SPEAKER_B", "participant-B", prefix="b"
        )
        mappings = self.engine.map_clusters(intervals, evidence, REGISTRY)
        qc = self.engine.qc(intervals, mappings, evidence)
        self.assertEqual(
            set(qc),
            {
                "speaker_coverage_by_speech_duration",
                "participant_coverage_by_speech_duration",
                "coverage_per_participant",
                "confirmed_anchor_count",
                "anchor_time_spread",
                "overlap_duration",
                "unknown_duration",
                "conflict_duration",
                "provider_acoustic_agreement",
                "visual_acoustic_agreement",
                "mapping_confidence",
                "alternatives",
                "failure_reasons",
            },
        )

    def test_operational_gate_requires_both_active_participants(self):
        intervals = [interval(x, "SPEAKER_A") for x in (0, 90, 180)]
        evidence = identity_evidence("SPEAKER_A", "participant-A")
        mappings = self.engine.map_clusters(intervals, evidence, REGISTRY, allow_exclusion=False)
        qc = self.engine.qc(intervals, mappings, evidence)
        gate = self.engine.operational_gate(qc, mappings, REGISTRY.keys())
        self.assertFalse(gate["operational"])
        self.assertTrue(any(item.startswith("PERSON_NOT_CONFIRMED:") for item in gate["blockers"]))

    def test_complete_two_cluster_evidence_passes_operational_gate(self):
        intervals = [
            *[interval(x, "SPEAKER_A") for x in (0, 90, 180)],
            *[interval(x + 30, "SPEAKER_B") for x in (0, 90, 180)],
        ]
        evidence = identity_evidence("SPEAKER_A", "participant-A") + identity_evidence(
            "SPEAKER_B", "participant-B", prefix="b"
        )
        payload = self.engine.build_speaker_map(
            [{"segment_id": f"s{i}", "start": x["start"], "end": x["end"], "text": "raw"} for i, x in enumerate(intervals)],
            intervals,
            evidence,
            REGISTRY,
        )
        self.assertTrue(payload["operationalGate"]["operational"])
        self.assertEqual(payload["speaker_mapping_qc"]["failure_reasons"], [])
        self.assertFalse(payload["privacy"]["speaker_embeddings_persisted"])
        self.assertFalse(payload["privacy"]["temporary_audio_anchors_persisted"])


if __name__ == "__main__":
    unittest.main()
