from __future__ import annotations

import unittest

import numpy as np

import bridge_speaker_diarization as diarization


class SpeakerDiarizationTests(unittest.TestCase):
    def test_existing_labels_are_preserved(self):
        segments = [
            {"start": i, "end": i + 1, "text": "text", "speaker": "A" if i % 2 == 0 else "B"}
            for i in range(20)
        ]
        out, report = diarization.diarize_transcript("missing.mp4", segments, "/tmp", enabled=True)
        self.assertEqual(report["status"], "EXISTING_SPEAKER_LABELS_PRESERVED")
        self.assertEqual([x["speaker"] for x in out], [x["speaker"] for x in segments])

    def test_insufficient_segments_fails_soft(self):
        segments = [{"start": 0, "end": 1, "text": "one"}]
        out, report = diarization.diarize_transcript("missing.mp4", segments, "/tmp", enabled=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(report["status"], "UNAVAILABLE_INSUFFICIENT_SEGMENTS")

    def test_deterministic_two_cluster_separation(self):
        first = np.array([[0.0, 0.1], [0.1, 0.0], [-0.1, 0.0], [0.0, -0.1], [0.05, 0.05], [-0.05, -0.05]])
        second = np.array([[4.0, 4.1], [4.1, 4.0], [3.9, 4.0], [4.0, 3.9], [4.05, 4.05], [3.95, 3.95]])
        labels, confidence, ratio = diarization._deterministic_kmeans(np.vstack([first, second]))
        self.assertGreater(ratio, 1.0)
        self.assertEqual(len(set(labels[:6])), 1)
        self.assertEqual(len(set(labels[6:])), 1)
        self.assertNotEqual(labels[0], labels[-1])
        self.assertGreater(float(np.mean(confidence)), 0.0)

    def test_role_mapping_requires_asymmetric_evidence(self):
        segments = [
            {"text": "Диана, почему ты так решила?"},
            {"text": "Давай посмотрим и посчитаем."},
            {"text": "Обрати внимание, это правильно."},
            {"text": "Я не знаю, я думаю шесть."},
            {"text": "Я не помню, мне кажется семь."},
            {"text": "Я поняла, тогда так."},
        ]
        mapping, report = diarization._map_roles(segments, [0, 0, 0, 1, 1, 1])
        self.assertTrue(report["mapping_supported"])
        self.assertEqual(set(mapping.values()), {"teacher", "student"})


class VisualReadinessStatusTests(unittest.TestCase):
    def test_explicit_incomplete_status_blocks_technical_readiness(self):
        from diana_longitudinal_quality_v2 import methodology_readiness

        for stage in (1, 2):
            for status in (f"VISUAL_PASS_{stage}_INCOMPLETE", "incomplete", "not complete"):
                for wrapped in (False, True):
                    with self.subTest(stage=stage, status=status, wrapped=wrapped):
                        master = {"technical_qc": {"visual": {"pass1": "VISUAL_PASS_1_COMPLETE", "pass2": "VISUAL_PASS_2_COMPLETE"}}}
                        master["transcript"] = [{"text": "synthetic evidence"}]
                        master["technical_qc"]["visual"][f"pass{stage}"] = (
                            {"status": status} if wrapped else status
                        )
                        result = methodology_readiness(master, [])
                        self.assertEqual(result["technical_status"], "TECHNICAL_NOT_READY")
                        self.assertIn(f"VISUAL_PASS_{stage}_INCOMPLETE", result["technical_issues"])

    def test_complete_status_forms_remain_accepted(self):
        from diana_longitudinal_quality_v2 import methodology_readiness

        for wrapped in (False, True):
            for generic in (False, True):
                master = {"technical_qc": {"visual": {"pass1": "VISUAL_PASS_1_COMPLETE", "pass2": "VISUAL_PASS_2_COMPLETE"}}}
                master["transcript"] = [{"text": "synthetic evidence"}]
                for stage in (1, 2):
                    status = " COMPLETE " if generic else f"VISUAL_PASS_{stage}_COMPLETE"
                    master["technical_qc"]["visual"][f"pass{stage}"] = (
                        {"status": status} if wrapped else status
                    )
                self.assertEqual(methodology_readiness(master, [])["technical_status"], "TECHNICAL_READY")


if __name__ == "__main__":
    unittest.main()
