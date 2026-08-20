from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb

from .db import connect

router = APIRouter(prefix="/v1/ai", tags=["bridge-ai-decision"])

ENGINE_VERSION = "finalizer-v1.1"
POLICY_MIN_MARGIN = Decimal("0.15")
POLICY_SINGLE_MIN_SCORE = Decimal("0.90")
SEARCH_TIE_EPSILON = Decimal("0.000001")
FORCED_RULE_STATUSES = {"FORCED", "REQUIRED"}
VETO_RULE_STATUSES = {"VETO", "FORBIDDEN", "ILLEGAL"}


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _metric_for_scoring(scoring: str | None) -> tuple[str, str]:
    normalized = (scoring or "").strip().lower()
    if normalized in {"imp", "imps"}:
        return "imp_ev", "IMP_EV"
    if normalized in {"mp", "matchpoint", "matchpoints"}:
        return "mp_ev", "MP_EV"
    return "raw_score_ev", "RAW_SCORE_EV"


def _candidate_allowed(row: dict[str, Any]) -> bool:
    if not row.get("legal", False):
        return False
    if row.get("system_compatible") is False:
        return False
    status = str(row.get("hard_rule_status") or "").upper()
    return status not in VETO_RULE_STATUSES


def _rank_policy(distribution: dict[str, Any]) -> list[tuple[str, Decimal]]:
    ranked: list[tuple[str, Decimal]] = []
    for action, score in (distribution or {}).items():
        number = _as_decimal(score)
        if number is not None:
            ranked.append((str(action), number))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _policy_second_action(distribution: dict[str, Any], top_action: str | None) -> str | None:
    for action, _ in _rank_policy(distribution):
        if action != top_action:
            return action
    return None


def _search_choice(rows: list[dict[str, Any]], scoring: str | None) -> dict[str, Any] | None:
    metric_key, metric_label = _metric_for_scoring(scoring)
    ranked: list[tuple[Decimal, dict[str, Any]]] = []
    for row in rows:
        value = _as_decimal(row.get(metric_key))
        if value is not None:
            ranked.append((value, row))
    if len(ranked) < 2:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_value, best = ranked[0]
    second_value, second = ranked[1]
    if abs(best_value - second_value) <= SEARCH_TIE_EPSILON:
        return None
    robustness = _as_decimal(best.get("robustness"))
    confidence = robustness if robustness is not None and Decimal("0") <= robustness <= Decimal("1") else None
    return {
        "chosen_action": best["action"],
        "second_action": second["action"],
        "decision_path": "SEARCH",
        "confidence": confidence,
        "robustness": robustness,
        "evidence": [{
            "evidence_class": "SEARCH_EVALUATION",
            "search_run_id": str(best["search_run_id"]),
            "metric": metric_label,
            "chosen_value": str(best_value),
            "second_value": str(second_value),
            "evaluation_count": len(ranked),
            "sample_quality": best.get("sample_quality"),
            "effective_sample_size": best.get("effective_sample_size"),
        }],
    }


