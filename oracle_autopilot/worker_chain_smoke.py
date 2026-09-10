"""Bounded product-level chain smoke for the Oracle Autopilot worker.

This wrapper keeps the existing shadow worker behavior and adds exactly one
synthetic chain behavior for AUTOPILOT_SMOKE_V1 tasks that explicitly request a
next smoke task. It is used to prove that the resident Autopilot can continue
from one terminal receipt to the next queued task without a director typing
"auto" between steps.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import worker as base
from .contract import ClaimedTask

_ORIGINAL_EXECUTE_TASK = base.execute_task
_TASK_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def _require_task_key(value: Any) -> str:
    if not isinstance(value, str) or _TASK_KEY_RE.fullmatch(value) is None:
        raise base.AutopilotContractError("AUTOPILOT_CHAIN_TASK_KEY_INVALID")
    return value


def _enqueue_next_smoke(config: base.WorkerConfig, task: ClaimedTask) -> dict[str, Any]:
    next_key = _require_task_key(task.goal_json.get("chain_next_task_key"))
    correlation_id = task.goal_json.get("correlation_id", task.task_id)
    if not isinstance(correlation_id, str) or not 1 <= len(correlation_id) <= 200:
        raise base.AutopilotContractError("AUTOPILOT_CHAIN_CORRELATION_INVALID")
    next_goal_json = {
        "correlation_id": f"{correlation_id}:next",
        "chain_parent_task_id": task.task_id,
        "chain_sequence": 2,
        "purpose": "autopilot_continues_after_terminal_receipt",
    }
    row = base._rpc_one(
        config,
        """
        WITH created AS (
            INSERT INTO autopilot.task (
                task_key, goal_type, goal_version, status, goal_json,
                governance_mode, risk_class, current_step_key, step_cursor,
                acceptance_contract_json, allowed_capabilities_json,
                priority, max_attempts, model_turn_cap, cost_cap_microusd,
                created_by, source
            ) VALUES (
                %s, 'AUTOPILOT_SMOKE_V1', '1.0', 'READY', %s::jsonb,
                'ASSURED', 'SHADOW_READ_ONLY', 'shadow.noop', 0,
                '{"expected_terminal":"DONE","evidence_class":"SYNTHETIC_SHADOW_COMPLETION","chain_sequence":2}'::jsonb,
                '["ORACLE_RESIDENT_SHADOW"]'::jsonb,
                0, 3, 0, 0,
                'ORACLE_AUTOPILOT', 'CHAIN_SMOKE'
            )
            ON CONFLICT (task_key) DO NOTHING
            RETURNING task_id
        ), existing AS (
            SELECT task_id FROM autopilot.task WHERE task_key = %s
        ), signaled AS (
            SELECT pg_notify('autopilot_ready', COALESCE((SELECT task_id::text FROM created), (SELECT task_id::text FROM existing)))
        )
        SELECT COALESCE((SELECT task_id::text FROM created), (SELECT task_id::text FROM existing)) AS task_id,
               EXISTS(SELECT 1 FROM created) AS inserted
        """,
        (next_key, json.dumps(next_goal_json, ensure_ascii=False, separators=(",", ":")), next_key),
    )
    if not row or not row["task_id"]:
        raise base.AutopilotContractError("AUTOPILOT_CHAIN_ENQUEUE_FAILED")
    return {"next_task_id": row["task_id"], "next_task_key": next_key, "inserted": bool(row["inserted"])}


def execute_task(config: base.WorkerConfig, task: ClaimedTask) -> None:
    if task.goal_type != "AUTOPILOT_SMOKE_V1" or "chain_next_task_key" not in task.goal_json:
        _ORIGINAL_EXECUTE_TASK(config, task)
        return

    base.validate_task_contract(task)
    chain = _enqueue_next_smoke(config, task)
    summary = {
        "task_id": task.task_id,
        "task_kind": task.goal_type,
        "runtime": "ORACLE_RESIDENT",
        "production_mutation": False,
        "model_calls": 0,
        "chain_next_enqueued": True,
        **chain,
    }
    base._complete(
        config,
        task,
        evidence_class="SYNTHETIC_SHADOW_COMPLETION",
        summary=summary,
    )


def main() -> None:
    previous = base.execute_task
    base.execute_task = execute_task
    try:
        base.main()
    finally:
        base.execute_task = previous


if __name__ == "__main__":
    main()
