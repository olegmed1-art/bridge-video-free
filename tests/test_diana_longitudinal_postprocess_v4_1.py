from __future__ import annotations

import importlib
import unittest


class DianaLongitudinalPostprocessV41Tests(unittest.TestCase):
    def test_wrapper_installs_v41_quality_layer_without_authority_escalation(self):
        module = importlib.import_module('diana_longitudinal_postprocess_v4_1')
        self.assertEqual(module.base.QUALITY_METHOD_VERSION, 'diana-quality-v4.1')
        self.assertEqual(module.base.QUALITY_SCHEMA_VERSION, 4)
        self.assertEqual(module.base.SCHEMA_VERSION, 4)
        master = {
            'job_id': '0' * 32,
            'content_quality': {'semantic_qc_status': 'PASS', 'semantic_critical_unresolved': 0},
            'technical_qc': {'visual': {'pass1': {'status': 'VISUAL_PASS_1_COMPLETE'}, 'pass2': {'status': 'VISUAL_PASS_2_COMPLETE'}}},
            'transcript': [], 'episodes': [], 'learning_interactions': [], 'canon_links': [], 'deals': [],
        }
        quality = module.base.build_quality_layer(master, {'lesson_id': 'test', 'lesson_number': 0})
        self.assertEqual(quality['method_version'], 'diana-quality-v4.1')
        self.assertEqual(quality['authority']['canon_activation'], 'DENY')
        self.assertEqual(quality['authority']['curriculum_activation'], 'DENY')
        self.assertEqual(quality['authority']['student_profile_production_write'], 'DENY')
        self.assertEqual(quality['authority']['database_destination'], 'STAGING_ONLY')
        self.assertFalse(quality['incremental_processing']['heavy_video_reprocessing_required'])
        self.assertFalse(quality['incremental_processing']['raw_asr_mutated'])


if __name__ == '__main__':
    unittest.main()
