#!/usr/bin/env python3
"""Read-only collector for the Oracle idle STOP guard.

No power or workload mutation exists in this module. It samples every required
workload family. Collector failures become UNKNOWN, never implicit zero work.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

MAX_AGE_SECONDS = int(os.getenv("ORACLE_IDLE_MAX_AGE_SECONDS", "120"))
LAB_ENV = Path(os.getenv("ASSISTANT_LAB_ENV_FILE", "/opt/bridge-school/assistant-lab/assistant-lab.env"))
VIDEO_DSN = Path(os.getenv("BRIDGE_VIDEO_QUEUE_DSN_FILE", "/opt/bridge-school/universal-video/secrets/video-queue-dsn"))
LEASE_FILE = Path(os.getenv("ORACLE_HOST_LEASE_FILE", "/run/bridge-school/oracle-host-lease"))
UV_STATUS = Path(os.getenv("UNIVERSAL_VIDEO_STATUS_PATH", "/run/bridge-school/universal-video-status.json"))
UV_SPOOL = Path(os.getenv("UNIVERSAL_VIDEO_SPOOL_ROOT", "/opt/bridge-school/universal-video/spool"))


def _now() -> float:
    return time.time()


def _entry(state: str, observed_at: float, **evidence: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"state": state, "observed_at": observed_at}
    if evidence:
        value["evidence"] = evidence
    return value


def _unknown(observed_at: float, reason: str, **evidence: Any) -> dict[str, Any]:
    return _entry("UNKNOWN", observed_at, reason=reason, **evidence)


def _read_assignment(path: Path, key: str) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                value = line.split("=", 1)[1].strip()
                return value or None
    except OSError:
        return None
    return None


def _read_secret(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def _assistant_stale_after() -> int | None:
    raw = os.getenv("ASSISTANT_LAB_STALE_AFTER_SECONDS", "").strip()
    if not raw:
        raw = (_read_assignment(LAB_ENV, "ASSISTANT_LAB_STALE_AFTER_SECONDS") or "900").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 120 <= value <= 86400 else None


def _db_snapshot(observed_at: float) -> dict[str, dict[str, Any]]:
    names = (
        "assistant_lab_job",
        "assistant_lab_control_command",
        "assistant_lab_research_job",
        "assistant_lab_research_children",
        "ben",
        "bulk",
        "other_allowed_workloads",
    )
    dsn = _read_assignment(LAB_ENV, "ASSISTANT_LAB_DATABASE_URL")
    if not dsn:
        return {name: _unknown(observed_at, "assistant_lab_dsn_unavailable") for name in names}
    stale_after = _assistant_stale_after()
    if stale_after is None:
        return {name: _unknown(observed_at, "assistant_lab_stale_timeout_invalid") for name in names}
    try:
        import psycopg  # type: ignore
    except Exception:
        return {name: _unknown(observed_at, "psycopg_unavailable") for name in names}
    try:
        with psycopg.connect(dsn, connect_timeout=8, application_name="oracle-idle-collector") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT observed_at, active_jobs, stale_running_jobs, active_control_commands, "
                    "active_research_jobs, active_research_children, active_ben_jobs, "
                    "active_bulk_jobs, active_other_jobs "
                    "FROM assistant_lab.oracle_idle_snapshot_v2(%s)",
                    (stale_after,),
                )
                row = cur.fetchone()
        if row is None or len(row) != 9:
            raise RuntimeError("invalid assistant snapshot")
        server_time = row[0].timestamp()
        active_jobs = int(row[1])
        stale_running = int(row[2])
        counts = [active_jobs] + [int(v) for v in row[3:]]
        if stale_running < 0 or any(v < 0 for v in counts):
            raise RuntimeError("negative count")
    except Exception:
        return {name: _unknown(observed_at, "assistant_lab_query_failed") for name in names}
    result = {
        name: _entry("BUSY" if count else "IDLE", server_time, active_count=count)
        for name, count in zip(names, counts)
    }
    if stale_running:
        result["assistant_lab_job"] = _unknown(
            server_time,
            "assistant_lab_stale_running_heartbeat",
            active_count=active_jobs,
            stale_running_count=stale_running,
            stale_after_seconds=stale_after,
        )
    return result


def _video_neon(observed_at: float) -> dict[str, Any]:
    dsn = _read_secret(VIDEO_DSN)
    if not dsn:
        return _unknown(observed_at, "video_queue_dsn_unavailable")
    try:
        import psycopg  # type: ignore
        with psycopg.connect(dsn, connect_timeout=8, application_name="oracle-idle-video") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT clock_timestamp(), count(*) FROM video_queue.job "
                    "WHERE status IN ('PENDING_CANARY','QUEUED','LEASED')"
                )
                row = cur.fetchone()
        if row is None or len(row) != 2:
            raise RuntimeError("invalid video snapshot")
        count = int(row[1])
        if count < 0:
            raise RuntimeError("negative count")
        return _entry("BUSY" if count else "IDLE", row[0].timestamp(), active_count=count)
    except Exception:
        return _unknown(observed_at, "video_queue_query_failed")


def _spool(observed_at: float) -> dict[str, Any]:
    try:
        if UV_SPOOL.is_symlink() or not UV_SPOOL.is_dir():
            return _unknown(observed_at, "uv_spool_unavailable")
        busy_files: list[str] = []
        for name in ("inbox", "running"):
            directory = UV_SPOOL / name
            if directory.is_symlink() or not directory.is_dir():
                return _unknown(observed_at, f"uv_spool_{name}_unavailable")
            for path in directory.iterdir():
                if path.is_symlink() or not path.is_file():
                    return _unknown(observed_at, f"uv_spool_{name}_invalid_entry")
                if path.suffix == ".json":
                    busy_files.append(f"{name}/{path.name}")
        return _entry("BUSY" if busy_files else "IDLE", observed_at, active_count=len(busy_files))
    except OSError:
        return _unknown(observed_at, "uv_spool_read_failed")


def _systemctl_state(service: str) -> str | None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service], capture_output=True, text=True, timeout=5
        )
        value = result.stdout.strip()
        return value or None
    except (OSError, subprocess.SubprocessError):
        return None


def _assistant_resident(observed_at: float) -> dict[str, Any]:
    state = _systemctl_state("assistant-lab.service")
    if state == "active":
        return _entry("IDLE", observed_at, service_state=state)
    if state is None:
        return _unknown(observed_at, "assistant_lab_service_state_unavailable")
    return _unknown(observed_at, f"assistant_lab_service_{state}")


def _resident(observed_at: float) -> dict[str, Any]:
    source_state = _systemctl_state("universal-video.service")
    container_state = _systemctl_state("universal-video-container.service")
    if source_state is None or container_state is None:
        return _unknown(observed_at, "resident_service_state_unavailable")
    if source_state not in {"active", "inactive", "failed"} or container_state not in {"active", "inactive", "failed"}:
        return _unknown(observed_at, "resident_service_state_transitional")
    if source_state == "failed" or container_state == "failed":
        return _unknown(observed_at, "resident_service_failed")
    if source_state == "active" and container_state == "active":
        return _unknown(observed_at, "conflicting_resident_services")
    if "active" not in {source_state, container_state}:
        return _entry("IDLE", observed_at, source_service=source_state, container_service=container_state)
    try:
        if UV_STATUS.is_symlink() or not UV_STATUS.is_file():
            return _unknown(observed_at, "resident_status_missing")
        status = json.loads(UV_STATUS.read_text(encoding="utf-8"))
        if not isinstance(status, dict) or status.get("schema") != "universal-video-resident-status-v2":
            return _unknown(observed_at, "resident_status_invalid")
        status_time = float(status.get("observed_at_unix"))
        active_jobs = status.get("active_jobs")
        if not isinstance(active_jobs, list):
            return _unknown(observed_at, "resident_active_jobs_invalid")
        return _entry("BUSY" if active_jobs else "IDLE", status_time, active_count=len(active_jobs))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return _unknown(observed_at, "resident_status_read_failed")


def _process_match(pattern: str) -> bool | None:
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        return None
    except (OSError, subprocess.SubprocessError):
        return None


def _external_processes(observed_at: float) -> dict[str, Any]:
    patterns = {
        "observer": r"[a]ssistant_lab.*observer.*experiment|[o]racle_assistant_lab_observer.*run",
        "ben": r"[a]ssistant_lab.*ben_runtime|[b]en.*compute",
        "bulk": r"[a]ssistant_lab.*bulk|[o]racle.*bulk",
    }
    active: list[str] = []
    for name, pattern in patterns.items():
        matched = _process_match(pattern)
        if matched is None:
            return _unknown(observed_at, f"process_telemetry_unavailable:{name}")
        if matched:
            active.append(name)
    return _entry("BUSY" if active else "IDLE", observed_at, active_process_families=active)


def _lease(observed_at: float) -> dict[str, Any]:
    try:
        if not LEASE_FILE.exists() and not LEASE_FILE.is_symlink():
            return _entry("IDLE", observed_at, lease_present=False)
        if LEASE_FILE.is_symlink() or not LEASE_FILE.is_file():
            return _unknown(observed_at, "operator_lease_unreadable")
        text = LEASE_FILE.read_text(encoding="utf-8").strip()
        match = re.fullmatch(r"expires_at_epoch=([0-9]{10,})", text)
        if not match:
            return _unknown(observed_at, "operator_lease_invalid")
        expires = int(match.group(1))
        if expires <= observed_at:
            return _unknown(observed_at, "operator_lease_stale")
        return _entry("BUSY", observed_at, lease_present=True, expires_at_epoch=expires)
    except OSError:
        return _unknown(observed_at, "operator_lease_read_failed")


def collect() -> dict[str, Any]:
    observed = _now()
    families = _db_snapshot(observed)
    families["assistant_lab_resident"] = _assistant_resident(observed)
    families["universal_video_neon"] = _video_neon(observed)
    families["universal_video_spool"] = _spool(observed)
    families["universal_video_resident"] = _resident(observed)
    families["observer_external_processes"] = _external_processes(observed)
    families["operator_maintenance_lease"] = _lease(observed)
    resident = families["universal_video_resident"]
    spool = families["universal_video_spool"]
    if resident.get("state") == "IDLE" and spool.get("state") == "BUSY":
        resident["conflict"] = True
    return {
        "schema": "oracle-idle-telemetry-v1",
        "generated_at": observed,
        "max_age_seconds": MAX_AGE_SECONDS,
        "families": families,
    }


def main() -> int:
    print(json.dumps(collect(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
