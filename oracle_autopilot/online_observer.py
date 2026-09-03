"""Server-resident SHADOW_ONLY online pilot observer and guarded generator.

The process has no table privileges. It calls only the database-owned online
pilot RPCs introduced by migration 0304. Those RPCs enforce one active task,
zero-cost smoke-only creation, a rolling rate cap, retained evidence and a
durable circuit breaker. Local heartbeat and finding files contain no secrets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .worker import validate_neon_direct_dsn


LOGGER = logging.getLogger("oracle_autopilot.online_observer")
RUNTIME_MODE = "SHADOW"
OBSERVER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TASK_KEY_PATTERN = re.compile(r"^phase3b-oracle-online-[A-Za-z0-9._:-]{1,170}$")
ALLOWED_ACTIONS = frozenset(
    {
        "CREATED",
        "MONITORING",
        "QUEUE_BUSY",
        "INTERVAL_HOLD",
        "RATE_HOLD",
        "CIRCUIT_OPEN",
    }
)


@dataclass(frozen=True)
class ObserverConfig:
    dsn: str
    observer_id: str
    state_dir: Path
    tick_seconds: float = 1.0
    min_interval_seconds: int = 5
    max_tasks_per_hour: int = 720
    error_threshold: int = 3


@dataclass(frozen=True)
class TickResult:
    action: str
    task_key: str | None
    task_status: str | None
    circuit_open: bool
    finding_code: str | None
    created_count: int
    pass_count: int
    finding_count: int


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def load_config() -> ObserverConfig:
    if os.getenv("AUTOPILOT_RUNTIME_MODE", "").strip().upper() != RUNTIME_MODE:
        raise RuntimeError("AUTOPILOT_RUNTIME_MODE must be SHADOW")
    observer_id = os.getenv(
        "AUTOPILOT_ONLINE_OBSERVER_ID",
        f"oracle-online-observer:{socket.gethostname()}",
    ).strip()
    if OBSERVER_ID_PATTERN.fullmatch(observer_id) is None:
        raise RuntimeError("AUTOPILOT_ONLINE_OBSERVER_ID is invalid")

    tick_seconds = float(os.getenv("AUTOPILOT_ONLINE_TICK_SECONDS", "1"))
    min_interval_seconds = int(
        os.getenv("AUTOPILOT_ONLINE_MIN_INTERVAL_SECONDS", "5")
    )
    max_tasks_per_hour = int(
        os.getenv("AUTOPILOT_ONLINE_MAX_TASKS_PER_HOUR", "720")
    )
    error_threshold = int(os.getenv("AUTOPILOT_ONLINE_ERROR_THRESHOLD", "3"))
    if not 0.25 <= tick_seconds <= 5:
        raise RuntimeError("AUTOPILOT_ONLINE_TICK_SECONDS is invalid")
    if not 5 <= min_interval_seconds <= 60:
        raise RuntimeError("AUTOPILOT_ONLINE_MIN_INTERVAL_SECONDS is invalid")
    if not 1 <= max_tasks_per_hour <= 720:
        raise RuntimeError("AUTOPILOT_ONLINE_MAX_TASKS_PER_HOUR is invalid")
    if max_tasks_per_hour > 3600 // min_interval_seconds:
        raise RuntimeError("online rate cap exceeds the interval boundary")
    if not 1 <= error_threshold <= 10:
        raise RuntimeError("AUTOPILOT_ONLINE_ERROR_THRESHOLD is invalid")

    state_dir = Path(
        os.getenv(
            "AUTOPILOT_ONLINE_STATE_DIR",
            "/var/lib/school-autopilot-online-observer",
        )
    )
    if not state_dir.is_absolute():
        raise RuntimeError("AUTOPILOT_ONLINE_STATE_DIR must be absolute")
    return ObserverConfig(
        dsn=validate_neon_direct_dsn(_require_env("AUTOPILOT_DATABASE_URL")),
        observer_id=observer_id,
        state_dir=state_dir,
        tick_seconds=tick_seconds,
        min_interval_seconds=min_interval_seconds,
        max_tasks_per_hour=max_tasks_per_hour,
        error_threshold=error_threshold,
    )


def _connect(config: ObserverConfig):
    return psycopg.connect(
        config.dsn,
        autocommit=True,
        connect_timeout=10,
        application_name="school-autopilot-online-observer",
        row_factory=dict_row,
    )


def _parse_tick(row: dict[str, Any] | None) -> TickResult:
    if not row or row.get("action") not in ALLOWED_ACTIONS:
        raise RuntimeError("AUTOPILOT_ONLINE_TICK_RESPONSE_INVALID")
    task_key = row.get("task_key")
    if task_key is not None and TASK_KEY_PATTERN.fullmatch(str(task_key)) is None:
        raise RuntimeError("AUTOPILOT_ONLINE_TASK_KEY_INVALID")
    finding_code = row.get("finding_code")
    if finding_code is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", str(finding_code)):
        raise RuntimeError("AUTOPILOT_ONLINE_FINDING_CODE_INVALID")
    result = TickResult(
        action=str(row["action"]),
        task_key=str(task_key) if task_key is not None else None,
        task_status=str(row["task_status"]) if row.get("task_status") else None,
        circuit_open=bool(row.get("circuit_open")),
        finding_code=str(finding_code) if finding_code is not None else None,
        created_count=int(row.get("created_count", -1)),
        pass_count=int(row.get("pass_count", -1)),
        finding_count=int(row.get("finding_count", -1)),
    )
    if min(result.created_count, result.pass_count, result.finding_count) < 0:
        raise RuntimeError("AUTOPILOT_ONLINE_COUNTER_INVALID")
    if result.pass_count > result.created_count:
        raise RuntimeError("AUTOPILOT_ONLINE_COUNTER_INVALID")
    if result.circuit_open != (result.action == "CIRCUIT_OPEN"):
        raise RuntimeError("AUTOPILOT_ONLINE_CIRCUIT_RESPONSE_INVALID")
    return result


def tick_once(conn, config: ObserverConfig) -> TickResult:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM autopilot.online_pilot_tick(%s, %s, %s)",
            (
                config.observer_id,
                config.min_interval_seconds,
                config.max_tasks_per_hour,
            ),
        )
        return _parse_tick(cur.fetchone())


def status_once(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM autopilot.online_pilot_status()")
        row = cur.fetchone()
    if not row or row.get("observer_mode") != "SHADOW_ONLY":
        raise RuntimeError("AUTOPILOT_ONLINE_STATUS_INVALID")
    return dict(row)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def persist_finding(
    config: ObserverConfig,
    *,
    code: str,
    task_key: str | None,
    required_fix: str,
) -> Path:
    identity = f"{code}:{task_key or 'none'}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    path = config.state_dir / "findings" / f"{digest}.json"
    if not path.exists():
        _atomic_json(
            path,
            {
                "finding_code": code,
                "observed_at": _utc_now(),
                "required_fix": required_fix,
                "task_key": task_key,
            },
        )
    return path


def open_local_circuit(
    config: ObserverConfig,
    *,
    code: str,
    task_key: str | None,
    required_fix: str,
) -> None:
    persist_finding(
        config,
        code=code,
        task_key=task_key,
        required_fix=required_fix,
    )
    _atomic_json(
        config.state_dir / "circuit-open.json",
        {
            "circuit_open": True,
            "finding_code": code,
            "observed_at": _utc_now(),
            "task_key": task_key,
        },
    )


def write_heartbeat(config: ObserverConfig, result: TickResult | dict[str, Any]) -> None:
    payload = asdict(result) if isinstance(result, TickResult) else dict(result)
    payload["observer_id"] = config.observer_id
    payload["runtime_mode"] = "SHADOW_ONLY"
    payload["updated_at"] = _utc_now()
    _atomic_json(config.state_dir / "heartbeat.json", payload)


def run_forever(config: ObserverConfig) -> None:
    config.state_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    circuit_path = config.state_dir / "circuit-open.json"
    LOGGER.info(
        "online_observer_started observer_id=%s mode=SHADOW_ONLY min_interval=%s max_per_hour=%s",
        config.observer_id,
        config.min_interval_seconds,
        config.max_tasks_per_hour,
    )
    consecutive_errors = 0
    while True:
        try:
            with _connect(config) as conn:
                while True:
                    if circuit_path.exists():
                        write_heartbeat(config, status_once(conn))
                        time.sleep(5)
                        continue
                    result = tick_once(conn, config)
                    consecutive_errors = 0
                    write_heartbeat(config, result)
                    if result.action == "CREATED":
                        LOGGER.info(
                            "online_task_created task_key=%s created_count=%s pass_count=%s",
                            result.task_key,
                            result.created_count,
                            result.pass_count,
                        )
                    elif result.action == "CIRCUIT_OPEN":
                        code = result.finding_code or "ONLINE_CIRCUIT_OPEN"
                        open_local_circuit(
                            config,
                            code=code,
                            task_key=result.task_key,
                            required_fix=(
                                "Inspect the retained database finding and repair the shadow "
                                "consumer before removing the circuit marker."
                            ),
                        )
                        LOGGER.error(
                            "online_circuit_open code=%s task_key=%s",
                            code,
                            result.task_key,
                        )
                    time.sleep(config.tick_seconds)
        except KeyboardInterrupt:
            return
        except psycopg.Error:
            consecutive_errors += 1
            LOGGER.warning(
                "online_observer_database_error consecutive=%s",
                consecutive_errors,
            )
        except Exception:
            consecutive_errors += 1
            LOGGER.exception(
                "online_observer_contract_error consecutive=%s",
                consecutive_errors,
            )

        if consecutive_errors >= config.error_threshold and not circuit_path.exists():
            open_local_circuit(
                config,
                code="ONLINE_OBSERVER_RUNTIME_ERROR",
                task_key=None,
                required_fix=(
                    "Inspect observer configuration and database RPC availability before "
                    "removing the circuit marker."
                ),
            )
            LOGGER.error("online_local_circuit_open code=ONLINE_OBSERVER_RUNTIME_ERROR")
        time.sleep(2)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("AUTOPILOT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_forever(load_config())


if __name__ == "__main__":
    main()
