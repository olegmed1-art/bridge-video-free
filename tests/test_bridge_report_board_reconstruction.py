from __future__ import annotations

import unittest

from bridge_report_board_reconstruction import (
    cluster_observations,
    compatible,
    visual_deals_from_clusters,
)


class BridgeReportBoardReconstructionTests(unittest.TestCase):
    def test_content_overlap_clusters_same_visible_board(self):
        a = {
            'evidence_id': 'frame_a', 'time': 10,
            'hands': {'N': ['AH','KH','QH','JH','TC','9C','8D','7S']},
            'recognized_card_count': 8,
        }
        b = {
            'evidence_id': 'frame_b', 'time': 20,
            'hands': {'N': ['AH','KH','QH','JH','TC','9C','6D','5S']},
            'recognized_card_count': 8,
        }
        self.assertTrue(compatible(a, b))
        clusters = cluster_observations([a, b])
        self.assertEqual(len(clusters), 1)
        deals, qc = visual_deals_from_clusters(clusters, '0' * 32)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]['statement_type'], 'VISUAL_EVIDENCE')
        self.assertFalse('E' in (deals[0].get('visual_seats_observed') or []))
        self.assertFalse('W' in (deals[0].get('visual_seats_observed') or []))
        self.assertIn('time/topic/board-number alone never identify a board', deals[0]['reconstruction_rule'])
        self.assertEqual(qc[0]['status'], 'ACCEPTED_PARTIAL')

    def test_time_proximity_without_card_overlap_never_merges(self):
        a = {'evidence_id': 'frame_a', 'time': 10, 'hands': {'N': ['AH','KH','QH','JH','TH','9H']}, 'recognized_card_count': 6}
        b = {'evidence_id': 'frame_b', 'time': 11, 'hands': {'N': ['AS','KS','QS','JS','TS','9S']}, 'recognized_card_count': 6}
        self.assertFalse(compatible(a, b))
        self.assertEqual(len(cluster_observations([a, b])), 2)

    def test_cross_seat_conflict_fails_closed(self):
        a = {'evidence_id': 'frame_a', 'hands': {'N': ['AH','KH','QH','JH','TH','9H']}}
        b = {'evidence_id': 'frame_b', 'hands': {'S': ['AH','2C','3C','4C','5C','6C']}}
        self.assertFalse(compatible(a, b))
        deals, qc = visual_deals_from_clusters([[a, b]], '0' * 32)
        self.assertEqual(deals, [])
        self.assertEqual(qc[0]['status'], 'REJECTED_CONFLICT')

    def test_partial_visual_deal_never_infers_hidden_hands(self):
        obs = {'evidence_id': 'frame_a', 'hands': {'N': ['AH','KH','QH','JH','TH','9H']}}
        deals, _ = visual_deals_from_clusters([[obs]], '0' * 32)
        self.assertEqual(len(deals), 1)
        self.assertIsNone(deals[0]['hands']['E'])
        self.assertIsNone(deals[0]['hands']['W'])
        self.assertIsNone(deals[0]['contract'])
        self.assertIsNone(deals[0]['declarer'])


if __name__ == '__main__':
    unittest.main()
