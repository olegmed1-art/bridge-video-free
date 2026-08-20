from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from .db import connect

router = APIRouter(prefix="/v1/ai", tags=["bridge-ai-worker"])

STALE_RUN_MINUTES = 15


class SearchRequest(BaseModel):
    sampler_key: str = "weighted-deal-sampler"
    sampler_version: str = "v1"
    rollout_policy: str | None = "ben"
    evaluator_key: str | None = "dds"
    scoring: str | None = None


class CandidateEvaluation(BaseModel):
    candidate_id: UUID
    rollout_policy: str | None = None
    raw_score_ev: float | None = None
    imp_ev: float | None = None
    mp_ev: float | None = None
    variance: float | None = None
    downside: float | None = None
    upside: float | None = None
    make_probability: float | None = None
    robustness: float | None = None
    contracts_json: dict = Field(default_factory=dict)
    metrics_json: dict = Field(default_factory=dict)


class SearchCompletion(BaseModel):
    status: Literal["COMPLETED", "FAILED"]
    samples_generated: int | None = None
    samples_accepted: int | None = None
    effective_sample_size: float | None = None
    sample_quality: float | None = None
    worlds_object_uri: str | None = None
    evaluations: list[CandidateEvaluation] = Field(default_factory=list)


@router.get("/positions/{position_id}/route")
def route_position(position_id: UUID) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ai.v_position_overview WHERE position_id=%s", (position_id,))
        position = cur.fetchone()
        if not position:
            raise HTTPException(status_code=404, detail="decision position not found")
        if position["input_status"] != "COMPLETE":
            return {"position_id": position_id, "route": "INPUT_INCOMPLETE", "reason": "position does not contain all mandatory decision inputs"}

        cur.execute("SELECT * FROM ai.v_latest_final_decision WHERE position_id=%s", (position_id,))
        cached = cur.fetchone()
        if cached:
            return {"position_id": position_id, "route": "CACHE_HIT", "decision": cached}

        cur.execute(
            """
            SELECT search_run_id, status, created_at
            FROM ai.search_run
            WHERE position_id=%s AND status IN ('QUEUED', 'RUNNING')
            ORDER BY created_at DESC LIMIT 1
            """,
            (position_id,),
        )
        pending = cur.fetchone()
        if pending:
            return {"position_id": position_id, "route": "SEARCH_PENDING", "search_run": pending}
        return {"position_id": position_id, "route": "FAST_REQUIRED", "reason": "no cached final decision and no heavy search is currently active"}


@router.post("/positions/{position_id}/search-runs")
def enqueue_search(position_id: UUID, request: SearchRequest) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT input_status, scoring FROM ai.decision_position WHERE position_id=%s", (position_id,))
        position = cur.fetchone()
        if not position:
            raise HTTPException(status_code=404, detail="decision position not found")
        if position["input_status"] != "COMPLETE":
            raise HTTPException(status_code=409, detail="decision position input is incomplete")

        cur.execute(
            """
            SELECT * FROM ai.search_run
            WHERE position_id=%s AND sampler_key=%s AND sampler_version=%s
              AND status IN ('QUEUED', 'RUNNING')
            ORDER BY created_at DESC LIMIT 1
            """,
            (position_id, request.sampler_key, request.sampler_version),
        )
        existing = cur.fetchone()
        if existing:
            return {"created": False, "search_run": existing}

        cur.execute(
            """
            INSERT INTO ai.search_run (
                position_id, sampler_key, sampler_version, rollout_policy,
                evaluator_key, scoring, status
            ) VALUES (%s,%s,%s,%s,%s,%s,'QUEUED') RETURNING *
            """,
            (position_id, request.sampler_key, request.sampler_version, request.rollout_policy,
             request.evaluator_key, request.scoring or position["scoring"]),
        )
        row = cur.fetchone()
        conn.commit()
        return {"created": True, "search_run": row}


