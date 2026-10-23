"""Synthetic UI method contracts, never calibration/card qualification."""
from copy import deepcopy
from hashlib import sha256
import unittest
import numpy as np
from video_adapters.diana20.runtime_semantics import Diana20RuntimeSemantics, annotate_observed_pairs
from bridge_vision.original_deal_solver import RecoveryInputError


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.source = 'a' * 64
        # Synthetic fixture bypasses production constructor deliberately. The
        # real constructor remains pinned; actual existing assets are replayed
        # in the private local integration run, never committed as card labels.
        self.adapter = Diana20RuntimeSemantics.__new__(Diana20RuntimeSemantics)
        self.adapter.profile = {'source_video_sha256': self.source, 'table_xywh': [0, 23, 1364, 935],
                                'compass_reference_center': [1278, 83]}
        rng = np.random.default_rng(6712)
        self.image = np.zeros((1010, 1920, 3), np.uint8)
        boxes = [[1267,36,1289,58], [1305,73,1323,94], [1230,73,1253,95], [0,24,50,70], [1370,30,1435,75]]
        regions = []
        for i, (box, label) in enumerate(zip(boxes, ['N', 'E', 'W', None, None])):
            x0,y0,x1,y1 = box
            self.image[y0:y1,x0:x1] = rng.integers(0,256,(y1-y0,x1-x0,3),dtype=np.uint8)
            regions.append({'id':str(i), 'bounds':box, 'label':label,
                            'pixel_sha256_bgr':sha256(self.image[y0:y1,x0:x1].tobytes()).hexdigest()})
        self.adapter.pins = {'reference_groups':[{'source_sha256':'b'*64, 'regions':regions}]}
        self.adapter._references = [self.image.copy()]

    def measure(self, image=None):
        return self.adapter.measure(self.image if image is None else image, source_video_sha256=self.source)

    def test_current_pixels_match_conditional_orientation_not_board_or_settled(self):
        result = self.measure()
        self.assertEqual(result['status'], 'CONDITIONAL_CURRENT_PIXEL_MATCH')
        mapping = result['conditional_compass_mapping']
        self.assertEqual(mapping['mapping'], dict(zip(('top','right','bottom','left'), 'NESW')))
        self.assertEqual(mapping['inferred_positions'], ['bottom'])
        self.assertIsNone(result['board_tile']['number'])
        self.assertIsNone(result['board_tile']['board_epoch'])
        self.assertEqual(result['settled']['status'], 'UNKNOWN')
        self.assertFalse(result['hard_evidence'])
        self.assertFalse(result['canonical_promotion_allowed'])
        self.assertEqual(result['independent_vote_count'], 0)

    def test_table_rotation_shift_occlusion_and_glyph_change_close_mapping(self):
        variants = [np.rot90(self.image, 2).copy(), np.roll(self.image, 15, axis=1), 255-self.image]
        occluded = self.image.copy();occluded[36:58,1267:1289] = 0;variants.append(occluded)
        altered = self.image.copy();altered[73:94,1305:1323] = 0;variants.append(altered)
        for image in variants:
            self.assertIsNone(self.measure(image)['conditional_compass_mapping'])

    def test_no_cross_group_assembly(self):
        second = self.image.copy();second[36:58,1267:1289] = 255-second[36:58,1267:1289]
        second[73:94,1305:1323] = 255-second[73:94,1305:1323]
        group = deepcopy(self.adapter.pins['reference_groups'][0]);group['source_sha256']='c'*64
        self.adapter.pins['reference_groups'].append(group);self.adapter._references.append(second)
        mixed = self.image.copy();mixed[36:58,1267:1289] = second[36:58,1267:1289]
        self.assertIsNone(self.measure(mixed)['conditional_compass_mapping'])

    def test_board_change_and_visual_animation_do_not_reuse_epoch(self):
        changed = self.image.copy();changed[62:106,1258:1298] = 255
        first, second = self.measure(), self.measure(changed)
        self.assertNotEqual(first['board_tile']['pixel_sha256_bgr'],second['board_tile']['pixel_sha256_bgr'])
        records = [self.record(0,first),self.record(1,second)]
        annotate_observed_pairs(records)
        pair = records[1]['semantic_measurements']['previous_observed_frame']
        self.assertFalse(pair['board_tile_pixels_equal'])
        self.assertFalse(pair['same_board_proven'])
        self.assertFalse(pair['settled_proven'])

    def record(self, n, measured):
        return {'source_video_sha256':self.source,'timestamp_ms':n*20000,'sequence':n,
                'input_row_sha256':str(n)*64,'semantic_measurements':deepcopy(measured)}

    def test_repeats_sparse_gaps_sources_idempotence_and_order(self):
        a,b = self.record(0,self.measure()),self.record(1,self.measure())
        records = [b,a]
        annotate_observed_pairs(records)
        snapshot = deepcopy(records)
        annotate_observed_pairs(records)
        self.assertEqual(records,snapshot)
        pair=b['semantic_measurements']['previous_observed_frame']
        self.assertTrue(pair['table_pixels_equal'])
        self.assertEqual(pair['interval_ms'],20000)
        self.assertFalse(pair['same_board_proven'])
        self.assertFalse(pair['settled_proven'])
        b['source_video_sha256']='z'*64
        annotate_observed_pairs(records)
        self.assertNotIn('previous_observed_frame',b['semantic_measurements'])

    def test_malformed_and_foreign_source_fail_closed(self):
        for image in (None, self.image[:12], self.image.astype(float)):
            with self.assertRaises(RecoveryInputError):
                self.adapter.measure(image,source_video_sha256=self.source)
        foreign = self.adapter.measure(self.image,source_video_sha256='f'*64)
        self.assertEqual(foreign['status'],'UNKNOWN_SOURCE_PROFILE_MISMATCH')
        self.assertIsNone(foreign['conditional_compass_mapping'])
        with self.assertRaisesRegex(RecoveryInputError,'PROFILE_PIN'):
            Diana20RuntimeSemantics(b'{}', b'{}', [])


if __name__ == '__main__':
    unittest.main()
