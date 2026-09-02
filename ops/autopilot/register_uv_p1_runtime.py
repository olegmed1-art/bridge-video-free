#!/usr/bin/env python3
"""Register the three director-approved UV P1 CI snapshots through migration 0314.

This program is intentionally narrow:
- it rewrites discovered Neon DSNs to one exact temporary endpoint;
- it accepts only the immutable task keys embedded in migration 0314;
- it resolves the relevant public PR immediately before every registration;
- it observes status only through the runtime-readable task_status view;
- it never selects Autopilot tables directly and never contacts production.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Final

import psycopg

PROJECT_ID: Final[str] = os.environ["PROJECT_ID"]
TEMP_BRANCH_ID: Final[str] = os.environ["TEMP_BRANCH_ID"]
PRODUCTION_BRANCH_ID: Final[str] = os.environ["PRODUCTION_BRANCH_ID"]
TEMP_ENDPOINT_ID: Final[str] = os.environ["TEMP_ENDPOINT_ID"]
GITHUB_REPOSITORY: Final[str] = "olegmed1-art/bridge-video-free"
MIGRATION_APPLIED_BY_THIS_RUN: Final[bool] = (
    os.environ.get("MIGRATION_APPLIED_BY_THIS_RUN") == "YES"
)
LIVE_HEADS: Final[dict[int, str]] = {
    997: os.environ["LIVE_RUNTIME_HEAD_SHA"],
    1062: os.environ["LIVE_CANARY_HEAD_SHA"],
    1061: os.environ["LIVE_IDLE_HEAD_SHA"],
}

APPROVED: Final[tuple[tuple[str, str, int, str], ...]] = (
    (
        "RUNTIME",
        "uv-p1-runtime-pr997-c1515c5af4a4-20260902",
        997,
        "c1515c5af4a47c7468d7c4769e91082f7afd163c",
    ),
    (
        "CANARY",
        "uv-p1-canary-pr1062-79aec3f732fd-20260902",
        1062,
        "79aec3f732fdcd8ca9f5f8a4a6ba5a88f4bba8d4",
    ),
    (
        "IDLE",
        "uv-p1-idle-pr1061-8ab8d74c2a0f-20260902",
        1061,
        "8ab8d74c2a0ffd281ae4ccea9e5c8e55eea2ab45",
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
UUID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
REGISTRATION_RETRY_SECONDS: Final[int] = 60


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
                           current_setting('neon.project_id', true),
                           current_setting('neon.branch_id', true),
                           to_regprocedure('autopilot.register_approved_uv_p1_ci(text)') IS NOT NULL,
                           CASE
                               WHEN to_regprocedure('autopilot.register_approved_uv_p1_ci(text)') IS NULL THEN false
                               ELSE has_function_privilege(
                                   current_user,
                                   'autopilot.register_approved_uv_p1_ci(text)',
                                   'EXECUTE'
                               )
                           END,
                           to_regclass('autopilot.task_status') IS NOT NULL,
                           CASE
                               WHEN to_regclass('autopilot.task_status') IS NULL THEN false
                               ELSE has_table_privilege(
                                   current_user,
                                   'autopilot.task_status',
                                   'SELECT'
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
            if row[2] != PROJECT_ID:
                observations["wrong_project"] += 1
                bounded_failure = "AUTOPILOT_UV_P1_PROJECT_IDENTITY_INVALID"
                connection.close()
                continue
            if row[3] != TEMP_BRANCH_ID:
                observations["wrong_branch"] += 1
                bounded_failure = "AUTOPILOT_UV_P1_BRANCH_IDENTITY_INVALID"
                connection.close()
                continue
            if not row[4]:
                observations["function_missing"] += 1
                bounded_failure = "AUTOPILOT_UV_P1_0314_FUNCTION_MISSING"
                connection.close()
                continue
            if not row[5]:
                observations["execute_denied"] += 1
                bounded_failure = "AUTOPILOT_UV_P1_0314_EXECUTE_DENIED"
                connection.close()
                continue
            if not row[6]:
                observations["status_view_missing"] += 1
                bounded_failure = "AUTOPILOT_UV_P1_STATUS_VIEW_MISSING"
                connection.close()
                continue
            if not row[7]:
                observations["status_view_select_denied"] += 1
                bounded_failure = "AUTOPILOT_UV_P1_STATUS_VIEW_SELECT_DENIED"
                connection.close()
                continue
            print("AUTOPILOT_UV_P1_SCHEMA_PREFLIGHT=PASS", flush=True)
            print("AUTOPILOT_UV_P1_CALLER=autopilot_runtime_login", flush=True)
            print("AUTOPILOT_UV_P1_0314_FUNCTION=AVAILABLE", flush=True)
            print("AUTOPILOT_UV_P1_STATUS_VIEW=AVAILABLE", flush=True)
            print(
                "MIGRATION_APPLIED_BY_THIS_RUN="
                + ("YES" if MIGRATION_APPLIED_BY_THIS_RUN else "NO"),
                flush=True,
            )
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
    task_id = str(row[0])
    if not UUID_PATTERN.fullmatch(task_id):
        raise RuntimeError("AUTOPILOT_UV_P1_MALFORMED_TASK_ID")
    return task_id, str(row[1]), bool(row[2])


def _observe(connection: psycopg.Connection[tuple], task_key: str) -> tuple[str, str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT task_id,status FROM autopilot.task_status WHERE task_key=%s",
            (task_key,),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("AUTOPILOT_UV_P1_TASK_MISSING_FROM_STATUS_VIEW")
    task_id = str(row[0])
    if not UUID_PATTERN.fullmatch(task_id):
        raise RuntimeError("AUTOPILOT_UV_P1_MALFORMED_OBSERVED_TASK_ID")
    return task_id, str(row[1])


def _verify_initial_live_heads() -> None:
    """Reject stale runner-resolved heads before any database connection."""
    for label, _task_key, pr_number, expected_head in APPROVED:
        live_head = LIVE_HEADS.get(pr_number, "")
        if not re.fullmatch(r"[0-9a-f]{40}", live_head):
            raise RuntimeError(f"AUTOPILOT_UV_P1_{label}_LIVE_HEAD_INVALID")
        if live_head != expected_head:
            print(f"UV_P1_{label}_EXPECTED_HEAD={expected_head}", flush=True)
            print(f"UV_P1_{label}_LIVE_HEAD={live_head}", flush=True)
            raise RuntimeError(f"AUTOPILOT_UV_P1_{label}_APPROVAL_STALE")
        print(f"UV_P1_{label}_RUNNER_HEAD_VERIFIED={live_head}", flush=True)


def _resolve_current_pr_head(label: str, pr_number: int) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/pulls/{pr_number}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "bridge-school-autopilot-uv-p1-registration-v3",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise RuntimeError(
            f"AUTOPILOT_UV_P1_{label}_LIVE_HEAD_RESOLUTION_FAILED"
        ) from error

    if payload.get("state") != "open" or payload.get("draft") is not True:
        raise RuntimeError(f"AUTOPILOT_UV_P1_{label}_PR_NOT_OPEN_DRAFT")
    head = payload.get("head", {}).get("sha")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError(f"AUTOPILOT_UV_P1_{label}_LIVE_HEAD_INVALID")
    return head


def _verify_current_live_head(label: str, pr_number: int, expected_head: str) -> None:
    """Resolve one PR immediately before mutation or receipt publication."""
    live_head = _resolve_current_pr_head(label, pr_number)
    if live_head != expected_head:
        print(f"UV_P1_{label}_EXPECTED_HEAD={expected_head}", flush=True)
        print(f"UV_P1_{label}_LIVE_HEAD={live_head}", flush=True)
        raise RuntimeError(f"AUTOPILOT_UV_P1_{label}_APPROVAL_STALE")
    print(f"UV_P1_{label}_LIVE_HEAD_VERIFIED={live_head}", flush=True)


def _verify_all_current_live_heads() -> None:
    """Reconcile every approved PR immediately before aggregate PASS."""
    for label, _task_key, pr_number, expected_head in APPROVED:
        _verify_current_live_head(label, pr_number, expected_head)
    print("AUTOPILOT_UV_P1_FINAL_ALL_HEADS_VERIFIED=PASS", flush=True)


def main() -> int:
    deadline = time.monotonic() + 1_800
    receipts: list[dict[str, object]] = []
    _verify_initial_live_heads()
    connection = _connect_runtime()
    try:
        for label, task_key, pr_number, expected_head in APPROVED:
            task_id: str | None = None
            created = False
            registration_wait_logged = False
            while time.monotonic() < deadline:
                _verify_current_live_head(label, pr_number, expected_head)
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
                    time.sleep(REGISTRATION_RETRY_SECONDS)
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
                    observed_task_id, status = _observe(connection, task_key)
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

            _verify_current_live_head(label, pr_number, expected_head)
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

        # Earlier lanes may have been terminal for most of the shared deadline
        # while a later lane completed. Never publish one aggregate PASS from
        # those cached receipts without re-reading every authoritative PR.
        _verify_all_current_live_heads()
        receipt = {
            "gate": "AUTOPILOT_UV_P1_DURABLE_REGISTRATION",
            "result": "PASS",
            "project_id": PROJECT_ID,
            "temporary_branch_id": TEMP_BRANCH_ID,
            "temporary_endpoint_id": TEMP_ENDPOINT_ID,
            "task_count": len(receipts),
            "tasks": receipts,
            "migration_applied_by_this_run": MIGRATION_APPLIED_BY_THIS_RUN,
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