def _forced_choice(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    forced = [
        row for row in rows
        if _candidate_allowed(row)
        and str(row.get("hard_rule_status") or "").upper() in FORCED_RULE_STATUSES
    ]
    if len(forced) != 1:
        return None
    row = forced[0]
    return {
        "chosen_action": row["action"],
        "second_action": None,
        "decision_path": "HARD_RULE",
        "confidence": None,
        "robustness": None,
        "evidence": [{
            "evidence_class": "HARD_RULE",
            "candidate_id": str(row["candidate_id"]),
            "hard_rule_status": row.get("hard_rule_status"),
        }],
    }


def _policy_choice(policy: dict[str, Any] | None, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not policy or not policy.get("top_action"):
        return None
    top_action = str(policy["top_action"])
    allowed = {str(row["action"]): row for row in candidates if _candidate_allowed(row)}
    if top_action not in allowed:
        return None

    distribution = policy.get("distribution_json") or {}
    ranked = _rank_policy(distribution)
    margin = _as_decimal(policy.get("margin"))
    gate = None
    gate_value = None
    gate_threshold = None

    if margin is not None and margin >= POLICY_MIN_MARGIN:
        gate = "MARGIN"
        gate_value = margin
        gate_threshold = POLICY_MIN_MARGIN
    elif len(ranked) == 1 and ranked[0][0] == top_action and ranked[0][1] >= POLICY_SINGLE_MIN_SCORE:
        gate = "SINGLE_SCORE"
        gate_value = ranked[0][1]
        gate_threshold = POLICY_SINGLE_MIN_SCORE
    else:
        return None

    second = _policy_second_action(distribution, top_action)
    return {
        "chosen_action": top_action,
        "second_action": second,
        "decision_path": "POLICY_ONLY",
        "confidence": None,
        "robustness": None,
        "evidence": [{
            "evidence_class": "POLICY",
            "policy_run_id": str(policy["policy_run_id"]),
            "model_key": policy.get("model_key"),
            "model_version": policy.get("model_version"),
            "gate": gate,
            "gate_value": str(gate_value),
            "threshold": str(gate_threshold),
            "margin": str(margin) if margin is not None else None,
            "entropy": policy.get("entropy"),
        }],
    }


def _insert_final(cur, *, position: dict[str, Any], choice: dict[str, Any]) -> dict[str, Any]:
    cur.execute(
        """
        SELECT * FROM ai.final_decision
        WHERE position_id=%s AND engine_version=%s
          AND COALESCE(system_version,'')=COALESCE(%s,'')
        ORDER BY created_at DESC LIMIT 1
        """,
        (position["position_id"], ENGINE_VERSION, position.get("system_us")),
    )
    existing = cur.fetchone()
    if existing:
        return existing

    cur.execute(
        """
        INSERT INTO ai.final_decision (
            position_id, engine_version, system_version,
            chosen_action, second_action, decision_path,
            confidence, robustness, evidence_json
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        (
            position["position_id"], ENGINE_VERSION, position.get("system_us"),
            choice["chosen_action"], choice.get("second_action"), choice["decision_path"],
            choice.get("confidence"), choice.get("robustness"), Jsonb(choice.get("evidence") or []),
        ),
    )
    return cur.fetchone()


@router.post("/positions/{position_id}/finalize")
def finalize_position(position_id: UUID) -> dict:
    """Create a final decision only when explicit hard-rule, search, or policy gates pass."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ai.decision_position WHERE position_id=%s", (position_id,))
        position = cur.fetchone()
        if not position:
            raise HTTPException(status_code=404, detail="decision position not found")
        if position["input_status"] != "COMPLETE":
            return {"finalized": False, "status": "INPUT_INCOMPLETE", "decision": None}

        cur.execute(
            """
            SELECT * FROM ai.final_decision
            WHERE position_id=%s AND engine_version=%s
              AND COALESCE(system_version,'')=COALESCE(%s,'')
            ORDER BY created_at DESC LIMIT 1
            """,
            (position_id, ENGINE_VERSION, position.get("system_us")),
        )
        cached = cur.fetchone()
        if cached:
            return {"finalized": False, "status": "CACHE_HIT", "decision": cached}

        cur.execute(
            "SELECT * FROM ai.candidate_action WHERE position_id=%s ORDER BY created_at",
            (position_id,),
        )
        candidates = cur.fetchall()

        choice = _forced_choice(candidates)
        if choice is None:
            metric_key, _ = _metric_for_scoring(position.get("scoring"))
            cur.execute(
                f"""
                SELECT e.*, c.action, s.sample_quality, s.effective_sample_size, s.created_at AS search_created_at
                FROM ai.candidate_evaluation e
                JOIN ai.candidate_action c ON c.candidate_id=e.candidate_id
                JOIN ai.search_run s ON s.search_run_id=e.search_run_id
                WHERE s.position_id=%s AND s.status='COMPLETED' AND e.{metric_key} IS NOT NULL
                ORDER BY s.created_at DESC, e.created_at DESC
                """,
                (position_id,),
            )
            search_rows = cur.fetchall()
            if search_rows:
                newest_run = search_rows[0]["search_run_id"]
                choice = _search_choice(
                    [row for row in search_rows if row["search_run_id"] == newest_run],
                    position.get("scoring"),
                )

        if choice is None:
            cur.execute(
                "SELECT * FROM ai.policy_run WHERE position_id=%s ORDER BY created_at DESC LIMIT 1",
                (position_id,),
            )
            choice = _policy_choice(cur.fetchone(), candidates)

        if choice is None:
            cur.execute("SELECT count(*) AS n FROM ai.teacher_output WHERE position_id=%s", (position_id,))
            teacher_count = int(cur.fetchone()["n"])
            conn.commit()
            return {
                "finalized": False,
                "status": "INSUFFICIENT_EVIDENCE",
                "decision": None,
                "teacher_outputs": teacher_count,
                "policy_margin_required": str(POLICY_MIN_MARGIN),
                "policy_single_score_required": str(POLICY_SINGLE_MIN_SCORE),
                "note": "teacher evidence alone does not authorize a final decision in finalizer-v1.1",
            }

        decision = _insert_final(cur, position=position, choice=choice)
        conn.commit()
        return {"finalized": True, "status": "FINALIZED", "decision": decision}
