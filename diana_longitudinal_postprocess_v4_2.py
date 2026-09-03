#!/usr/bin/env python3
"""Idempotent semantic postprocess with report-visual board reconstruction v4.2."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

import diana_longitudinal_postprocess as base
from bridge_report_board_reconstruction import reconstruct_report_visual_deals
from diana_longitudinal_quality_v4_2 import (
    QUALITY_METHOD_VERSION,
    QUALITY_SCHEMA_VERSION,
    build_quality_layer,
)

SCHEMA_VERSION = 5


def _safe_filename(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', '_', value).strip()[:160]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode('utf-8')


def _stable_database_summary(runtime: Mapping[str, Any] | None, candidate_records: int) -> dict[str, Any]:
    runtime = dict(runtime or {})
    status = str(runtime.get('status') or 'UNKNOWN')
    result = {
        'status': status,
        'candidate_records': int(runtime.get('candidate_records') or candidate_records or 0),
        'input_fingerprint': runtime.get('input_fingerprint'),
        'method_version': runtime.get('method_version') or QUALITY_METHOD_VERSION,
        'authoritative_tables_modified': bool(runtime.get('authoritative_tables_modified', False)),
    }
    # Stable failure state is material and belongs in the artifact; volatile row
    # counts/analysis_run_id do not.
    if status not in {'PERSISTED', 'NOT_REQUESTED'}:
        for key in ('required_migration', 'error_class', 'detail'):
            if runtime.get(key) is not None:
                result[key] = runtime.get(key)
    return result


def _stable_created_at(master: Mapping[str, Any], done_item: Mapping[str, Any], master_pdf_meta: Mapping[str, Any]) -> str:
    for candidate in (
        master.get('createdAt'),
        done_item.get('createdTime'),
        master_pdf_meta.get('createdTime'),
    ):
        value = str(candidate or '').strip()
        if value:
            return value
    # Deterministic fail-closed fallback; never inject wall-clock time into content identity.
    return '1970-01-01T00:00:00Z'


def _generation_key(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:12]


def _upload_idempotent_verified(token: str, parent: str, path: Path, mime: str) -> dict[str, Any]:
    escaped = path.name.replace("'", "\\'")
    existing = base.io.search(token, f"'{parent}' in parents and trashed=false and name='{escaped}'")
    if existing:
        existing.sort(key=lambda item: item.get('modifiedTime') or '', reverse=True)
        local_sha = base.io.sha(path)
        with tempfile.TemporaryDirectory(prefix='diana-v42-existing-') as td:
            existing_path = Path(td) / path.name
            base.io.download(token, existing[0]['id'], existing_path)
            existing_sha = base.io.sha(existing_path)
        if existing_sha != local_sha:
            raise RuntimeError('LONGITUDINAL_EXISTING_ARTIFACT_CONTENT_MISMATCH')
        return {
            'id': existing[0]['id'],
            'name': existing[0].get('name'),
            'status': 'already_exists_verified',
            'sha256': local_sha,
        }
    uploaded = base.io.upload_file(token, parent, path, mime)
    return {
        'id': uploaded['id'],
        'name': uploaded.get('name'),
        'status': 'uploaded',
        'sha256': base.io.sha(path),
    }


def _summary_markdown(payload: Mapping[str, Any]) -> str:
    text = base._summary_markdown(payload)
    text = text.replace('# Диана — продольное извлечение v2', '# Диана — продольное извлечение v4.2')
    marker = '## Quality-first counts'
    note = '\n'.join([
        '## Evidence-linked learning + board reconstruction v4.2',
        '',
        '- Learning Interaction keeps the evidence-linked v4.1 gates unchanged.',
        '- Existing master-PDF report screenshots are parsed without reprocessing source video or ASR.',
        '- Only clearly visible legacy-BBO North/South horizontal hands are accepted by the first board parser.',
        '- Hidden East/West cards are never inferred and the deck complement is never used as evidence.',
        '- Screenshot states are joined only by strong exact seat-card content overlap; time/topic/board number alone never identify a board.',
        '- A full board still requires 52 unique cards under the inherited verification gate.',
        '- Repeat semantic runs use a deterministic content generation key and verify existing Drive file hashes before reuse.',
        '- Runtime database insert/already-existing counts do not alter artifact identity.',
        '- Canon, curriculum, methodology activation and person-specific profile writes remain denied.',
        '',
    ])
    return text.replace(marker, note + marker)


def _reconstruct_from_master_pdf(token: str, master: Mapping[str, Any], master_pdf: Mapping[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='diana-v42-board-') as td:
        path = Path(td) / 'master.pdf'
        base.io.download(token, str(master_pdf['drive_id']), path)
        if base.io.sha(path).lower() != str(master_pdf.get('pdf_sha256') or '').lower():
            raise RuntimeError('LONGITUDINAL_V42_MASTER_PDF_SHA_MISMATCH')
        return reconstruct_report_visual_deals(path, master)


def main() -> int:
    job_id = os.environ.get('BRIDGE_JOB_ID', '').strip()
    result_folder = os.environ.get('BRIDGE_OUTPUT_FOLDER_ID', '').strip()
    work_folder = os.environ.get('BRIDGE_WORK_FOLDER_ID', '').strip()
    if not job_id or not result_folder:
        raise RuntimeError('LONGITUDINAL_JOB_AND_OUTPUT_FOLDER_REQUIRED')
    token = base.user_oauth_token()
    if not token:
        raise RuntimeError('BLOCKED_ACCESS: Drive OAuth unavailable')

    done_item, done = base._latest_done(token, job_id)
    master, master_pdf = base._load_master(token, done)
    if master.get('job_id') != job_id:
        raise RuntimeError('LONGITUDINAL_JOB_ID_MISMATCH')

    source_id = str((master.get('source') or {}).get('driveId') or '')
    source_meta = base._drive_metadata(token, source_id)
    original_id = os.environ.get('BRIDGE_ORIGINAL_SOURCE_DRIVE_ID', '').strip()
    original_meta = base._drive_metadata(token, original_id) if original_id else None
    lesson = base._lesson_identity(master, source_meta, original_meta)

    reconstruction = _reconstruct_from_master_pdf(token, master, master_pdf)
    working_master = deepcopy(master)
    working_master['report_visual_board_deals'] = reconstruction.get('deals') or []
    working_master['report_visual_board_reconstruction'] = {
        'method_version': reconstruction.get('method_version'),
        'parser_scope': reconstruction.get('parser_scope'),
        'qc': reconstruction.get('qc') or {},
    }
    quality = build_quality_layer(working_master, lesson)
    curriculum = base._curriculum(working_master, lesson, quality)
    gaps = list(working_master.get('knowledge_gaps') or [])
    if lesson.get('lesson_date_status') != 'CONFIRMED':
        gaps.append({
            'gap_id': base._stable_id('gap', job_id, 'lesson-date-confirmation'),
            'gap_type': 'SOURCE_WEAK',
            'status': 'OPEN',
            'question': 'Подтвердить фактическую дату занятия независимым источником.',
            'date_evidence': lesson.get('date_evidence') or [],
        })

    master_pdf_drive_meta = base._drive_metadata(token, str(master_pdf['drive_id']))
    stable_created_at = _stable_created_at(working_master, done_item, master_pdf_drive_meta)
    payload: dict[str, Any] = {
        'schema': base.SCHEMA,
        'schema_version': SCHEMA_VERSION,
        'quality_method_version': QUALITY_METHOD_VERSION,
        'created_at': stable_created_at,
        'job_id': job_id,
        'algorithm_version': working_master.get('algorithmVersion'),
        'algorithm_revision': working_master.get('algorithmRevision'),
        'authority': {
            'canon_activation': 'DENY',
            'curriculum_activation': 'DENY',
            'student_profile_production_write': 'DENY',
            'methodology_activation': 'DENY',
            'artifact_status': 'A1_CANDIDATE_EVIDENCE',
        },
        'provenance': {
            'ai_done_drive_id': done_item['id'],
            'master_pdf': master_pdf,
            'master_json_job_id_verified': True,
            'source_read_only': True,
            'report_visual_board_reconstruction': working_master['report_visual_board_reconstruction'],
        },
        'lesson_identity': lesson,
        'quality_v2': quality,
        'curriculum': curriculum,
        'knowledge_gaps': gaps,
        'technical_qc': working_master.get('technical_qc') or {},
        'warnings': working_master.get('warnings') or [],
        'cost': {
            'paid_ai_api_cost': 0,
            'paid_cloud_fallback_used': False,
            'source_size_bytes': int(source_meta.get('size') or 0),
            'heavy_video_reprocessed': False,
            'asr_reprocessed': False,
            'master_pdf_reused_for_visual_board_reconstruction': True,
            'persistent_heavy_working_video_copy_created': False,
            'reused_master_pdf_bytes': master_pdf.get('size_bytes', 0),
            'cost_note': 'v4.2 reuses embedded master analysis and report images; provider plan accounting remains external.',
        },
    }

    # Persist before artifact sealing; only a stable summary enters content identity.
    runtime_database = base._persist_staging_if_configured(payload)
    payload['database_staging'] = _stable_database_summary(
        runtime_database,
        int((quality.get('counts') or {}).get('staging_records') or 0),
    )
    key = _generation_key(payload)
    payload['file_idempotency'] = {
        'generation_key': key,
        'content_addressed': True,
        'wall_clock_excluded_from_generation': True,
        'database_runtime_insert_counts_excluded_from_generation': True,
        'existing_name_requires_sha256_match': True,
    }
    raw = _canonical_bytes(payload)

    lesson_label = lesson.get('lesson_number') or '?'
    base_name = _safe_filename(f'Диана {lesson_label} — longitudinal v4.2 {key}')
    with tempfile.TemporaryDirectory(prefix='diana-longitudinal-v42-output-') as td:
        td_path = Path(td)
        json_path = td_path / f'{base_name}.json'
        summary_path = td_path / f'{base_name} — summary.md'
        teacher_path = td_path / f'Диана {lesson_label} — Teacher Brief v4.2 {key}.md'
        cards_path = td_path / f'Диана {lesson_label} — Learning Cards v4.2 {key}.md'
        staging_path = td_path / f'Диана {lesson_label} — Candidate Staging v4.2 {key}.json'
        receipt_path = td_path / f'LONGITUDINAL_V42_DONE_{job_id}_{key}.json'

        json_path.write_bytes(raw)
        summary_path.write_text(_summary_markdown(payload), encoding='utf-8')
        teacher_path.write_text(base._teacher_brief_markdown(payload), encoding='utf-8')
        cards_path.write_text(base._cards_markdown(payload), encoding='utf-8')
        staging_path.write_text(
            json.dumps(quality.get('candidate_staging_records') or [], ensure_ascii=False, indent=2, sort_keys=True),
            encoding='utf-8',
        )
        receipt = {
            'status': 'LONGITUDINAL_V42_DONE',
            'job_id': job_id,
            'generation_key': key,
            'schema_version': SCHEMA_VERSION,
            'quality_method_version': QUALITY_METHOD_VERSION,
            'readiness': quality.get('readiness') or {},
            'counts': quality.get('counts') or {},
            'board_reconstruction': quality.get('board_reconstruction_v4_2') or {},
            'master_pdf_drive_id': master_pdf['drive_id'],
            'source_drive_id': source_id,
            'source_untouched': True,
            'heavy_video_reprocessed': False,
            'asr_reprocessed': False,
            'canon_activated': False,
            'student_profile_written': False,
            'curriculum_activated': False,
            'database_staging': payload.get('database_staging'),
            'created_at': stable_created_at,
        }
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        uploads = [
            _upload_idempotent_verified(token, result_folder, json_path, 'application/json'),
            _upload_idempotent_verified(token, result_folder, summary_path, 'text/markdown'),
            _upload_idempotent_verified(token, result_folder, teacher_path, 'text/markdown'),
            _upload_idempotent_verified(token, result_folder, cards_path, 'text/markdown'),
            _upload_idempotent_verified(token, result_folder, staging_path, 'application/json'),
            _upload_idempotent_verified(token, work_folder or result_folder, receipt_path, 'application/json'),
        ]

    source_after = base._drive_metadata(token, source_id)
    if result_folder in (source_after.get('parents') or []) or (work_folder and work_folder in (source_after.get('parents') or [])):
        raise RuntimeError('LONGITUDINAL_SOURCE_MOVED_TO_WORK_OR_RESULT')

    print(json.dumps({
        'stage': 'DIANA_LONGITUDINAL_POSTPROCESS_V42',
        'status': 'DONE',
        'job_id': job_id,
        'generation_key': key,
        'source_untouched': True,
        'heavy_video_reprocessed': False,
        'asr_reprocessed': False,
        'readiness': quality.get('readiness'),
        'counts': quality.get('counts'),
        'board_reconstruction': quality.get('board_reconstruction_v4_2'),
        'database_staging_runtime': runtime_database,
        'database_staging_artifact': payload.get('database_staging'),
        'uploads': uploads,
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