@router.post("/search-runs/claim")
def claim_search_run() -> dict:
    """Recover stale jobs, then atomically claim one queued job."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE ai.search_run
            SET status='QUEUED', started_at=NULL
            WHERE status='RUNNING'
              AND started_at < now() - interval '{STALE_RUN_MINUTES} minutes'
            """
        )
        recovered = cur.rowcount
        cur.execute(
            """
            WITH next_job AS (
                SELECT s.search_run_id
                FROM ai.search_run s
                JOIN ai.decision_position p ON p.position_id=s.position_id
                WHERE s.status='QUEUED' AND p.input_status='COMPLETE'
                ORDER BY s.created_at
                FOR UPDATE OF s SKIP LOCKED
                LIMIT 1
            )
            UPDATE ai.search_run s
            SET status='RUNNING', started_at=now(), completed_at=NULL
            FROM next_job
            WHERE s.search_run_id=next_job.search_run_id
            RETURNING s.*
            """
        )
        search_run = cur.fetchone()
        if not search_run:
            conn.commit()
            return {"claimed": False, "search_run": None, "recovered_stale": recovered}

        cur.execute("SELECT * FROM ai.v_position_overview WHERE position_id=%s", (search_run["position_id"],))
        position = cur.fetchone()
        cur.execute(
            """
            SELECT feature_version, hcp, shape, controls, features_json
            FROM ai.hand_features WHERE position_id=%s
            ORDER BY created_at DESC LIMIT 1
            """,
            (search_run["position_id"],),
        )
        features = cur.fetchone()
        cur.execute(
            """
            SELECT candidate_id, action, legal, system_compatible, hard_rule_status,
                   policy_score, teacher_score, search_included
            FROM ai.candidate_action
            WHERE position_id=%s AND legal=true
            ORDER BY search_included DESC, policy_score DESC NULLS LAST, created_at
            """,
            (search_run["position_id"],),
        )
        candidates = cur.fetchall()
        conn.commit()
        return {"claimed": True, "search_run": search_run, "position": position,
                "hand_features": features, "candidates": candidates,
                "recovered_stale": recovered}


@router.get("/search-runs/{search_run_id}")
def get_search_run(search_run_id: UUID) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ai.search_run WHERE search_run_id=%s", (search_run_id,))
        search_run = cur.fetchone()
        if not search_run:
            raise HTTPException(status_code=404, detail="search run not found")
        cur.execute(
            "SELECT * FROM ai.candidate_evaluation WHERE search_run_id=%s ORDER BY imp_ev DESC NULLS LAST, raw_score_ev DESC NULLS LAST, created_at",
            (search_run_id,),
        )
        return {"search_run": search_run, "evaluations": cur.fetchall()}


@router.post("/search-runs/{search_run_id}/complete")
def complete_search_run(search_run_id: UUID, result: SearchCompletion) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT position_id, status, rollout_policy FROM ai.search_run WHERE search_run_id=%s FOR UPDATE", (search_run_id,))
        search_run = cur.fetchone()
        if not search_run:
            raise HTTPException(status_code=404, detail="search run not found")
        if search_run["status"] == "COMPLETED":
            cur.execute("SELECT * FROM ai.search_run WHERE search_run_id=%s", (search_run_id,))
            return {"updated": False, "search_run": cur.fetchone()}
        if search_run["status"] not in {"QUEUED", "RUNNING", "FAILED"}:
            raise HTTPException(status_code=409, detail="search run is not completable")
        if result.status == "COMPLETED" and not result.evaluations:
            raise HTTPException(status_code=409, detail="completed search requires explicit candidate evaluations")

        if result.status == "COMPLETED":
            for evaluation in result.evaluations:
                cur.execute("SELECT 1 FROM ai.candidate_action WHERE candidate_id=%s AND position_id=%s", (evaluation.candidate_id, search_run["position_id"]))
                if not cur.fetchone():
                    raise HTTPException(status_code=409, detail=f"candidate {evaluation.candidate_id} does not belong to the search position")
                rollout_policy = evaluation.rollout_policy or search_run["rollout_policy"]
                cur.execute(
                    """
                    INSERT INTO ai.candidate_evaluation (
                        search_run_id, candidate_id, rollout_policy, raw_score_ev, imp_ev, mp_ev,
                        variance, downside, upside, make_probability, robustness, contracts_json, metrics_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (search_run_id, candidate_id, rollout_policy)
                    DO UPDATE SET raw_score_ev=EXCLUDED.raw_score_ev, imp_ev=EXCLUDED.imp_ev,
                        mp_ev=EXCLUDED.mp_ev, variance=EXCLUDED.variance, downside=EXCLUDED.downside,
                        upside=EXCLUDED.upside, make_probability=EXCLUDED.make_probability,
                        robustness=EXCLUDED.robustness, contracts_json=EXCLUDED.contracts_json,
                        metrics_json=EXCLUDED.metrics_json
                    """,
                    (search_run_id, evaluation.candidate_id, rollout_policy, evaluation.raw_score_ev,
                     evaluation.imp_ev, evaluation.mp_ev, evaluation.variance, evaluation.downside,
                     evaluation.upside, evaluation.make_probability, evaluation.robustness,
                     Jsonb(evaluation.contracts_json), Jsonb(evaluation.metrics_json)),
                )

        cur.execute(
            """
            UPDATE ai.search_run SET status=%s, samples_generated=%s, samples_accepted=%s,
                effective_sample_size=%s, sample_quality=%s, worlds_object_uri=%s, completed_at=now()
            WHERE search_run_id=%s RETURNING *
            """,
            (result.status, result.samples_generated, result.samples_accepted,
             result.effective_sample_size, result.sample_quality, result.worlds_object_uri, search_run_id),
        )
        updated = cur.fetchone()
        conn.commit()
        return {"updated": True, "search_run": updated}
