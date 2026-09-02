from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from .db import connect

router = APIRouter(prefix="/v1/ai", tags=["bridge-ai"])


@router.get("/overview")
def ai_overview() -> dict:
    """Compact operational snapshot of the Bridge Decision Engine data layer."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM ai.knowledge_fact) AS knowledge_facts,
                (SELECT count(*) FROM ai.system_rule WHERE status='ACTIVE') AS active_rules,
                (SELECT count(*) FROM ai.decision_position) AS positions,
                (SELECT count(*) FROM ai.decision_position WHERE input_status='COMPLETE') AS complete_positions,
                (SELECT count(*) FROM ai.teacher_output) AS teacher_outputs,
                (SELECT count(*) FROM ai.policy_run) AS policy_runs,
                (SELECT count(*) FROM ai.search_run) AS search_runs,
                (SELECT count(*) FROM ai.final_decision) AS final_decisions,
                (SELECT count(*) FROM ai.v_work_queue WHERE work_status <> 'DONE') AS queued_positions
            """
        )
        return cur.fetchone()


@router.get("/positions")
def ai_positions(
    input_status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM ai.v_position_overview
            WHERE (%s IS NULL OR input_status = %s)
            ORDER BY stable_key, position_id
            LIMIT %s OFFSET %s
            """,
            (input_status, input_status, limit, offset),
        )
        return cur.fetchall()


@router.get("/positions/{position_id}")
def ai_position(position_id: UUID) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ai.v_position_overview WHERE position_id=%s", (position_id,))
        position = cur.fetchone()
        if not position:
            raise HTTPException(status_code=404, detail="decision position not found")

        cur.execute(
            """
            SELECT feature_version, hcp, shape, controls, features_json, created_at
            FROM ai.hand_features
            WHERE position_id=%s
            ORDER BY created_at DESC
            """,
            (position_id,),
        )
        features = cur.fetchall()

        cur.execute(
            """
            SELECT teacher_key, teacher_version, teacher_system, action, confidence,
                   candidate_scores_json, explanation, created_at
            FROM ai.teacher_output
            WHERE position_id=%s
            ORDER BY created_at DESC
            """,
            (position_id,),
        )
        teachers = cur.fetchall()

        cur.execute(
            """
            SELECT candidate_id, action, legal, system_compatible, hard_rule_status,
                   policy_score, teacher_score, search_included, created_at
            FROM ai.candidate_action
            WHERE position_id=%s
            ORDER BY policy_score DESC NULLS LAST, created_at
            """,
            (position_id,),
        )
        candidates = cur.fetchall()

        cur.execute(
            """
            SELECT *
            FROM ai.v_latest_final_decision
            WHERE position_id=%s
            """,
            (position_id,),
        )
        decision = cur.fetchone()

        return {
            "position": position,
            "hand_features": features,
            "teachers": teachers,
            "candidates": candidates,
            "latest_decision": decision,
        }


@router.get("/rules")
def ai_rules(
    system_version: str | None = Query(default=None, max_length=80),
    status: str | None = Query(default="ACTIVE", max_length=40),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM ai.v_rule_provenance
            WHERE (%s IS NULL OR system_version=%s)
              AND (%s IS NULL OR rule_status=%s)
            ORDER BY rule_key, fact_key
            LIMIT %s OFFSET %s
            """,
            (system_version, system_version, status, status, limit, offset),
        )
        return cur.fetchall()


@router.get("/work-queue")
def ai_work_queue(
    work_status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT position_id, stable_key, input_status, work_status
            FROM ai.v_work_queue
            WHERE (%s IS NULL OR work_status=%s)
            ORDER BY stable_key
            LIMIT %s
            """,
            (work_status, work_status, limit),
        )
        return cur.fetchall()
