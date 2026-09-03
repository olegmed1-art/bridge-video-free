from __future__ import annotations

import time
import unittest

from diana_longitudinal_quality_v4_2 import build_quality_layer


def base_master() -> dict:
    return {
        'job_id': '6' * 32,
        'algorithmRevision': 'test-r25.15',
        'createdAt': '2026-01-02T03:04:05Z',
        'content_quality': {'semantic_qc_status': 'PASS', 'semantic_critical_unresolved': 0},
        'technical_qc': {'visual': {'pass1': {'status': 'VISUAL_PASS_1_COMPLETE'}, 'pass2': {'status': 'VISUAL_PASS_2_COMPLETE'}}},
        'transcript': [],
        'episodes': [],
        'learning_interactions': [],
        'canon_links': [],
        'deals': [],
        'report_visual_board_deals': [{
            'deal_id': 'visualdeal_test',
            'board_fingerprint': 'report-visual:test',
            'platform_board_key': 'report-visual:test',
            'hands': {
                'N': ['AH','KH','QH','JC','TC','9D'],
                'E': None,
                'S': ['AS','KS','QS','8C','7C','6D'],
                'W': None,
            },
            'evidence': ['frame_a','frame_b'],
            'statement_type': 'VISUAL_EVIDENCE',
        }],
        'report_visual_board_reconstruction': {
            'method_version': 'report-visual-board-v1',
            'parser_scope': 'legacy-bbo-horizontal-N/S-only',
            'qc': {
                'report_visual_observation_count': 2,
                'board_cluster_count': 1,
                'recognized_card_union_total': 12,
            },
        },
    }


