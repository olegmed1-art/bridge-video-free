from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from .db import connect

router = APIRouter(prefix="/v1/ai", tags=["bridge-ai-policy"])


class PolicyEvidence(BaseModel):
    model_key: str
    model_version: str = "NOT_SPECIFIED"
    distribution: dict = Field(default_factory=dict)
    top_action: str | None = None
    entropy: float | None = None
    search_top_n: int = Field(default=5, ge=1, le=20)


def _rank_distribution(distribution: dict) -> tuple[str | None, Decimal | None]:
    ranked: list[tuple[str, Decimal]] = []
    for action, value in distribution.items():
        if value is None:
            continue
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if not numeric.is_finite():
            continue
        ranked.append((str(action), numeric))
    ranked.sort(key=lambda item: item[1], reverse=True)
    if not ranked:
        return None, None
    top = ranked[0][0]
    margin = ranked[0][1] - ranked[1][1] if len(ranked) >= 2 else None
    return top, margin


def _search_actions(distribution: dict, limit: int) -> set[str]:
    """Return a deterministic finite-score Top-N candidate set for heavy search."""
    ranked: list[tuple[str, Decimal]] = []
    for action, value in distribution.items():
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if numeric.is_finite():
            ranked.append((str(action), numeric))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return {action for action, _ in ranked[:limit]}


@router.post("/positions/{position_id}/policy-evidence")
def record_policy_evidence(position_id: UUID, evidence: PolicyEvidence) -> dict:
    """Persist explicit policy scores and create candidate rows without inventing EV/confidence."""
    computed_top, margin = _rank_distribution(evidence.distribution)
    top_action = evidence.top_action or computed_top
    if top_action is None:
        raise HTTPException(status_code=422, detail="policy distribution contains no numeric candidate scores")
    numeric_actions = {
        action for action, value in evidence.distribution.items()
        if _rank_distribution({action: value})[0] is not None
    }
    search_actions = _search_actions(evidence.distribution, evidence.search_top_n)
    if str(top_action) not in {str(action) for action in numeric_actions}:
        raise HTTPException(status_code=422, detail="policy top_action has no finite numeric score")

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT input_status FROM ai.decision_position WHERE position_id=%s", (position_id,))
        position = cur.fetchone()
        if not position:
            raise HTTPException(status_code=404, detail="decision position not found")

        cur.execute(
            """
            INSERT INTO ai.policy_run (
                position_id, model_key, model_version, distribution_json,
                top_action, margin, entropy
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (
                position_id,
                evidence.model_key,
                evidence.model_version or "NOT_SPECIFIED",
                Jsonb(evidence.distribution),
                top_action,
                margin,
                evidence.entropy,
            ),
        )
        policy = cur.fetchone()

        created_candidates = []
        for action, score in evidence.distribution.items():
            if score is None:
                continue
            try:
                numeric_score = Decimal(str(score))
            except (InvalidOperation, ValueError):
                continue
            if not numeric_score.is_finite():
                continue
            cur.execute(
                """
                INSERT INTO ai.candidate_action (
                    position_id, policy_run_id, action, legal,
                    system_compatible, hard_rule_status,
                    policy_score, search_included
                ) VALUES (%s,%s,%s,true,NULL,NULL,%s,%s)
                ON CONFLICT (position_id, policy_run_id, action)
                DO UPDATE SET policy_score=EXCLUDED.policy_score,
                              search_included=EXCLUDED.search_included
                RETURNING *
                """,
                (position_id, policy["policy_run_id"], str(action), numeric_score,
                 str(action) in search_actions),
            )
            created_candidates.append(cur.fetchone())

        conn.commit()
        return {"policy_run": policy, "candidates": created_candidates}
