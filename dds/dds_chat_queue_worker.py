#!/usr/bin/env python3
"""Claim DDS3 chat jobs from Neon and execute the persistent DDS3 position worker."""
from __future__ import annotations
import json, os, subprocess, sys
import psycopg

DB=os.environ["BRIDGE_APP_DATABASE_URL"]
WORKER=os.environ.get("DDS3_POSITION_WORKER","/opt/bridge-school-dds3/dds_position_worker")

def main():
    with psycopg.connect(DB) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT job_id,trump,first_hand,current_trick,pbn FROM ai.dds3_chat_job
                           WHERE status='QUEUED' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""")
            row=cur.fetchone()
            if not row:
                print('{"claimed":false}')
                return 0
            job_id,trump,first,current,pbn=row
            cur.execute("UPDATE ai.dds3_chat_job SET status='RUNNING',started_at=now() WHERE job_id=%s",(job_id,))
        conn.commit()
        line=f"POSITION\t{trump}\t{first}\t{current or '-'}\t{pbn}\n"
        try:
            proc=subprocess.run([WORKER],input=line,text=True,capture_output=True,timeout=120,check=True)
            output=proc.stdout.strip().splitlines()[-1]
            result=json.loads(output)
            if not result.get('ok') or result.get('engine')!='DDS3' or result.get('fallback_used') is not False:
                raise RuntimeError(f"fail-closed DDS3 result: {output}")
            with conn.cursor() as cur:
                cur.execute("UPDATE ai.dds3_chat_job SET status='COMPLETED',result_json=%s::jsonb,completed_at=now() WHERE job_id=%s",(json.dumps(result),job_id))
            conn.commit()
            print(json.dumps({'job_id':str(job_id),'result':result}))
        except Exception as exc:
            with conn.cursor() as cur:
                cur.execute("UPDATE ai.dds3_chat_job SET status='FAILED',error_text=%s,completed_at=now() WHERE job_id=%s",(str(exc),job_id))
            conn.commit()
            raise
    return 0
if __name__=='__main__': raise SystemExit(main())
