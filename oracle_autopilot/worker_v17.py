"""Shadow worker entrypoint retaining complete structured IBF source evidence.

The IBF path fetches each official page once, stores the complete de-identified
artifact through the fenced database RPC, and only then completes the task with
the existing compact summary. All other task types keep the established worker
behavior unchanged.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import worker as base
from .contract import ClaimedTask
from .ibf_read_only import fetch_ibf_structured_evidence

_ORIGINAL_EXECUTE_TASK = base.execute_task
IBF_EVIDENCE_CLASS = "IBF_READ_ONLY_ANALYSIS_EVIDENCE"
IBF_ARTIFACT_SCHEMA = "IBF_STRUCTURED_TOURNAMENT_V1"


def _artifact_bytes(artifact: dict[str, Any]) -> bytes:
    return json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _store_structured_artifact(
    config: base.WorkerConfig,
    task: ClaimedTask,
    artifact: dict[str, Any],
) -> None:
    content = _artifact_bytes(artifact)
    content_sha256 = hashlib.sha256(content).hexdigest()
    participation = artifact.get("latest_participation")
    if not isinstance(participation, dict):
        raise base.AutopilotContractError("AUTOPILOT_IBF_ARTIFACT_INVALID")
    manifest = {
        "analysis_scope": "STRUCTURED_SOURCE_AND_REVIEW_CANDIDATES",
        "artifact_bytes": len(content),
        "artifact_schema_version": IBF_ARTIFACT_SCHEMA,
        "artifact_sha256": content_sha256,
        "board_count": artifact.get("board_count"),
        "event_id": participation.get("event_id"),
        "ibf_player_id": artifact.get("ibf_player_id"),
        "methodology_or_canon_applied": False,
        "model_calls": 0,
        "production_mutation": False,
        "round_id": participation.get("round_id"),
        "seat": participation.get("seat"),
        "source_authority": artifact.get("source_authority"),
    }
    row = base._rpc_one(
        config,
        "SELECT autopilot.store_ibf_structured_artifact("
        "%s::uuid,%s,%s,%s,%s,%s,%s::jsonb) AS stored",
        (
            task.task_id,
            config.worker_id,
            task.lease_epoch,
            IBF_ARTIFACT_SCHEMA,
            content_sha256,
            content,
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    if not row or not row["stored"]:
        raise base.AutopilotContractError("AUTOPILOT_IBF_ARTIFACT_FENCED")


def execute_task(config: base.WorkerConfig, task: ClaimedTask) -> None:
    """Retain complete IBF facts before the existing compact completion."""

    if task.goal_type != "IBF_READ_ONLY_ANALYSIS":
        _ORIGINAL_EXECUTE_TASK(config, task)
        return

    base.validate_task_contract(task)
    snapshot, artifact = fetch_ibf_structured_evidence(task.goal_json)
    for evidence in (snapshot, artifact):
        if (
            evidence.get("production_mutation") is not False
            or evidence.get("model_calls") != 0
            or evidence.get("cost_actual_microusd") != 0
        ):
            raise base.AutopilotContractError("AUTOPILOT_IBF_EVIDENCE_INVALID")
    _store_structured_artifact(config, task, artifact)
    base._complete(
        config,
        task,
        evidence_class=IBF_EVIDENCE_CLASS,
        summary=snapshot,
    )


def main() -> None:
    """Run the polling loop with the complete structured IBF dispatch."""

    previous = base.execute_task
    base.execute_task = execute_task
    try:
        base.main()
    finally:
        base.execute_task = previous


if __name__ == "__main__":
    main()
