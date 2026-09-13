#!/usr/bin/env python3
import os
import unittest

import bridge_runtime_hardening_r25_4 as r25_4
import run_master_3_1_free as base


class R254IsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['BRIDGE_REQUESTED_ALGORITHM_REVISION'] = r25_4.REVISION
        r25_4.install(lambda: 'fresh-token')

    def test_unreliable_segment_does_not_create_semantic_episode(self):
        segments = [
            {
                'segment_id': 'seg_good', 'start': 0.0, 'end': 4.0,
                'text': 'Обсуждаем контракт три без козыря и первый ход.',
                'speaker': None, 'unreliable': False,
            },
            {
                'segment_id': 'seg_bad', 'start': 5.0, 'end': 9.0,
                'text': 'Ошибка, контра, импас, неправильно, почему вы так сыграли.',
                'speaker': None, 'unreliable': True,
            },
        ]
        episodes = base.semantic_episode_plan(segments, 'ci-r25-4')
        evidence = {x for e in episodes for x in e.get('evidence', [])}
        text = ' '.join(e.get('summary_text', '') for e in episodes)
        self.assertIn('seg_good', evidence)
        self.assertNotIn('seg_bad', evidence)
        self.assertNotIn('неправильно', text.lower())

    def test_validator_rejects_unreliable_evidence_in_derived_analytics(self):
        master = {
            'algorithmRevision': r25_4.REVISION,
            'transcript': [
                {'segment_id': 'seg_bad', 'unreliable': True},
            ],
            'episodes': [],
            'learning_interactions': [],
            'student_analysis': {'observations': []},
            'errors': [
                {'evidence': ['seg_bad']},
            ],
            'recommendations': [{'text': 'x'}],
            'teacher_analysis': [],
            'canon_links': [],
        }
        result = base.validate_r24_master(master)
        self.assertFalse(result['ok'])
        self.assertIn('unreliable-asr-used-in-derived-analytics', result['issues'])
        self.assertEqual(result['unreliableDerivedEvidenceCount'], 1)

    def test_public_name_is_unchanged(self):
        self.assertEqual(base.ALGORITHM_REVISION, r25_4.REVISION)
        import bridge_worker_3_1_free as core
        self.assertEqual(core.ALGORITHM_VERSION, '3.1 FREE')


if __name__ == '__main__':
    unittest.main()
