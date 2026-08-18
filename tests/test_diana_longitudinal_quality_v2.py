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


if __name__ == "__main__":
    unittest.main()
