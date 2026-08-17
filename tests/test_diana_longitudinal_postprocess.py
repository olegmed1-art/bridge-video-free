from __future__ import annotations

import unittest

import diana_longitudinal_postprocess as module


class DianaLongitudinalPostprocessTests(unittest.TestCase):
    def test_explicit_lesson_date(self):
        self.assertEqual(module._date_from_name('Диана 1. 22.02.2021.mp4')[0], '2021-02-22')
        self.assertEqual(module._date_from_name('Диана 1 2021-02-22.mp4')[0], '2021-02-22')
        self.assertIsNone(module._date_from_name('Диана 1.mp4')[0])

    def test_candidates_preserve_non_authoritative_status(self):
        master = {
            'job_id': 'a' * 32,
            'episodes': [
                {
                    'episode_id': 'ep1',
                    'type': 'торговля',
                    'summary_text': 'Пример учебного правила торговли.',
                    'terms': ['торговля', 'открытие'],
                    'evidence': ['seg1'],
                    'visual_evidence': [],
                    'confidence': 'medium',
                }
            ],
            'canon_links': [
                {
                    'episode_id': 'ep1',
                    'status': 'вероятное тематическое совпадение',
                    'score': 0.5,
                    'canonical_excerpt': 'Подтверждённый письменный фрагмент.',
                }
            ],
            'session_summary': {'top_topic_counts': [['торговля', 1], ['открытие', 1]]},
        }
        episodes = module._episode_map(master)
        canon = module._canon_candidates(master, episodes)
        knowledge = module._knowledge_candidates(master)
        self.assertEqual(len(canon), 1)
        self.assertFalse(canon[0]['activation_allowed'])
        self.assertEqual(canon[0]['status'], 'CANON_MATCH_CANDIDATE')
        self.assertEqual(len(knowledge), 1)
        self.assertEqual(knowledge[0]['status'], 'CANDIDATE_KNOWLEDGE')
        self.assertTrue(knowledge[0]['verification_required'])

    def test_curriculum_remains_candidate(self):
        master = {
            'job_id': 'b' * 32,
            'episodes': [{'type': 'торговля', 'terms': ['открытие']}],
            'session_summary': {'top_topic_counts': [['открытие', 1]]},
        }
        lesson = {'lesson_number': 1, 'lesson_date': '2021-02-22'}
        curriculum = module._curriculum(master, lesson)
        candidate = curriculum['candidate_school_curriculum']
        self.assertFalse(candidate['activation_allowed'])
        self.assertIsNone(candidate['modules'][0]['proposed_school_stage'])


if __name__ == '__main__':
    unittest.main()
