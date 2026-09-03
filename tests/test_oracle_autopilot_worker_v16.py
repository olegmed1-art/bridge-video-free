from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from oracle_autopilot.contract import AutopilotContractError, ClaimedTask
from oracle_autopilot.ibf_read_only import IBF_SOURCE_AUTHORITY
from oracle_autopilot import worker_v16


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
        lease_epoch=1,
        attempts=1,
        max_attempts=3,
        cost_cap_microusd=0,
        cost_reserved_microusd=0,
    )


def _config():
    return worker_v16.base.WorkerConfig(
        dsn="postgresql://runtime:secret@example.neon.tech/autopilot?sslmode=require",
        worker_id="test-worker",
        lease_seconds=60,
        heartbeat_seconds=15,
        recovery_poll_seconds=30.0,
    )


def test_ibf_dispatch_retrieves_then_completes_with_retained_evidence():
    snapshot = {
        "source_authority": IBF_SOURCE_AUTHORITY,
        "ibf_player_id": "15031",
        "latest_participation": {"event_id": 30041, "round_id": 3, "seat": "7"},
        "board_count": 2,
        "boards": [],
        "production_mutation": False,
        "model_calls": 0,
        "cost_actual_microusd": 0,
    }
    with patch.object(worker_v16, "fetch_ibf_read_only_snapshot", return_value=snapshot) as fetch, patch.object(
        worker_v16.base, "_complete"
    ) as complete:
        worker_v16.execute_task(_config(), _ibf_task())

    fetch.assert_called_once_with(_ibf_task().goal_json)
    complete.assert_called_once_with(
        _config(),
        _ibf_task(),
        evidence_class=worker_v16.IBF_EVIDENCE_CLASS,
        summary=snapshot,
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("production_mutation", True),
        ("model_calls", 1),
        ("cost_actual_microusd", 1),
    ],
)
def test_ibf_dispatch_rejects_evidence_that_breaks_shadow_bounds(key, value):
    snapshot = {
        "production_mutation": False,
        "model_calls": 0,
        "cost_actual_microusd": 0,
    }
    snapshot[key] = value
    with patch.object(worker_v16, "fetch_ibf_read_only_snapshot", return_value=snapshot), patch.object(
        worker_v16.base, "_complete"
    ) as complete:
        with pytest.raises(AutopilotContractError, match="AUTOPILOT_IBF_EVIDENCE_INVALID"):
            worker_v16.execute_task(_config(), _ibf_task())
    complete.assert_not_called()


def test_non_ibf_task_delegates_to_original_worker_unchanged():
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
    with patch.object(worker_v16, "_ORIGINAL_EXECUTE_TASK", delegated):
        worker_v16.execute_task(_config(), smoke)
    delegated.assert_called_once_with(_config(), smoke)


def test_main_patches_dispatch_only_for_runtime_and_restores_it():
    original = worker_v16.base.execute_task

    def observe_main():
        assert worker_v16.base.execute_task is worker_v16.execute_task

    with patch.object(worker_v16.base, "main", side_effect=observe_main) as run:
        worker_v16.main()

    run.assert_called_once_with()
    assert worker_v16.base.execute_task is original
