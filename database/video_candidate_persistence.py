#!/usr/bin/env python3
"""Immutable, exactly source-bound candidates and full replay in staging only."""
from __future__ import annotations
import hashlib
import json
import uuid
from typing import Mapping
import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb
from database.runtime_worker_preflight import normalize_dsn
from database.video_staging_lineage import exact_lineage, fixed_school_id

RECEIPT_TYPE = 'video_staging_replay_snapshot'
RECEIPT_SCHEMA = 'video-staging-replay-snapshot-v1'


def _stable_uuid(kind, *parts):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"bridge-school:{kind}:" + '|'.join(map(str, parts)))


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()


def _versioned_stable_key(candidate_type, stable_key, payload_hash):
    if candidate_type != 'video_school_canon_candidate':
        return stable_key
    suffix = f':sha256:{payload_hash}'
    return stable_key if stable_key.endswith(suffix) else f'{stable_key}{suffix}'


def validate_replay(replay):
    if not isinstance(replay, dict) or replay.get('schema') != 'video-canon-staging-replay-v1':
        raise ValueError('FULL_STAGING_REPLAY_REQUIRED')
    if (replay.get('authoritative_write_performed') is not False or replay.get('promotion_commands') != []
            or not isinstance(replay.get('candidates'), list)
            or replay.get('status') not in ('BLOCKED', 'PARTIAL', 'STAGING_READY')):
        raise ValueError('UNSAFE_STAGING_REPLAY')
    if replay.get('replay_sha256') != _digest({k: v for k, v in replay.items() if k != 'replay_sha256'}):
        raise ValueError('REPLAY_HASH_MISMATCH')
    members = replay['candidates']
    if (any(not isinstance(m, dict) or not isinstance(m.get('payload'), dict)
            or m.get('payload_hash') != _digest(m['payload']) for m in members)
            or len({m['payload_hash'] for m in members}) != len(members)):
        raise ValueError('REPLAY_MEMBER_NOT_UNIQUE_OR_INVALID')


def _record_values(record, school_id, lineage, fingerprint, default_method):
    if not isinstance(record, Mapping) or not isinstance(record.get('payload'), Mapping):
        raise ValueError('CANDIDATE_RECORD_INCOMPLETE')
    kind = record.get('candidate_type')
    key = record.get('stable_key') or record.get('candidate_id')
    method = record.get('method_version') or default_method
    if not all(isinstance(v, str) and v for v in (kind, key, method)):
        raise ValueError('CANDIDATE_IDENTITY_INCOMPLETE')
    promotion = str(record.get('promotion_status') or 'STAGING_ONLY').casefold()
    if promotion not in ('staging_only', 'staging', 'review_queue') or record.get('authoritative_tables_modified') is True:
        raise ValueError('CANDIDATE_NOT_STAGING')
    payload = dict(record['payload'])
    payload_hash = _digest(payload)
    if record.get('payload_hash', payload_hash) != payload_hash:
        raise ValueError('CANDIDATE_PAYLOAD_HASH_MISMATCH')
    if kind in ('video_artifact_source_observation', 'video_source_knowledge_draft'):
        from bridge_contracts.video_teacher_read import _project
        _, _, claims = _project(payload, kind)
        if any(claims[k] != lineage[k] for k in ('source_video_file_id', 'source_media_sha256', 'parent_raw_sha256')):
            raise ValueError('CANDIDATE_SOURCE_HASH_MISMATCH')
    key = _versioned_stable_key(kind, key, payload_hash)
    candidate_id = _stable_uuid('analysis-candidate', school_id, kind, key, method, fingerprint)
    return (candidate_id, school_id, uuid.UUID(lineage['analysis_run_id']), uuid.UUID(lineage['source_id']),
            kind, key, fingerprint, str(record.get('quality_status') or 'UNKNOWN'),
            'review_queue' if promotion == 'review_queue' else 'staging', payload, payload_hash,
            list(record.get('evidence_refs') or []), list(record.get('reasons') or []), method, 'active')


_COLUMNS = '''analysis_candidate_id, school_id, analysis_run_id, source_id, candidate_type,
              stable_key, input_fingerprint, quality_status, promotion_status, payload,
              payload_hash, evidence_refs, rejection_reasons, method_version, status'''


def _insert_exact(cursor, values):
    params = list(values)
    for i in (9, 11, 12):
        params[i] = Jsonb(params[i])
    cursor.execute(f'''INSERT INTO public.analysis_candidate ({_COLUMNS})
                      VALUES ({', '.join(['%s'] * len(values))})
                      ON CONFLICT (school_id,candidate_type,stable_key,method_version,input_fingerprint)
                      DO NOTHING''', params)
    inserted = bool(cursor.rowcount)
    cursor.execute(f'''SELECT {_COLUMNS} FROM public.analysis_candidate
                       WHERE school_id=%s AND analysis_candidate_id=%s''', (values[1], values[0]))
    stored = cursor.fetchone()
    if stored != values or _digest(stored[9]) != stored[10]:
        raise ValueError('CANDIDATE_IDEMPOTENCY_CONFLICT')
    return inserted


