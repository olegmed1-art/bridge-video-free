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
            "SELECT * FROM assistant_lab.claim_control_command(%s)",
            (config.worker_id,),
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row else None


def _finish(
    conn: psycopg.Connection[Any],
    config: BridgeConfig,
    command_id: Any,
    status: str,
    *,
    result: Any = None,
    error: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT assistant_lab.finish_control_command(
                %s, %s, %s, %s::jsonb, %s
            )
            """,
            (
                command_id,
                config.worker_id,
                status,
                json.dumps(result) if result is not None else None,
                error,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    if not row or row[0] is not True:
        raise RuntimeError("control command terminal transition rejected")


def _execute(config: BridgeConfig, row: dict[str, Any]) -> dict[str, Any]:
    source_path = row.get("source_path")
    source_sha256 = row.get("source_sha256")
    if not isinstance(source_path, str) or not source_path or len(source_path) > 4096:
        raise RuntimeError("control command source_path is missing or invalid")
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in source_sha256)
    ):
        raise RuntimeError("control command source_sha256 is missing or invalid")
    payload = {
        "tool_id": row["tool_id"],
        "source_path": source_path,
        "source_sha256": source_sha256.lower(),
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
            exit_code = report.get("exit_code")
            archive_status = report.get("archive_status")
            if exit_code != 0:
                raise RuntimeError(
                    f"observer experiment {experiment_id} failed "
                    f"(exit_code={exit_code}, archive_status={archive_status})"
                )
            if archive_status == "PENDING":
                time.sleep(config.poll_seconds)
                continue
            if archive_status != "COPIED":
                raise RuntimeError(
                    f"observer experiment {experiment_id} archive failed "
                    f"(archive_status={archive_status})"
                )
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
            "SELECT assistant_lab.recover_stale_control_commands(%s)",
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
                        _finish(conn, config, row["command_id"], "FAILED", error=f"{type(exc).__name__}: {exc}"[:4000])
                    else:
                        _finish(conn, config, row["command_id"], "COMPLETED", result=result)
        except Exception:
            time.sleep(max(config.poll_seconds, 2.0))


def main() -> int:
    run_forever(load_config())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
