from __future__ import annotations
from typing import Any
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .research_pipeline import ResearchKind, build_artifact_manifest, build_methodical_result, canonical_research_key, plan_execution, validate_compute_result

def enqueue(conn: psycopg.Connection, *, kind: ResearchKind | str, payload: dict[str, Any], source: str='CHAT', priority: int=20) -> dict[str, Any]:
    normalized=ResearchKind(kind)
    if normalized not in {ResearchKind.DDS3,ResearchKind.BEN}: raise ValueError('durable executable enqueue currently supports DDS3/BEN')
    plan=plan_execution(normalized,payload)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM assistant_lab.enqueue_research_job(%s,%s,%s,%s,%s,%s,%s,%s)',
            (normalized.value,Jsonb(payload),canonical_research_key(normalized,payload),plan.assistant_lab_kind,Jsonb(plan.assistant_lab_payload),plan.idempotency_key,priority,source))
        row=cur.fetchone()
    conn.commit(); return dict(row or {})

def finalize(conn: psycopg.Connection,research_id:str)->dict[str,Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT r.*,j.status child_status,j.result_json,j.provenance_json child_provenance,j.error_text child_error,j.completed_at child_completed_at FROM assistant_lab.research_job r LEFT JOIN assistant_lab.job j ON j.job_id=r.child_job_id WHERE r.research_id=%s::uuid FOR UPDATE OF r''',(research_id,))
        row=cur.fetchone()
        if not row: raise KeyError('research job not found')
        if row['stage']=='COMPLETED': return dict(row)
        if row['child_status']=='FAILED':
            cur.execute("UPDATE assistant_lab.research_job SET stage='FAILED',error_text=%s,completed_at=now() WHERE research_id=%s::uuid",(row['child_error'],research_id)); conn.commit(); return {**dict(row),'stage':'FAILED'}
        if row['child_status']!='COMPLETED':
            stage='RUNNING' if row['child_status']=='RUNNING' else 'ACCEPTED'; cur.execute('UPDATE assistant_lab.research_job SET stage=%s WHERE research_id=%s::uuid',(stage,research_id)); conn.commit(); return {**dict(row),'stage':stage}
        kind=ResearchKind(row['kind']); verified=validate_compute_result(kind,row['result_json'],row['payload_json'])
        provenance=dict(row['child_provenance'] or {}); provenance.update({'child_job_id':str(row['child_job_id']),'child_completed_at':str(row['child_completed_at'] or '')})
        artifact=build_artifact_manifest(research_id=research_id,compute_result=verified,provenance=provenance)
        methodical=build_methodical_result(research_id=research_id,artifact_manifest=artifact)
        validation={'validated':True,'kind':kind.value,'engine':verified.get('engine'),'fallback_used':verified.get('fallback_used') if kind is ResearchKind.DDS3 else None,'evidence_class':verified.get('evidence_class') if kind is ResearchKind.BEN else 'DDS'}
        cur.execute("""UPDATE assistant_lab.research_job SET stage='COMPLETED',validation_json=%s,provenance_json=provenance_json||%s,artifact_json=%s,artifact_sha256=%s,methodical_json=%s,canonical_promotion=false,completed_at=now(),error_text=NULL WHERE research_id=%s::uuid RETURNING *""",
            (Jsonb(validation),Jsonb(provenance),Jsonb(artifact),artifact['sha256'],Jsonb(methodical),research_id))
        final=cur.fetchone()
    conn.commit(); return dict(final or {})