def persist_quality_candidates_on_connection(connection, payload, *, persisted_result):
    """Actual SQL on a caller-owned authorized connection.

    Supply the exact persist_video_result return separately from artifact JSON.
    IDs select rows; DB lineage is verified independently. This seam permits a
    disposable PostgreSQL rehearsal without SQL mocks or weaker production DSNs.
    Begin an outer transaction before combining helpers: transaction() commits
    on exit when the connection was idle, otherwise it uses a savepoint.
    """
    quality = payload.get('quality_v2') or {}
    records = quality.get('candidate_staging_records') or []
    if not isinstance(records, list):
        raise ValueError('candidate_staging_records must be a list')
    if not persisted_result or persisted_result.get('persisted') is not True:
        return {'status': 'BLOCKED', 'gaps': ['EXACT_PERSISTED_RESULT_REQUIRED'], 'authoritative_tables_modified': False}
    job_id = str(payload.get('job_id') or '')
    default_method = str(quality.get('method_version') or payload.get('quality_method_version') or 'unknown')
    replay = quality.get('video_canon_auto_pipeline')
    has_replay = isinstance(replay, dict) and replay.get('schema') == 'video-canon-staging-replay-v1'
    if any(isinstance(r, Mapping) and str(r.get('candidate_type', '')).startswith('video_') for r in records) and not has_replay:
        raise ValueError('FULL_STAGING_REPLAY_REQUIRED')
    if has_replay:
        validate_replay(replay)
        for member in replay['candidates']:
            if sum(r == member for r in records) != 1:
                raise ValueError('REPLAY_MEMBER_NOT_UNIQUE')
        for record in records:
            if (isinstance(record, Mapping) and str(record.get('candidate_type', '')).startswith('video_')
                    and record not in replay['candidates']):
                raise ValueError('VIDEO_CANDIDATE_OUTSIDE_REPLAY')
    with connection.transaction():
        with connection.cursor(row_factory=tuple_row) as cursor:
            cursor.execute("SELECT to_regclass('public.analysis_candidate') IS NOT NULL")
            if not cursor.fetchone()[0]:
                return {'status': 'SCHEMA_NOT_READY', 'required_migration': '0049_analysis_candidate_staging'}
            school_id = fixed_school_id(cursor)
            lineage = exact_lineage(cursor, school_id, persisted_result, job_id=job_id)
            binding = {k: v for k, v in lineage.items() if k not in ('run_status', 'publication_authorization_status')}
            fingerprint = _digest({'quality_input_fingerprint': (quality.get('incremental_processing') or {}).get('input_fingerprint'),
                                   'binding': binding, 'replay_sha256': replay['replay_sha256'] if has_replay else None})
            values = [_record_values(r, school_id, lineage, fingerprint, default_method) for r in records]
            if len({r[0] for r in values}) != len(values):
                raise ValueError('DUPLICATE_CANDIDATE_IDENTITY')
            receipt_id = None
            if has_replay:
                members = [{'analysis_candidate_id': str(v[0]), 'payload_hash': v[10]}
                           for record, v in zip(records, values) if record in replay['candidates']]
                receipt_payload = {'schema': RECEIPT_SCHEMA, 'binding': binding, 'members': members, 'replay': replay}
                receipt_record = {'candidate_type': RECEIPT_TYPE, 'stable_key': _digest(receipt_payload),
                                  'method_version': RECEIPT_SCHEMA, 'quality_status': 'TECHNICAL_REPLAY_SNAPSHOT',
                                  'payload': receipt_payload}
                receipt_values = _record_values(receipt_record, school_id, lineage, fingerprint, default_method)
                receipt_id = receipt_values[0]
                values.append(receipt_values)
            inserted = sum(_insert_exact(cursor, row) for row in values)
            if receipt_id:
                output_id = _stable_uuid('video-replay-output', lineage['analysis_run_id'], receipt_id)
                cursor.execute('''INSERT INTO public.analysis_run_output
                    (analysis_run_output_id, analysis_run_id, output_entity_id, output_entity_type, output_role, status)
                    VALUES (%s,%s,%s,'analysis_candidate','derived','staging') ON CONFLICT DO NOTHING''',
                    (output_id,
                     lineage['analysis_run_id'], receipt_id))
                cursor.execute('''SELECT analysis_run_id,output_entity_id,output_entity_type,output_role,status,
                                         artifact_version_id,publication_id
                                    FROM public.analysis_run_output WHERE analysis_run_output_id=%s''', (output_id,))
                if cursor.fetchone() != (uuid.UUID(lineage['analysis_run_id']), receipt_id,
                                         'analysis_candidate', 'derived', 'staging', None, None):
                    raise ValueError('REPLAY_OUTPUT_IDEMPOTENCY_CONFLICT')
    return {'status': 'PERSISTED', 'inserted': inserted, 'already_existing': len(values)-inserted,
            'candidate_records': len(records), 'analysis_run_id': lineage['analysis_run_id'],
            'source_id': lineage['source_id'], 'source_asset_id': lineage['source_asset_id'],
            'candidate_ids': [str(row[0]) for row in values if row[4] != RECEIPT_TYPE],
            'replay_receipt_id': str(receipt_id) if receipt_id else None,
            'input_fingerprint': fingerprint, 'method_version': default_method, 'authoritative_tables_modified': False}


def persist_quality_candidates(raw_dsn, payload, *, persisted_result=None):
    if not persisted_result or persisted_result.get('persisted') is not True:
        return {'status': 'BLOCKED', 'gaps': ['EXACT_PERSISTED_RESULT_REQUIRED'], 'authoritative_tables_modified': False}
    dsn = normalize_dsn(raw_dsn)
    if not dsn:
        return {'status': 'DATABASE_URL_NOT_CONFIGURED'}
    with psycopg.connect(dsn, connect_timeout=10, application_name='bridge-video-candidate-staging') as connection:
        return persist_quality_candidates_on_connection(connection, payload, persisted_result=persisted_result)


__all__ = ['persist_quality_candidates', 'persist_quality_candidates_on_connection']
