from __future__ import annotations

import hashlib
import json
import time
import unittest

import diana_longitudinal_quality_v4_2 as v42
from bridge_school_api.dds3.service import DDS_UPSTREAM
from diana_longitudinal_quality_v4_2 import build_quality_layer


PBN = 'N:AKQJ.T98.765.432 T987.654.32.AKQ 6543.AKQ.JT98.76 2.J732.AKQ4.JT985'
DEAL_SHA = hashlib.sha256(PBN.encode()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()


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

    def test_video_canon_candidates_enter_the_shared_persistence_stream(self):
        master = base_master()
        master['video_canon_learning_candidate'] = {'candidate': 'input'}
        master['video_canon_assertions'] = [{'assertion_id': 'rule-1'}]
        master['video_canon_verification_bundles'] = {'rule-1': {'bundle': 'input'}}
        candidate = {
            'candidate_type': 'video_school_canon_candidate',
            'stable_key': 'rule-1',
            'quality_status': 'AI_VERIFICATION_PENDING',
            'promotion_status': 'STAGING_ONLY',
            'payload': {'schema': 'video-canon-evidence-v2'},
            'payload_hash': 'a' * 64,
            'evidence_refs': ['transcript#1'],
            'method_version': 'video-canon-evidence-v2',
        }
        old_pipeline = v42.run_video_canon_auto_pipeline
        try:
            v42.run_video_canon_auto_pipeline = lambda *_: {
                'schema': 'video-canon-auto-pipeline-v1',
                'status': 'AUTO_PROMOTION_READY',
                'candidates': [candidate],
                'promotion_commands': [{'operation': 'ACTIVATE_AI_VERIFIED_VIDEO_CANON'}],
                'gaps': [],
                'human_approval_required': False,
                'world_lookup_performed': False,
                'authoritative_write_performed': False,
            }
            quality = build_quality_layer(master, {'lesson_id': 'lesson-test', 'lesson_number': 5})
        finally:
            v42.run_video_canon_auto_pipeline = old_pipeline
        persisted = [
            row for row in quality['candidate_staging_records']
            if row.get('candidate_type') == 'video_school_canon_candidate'
        ]
        self.assertEqual(len(persisted), 1)
        self.assertEqual(quality['counts']['video_canon_candidates'], 1)
        self.assertEqual(quality['counts']['video_canon_auto_promotions_ready'], 0)
        self.assertEqual(quality['video_canon_auto_pipeline']['status'], 'NEEDS_I2_I3')
        self.assertEqual(
            quality['counts']['staging_records'], len(quality['candidate_staging_records'])
        )

    def test_verified_video_runtime_routes_extractor_and_independent_verification(self):
        master = base_master()
        master['video_canon_verified_result'] = {'schema': 'verified-input'}
        master['video_canon_verification_bundles'] = {'rule-1': {'bundle': 'sealed'}}
        master['video_canon_assurance_verdicts'] = {'rule-1': [{'assurance_level': 'I2'}, {'assurance_level': 'I3'}]}
        candidate = {
            'candidate_type': 'video_school_canon_candidate',
            'stable_key': 'rule-1:sha256:' + 'a' * 64,
            'quality_status': 'AI_VERIFICATION_PENDING',
            'promotion_status': 'STAGING_ONLY',
            'payload': {'candidate_id': 'rule-1', 'authority_class': 'TEACHER_VIDEO'},
            'payload_hash': 'a' * 64,
            'evidence_refs': ['transcript#1'],
            'method_version': 'video-canon-evidence-v2',
        }
        old_extract = v42.extract_canon_candidates
        old_verify = v42.verify_canon_candidate
        try:
            v42.extract_canon_candidates = lambda *_: {
                'schema': 'video-canon-extractor-result-v1', 'status': 'EXTRACTED',
                'candidates': [candidate], 'gaps': [],
                'authoritative_write_performed': False,
            }
            v42.verify_canon_candidate = lambda *_: {
                'schema': 'video-canon-verification-result-v1',
                'status': 'VERIFIED_I2_I3',
                'promotion': {'operation': 'ACTIVATE_AI_VERIFIED_VIDEO_CANON'},
                'authoritative_write_performed': False,
            }
            quality = build_quality_layer(master, {'lesson_id': 'lesson-test', 'lesson_number': 5})
        finally:
            v42.extract_canon_candidates = old_extract
            v42.verify_canon_candidate = old_verify
        runtime = quality['video_canon_runtime_pipeline']
        self.assertEqual(runtime['status'], 'VERIFIED_I2_I3')
        self.assertEqual(len(runtime['promotion_commands']), 1)
        self.assertFalse(runtime['authoritative_write_performed'])
        self.assertEqual(quality['counts']['video_canon_runtime_candidates'], 1)
        self.assertEqual(quality['counts']['video_canon_runtime_verified_i2_i3'], 1)
        self.assertTrue(any(row is candidate for row in quality['candidate_staging_records']))

    def test_integrated_master_routes_verified_dds_proofs_to_comparison(self):
        master = base_master()
        dds_result = {
            'engine': 'DDS3', 'engine_version': DDS_UPSTREAM,
            'fallback_used': False, 'operation': 'position_all_moves',
            'binary_sha256': 'e' * 64,
            'moves': [
                {'card': 'SA', 'tricks': 10, 'regret': 0, 'optimal': True},
                {'card': 'SK', 'tricks': 9, 'regret': 1, 'optimal': False},
            ],
        }
        master['dds_decision_evaluations'] = [{
            'dds_request': {
                'operation': 'position_all_moves',
                'position': {'pbn': PBN, 'trump': 'NT', 'first': 'S'},
            },
            'decision': {
                'decision_id': 'play-7', 'domain': 'PLAY', 'selected_action': 'SA',
                'logic_candidate_id': 'why:rule-7:segment-7',
                'source_sha256': 'c' * 64,
                'public_context': {'auction': ['1NT', '3NT'], 'played_cards': []},
                'evidence_refs': ['segment-7'],
            },
            'full_deal_evidence': {
                'board_evidence_id': 'board-proof-7',
                'deal_pbn_sha256': DEAL_SHA, 'source_refs': ['frame-52'],
                'verified_full_board': True,
            },
        }]
        master['verified_full_board_evidence'] = [{
            'status': 'VERIFIED_FULL_BOARD', 'board_evidence_id': 'board-proof-7',
            'deal_pbn_sha256': DEAL_SHA, 'card_count': 52,
            'unique_card_count': 52, 'source_refs': ['frame-52'],
            'evidence_sha256': 'd' * 64,
        }]
        master['source_bound_logic_evidence'] = [{
            'status': 'SOURCE_BOUND',
            'logic_candidate_id': 'why:rule-7:segment-7',
            'source_sha256': 'c' * 64, 'evidence_refs': ['segment-7'],
        }]

        quality = build_quality_layer(
            master, {'lesson_id': 'lesson-test', 'lesson_number': 5},
            dds_request_executor=lambda _: dds_result,
        )
        records = quality['extended_knowledge_extraction']['candidate_records']
        self.assertTrue(any(row['candidate_type'] == 'DDS_DECISION_COMPARISON' for row in records))
        routed = quality['integrated_verification_evidence']['collections']
        self.assertEqual(routed['verified_full_board_evidence']['status'], 'PASSED_TO_VALIDATOR')
        self.assertEqual(routed['source_bound_logic_evidence']['status'], 'PASSED_TO_VALIDATOR')
        self.assertNotIn('verified_full_board_evidence', quality)
        self.assertNotIn('source_bound_logic_evidence', quality)

    def test_integrated_master_routes_verified_teacher_correction_receipt(self):
        master = base_master()
        master['source'] = {'sha256': 'a' * 64}
        correction = {
            'correction_id': 'c-1', 'kind': 'ASR', 'input_ref': 'segment-7',
            'corrected_value': 'форсирует', 'reviewer_ref': 'teacher:diana',
            'evidence_refs': ['segment-7'],
        }
        master['human_corrections'] = [correction]
        receipt = {
            'correction_id': 'c-1', 'reviewer_ref': 'teacher:diana',
            'source_sha256': 'a' * 64, 'input_ref': 'segment-7',
            'corrected_value_sha256': digest('форсирует'),
            'evidence_refs': ['segment-7'], 'status': 'VERIFIED',
        }
        receipt['receipt_sha256'] = digest(receipt)
        master['correction_review_receipts'] = [receipt]

        quality = build_quality_layer(
            master, {'lesson_id': 'lesson-test', 'lesson_number': 5},
            correction_receipt_resolver=(
                lambda receipt_sha: receipt
                if receipt_sha == receipt['receipt_sha256'] else None
            ),
        )
        records = quality['extended_knowledge_extraction']['candidate_records']
        examples = [row for row in records if row['candidate_type'] == 'ANALYZER_TRAINING_EXAMPLE']
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]['payload']['review_receipt_sha256'], receipt['receipt_sha256'])
        routed = quality['integrated_verification_evidence']['collections']
        self.assertEqual(routed['correction_review_receipts']['status'], 'PASSED_TO_VALIDATOR')
        self.assertNotIn('correction_review_receipts', quality)

    def test_raw_proof_collections_never_survive_into_quality_artifact(self):
        master = base_master()
        master['verified_full_board_evidence'] = [{
            'board_evidence_id': 'bad',
            'full_deal': 'N:AKQJ.T98.765.432 E:... S:... W:...',
        }]
        master['source_bound_logic_evidence'] = [{'partner_hand': 'AKQJ'}]
        master['correction_review_receipts'] = [
            {'forbidden_private_cards_marker': 'PRIVATE-AKQJ-SECRET'}
        ]
        quality = build_quality_layer(
            master, {'lesson_id': 'lesson-test', 'lesson_number': 5}
        )
        self.assertNotIn('verified_full_board_evidence', quality)
        self.assertNotIn('source_bound_logic_evidence', quality)
        self.assertNotIn('correction_review_receipts', quality)
        encoded = json.dumps(quality, ensure_ascii=False, sort_keys=True)
        self.assertNotIn('N:AKQJ.T98.765.432', encoded)
        self.assertNotIn('partner_hand', encoded)
        self.assertNotIn('forbidden_private_cards_marker', encoded)
        self.assertNotIn('PRIVATE-AKQJ-SECRET', encoded)

    def test_self_hashed_teacher_correction_is_not_trusted_by_default(self):
        master = base_master()
        master['source'] = {'sha256': 'a' * 64}
        correction = {
            'correction_id': 'c-1', 'kind': 'ASR', 'input_ref': 'segment-7',
            'corrected_value': 'форсирует', 'reviewer_ref': 'teacher:diana',
            'evidence_refs': ['segment-7'],
        }
        master['human_corrections'] = [correction]
        receipt = {
            'correction_id': 'c-1', 'reviewer_ref': 'teacher:diana',
            'source_sha256': 'a' * 64, 'input_ref': 'segment-7',
            'corrected_value_sha256': digest('форсирует'),
            'evidence_refs': ['segment-7'], 'status': 'VERIFIED',
        }
        receipt['receipt_sha256'] = digest(receipt)
        master['correction_review_receipts'] = [receipt]
        quality = build_quality_layer(
            master, {'lesson_id': 'lesson-test', 'lesson_number': 5}
        )
        records = quality['extended_knowledge_extraction']['candidate_records']
        self.assertFalse(any(
            row['candidate_type'] == 'ANALYZER_TRAINING_EXAMPLE' for row in records
        ))
        self.assertTrue(any(
            row['payload'].get('gap_type') == 'LEARNING_FEEDBACK_INVALID'
            for row in records
        ))

    def test_malformed_integrated_evidence_fails_to_explicit_gap(self):
        master = base_master()
        master['source'] = {'sha256': 'a' * 64}
        master['human_corrections'] = [{
            'correction_id': 'c-1', 'kind': 'ASR', 'input_ref': 'segment-7',
            'corrected_value': 'форсирует', 'reviewer_ref': 'teacher:diana',
            'evidence_refs': ['segment-7'],
        }]
        master['correction_review_receipts'] = {'not': 'a list'}
        quality = build_quality_layer(master, {'lesson_id': 'lesson-test', 'lesson_number': 5})
        records = quality['extended_knowledge_extraction']['candidate_records']
        self.assertTrue(any(
            row['payload'].get('gap_type') == 'LEARNING_FEEDBACK_INVALID'
            for row in records
        ))
        routed = quality['integrated_verification_evidence']['collections']
        self.assertEqual(
            routed['correction_review_receipts']['status'],
            'INVALID_CONTAINER_PASSED_TO_VALIDATOR',
        )


if __name__ == '__main__':
    unittest.main()
