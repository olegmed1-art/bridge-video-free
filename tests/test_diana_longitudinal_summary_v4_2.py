from __future__ import annotations

import unittest

from diana_longitudinal_summary_v4_2 import render_summary


def payload(total_complete=11, evidence_complete=9, partial=86):
    return {
        'job_id': 'e2ab670e74c806d22feb823c902da483',
        'lesson_identity': {'lesson_date': '2021-03-29', 'lesson_date_status': 'CANDIDATE_MEDIUM'},
        'quality_v2': {
            'readiness': {
                'technical_status': 'TECHNICAL_READY',
                'content_status': 'CONTENT_EXTRACTED',
                'methodology_status': 'METHODOLOGY_READY',
            },
            'counts': {
                'complete_learning_interactions': total_complete,
                'partial_learning_interactions': partial,
                'transcript_decision_window_complete_interactions_v4_1': evidence_complete,
                'knowledge_candidates_for_review': 59,
                'promotable_knowledge_candidates': 59,
                'promotable_knowledge_candidates_deprecated_alias': 59,
            },
            'board_reconstruction_v4_2': {
                'report_visual_clusters': 5,
                'report_visual_partial_boards': 5,
                'report_visual_verified_full_boards': 0,
                'recognized_card_union_total': 91,
            },
        },
        'cost': {'heavy_video_reprocessed': False},
        'provenance': {'master_pdf': {'size_bytes': 19943975}},
    }


class DianaLongitudinalSummaryV42Tests(unittest.TestCase):
    def test_d4_style_count_divergence_is_explained(self):
        text = render_summary(payload())
        self.assertIn('Evidence-linked complete interactions (v4.1 decision windows): **9**', text)
        self.assertIn('Все complete interactions в агрегированном quality layer: **11**', text)
        self.assertIn('разный scope', text)
        self.assertIn('METHODOLOGY_READY означает', text)

    def test_deprecated_promotable_aliases_are_hidden_from_counts(self):
        text = render_summary(payload())
        quality_section = text.split('## Quality-first counts', 1)[1].split('## Knowledge authority', 1)[0]
        self.assertIn('knowledge_candidates_for_review', quality_section)
        self.assertNotIn('promotable_knowledge_candidates', quality_section)

    def test_board_gate_is_explicit_and_not_weakened(self):
        text = render_summary(payload())
        self.assertIn('Verified full boards: **0**', text)
        self.assertIn('52 уникальные доказанные карты', text)
        self.assertIn('скрытые руки не достраиваются дополнением колоды', text)

    def test_equal_scopes_do_not_emit_divergence_note(self):
        text = render_summary(payload(total_complete=3, evidence_complete=3, partial=65))
        self.assertNotIn('разный scope', text)


if __name__ == '__main__':
    unittest.main()
