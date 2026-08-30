"""Resident Oracle dispatcher for School Autopilot Lite.

The first implementation is intentionally shadow-only. It does not execute a
shell command, call a model, mutate GitHub, access Drive, or change production.
It proves the durable queue mechanics that remove the one-hour chat gap:

* direct Neon LISTEN/NOTIFY wake-up;
* bounded recovery polling;
* SKIP LOCKED claims, expiring leases, heartbeat and fencing epochs;
* immediate queue draining after every completed transition;
* event dedupe and retained completion evidence through database RPCs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import threading
import time
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .contract import (
    AutopilotContractError,
    ClaimedTask,
    claimed_task_from_row,
    validate_task_contract,
)


CHANNEL = "autopilot_ready"
RUNTIME_MODE = "SHADOW"
LOGGER = logging.getLogger("oracle_autopilot")


@dataclass(frozen=True)
class WorkerConfig:
    dsn: str
    worker_id: str
    lease_seconds: int = 60
    heartbeat_seconds: int = 15
    recovery_poll_seconds: float = 30.0


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def validate_neon_direct_dsn(raw: str) -> str:
    value = raw.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise RuntimeError("autopilot DSN must be PostgreSQL")
    if not parsed.username or not parsed.password:
        raise RuntimeError("autopilot DSN must include a dedicated login and password")
    hostname = (parsed.hostname or "").lower()
    if not hostname.endswith(".neon.tech"):
        raise RuntimeError("autopilot DSN must target Neon")
    if "-pooler." in hostname:
        raise RuntimeError("autopilot LISTEN/NOTIFY requires a direct Neon endpoint")
    if parsed.path != "/neondb":
        raise RuntimeError("autopilot DSN must target neondb")
    if parsed.fragment:
        raise RuntimeError("autopilot DSN must not contain a fragment")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if query.get("sslmode", [""])[0] not in {"require", "verify-full"}:
        raise RuntimeError("autopilot DSN must require TLS")
    if query.get("channel_binding") != ["require"]:
        raise RuntimeError("autopilot DSN must require channel binding")
    expected_user = os.getenv("AUTOPILOT_EXPECTED_DB_USER", "").strip()
    if expected_user and parsed.username != expected_user:
        raise RuntimeError("autopilot DSN uses an unexpected principal")
    return value


def load_config() -> WorkerConfig:
    if os.getenv("AUTOPILOT_RUNTIME_MODE", "").strip().upper() != RUNTIME_MODE:
        raise RuntimeError("AUTOPILOT_RUNTIME_MODE must be SHADOW")
    worker_id = os.getenv(
        "AUTOPILOT_WORKER_ID", f"oracle-autopilot:{socket.gethostname()}:{os.getpid()}"
    ).strip()
    if not worker_id or len(worker_id) > 256:
        raise RuntimeError("AUTOPILOT_WORKER_ID is invalid")
    lease_seconds = int(os.getenv("AUTOPILOT_LEASE_SECONDS", "60"))
    heartbeat_seconds = int(os.getenv("AUTOPILOT_HEARTBEAT_SECONDS", "15"))
    recovery_poll_seconds = float(os.getenv("AUTOPILOT_RECOVERY_POLL_SECONDS", "30"))
    if not 30 <= lease_seconds <= 300:
        raise RuntimeError("AUTOPILOT_LEASE_SECONDS must be between 30 and 300")
    if heartbeat_seconds < 5 or heartbeat_seconds * 3 >= lease_seconds:
        raise RuntimeError("autopilot heartbeat is inconsistent with the lease")
    if not 5 <= recovery_poll_seconds <= 60:
        raise RuntimeError("AUTOPILOT_RECOVERY_POLL_SECONDS must be between 5 and 60")
    return WorkerConfig(
        dsn=validate_neon_direct_dsn(_require_env("AUTOPILOT_DATABASE_URL")),
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
        recovery_poll_seconds=recovery_poll_seconds,
    )


def _connect(dsn: str, *, autocommit: bool = False):
    return psycopg.connect(
        dsn,
        autocommit=autocommit,
        connect_timeout=10,
        application_name="school-autopilot-oracle-shadow",
        row_factory=dict_row,
    )


def _rpc_one(config: WorkerConfig, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    with _connect(config.dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        conn.commit()
        return row


def reconcile_stale(config: WorkerConfig) -> tuple[int, int]:
    row = _rpc_one(config, "SELECT * FROM autopilot.reconcile_stale_tasks()", ())
    if not row:
        return 0, 0
    return int(row["requeued"]), int(row["failed_closed"])


def claim_one(config: WorkerConfig) -> ClaimedTask | None:
    row = _rpc_one(
        config,
        "SELECT * FROM autopilot.claim_next_task(%s, %s)",
        (config.worker_id, config.lease_seconds),
    )
    if not row:
        return None
    task = claimed_task_from_row(row)
    validate_task_contract(task)
    return task


def heartbeat_task(config: WorkerConfig, task: ClaimedTask) -> bool:
    row = _rpc_one(
        config,
        "SELECT autopilot.heartbeat_task(%s::uuid, %s, %s, %s) AS owned",
        (task.task_id, config.worker_id, task.lease_epoch, config.lease_seconds),
    )
    return bool(row and row["owned"])


@contextmanager
def keep_lease_alive(config: WorkerConfig, task: ClaimedTask) -> Iterator[None]:
    stop = threading.Event()
    ownership_lost = threading.Event()

    def _loop() -> None:
        while not stop.wait(config.heartbeat_seconds):
            try:
                if not heartbeat_task(config, task):
                    ownership_lost.set()
                    return
            except psycopg.Error:
                continue

    thread = threading.Thread(target=_loop, name="autopilot-heartbeat", daemon=True)
    thread.start()
    try:
        yield
        if ownership_lost.is_set():
            raise AutopilotContractError("AUTOPILOT_LEASE_LOST")
    finally:
        stop.set()
        thread.join(timeout=2)


def _canonical_evidence(summary: dict[str, Any]) -> str:
    encoded = json.dumps(
        summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _complete(
    config: WorkerConfig,
    task: ClaimedTask,
    *,
    evidence_class: str,
    summary: dict[str, Any],
) -> None:
    row = _rpc_one(
        config,
        "SELECT autopilot.complete_task(%s::uuid, %s, %s, %s, %s, %s::jsonb) AS completed",
        (
            task.task_id,
            config.worker_id,
            task.lease_epoch,
            evidence_class,
            _canonical_evidence(summary),
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    if not row or not row["completed"]:
        raise AutopilotContractError("AUTOPILOT_COMPLETION_FENCED")


def execute_task(config: WorkerConfig, task: ClaimedTask) -> None:
    validate_task_contract(task)

    if task.goal_type == "AUTOPILOT_SMOKE_V1":
        _complete(
            config,
            task,
            evidence_class="SYNTHETIC_SHADOW_COMPLETION",
            summary={
                "task_id": task.task_id,
                "task_kind": task.goal_type,
                "runtime": "ORACLE_RESIDENT",
                "production_mutation": False,
                "model_calls": 0,
            },
        )
        return

    if task.goal_type == "OWNER_BOUNDARY_V1":
        row = _rpc_one(
            config,
            "SELECT autopilot.mark_owner_required(%s::uuid, %s, %s, %s) AS marked",
            (
                task.task_id,
                config.worker_id,
                task.lease_epoch,
                "ACCOUNT_OWNER_ACTION_REQUIRED",
            ),
        )
        if not row or not row["marked"]:
            raise AutopilotContractError("AUTOPILOT_OWNER_BOUNDARY_FENCED")
        return

    if task.step_cursor == 0:
        row = _rpc_one(
            config,
            "SELECT autopilot.mark_waiting_external(%s::uuid, %s, %s, %s, %s, %s, %s) AS waiting",
            (
                task.task_id,
                config.worker_id,
                task.lease_epoch,
                "SYNTHETIC",
                task.goal_json["correlation_id"],
                "SHADOW_RESUME",
                300,
            ),
        )
        if not row or not row["waiting"]:
            raise AutopilotContractError("AUTOPILOT_WAIT_FENCED")
        return

    _complete(
        config,
        task,
        evidence_class="SYNTHETIC_SHADOW_RESUME",
        summary={
            "task_id": task.task_id,
            "task_kind": task.goal_type,
            "correlation_id": task.goal_json["correlation_id"],
            "runtime": "ORACLE_RESIDENT",
            "production_mutation": False,
            "model_calls": 0,
        },
    )


def fail_task(
    config: WorkerConfig, task: ClaimedTask, *, error_code: str, retryable: bool
) -> str:
    row = _rpc_one(
        config,
        "SELECT autopilot.fail_task(%s::uuid, %s, %s, %s, %s) AS resulting_state",
        (task.task_id, config.worker_id, task.lease_epoch, error_code, retryable),
    )
    return str(row["resulting_state"]) if row else "FENCED"


def process_one(config: WorkerConfig) -> bool:
    reconcile_stale(config)
    task = claim_one(config)
    if task is None:
        return False
    LOGGER.info(
        "task_claimed task_id=%s kind=%s lease_epoch=%s attempt=%s",
        task.task_id,
        task.goal_type,
        task.lease_epoch,
        task.attempts,
    )
    try:
        with keep_lease_alive(config, task):
            execute_task(config, task)
        LOGGER.info("task_transitioned task_id=%s kind=%s", task.task_id, task.goal_type)
    except AutopilotContractError as exc:
        resulting_state = fail_task(config, task, error_code=str(exc), retryable=False)
        LOGGER.warning(
            "task_contract_failure task_id=%s code=%s state=%s",
            task.task_id,
            exc,
            resulting_state,
        )
    except psycopg.OperationalError:
        resulting_state = fail_task(
            config, task, error_code="AUTOPILOT_TRANSIENT_DATABASE_ERROR", retryable=True
        )
        LOGGER.warning(
            "task_database_failure task_id=%s state=%s", task.task_id, resulting_state
        )
    except Exception:
        resulting_state = fail_task(
            config, task, error_code="AUTOPILOT_UNCLASSIFIED_FAILURE", retryable=False
        )
        LOGGER.exception(
            "task_unclassified_failure task_id=%s state=%s", task.task_id, resulting_state
        )
    return True


def wait_for_wakeup(listener, timeout_seconds: float) -> None:
    for _notification in listener.notifies(timeout=timeout_seconds, stop_after=1):
        return


def drain_ready(config: WorkerConfig) -> int:
    processed = 0
    while process_one(config):
        processed += 1
    return processed


def run_forever(config: WorkerConfig) -> None:
    LOGGER.info(
        "worker_started worker_id=%s mode=%s recovery_poll_seconds=%s",
        config.worker_id,
        RUNTIME_MODE,
        config.recovery_poll_seconds,
    )
    while True:
        try:
            with _connect(config.dsn, autocommit=True) as listener:
                listener.execute(f"LISTEN {CHANNEL}")
                while True:
                    # Drain every ready transition without sleeping. The polling
                    # timeout is reached only when no runnable task exists.
                    if drain_ready(config):
                        continue
                    wait_for_wakeup(listener, config.recovery_poll_seconds)
        except KeyboardInterrupt:
            return
        except psycopg.Error as exc:
            LOGGER.warning("listener_reconnect error_type=%s", type(exc).__name__)
            time.sleep(2)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("AUTOPILOT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_forever(load_config())


if __name__ == "__main__":
    main()
