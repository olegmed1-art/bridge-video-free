from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BridgeConfig:
    database_url: str
    control_token: str
    worker_id: str
    control_url: str = "http://127.0.0.1:8765"
    poll_seconds: float = 1.0
    stale_after_seconds: int = 900


def load_config() -> BridgeConfig:
    database_url = os.getenv("ASSISTANT_LAB_DATABASE_URL", "").strip()
    token = os.getenv("ASSISTANT_LAB_CONTROL_TOKEN", "").strip()
    if not database_url:
        raise RuntimeError("ASSISTANT_LAB_DATABASE_URL is required")
    if len(token) < 32:
        raise RuntimeError("ASSISTANT_LAB_CONTROL_TOKEN is required")
    return BridgeConfig(
        database_url=database_url,
        control_token=token,
        worker_id=os.getenv("ASSISTANT_LAB_CONTROL_BRIDGE_ID", f"oracle-control-bridge-{socket.gethostname()}"),
        control_url=os.getenv("ASSISTANT_LAB_CONTROL_URL", "http://127.0.0.1:8765").rstrip("/"),
        poll_seconds=float(os.getenv("ASSISTANT_LAB_CONTROL_POLL_SECONDS", "1")),
        stale_after_seconds=int(os.getenv("ASSISTANT_LAB_CONTROL_STALE_SECONDS", "900")),
    )


def _request(config: BridgeConfig, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        f"{config.control_url}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {config.control_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise RuntimeError(f"control API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"control API unavailable: {exc.reason}") from exc
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("control API returned non-object JSON")
    return value


def _claim(conn: psycopg.Connection[Any], config: BridgeConfig) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            WITH candidate AS (
                SELECT command_id
                FROM assistant_lab.control_command
                WHERE status = 'QUEUED'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE assistant_lab.control_command AS c
               SET status = 'RUNNING',
                   claimed_by = %s,
                   claimed_at = now(),
                   updated_at = now(),
                   attempts = attempts + 1
              FROM candidate
             WHERE c.command_id = candidate.command_id
         RETURNING c.*
            """,
            (config.worker_id,),
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row else None


def _finish(conn: psycopg.Connection[Any], command_id: Any, status: str, *, result: Any = None, error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE assistant_lab.control_command
               SET status = %s,
                   result_json = %s,
                   error_text = %s,
                   completed_at = now(),
                   updated_at = now()
             WHERE command_id = %s
            """,
            (status, json.dumps(result) if result is not None else None, error, command_id),
        )
    conn.commit()


def _execute(config: BridgeConfig, row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "tool_id": row["tool_id"],
        "experiment_id": row.get("experiment_id") or None,
        "timeout_seconds": int(row.get("timeout_seconds") or 3600),
        "label": row.get("label") or row["tool_id"],
    }
    queued = _request(config, "POST", "/v1/run", payload)
    experiment_id = queued.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise RuntimeError("control API did not return experiment_id")

    deadline = time.monotonic() + int(payload["timeout_seconds"]) + 60
    while time.monotonic() < deadline:
        try:
            summary = _request(config, "GET", f"/v1/experiments/{experiment_id}")
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
            time.sleep(config.poll_seconds)
            continue
        report = summary.get("observer_report")
        if isinstance(report, dict):
            return {
                "schema": "assistant-lab-control-bridge-result/v0.1",
                "experiment_id": experiment_id,
                "tool_id": row["tool_id"],
                "observer": summary,
                "completed_at": utc_now(),
            }
        time.sleep(config.poll_seconds)
    raise RuntimeError(f"experiment {experiment_id} did not finish before bridge deadline")


def _recover_stale(conn: psycopg.Connection[Any], config: BridgeConfig) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE assistant_lab.control_command
               SET status = CASE WHEN attempts < max_attempts THEN 'QUEUED' ELSE 'FAILED' END,
                   error_text = CASE WHEN attempts < max_attempts THEN error_text ELSE 'stale control bridge claim exhausted retries' END,
                   claimed_by = NULL,
                   claimed_at = NULL,
                   updated_at = now(),
                   completed_at = CASE WHEN attempts < max_attempts THEN completed_at ELSE now() END
             WHERE status = 'RUNNING'
               AND claimed_at < now() - (%s * interval '1 second')
            """,
            (config.stale_after_seconds,),
        )
    conn.commit()


def run_forever(config: BridgeConfig) -> None:
    while True:
        try:
            with psycopg.connect(config.database_url, connect_timeout=10, application_name="assistant-lab-control-bridge") as conn:
                _recover_stale(conn, config)
                while True:
                    row = _claim(conn, config)
                    if row is None:
                        time.sleep(config.poll_seconds)
                        continue
                    try:
                        result = _execute(config, row)
                    except Exception as exc:
                        _finish(conn, row["command_id"], "FAILED", error=f"{type(exc).__name__}: {exc}"[:4000])
                    else:
                        _finish(conn, row["command_id"], "COMPLETED", result=result)
        except Exception:
            time.sleep(max(config.poll_seconds, 2.0))


def main() -> int:
    run_forever(load_config())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