class DianaLongitudinalQualityV42Tests(unittest.TestCase):
    def test_visual_evidence_creates_partial_not_full_board(self):
        quality = build_quality_layer(base_master(), {'lesson_id': 'lesson-test', 'lesson_number': 5})
        counts = quality['counts']
        self.assertEqual(quality['method_version'], 'diana-quality-v4.2')
        self.assertEqual(quality['schema_version'], 5)
        self.assertEqual(counts['verified_full_boards'], 0)
        self.assertGreaterEqual(counts['partial_boards'], 1)
        self.assertEqual(counts['report_visual_partial_boards_v4_2'], 1)
        self.assertEqual(counts['report_visual_verified_full_boards_v4_2'], 0)
        self.assertEqual(counts['report_visual_board_observations_v4_2'], 2)
        self.assertEqual(counts['report_visual_recognized_cards_v4_2'], 12)
        info = quality['board_reconstruction_v4_2']
        self.assertFalse(info['hidden_hand_complement_inference_allowed'])
        self.assertFalse(info['time_topic_board_number_identity_allowed'])
        self.assertTrue(info['full_board_requires_52_unique_cards'])

    def test_authority_and_cost_gates_remain_closed(self):
        quality = build_quality_layer(base_master(), {'lesson_id': 'lesson-test', 'lesson_number': 5})
        for key in ('canon_activation', 'curriculum_activation', 'methodology_activation', 'student_profile_production_write'):
            self.assertEqual(quality['authority'][key], 'DENY')
        self.assertEqual(quality['authority']['database_destination'], 'STAGING_ONLY')
        self.assertFalse(quality['incremental_processing']['heavy_video_reprocessing_required'])
        self.assertFalse(quality['incremental_processing']['raw_asr_mutated'])
        self.assertFalse(quality['cost_gate']['paid_ai_api_required'])
        self.assertFalse(quality['cost_gate']['paid_cloud_required'])

    def test_extended_knowledge_harvest_is_staging_only(self):
        master = base_master()
        master['terminology_observations'] = [{
            'stable_key': 'term:forcing', 'term': 'форсинг',
            'status': 'REVIEW_REQUIRED', 'evidence_refs': ['segment-7'],
        }]
        master['system_evolution_observations'] = [{
            'stable_key': 'evolution:lesson-5', 'status': 'REVIEW_REQUIRED',
            'observed_version': 'candidate-v2', 'evidence_refs': ['segment-8'],
        }]
        master['world_comparison_links'] = [{
            'stable_key': 'world:link-1', 'status': 'REVIEW_REQUIRED',
            'world_object_id': 'world-rule-1', 'evidence_refs': ['segment-9'],
        }]
        master['explanation_observations'] = [{
            'stable_key': 'why:rule-1', 'rule_stable_key': 'rule-1',
            'status': 'REVIEW_REQUIRED',
            'why_chain': ['У партнёра ограничена сила.', 'Поэтому гейм не форсируется.'],
            'rejected_alternatives': [{'action': '3NT', 'reason': 'Недостаточно силы.'}],
            'prerequisites': ['оценка силы'],
            'example': {'auction': ['1NT', '2NT']},
            'counterexample': {'auction': ['1NT', '3NT']},
            'evidence_refs': ['segment-10'],
        }]
        master['transcript'] = [{
            'segment_id': 'segment-10', 'speaker_role': 'teacher',
            'speaker_role_confidence': 0.99,
            'text': 'У партнёра ограничена сила. Поэтому гейм не форсируется.',
        }]
        master['canon_links'] = [{
            'stable_key': 'rule-1', 'classification': 'RULE_PARAPHRASE_MATCH',
            'evidence_refs': ['segment-10'],
        }]
        quality = build_quality_layer(master, {'lesson_id': 'lesson-test', 'lesson_number': 5})
        extraction = quality['extended_knowledge_extraction']
        self.assertEqual(extraction['status'], 'STAGING_ONLY')
        self.assertEqual(extraction['authority']['canon_activation'], 'DENY')
        kinds = {row['candidate_type'] for row in extraction['candidate_records']}
        self.assertTrue({
            'SCHOOL_TERMINOLOGY', 'SYSTEM_EVOLUTION_OBSERVATION',
            'WORLD_COMPARISON_LINK', 'ANALYSIS_QUALITY_EVIDENCE',
            'GAP_OR_CONFLICT',
        } <= kinds)
        self.assertTrue(any(
            row['payload'].get('gap_type') == 'EXPLANATION_EVIDENCE_INVALID'
            for row in extraction['candidate_records']
        ))
        self.assertTrue(all(row['promotion_allowed'] is False for row in extraction['candidate_records']))

    def test_quality_created_at_is_master_derived_and_repeat_stable(self):
        master = base_master()
        first = build_quality_layer(master, {'lesson_id': 'lesson-test', 'lesson_number': 5})
        time.sleep(0.01)
        second = build_quality_layer(master, {'lesson_id': 'lesson-test', 'lesson_number': 5})
        self.assertEqual(first['created_at'], '2026-01-02T03:04:05Z')
        self.assertEqual(second['created_at'], first['created_at'])
        self.assertEqual(first, second)

    def test_missing_master_timestamp_has_deterministic_fallback(self):
        master = base_master()
        master.pop('createdAt')
        first = build_quality_layer(master, {'lesson_id': 'lesson-test', 'lesson_number': 5})
        second = build_quality_layer(master, {'lesson_id': 'lesson-test', 'lesson_number': 5})
        self.assertEqual(first['created_at'], '1970-01-01T00:00:00Z')
        self.assertEqual(first, second)

    def test_video_canon_auto_pipeline_is_wired_but_not_a_direct_database_write(self):
        quality = build_quality_layer(base_master(), {'lesson_id': 'lesson-test', 'lesson_number': 5})
        pipeline = quality['video_canon_auto_pipeline']
        self.assertEqual(pipeline['status'], 'NOT_REQUESTED')
        self.assertFalse(pipeline['human_approval_required'])
        self.assertFalse(pipeline['authoritative_write_performed'])
        self.assertFalse(pipeline['world_lookup_performed'])


if __name__ == '__main__':
    unittest.main()
