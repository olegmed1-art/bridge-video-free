#!/usr/bin/env python3
"""Register the three director-approved UV P1 CI snapshots through migration 0309.

This program is intentionally narrow:
- it rewrites discovered Neon DSNs to one exact temporary endpoint;
- it accepts only the immutable task keys embedded in migration 0309;
- it observes status by replaying the same SECURITY DEFINER function;
- it never selects Autopilot tables directly and never contacts production.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Final

import psycopg

PROJECT_ID: Final[str] = os.environ["PROJECT_ID"]
TEMP_BRANCH_ID: Final[str] = os.environ["TEMP_BRANCH_ID"]
PRODUCTION_BRANCH_ID: Final[str] = os.environ["PRODUCTION_BRANCH_ID"]
TEMP_ENDPOINT_ID: Final[str] = os.environ["TEMP_ENDPOINT_ID"]

APPROVED: Final[tuple[tuple[str, str, int, str], ...]] = (
    (
        "RUNTIME",
        "uv-p1-runtime-pr997-545ef013-20260901",
        997,
        "545ef0135e3cfe436b918c3ec26f5e2b77500977",
    ),
    (
        "INTAKE",
        "uv-p1-intake-pr1000-5af0675a-20260901",
        1000,
        "5af0675a9e13a9725348661be297abc5f52ff0e4",
    ),
    (
        "IDLE",
        "uv-p1-idle-pr1047-621ab073-20260901",
        1047,
        "621ab073418b3f3d1b75cb6abb074dba4ea305cb",
    ),
)
TERMINAL: Final[set[str]] = {
    "OWNER_REQUIRED",
    "FAILED_CLOSED",
    "BUDGET_STOP",
    "DONE",
    "CANCELLED",
}
ENV_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:export\s+)?([A-Z0-9_]*(?:DATABASE_URL|POSTGRES_URL|DIRECT_URL|NEON_DSN|DATABASE_DSN)[A-Z0-9_]*)=(.*)$"
)
ROOTS: Final[tuple[Path, ...]] = (
    Path("/opt/bridge-school"),
    Path("/etc/systemd/system"),
    Path("/etc/default"),
)


def _parse_value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        try:
            parts = shlex.split(raw)
        except ValueError:
            return ""
        return parts[0] if len(parts) == 1 else ""
    return raw


def _discover_candidates() -> list[str]:
    candidates: list[str] = []
    seen_inodes: set[tuple[int, int]] = set()
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                stat_result = path.stat()
            except OSError:
                continue
            if not path.is_file() or stat_result.st_size > 131_072:
                continue
            if (
                path.suffix.lower() not in {".env", ".service", ".conf", ".config", ".ini"}
                and ".env" not in path.name.lower()
            ):
                continue
            inode = (stat_result.st_dev, stat_result.st_ino)
            if inode in seen_inodes:
                continue
            seen_inodes.add(inode)
            try:
                lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
            except (OSError, UnicodeError):
                continue
            for raw_line in lines:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                match = ENV_PATTERN.fullmatch(line)
                if match:
                    value = _parse_value(match.group(2))
                    if value:
                        candidates.append(value)
    return candidates


def _temporary_dsn(raw_dsn: str) -> tuple[tuple[str, str, str, str], str] | None:
    try:
        parsed = urllib.parse.urlsplit(raw_dsn)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not host.endswith(".neon.tech")
        or not parsed.username
        or parsed.password is None
    ):
        return None
    first, separator, suffix = host.partition(".")
    if not separator or not first.startswith("ep-"):
        return None
    temporary_host = TEMP_ENDPOINT_ID + ("-pooler" if first.endswith("-pooler") else "") + "." + suffix
    username = urllib.parse.quote(parsed.username, safe="")
    password = urllib.parse.quote(parsed.password, safe="")
    port = f":{parsed.port}" if parsed.port else ""
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "channel_binding"
    ]
    if not any(key.lower() == "sslmode" for key, _ in query):
        query.append(("sslmode", "require"))
    dsn = urllib.parse.urlunsplit(
        (
            parsed.scheme,
            f"{username}:{password}@{temporary_host}{port}",
            parsed.path or "/neondb",
            urllib.parse.urlencode(query),
            "",
        )
    )
    identity = (parsed.username, parsed.password, parsed.path, temporary_host)
    return identity, dsn


def _connect_runtime() -> psycopg.Connection[tuple]:
    if not re.fullmatch(r"ep-[a-z0-9-]+", TEMP_ENDPOINT_ID):
        raise RuntimeError("AUTOPILOT_UV_P1_TEMP_ENDPOINT_INVALID")
    if TEMP_BRANCH_ID == PRODUCTION_BRANCH_ID:
        raise RuntimeError("AUTOPILOT_UV_P1_BRANCH_ISOLATION_FAILED")

    tested: set[tuple[str, str, str, str]] = set()
    observations: Counter[str] = Counter()
    bounded_failure = "AUTOPILOT_UV_P1_RUNTIME_DSN_UNAVAILABLE"

    for raw_dsn in _discover_candidates()[:64]:
        derived = _temporary_dsn(raw_dsn)
        if derived is None:
            observations["candidate_rejected"] += 1
            continue
        identity, dsn = derived
        if identity in tested:
            continue
        tested.add(identity)
        try:
            connection = psycopg.connect(
                dsn,
                connect_timeout=5,
                application_name="autopilot-uv-p1-bounded-registration-v3",
                autocommit=True,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_user,
                           current_database(),
                           to_regprocedure('autopilot.register_approved_uv_p1_ci(text)') IS NOT NULL,
                           CASE
                               WHEN to_regprocedure('autopilot.register_approved_uv_p1_ci(text)') IS NULL THEN false
                               ELSE has_function_privilege(
                                   current_user,
                                   'autopilot.register_approved_uv_p1_ci(text)',
                                   'EXECUTE'
                               )
                           END
                    """
                )
                row = cursor.fetchone()
            observations["connected"] += 1
            if row is None or row[0] != "autopilot_runtime_login":
                observations["wrong_role"] += 1
                connection.close()
                continue
            if row[1] != "neondb":
                observations["wrong_database"] += 1
                connection.close()
                continue
            if not row[2]:
                observations["function_missing"] += 1
                bounded_failure = "AUTOPILOT_UV_P1_0309_FUNCTION_MISSING"
                connection.close()
                continue
            if not row[3]:
                observations["execute_denied"] += 1
                bounded_failure = "AUTOPILOT_UV_P1_0309_EXECUTE_DENIED"
                connection.close()
                continue
            print("AUTOPILOT_UV_P1_SCHEMA_PREFLIGHT=PASS", flush=True)
            print("AUTOPILOT_UV_P1_CALLER=autopilot_runtime_login", flush=True)
            print("AUTOPILOT_UV_P1_0309_FUNCTION=AVAILABLE", flush=True)
            print("MIGRATION_APPLIED_BY_THIS_RUN=NO", flush=True)
            print("TEMPORARY_NEON_ENDPOINT_ONLY=YES", flush=True)
            print("PRODUCTION_DATABASE_CONTACTED=NO", flush=True)
            return connection
        except psycopg.OperationalError:
            observations["connect_failed"] += 1
        except psycopg.Error:
            observations["preflight_failed"] += 1

    print(f"AUTOPILOT_UV_P1_REGISTRATION={bounded_failure}", flush=True)
    print(f"AUTOPILOT_UV_P1_CREDENTIALS_TESTED={len(tested)}", flush=True)
    for name in sorted(observations):
        print(f"AUTOPILOT_UV_P1_OBSERVATION_{name.upper()}={observations[name]}", flush=True)
    print("TEMPORARY_NEON_ENDPOINT_ONLY=YES", flush=True)
    print("PRODUCTION_DATABASE_CONTACTED=NO", flush=True)
    print("PRODUCTION_MUTATION=NO", flush=True)
    raise RuntimeError(bounded_failure)


