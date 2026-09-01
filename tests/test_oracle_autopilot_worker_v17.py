from __future__ import annotations

import hashlib
import json
from unittest.mock import Mock, patch

import pytest

from oracle_autopilot import worker_v17
from oracle_autopilot.contract import AutopilotContractError, ClaimedTask
from oracle_autopilot.ibf_read_only import IBF_SOURCE_AUTHORITY


def _ibf_task() -> ClaimedTask:
    return ClaimedTask(
        task_id="00000000-0000-0000-0000-000000001013",
        goal_type="IBF_READ_ONLY_ANALYSIS",
        goal_json={
            "approval_ref": "github:issue:1013#director-go",
            "ibf_player_id": "15031",
            "source_authority": IBF_SOURCE_AUTHORITY,
        },
        current_step_key="ibf.read_only_analysis",
        step_cursor=0,
        lease_epoch=7,
        attempts=1,
        max_attempts=3,
        cost_cap_microusd=0,
        cost_reserved_microusd=0,
    )


def _config() -> worker_v17.base.WorkerConfig:
    return worker_v17.base.WorkerConfig(
        dsn="postgresql://runtime:secret@example.neon.tech/neondb?sslmode=require",
        worker_id="test-worker",
        lease_seconds=60,
        heartbeat_seconds=15,
        recovery_poll_seconds=30.0,
    )


def _evidence() -> tuple[dict, dict]:
    snapshot = {
        "source_authority": IBF_SOURCE_AUTHORITY,
        "ibf_player_id": "15031",
        "latest_participation": {"event_id": 29692, "round_id": 9, "seat": "4"},
        "board_count": 1,
        "boards": [],
        "production_mutation": False,
        "model_calls": 0,
        "cost_actual_microusd": 0,
    }
    artifact = {
        "schema_version": worker_v17.IBF_ARTIFACT_SCHEMA,
        "source_authority": IBF_SOURCE_AUTHORITY,
        "ibf_player_id": "15031",
        "latest_participation": {"event_id": 29692, "round_id": 9, "seat": "4"},
        "board_count": 1,
        "boards": [{"board_number": 1}],
        "teaching_analysis": {},
        "production_mutation": False,
        "model_calls": 0,
        "cost_actual_microusd": 0,
    }
    return snapshot, artifact


def test_ibf_dispatch_stores_exact_artifact_before_compact_completion():
    snapshot, artifact = _evidence()
    order = Mock()
    with patch.object(
        worker_v17, "fetch_ibf_structured_evidence", return_value=(snapshot, artifact)
    ) as fetch, patch.object(
        worker_v17.base, "_rpc_one", return_value={"stored": True}
    ) as rpc, patch.object(worker_v17.base, "_complete", side_effect=order.complete) as complete:
        rpc.side_effect = lambda *args: (order.store(), {"stored": True})[1]
        worker_v17.execute_task(_config(), _ibf_task())

    fetch.assert_called_once_with(_ibf_task().goal_json)
    content = worker_v17._artifact_bytes(artifact)
    manifest = {
        "analysis_scope": "STRUCTURED_SOURCE_AND_REVIEW_CANDIDATES",
        "artifact_bytes": len(content),
        "artifact_schema_version": worker_v17.IBF_ARTIFACT_SCHEMA,
        "artifact_sha256": hashlib.sha256(content).hexdigest(),
        "board_count": 1,
        "event_id": 29692,
        "ibf_player_id": "15031",
        "methodology_or_canon_applied": False,
        "model_calls": 0,
        "production_mutation": False,
        "round_id": 9,
        "seat": "4",
        "source_authority": IBF_SOURCE_AUTHORITY,
    }
    rpc.assert_called_once_with(
        _config(),
        "SELECT autopilot.store_ibf_structured_artifact("
        "%s::uuid,%s,%s,%s,%s,%s,%s::jsonb) AS stored",
        (
            _ibf_task().task_id,
            _config().worker_id,
            _ibf_task().lease_epoch,
            worker_v17.IBF_ARTIFACT_SCHEMA,
            hashlib.sha256(content).hexdigest(),
            content,
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    complete.assert_called_once_with(
        _config(),
        _ibf_task(),
        evidence_class=worker_v17.IBF_EVIDENCE_CLASS,
        summary=snapshot,
    )
    assert [record[0] for record in order.mock_calls] == ["store", "complete"]


def test_fenced_artifact_store_prevents_task_completion():
    snapshot, artifact = _evidence()
    with patch.object(
        worker_v17, "fetch_ibf_structured_evidence", return_value=(snapshot, artifact)
    ), patch.object(
        worker_v17.base, "_rpc_one", return_value={"stored": False}
    ), patch.object(worker_v17.base, "_complete") as complete, pytest.raises(
        AutopilotContractError, match="AUTOPILOT_IBF_ARTIFACT_FENCED"
    ):
        worker_v17.execute_task(_config(), _ibf_task())
    complete.assert_not_called()


@pytest.mark.parametrize(
    ("target", "key", "value"),
    [
        ("snapshot", "production_mutation", True),
        ("artifact", "model_calls", 1),
        ("artifact", "cost_actual_microusd", 1),
    ],
)
def test_unsafe_evidence_is_rejected_before_storage(target, key, value):
    snapshot, artifact = _evidence()
    (snapshot if target == "snapshot" else artifact)[key] = value
    with patch.object(
        worker_v17, "fetch_ibf_structured_evidence", return_value=(snapshot, artifact)
    ), patch.object(worker_v17.base, "_rpc_one") as rpc, pytest.raises(
        AutopilotContractError, match="AUTOPILOT_IBF_EVIDENCE_INVALID"
    ):
        worker_v17.execute_task(_config(), _ibf_task())
    rpc.assert_not_called()


def test_non_ibf_task_delegates_unchanged_and_main_restores_dispatch():
    smoke = ClaimedTask(
        task_id="00000000-0000-0000-0000-000000000001",
        goal_type="AUTOPILOT_SMOKE_V1",
        goal_json={},
        current_step_key="shadow.noop",
        step_cursor=0,
        lease_epoch=1,
        attempts=1,
        max_attempts=1,
        cost_cap_microusd=0,
        cost_reserved_microusd=0,
    )
    delegated = Mock()
    with patch.object(worker_v17, "_ORIGINAL_EXECUTE_TASK", delegated):
        worker_v17.execute_task(_config(), smoke)
    delegated.assert_called_once_with(_config(), smoke)

    original = worker_v17.base.execute_task

    def observe_main():
        assert worker_v17.base.execute_task is worker_v17.execute_task

    with patch.object(worker_v17.base, "main", side_effect=observe_main):
        worker_v17.main()
    assert worker_v17.base.execute_task is original
