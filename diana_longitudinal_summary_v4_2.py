#!/usr/bin/env python3
"""Human-facing v4.2 summary with explicit evidence provenance and coverage.

This module changes reporting only. It does not alter evidence, readiness,
board reconstruction, candidate JSON, authority gates, or source media.
"""
from __future__ import annotations

from typing import Any, Mapping

_DEPRECATED_HUMAN_COUNT_KEYS = {
    'promotable_knowledge_candidates',
    'promotable_knowledge_candidates_deprecated_alias',
}


def _int(counts: Mapping[str, Any], key: str) -> int:
    try:
        return int(counts.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def render_summary(payload: Mapping[str, Any]) -> str:
    lesson = payload['lesson_identity']
    quality = payload['quality_v2']
    counts = quality['counts']
    readiness = quality['readiness']
    board = quality.get('board_reconstruction_v4_2') or {}

    total_complete = _int(counts, 'complete_learning_interactions')
    evidence_complete = _int(counts, 'transcript_decision_window_complete_interactions_v4_1')
    partial = _int(counts, 'partial_learning_interactions')
    evidence_denominator = evidence_complete + partial
    coverage = (100.0 * evidence_complete / evidence_denominator) if evidence_denominator else 0.0

    visual_clusters = int(board.get('report_visual_clusters') or 0)
    visual_partial = int(board.get('report_visual_partial_boards') or 0)
    visual_full = int(board.get('report_visual_verified_full_boards') or 0)
    recognized = int(board.get('recognized_card_union_total') or 0)

    lines = [
        '# Диана — продольное извлечение v4.2',
        '',
        f"- Job ID: `{payload['job_id']}`",
        f"- Дата занятия: **{lesson.get('lesson_date') or 'не установлена'}** ({lesson.get('lesson_date_status')})",
        f"- TECHNICAL: **{readiness.get('technical_status')}**",
        f"- CONTENT: **{readiness.get('content_status')}**",
        f"- METHODOLOGY: **{readiness.get('methodology_status')}**",
        '- METHODOLOGY_READY означает готовность доказанных эпизодов к разрешённому методическому анализу; это не утверждение о полном покрытии урока.',
        '- MASTER-источник использован только для чтения.',
        '- Тяжёлая повторная обработка видео не выполнялась этим слоем.',
        '',
        '## Evidence coverage / provenance',
        '',
        f'- Evidence-linked complete interactions (v4.1 decision windows): **{evidence_complete}**.',
        f'- Все complete interactions в агрегированном quality layer: **{total_complete}**.',
        f'- Partial learning interactions: **{partial}**.',
        f'- Evidence-linked complete coverage среди complete+partial: **{coverage:.1f}%**.',
    ]
    if total_complete != evidence_complete:
        lines.append(
            '- Provenance note: агрегированный complete-счётчик и evidence-linked v4.1 счётчик различаются; '
            'они имеют разный scope и не должны интерпретироваться как один показатель.'
        )

    lines += [
        '',
        '## Board reconstruction coverage',
        '',
        f'- Report-visual board clusters: **{visual_clusters}**.',
        f'- Partial boards: **{visual_partial}**.',
        f'- Verified full boards: **{visual_full}**.',
        f'- Recognized card union total: **{recognized}**.',
        '- VERIFIED_FULL_BOARD требует 52 уникальные доказанные карты; скрытые руки не достраиваются дополнением колоды.',
        '',
        '## Quality-first counts',
        '',
    ]
    for key, value in counts.items():
        if key in _DEPRECATED_HUMAN_COUNT_KEYS:
            continue
        lines.append(f'- {key}: **{value}**')

    lines += [
        '',
        '## Knowledge authority',
        '',
        '- Knowledge candidates остаются review/staging candidates. Наличие кандидата не даёт permission на canon/curriculum/profile promotion.',
        '- Deprecated `promotable_knowledge_candidates` aliases сохранены только в machine-readable JSON для backward compatibility и намеренно скрыты здесь.',
        '',
        '## Cost Gate',
        '',
        '- Платные AI API: **0**.',
        f"- Повторное скачивание/ASR оригинального видео: **{payload['cost']['heavy_video_reprocessed']}**.",
        f"- Повторно использован master PDF размером {payload['provenance']['master_pdf'].get('size_bytes', 0)} байт.",
        '',
    ]
    return '\n'.join(lines) + '\n'


__all__ = ['render_summary']
