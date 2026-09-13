from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from .db import connect

router = APIRouter(prefix="/v1/ai", tags=["bridge-ai-orchestrator"])

DEFAULT_SAMPLER_KEY = "ben-queue"
DEFAULT_SAMPLER_VERSION = "v1"
DEFAULT_ROLLOUT_POLICY = "ben"
DEFAULT_EVALUATOR_KEY = "dds"


@router.post("/positions/{position_id}/process")
def process_position(position_id: UUID) -> dict:
    """Idempotently route a complete position into cache or autonomous compute queue."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ai.decision_position WHERE position_id=%s", (position_id,))
        position = cur.fetchone()
        if not position:
            raise HTTPException(status_code=404, detail="decision position not found")
        if position["input_status"] != "COMPLETE":
            return {"status": "INPUT_INCOMPLETE", "position_id": position_id, "queued": False}

        cur.execute(
            "SELECT * FROM ai.v_latest_final_decision WHERE position_id=%s",
            (position_id,),
        )
        cached = cur.fetchone()
        if cached:
            return {"status": "CACHE_HIT", "position_id": position_id, "queued": False, "decision": cached}

        cur.execute(
            """
            SELECT * FROM ai.search_run
            WHERE position_id=%s AND status IN ('QUEUED','RUNNING')
            ORDER BY created_at DESC LIMIT 1
            """,
            (position_id,),
        )
        pending = cur.fetchone()
        if pending:
            return {"status": "SEARCH_PENDING", "position_id": position_id, "queued": False, "search_run": pending}

        cur.execute(
            """
            INSERT INTO ai.search_run (
                position_id, sampler_key, sampler_version,
                rollout_policy, evaluator_key, scoring, status
            ) VALUES (%s,%s,%s,%s,%s,%s,'QUEUED')
            RETURNING *
            """,
            (
                position_id,
                DEFAULT_SAMPLER_KEY,
                DEFAULT_SAMPLER_VERSION,
                DEFAULT_ROLLOUT_POLICY,
                DEFAULT_EVALUATOR_KEY,
                position.get("scoring"),
            ),
        )
        run = cur.fetchone()
        conn.commit()
        return {"status": "QUEUED", "position_id": position_id, "queued": True, "search_run": run}
