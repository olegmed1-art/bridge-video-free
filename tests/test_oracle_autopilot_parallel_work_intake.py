from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import psycopg
import pytest

from oracle_autopilot.parallel_work_intake import (
    PARALLEL_WORK_MANIFEST,
    ParallelWorkManifestError,
    canonical_manifest_text,
    manifest_sha256,
    validate_parallel_work_manifest,
)
from oracle_autopilot.worker import WorkerConfig, reconcile_parallel_work_intake

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0365_autopilot_parallel_work_intake.sql"
ROLLBACK = ROOT / "database/rollbacks/0365_autopilot_parallel_work_intake.sql"


def test_release_manifest_is_independent_repository_only_and_canonical() -> None:
    validate_parallel_work_manifest(PARALLEL_WORK_MANIFEST)
    items = PARALLEL_WORK_MANIFEST["items"]
    assert [item["target_pr"] for item in items] == [1148, 1599, 1683, 1736]
    assert len({item["role"] for item in items}) == len(items)
    assert all(item["task_spec_json"]["parallel_safe"] is True for item in items)
    assert all(
        item["task_spec_json"]["execution_scope"] == "REPOSITORY"
        and item["task_spec_json"]["production_mutation"] is False
        for item in items
    )
    body = canonical_manifest_text()
    assert manifest_sha256() == hashlib.sha256(body.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda manifest: manifest["items"][1]["task_spec_json"].update(
                execution_scope="OWNER_GATED"
            ),
            "AUTOPILOT_PARALLEL_SCOPE_INVALID",
        ),
        (
            lambda manifest: manifest["items"][1]["task_spec_json"][
                "expected_changed_files"
            ].append("database/migrations/9999_unsafe.sql"),
            "AUTOPILOT_PARALLEL_REPAIR_PATH_INVALID",
        ),
        (
            lambda manifest: manifest["items"][1].update(
                role=manifest["items"][0]["role"]
            ),
            "AUTOPILOT_PARALLEL_DUPLICATE_INVALID",
        ),
    ],
)
def test_release_manifest_rejects_scope_expansion(mutation, error: str) -> None:
    manifest = copy.deepcopy(PARALLEL_WORK_MANIFEST)
    mutation(manifest)
    with pytest.raises(ParallelWorkManifestError, match=error):
        validate_parallel_work_manifest(manifest)


def test_worker_registers_manifest_once_per_release(monkeypatch) -> None:
    calls = []

    def fake_rpc(_config, sql, params):
        calls.append((sql, params))
        return {
            "manifest_sha256": manifest_sha256(),
            "item_count": 4,
            "registered_count": 4,
            "replayed": False,
        }

    monkeypatch.setattr("oracle_autopilot.worker._rpc_one", fake_rpc)
    monkeypatch.setattr(
        "oracle_autopilot.worker._PARALLEL_WORK_MANIFEST_REGISTERED", False
    )
    config = WorkerConfig(dsn="postgresql://unused", worker_id="parallel-intake")
    assert reconcile_parallel_work_intake(config) == 4
    assert reconcile_parallel_work_intake(config) == 0
    assert len(calls) == 1
    assert "register_parallel_work_manifest" in calls[0][0]
    assert calls[0][1] == (canonical_manifest_text(), manifest_sha256())


def test_worker_tolerates_migration_rolling_order(monkeypatch) -> None:
    def missing_rpc(_config, _sql, _params):
        raise psycopg.errors.UndefinedFunction("0365 not installed")

    monkeypatch.setattr("oracle_autopilot.worker._rpc_one", missing_rpc)
    monkeypatch.setattr(
        "oracle_autopilot.worker._PARALLEL_WORK_MANIFEST_REGISTERED", False
    )
    config = WorkerConfig(dsn="postgresql://unused", worker_id="parallel-intake")
    assert reconcile_parallel_work_intake(config) == 0


def test_migration_enforces_capacity_roles_and_control_pr_exclusions() -> None:
    sql = MIGRATION.read_text()
    assert "AUTOPILOT_PARALLEL_WORK_V1" in sql
    assert "candidate_role=ANY(seen_roles)" in sql
    assert "role.execution_scope='REPOSITORY'" in sql
    assert "role.can_repair" in sql
    assert "github_dispatch_comment_id=candidate_target_pr::bigint" in sql
    assert "outbox.codex_command_pr=candidate_target_pr" in sql
    assert "work.mailbox_pr=candidate_target_pr" in sql
    assert "active_task.goal_json->>'role'=item.role" in sql
    assert "reserved.role=item.role" in sql
    assert "WAITING_FOR_ROLE_CAPACITY" in sql
    assert "GRANT EXECUTE ON FUNCTION autopilot.register_parallel_work_manifest" in sql
    assert "TO autopilot_runtime,autopilot_runtime_principal" in sql
    assert "merge" in sql and "production_write" in sql and "neon_write" in sql


def test_rollback_refuses_to_delete_progressed_manifest_work() -> None:
    sql = ROLLBACK.read_text()
    assert "AUTOPILOT_PARALLEL_INTAKE_ROLLBACK_REQUIRES_RECONCILIATION" in sql
    assert "state<>'READY' OR last_task_id IS NOT NULL" in sql
    assert "UPDATE autopilot.project_planner_state AS planner" in sql
    assert "SET last_work_item_id=NULL" in sql
    assert "planner.last_work_item_id IN" in sql
    assert "EXECUTE original" in sql