def _register(connection: psycopg.Connection[tuple], task_key: str) -> tuple[str, str, bool]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT task_id,status,created FROM autopilot.register_approved_uv_p1_ci(%s)",
            (task_key,),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("AUTOPILOT_UV_P1_EMPTY_INGRESS_RESULT")
    return str(row[0]), str(row[1]), bool(row[2])


def main() -> int:
    deadline = time.monotonic() + 1_800
    receipts: list[dict[str, object]] = []
    connection = _connect_runtime()
    try:
        for label, task_key, pr_number, expected_head in APPROVED:
            task_id: str | None = None
            created = False
            registration_wait_logged = False
            while time.monotonic() < deadline:
                try:
                    task_id, status, created = _register(connection, task_key)
                    break
                except psycopg.Error as error:
                    primary = getattr(error.diag, "message_primary", "") or ""
                    if primary != "AUTOPILOT_INGRESS_ACTIVE_LIMIT":
                        print(f"AUTOPILOT_UV_P1_{label}_REGISTRATION=FAILED_CLOSED", flush=True)
                        print(f"AUTOPILOT_UV_P1_{label}_SQLSTATE={error.sqlstate or 'UNKNOWN'}", flush=True)
                        return 21
                    if not registration_wait_logged:
                        print(f"AUTOPILOT_UV_P1_{label}_REGISTRATION=WAITING_EXTERNAL", flush=True)
                        registration_wait_logged = True
                    time.sleep(5)
            else:
                print(f"AUTOPILOT_UV_P1_{label}_REGISTRATION=WAIT_TIMEOUT", flush=True)
                return 22

            assert task_id is not None
            print(f"UV_P1_{label}_TASK_ID={task_id}", flush=True)
            print(f"UV_P1_{label}_PR={pr_number}", flush=True)
            print(f"UV_P1_{label}_EXPECTED_HEAD={expected_head}", flush=True)
            print(f"UV_P1_{label}_REGISTER_CREATED={str(created).lower()}", flush=True)
            print(f"UV_P1_{label}_REGISTER_STATUS={status}", flush=True)

            stable_task_id = task_id
            last_status: str | None = None
            while time.monotonic() < deadline:
                try:
                    observed_task_id, status, _ = _register(connection, task_key)
                except psycopg.Error as error:
                    print(f"AUTOPILOT_UV_P1_{label}_OBSERVATION=FAILED_CLOSED", flush=True)
                    print(f"AUTOPILOT_UV_P1_{label}_OBSERVATION_SQLSTATE={error.sqlstate or 'UNKNOWN'}", flush=True)
                    return 23
                if observed_task_id != stable_task_id:
                    print(f"AUTOPILOT_UV_P1_{label}_TASK_ID_STABILITY=FAIL", flush=True)
                    return 24
                if status != last_status:
                    print(f"UV_P1_{label}_STATE={status}", flush=True)
                    last_status = status
                if status in TERMINAL:
                    break
                time.sleep(5)
            else:
                print(f"AUTOPILOT_UV_P1_{label}_OBSERVATION=WAITING_EXTERNAL_TIMEOUT", flush=True)
                return 25

            receipts.append(
                {
                    "lane": label,
                    "task_id": stable_task_id,
                    "pr_number": pr_number,
                    "expected_head_sha": expected_head,
                    "terminal_status": status,
                    "created_by_this_run": created,
                }
            )
            print(f"UV_P1_{label}_TERMINAL_STATUS={status}", flush=True)

        receipt = {
            "gate": "AUTOPILOT_UV_P1_DURABLE_REGISTRATION",
            "result": "PASS",
            "project_id": PROJECT_ID,
            "temporary_branch_id": TEMP_BRANCH_ID,
            "temporary_endpoint_id": TEMP_ENDPOINT_ID,
            "task_count": len(receipts),
            "tasks": receipts,
            "migration_applied_by_this_run": False,
            "real_video_execution": False,
            "drive_write": False,
            "oracle_lifecycle_action": False,
            "production_database_contacted": False,
            "production_mutation": False,
        }
        print("UV_P1_REGISTRATION_RECEIPT=" + json.dumps(receipt, sort_keys=True), flush=True)
        print("AUTOPILOT_DURABLE_TASK_COUNT=3", flush=True)
        print("REAL_VIDEO_EXECUTION=NO", flush=True)
        print("DRIVE_WRITE=NO", flush=True)
        print("ORACLE_LIFECYCLE_ACTION=NO", flush=True)
        print("PRODUCTION_MUTATION=NO", flush=True)
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"AUTOPILOT_UV_P1_RUNTIME_ERROR={error}", flush=True)
        raise SystemExit(20)
