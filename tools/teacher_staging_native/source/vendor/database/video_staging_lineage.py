"""Exact database lineage for internal video staging; not an authorization service."""
from __future__ import annotations

from uuid import UUID

from database.source_identity_persistence import drive_source_native_key
from database.video_result_persistence import SCHOOL_STABLE_NAME


def fixed_school_id(cursor):
    cursor.execute("SELECT school_id FROM public.school WHERE stable_name=%s AND status='active'",
                   (SCHOOL_STABLE_NAME,))
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise ValueError('FIXED_SCHOOL_UNAVAILABLE')
    return rows[0][0]


def exact_lineage(cursor, school_id, persisted_result, *, job_id=None):
    """Validate IDs returned by persist_video_result against existing DB facts.

    The supplied IDs are selectors, not proof or school/identity authorization.
    No latest-run lookup and no inferred source from a shared asset are allowed.
    """
    try:
        ids = [UUID(str(persisted_result[k])) for k in
               ('source_id', 'source_asset_id', 'analysis_run_id', 'artifact_version_id')]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError('EXACT_RESULT_BINDING_REQUIRED') from exc
    cursor.execute('''
        SELECT s.source_id, a.asset_id, r.analysis_run_id, av.artifact_version_id,
               r.parameters_snapshot->>'job_id', r.parameters_snapshot->>'source_drive_id',
               a.checksum_value, s.canonical_locator, r.run_status,
               r.publication_authorization_status, av.provenance->>'master_json_sha256'
          FROM public.source s
          JOIN public.source_asset sa ON sa.source_id=s.source_id AND sa.relation_type='embodies'
          JOIN public.asset a ON a.asset_id=sa.asset_id AND a.school_id=s.school_id
          JOIN public.analysis_run r ON r.school_id=s.school_id
          JOIN public.analysis_run_input ri ON ri.analysis_run_id=r.analysis_run_id
               AND ri.asset_id=a.asset_id AND ri.input_role='primary'
               AND ri.metadata->>'source_id'=s.source_id::text
          JOIN public.artifact_version av ON av.generated_by_analysis_run_id=r.analysis_run_id
          JOIN public.artifact ar ON ar.artifact_id=av.artifact_id AND ar.school_id=s.school_id
          JOIN public.analysis_run_output ro ON ro.analysis_run_id=r.analysis_run_id
               AND ro.artifact_version_id=av.artifact_version_id AND ro.output_entity_id=ar.artifact_id
               AND ro.output_entity_type='artifact' AND ro.status='staging'
         WHERE s.school_id=%s AND s.source_id=%s AND a.asset_id=%s
           AND r.analysis_run_id=%s AND av.artifact_version_id=%s
           AND s.status='active' AND s.source_type='lesson_video'
           AND a.checksum_algorithm='sha256' AND a.immutable_flag
           AND ar.status IN ('active','staging') AND av.status='candidate'
           AND r.run_status IN ('success','partial_success','running')
           AND r.technical_record_status='recorded'
    ''', (school_id, *ids))
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise ValueError('EXACT_SOURCE_RUN_ASSET_BINDING_MISMATCH')
    row = rows[0]
    if not row[5] or (job_id is not None and row[4] != job_id):
        raise ValueError('SOURCE_JOB_BINDING_MISMATCH')
    if row[7] != 'gdrive:file:' + row[5]:
        raise ValueError('SOURCE_LOCATOR_MISMATCH')
    cursor.execute('''SELECT source_identity_id FROM public.source_identity
                      WHERE source_id=%s AND source_native_key=%s''',
                   (row[0], drive_source_native_key(row[5])))
    if len(cursor.fetchall()) != 1:
        raise ValueError('SOURCE_NATIVE_IDENTITY_MISSING')
    keys = ('source_id', 'source_asset_id', 'analysis_run_id', 'artifact_version_id',
            'job_id', 'source_video_file_id', 'source_media_sha256', 'canonical_locator',
            'run_status', 'publication_authorization_status', 'parent_raw_sha256')
    return dict(zip(keys, [str(v) if isinstance(v, UUID) else v for v in row])) | {'school_id': str(school_id)}
