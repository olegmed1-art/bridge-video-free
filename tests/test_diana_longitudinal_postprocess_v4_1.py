from __future__ import annotations

import importlib
import unittest


class DianaLongitudinalPostprocessCompatibilityTests(unittest.TestCase):
    def test_production_compat_wrapper_installs_current_v42_without_authority_escalation(self):
        module = importlib.import_module('diana_longitudinal_postprocess_v3')
        self.assertEqual(module.base.QUALITY_METHOD_VERSION, 'diana-quality-v4.2')
        self.assertEqual(module.base.QUALITY_SCHEMA_VERSION, 5)
        self.assertEqual(module.base.SCHEMA_VERSION, 5)
        master = {
            'job_id': '0' * 32,
            'content_quality': {'semantic_qc_status': 'PASS', 'semantic_critical_unresolved': 0},
            'technical_qc': {'visual': {'pass1': {'status': 'VISUAL_PASS_1_COMPLETE'}, 'pass2': {'status': 'VISUAL_PASS_2_COMPLETE'}}},
            'transcript': [], 'episodes': [], 'learning_interactions': [], 'canon_links': [], 'deals': [],
            'report_visual_board_deals': [],
            'report_visual_board_reconstruction': {'qc': {}},
        }
        quality = module.base.build_quality_layer(master, {'lesson_id': 'test', 'lesson_number': 0})
        self.assertEqual(quality['method_version'], 'diana-quality-v4.2')
        self.assertEqual(quality['schema_version'], 5)
        self.assertEqual(quality['authority']['canon_activation'], 'DENY')
        self.assertEqual(quality['authority']['curriculum_activation'], 'DENY')
        self.assertEqual(quality['authority']['student_profile_production_write'], 'DENY')
        self.assertEqual(quality['authority']['database_destination'], 'STAGING_ONLY')
        self.assertFalse(quality['incremental_processing']['heavy_video_reprocessing_required'])
        self.assertFalse(quality['incremental_processing']['raw_asr_mutated'])


if __name__ == '__main__':
    unittest.main()
