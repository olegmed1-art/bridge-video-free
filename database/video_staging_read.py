"""Internal AI staging read on an already authorized read-only DB connection.

No HTTP route, credential loader, role claims or school selectors are provided.
The service owner supplies the connection via its existing trusted context.
This function is not student/private-person publication authorization.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID
from urllib.parse import quote
import re
import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from bridge_contracts.video_teacher_read import _Blocked, _project, _validate
from database.video_candidate_persistence import RECEIPT_SCHEMA, RECEIPT_TYPE, _COLUMNS, _digest, validate_replay
from database.video_staging_lineage import exact_lineage, fixed_school_id


# Public diagnostics are application constants, never formatted exception text.
# A new domain code needs explicit review here before it can cross the API boundary.
_PUBLIC_BLOCK_CODES = (
    'CANDIDATE_ID_INVALID', 'CANDIDATE_NOT_AVAILABLE', 'CANDIDATE_NOT_CURRENT_STAGING',
    'DISCLOSURE_BINDING_MISSING', 'EXACT_RESULT_BINDING_REQUIRED',
    'EXACT_SOURCE_RUN_ASSET_BINDING_MISMATCH', 'FIXED_SCHOOL_UNAVAILABLE',
    'FULL_STAGING_REPLAY_REQUIRED', 'INTERVAL_INVALID', 'PAYLOAD_HASH_MISMATCH',
    'READ_BINDING_MISMATCH', 'READ_BINDING_STALE_OR_REVOKED',
    'READ_ONLY_CONNECTION_REQUIRED', 'REPEATABLE_READ_REQUIRED',
    'REPLAY_CANDIDATE_BINDING_MISMATCH', 'REPLAY_HASH_MISMATCH', 'REPLAY_LINEAGE_MISMATCH',
    'REPLAY_MEMBER_NOT_UNIQUE_OR_INVALID', 'REPLAY_OUTPUT_LINK_MISSING',
    'REPLAY_RECEIPT_HASH_MISMATCH', 'REPLAY_RECEIPT_MISSING', 'REPLAY_RECEIPT_UNSAFE',
    'REPLAY_SNAPSHOT_INTEGRITY_MISMATCH', 'REPLAY_SNAPSHOT_NOT_UNIQUE',
    'SCOPE_BINDING_MISMATCH', 'SOURCE_BINDING_MISSING', 'SOURCE_HASH_BINDING_MISMATCH',
    'SOURCE_JOB_BINDING_MISMATCH', 'SOURCE_LOCATOR_MISMATCH', 'SOURCE_NATIVE_IDENTITY_MISSING',
    'SOURCE_RUN_BINDING_MISMATCH', 'SOURCE_RUN_BINDING_MISSING', 'SOURCE_RUN_NOT_CURRENT',
    'STAGE_UNSUPPORTED', 'TEXT_HASH_MISMATCH', 'TEXT_INVALID', 'TRUSTED_READ_BINDING_MISSING',
    'UNSAFE_STAGING_REPLAY',
)


def _as_dict(values):
    return {k.strip(): str(v) if isinstance(v, UUID) else v for k, v in zip(_COLUMNS.split(','), values)}


def read_internal_school_video_candidate(connection, candidate_id):
    """Read one exact staging candidate; the only data selector is its UUID.

    Requires a read-only transaction. The connection is a server dependency,
    never a DSN or role/school assertion from a request or generated artifact.
    Pending runs may yield a SOURCE_OBSERVATION with their pending state shown.
    """
    try:
        if str(UUID(candidate_id)) != candidate_id:
            raise ValueError('CANDIDATE_ID_INVALID')
        with connection.transaction():
            with connection.cursor(row_factory=tuple_row) as cursor:
                cursor.execute('SHOW transaction_read_only')
                if cursor.fetchone()[0] != 'on':
                    raise ValueError('READ_ONLY_CONNECTION_REQUIRED')
                cursor.execute('SHOW transaction_isolation')
                if cursor.fetchone()[0] != 'repeatable read':
                    raise ValueError('REPEATABLE_READ_REQUIRED')
                school_id = fixed_school_id(cursor)
                cursor.execute(f'''SELECT {_COLUMNS} FROM public.analysis_candidate
                    WHERE school_id=%s AND analysis_candidate_id=%s AND status='active'
                      AND promotion_status IN ('staging','review_queue')''', (school_id, candidate_id))
                values = cursor.fetchone()
                if values is None:
                    raise ValueError('CANDIDATE_NOT_AVAILABLE')
                row = _as_dict(values)
                cursor.execute(f'''SELECT {_COLUMNS} FROM public.analysis_candidate
                    WHERE school_id=%s AND analysis_run_id=%s AND source_id=%s
                      AND candidate_type=%s AND status='active' AND promotion_status='staging'
                      AND payload->'members' @> %s''',
                    (school_id, row['analysis_run_id'], row['source_id'], RECEIPT_TYPE,
                     Jsonb([{'analysis_candidate_id': candidate_id, 'payload_hash': row['payload_hash']}])) )
                receipts = cursor.fetchall()
                if len(receipts) != 1:
                    raise ValueError('REPLAY_SNAPSHOT_NOT_UNIQUE')
                snapshot = _as_dict(receipts[0])
                envelope = snapshot['payload']
                if (envelope.get('schema') != RECEIPT_SCHEMA or snapshot['payload_hash'] != _digest(envelope)
                        or snapshot['stable_key'] != snapshot['payload_hash']
                        or snapshot['method_version'] != RECEIPT_SCHEMA
                        or snapshot['input_fingerprint'] != row['input_fingerprint']):
                    raise ValueError('REPLAY_SNAPSHOT_INTEGRITY_MISMATCH')
                lineage = exact_lineage(cursor, school_id, envelope['binding'])
                expected = {k: v for k, v in lineage.items() if k not in ('run_status', 'publication_authorization_status')}
                if envelope['binding'] != expected or any(row[k] != lineage[k] for k in ('school_id','source_id','analysis_run_id')):
                    raise ValueError('REPLAY_LINEAGE_MISMATCH')
                cursor.execute('''SELECT analysis_run_output_id FROM public.analysis_run_output
                    WHERE analysis_run_id=%s AND output_entity_id=%s AND output_entity_type='analysis_candidate'
                      AND output_role='derived' AND status='staging'
                      AND artifact_version_id IS NULL AND publication_id IS NULL ''',
                    (row['analysis_run_id'], snapshot['analysis_candidate_id']))
                if len(cursor.fetchall()) != 1:
                    raise ValueError('REPLAY_OUTPUT_LINK_MISSING')
                replay = envelope['replay']
                validate_replay(replay)
                _, _, claims = _project(row['payload'], row['candidate_type'])
                if any(claims[k] != lineage[k] for k in ('source_video_file_id','source_media_sha256','parent_raw_sha256')):
                    raise ValueError('SOURCE_HASH_BINDING_MISMATCH')
                now = datetime.now(timezone.utc)
                binding = {k: row[k] for k in ('analysis_candidate_id','school_id','source_id','analysis_run_id','payload_hash')}
                binding.update(claims, replay_sha256=replay['replay_sha256'], status='active', revoked=False,
                               checked_at=now, valid_until=now+timedelta(seconds=60))
                data = {'candidate': row, 'receipt': replay, 'binding': binding,
                        'source': {'source_id': row['source_id'], 'school_id': str(school_id), 'status':'active',
                                   'video_file_id': lineage['source_video_file_id'], 'media_sha256': lineage['source_media_sha256']},
                        'run': {'analysis_run_id': row['analysis_run_id'], 'school_id': str(school_id),
                                'input_source_id': row['source_id'], 'run_status': lineage['run_status'],
                                'publication_authorization_status': lineage['publication_authorization_status']}}
                result = _validate(candidate_id, str(school_id), data, now, school_internal=True)
                result.update(schema='video-school-internal-staging-read-v1', audience='INTERNAL_SCHOOL_AI',
                              source_locator=lineage['canonical_locator'], source_asset_id=lineage['source_asset_id'],
                              artifact_version_id=lineage['artifact_version_id'], run_status=lineage['run_status'],
                              replay_status=replay['status'], replay_receipt_id=snapshot['analysis_candidate_id'],
                              source_url='https://drive.google.com/file/d/'+quote(lineage['source_video_file_id'],safe='')+'/view',
                              parent_raw_sha256=claims['parent_raw_sha256'],
                              parent_canonical_sha256=claims['parent_canonical_sha256'],
                              source_media_sha256_claim=claims['source_media_sha256'],
                              student_delivery_allowed=False)
                # Codes only: never disclose arbitrary private text from diagnostic payloads.
                result['replay_gap_codes'] = [g['code'] for g in replay.get('gaps', [])
                                              if isinstance(g, dict) and isinstance(g.get('code'), str)
                                              and re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', g['code'])]
                return result
    except (_Blocked, ValueError) as exc:
        code = next((public_code for public_code in _PUBLIC_BLOCK_CODES
                     if exc.args == (public_code,)), 'READ_CONTRACT_INCOMPLETE')
    except (KeyError, TypeError, AttributeError, psycopg.Error):
        code = 'READ_CONTRACT_INCOMPLETE'
    return {'schema': 'video-school-internal-staging-read-v1', 'status': 'BLOCKED', 'gaps': [code],
            'accepted_canon': [], 'canon_write_allowed': False, 'student_delivery_allowed': False}
