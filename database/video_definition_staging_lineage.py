"""Existing transcript-row read, scoped by the already checked native lineage.

No connection/provider, new storage, grant, issuer or authority is provided.
"""
from decimal import Decimal,InvalidOperation
from bridge_contracts.video_definition_staging import validate_definition_lineage,need
from database.video_result_persistence import SCHOOL_STABLE_NAME

def require_definition_segment(cursor,payload,replay,lineage):
    stage,display,claims=validate_definition_lineage(payload,replay,lineage)
    need(replay['definition_staging']['scope']['school_key']==SCHOOL_STABLE_NAME,'DEFINITION_SCHOOL_KEY_MISMATCH')
    source=payload['knowledge_version']['content']['definition_evidence']['source_binding']
    cursor.execute('''SELECT ts.transcript_segment_id,ts.start_seconds,ts.end_seconds,ts.text,ts.metadata->>'raw_text'
 FROM public.transcript t JOIN public.transcript_segment ts ON ts.transcript_id=t.transcript_id
 WHERE t.school_id=%s::uuid AND t.source_id=%s::uuid AND t.media_asset_id=%s::uuid
 AND t.provenance->>'job_id'=%s AND t.provenance->>'source_drive_id'=%s AND t.status='staging'
 AND ts.metadata->>'worker_segment_id'=%s ORDER BY ts.transcript_segment_id LIMIT 2''',
        (lineage['school_id'],lineage['source_id'],lineage['source_asset_id'],lineage['job_id'],
         lineage['source_video_file_id'],source['source_segment_id']))
    found=cursor.fetchall()
    need(len(found)==1,'DEFINITION_NATIVE_SEGMENT_NOT_UNIQUE')
    segment=found[0]
    raw=segment[4] if segment[4] is not None else segment[3]
    try:
        matching=Decimal(str(segment[1]))==Decimal(str(source['interval_seconds'][0])) and Decimal(str(segment[2]))==Decimal(str(source['interval_seconds'][1]))
    except (InvalidOperation,ValueError,TypeError):matching=False
    need(matching and raw==source['raw_text'],'DEFINITION_NATIVE_SEGMENT_MISMATCH')
    return stage,display,claims
