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
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .contract import (
    AutopilotContractError,
    AutopilotRetryableError,
    ClaimedTask,
    build_draft_repair_broker_payload,
    claimed_task_from_row,
    validate_task_contract,
)


CHANNEL = "autopilot_ready"
RUNTIME_MODE = "SHADOW"
LOGGER = logging.getLogger("oracle_autopilot")
GITHUB_API_HOST = "api.github.com"
GITHUB_REPOSITORY = "olegmed1-art/bridge-video-free"
GITHUB_RESPONSE_LIMIT_BYTES = 1_048_576
GITHUB_CHECK_RUN_LIMIT = 100
GITHUB_FAILED_CHECK_LIMIT = 5
GITHUB_DIAGNOSTIC_TEXT_LIMIT = 300
GITHUB_CHECK_NAME_LIMIT = 200
GITHUB_PATH_LIMIT = 200
GITHUB_CHECK_STATUSES = frozenset(
    {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
)
GITHUB_CHECK_CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)
GITHUB_HARD_FAILURES = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "stale",
        "startup_failure",
        "timed_out",
    }
)
TOKEN_BROKER_RESPONSE_LIMIT_BYTES = 32_768
TOKEN_BROKER_REQUEST_LIMIT_BYTES = 65_536
TOKEN_BROKER_PATH = "/v1/github/draft-repair"
TOKEN_BROKER_HOST_PATTERN = re.compile(
    r"bridge-school-autopilot-[a-z0-9]+-olegmed1-4368s-projects\.vercel\.app"
)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Reject every redirect before urllib can contact the new origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise AutopilotContractError("GITHUB_API_REDIRECT_REJECTED")


class _RejectBrokerRedirects(urllib.request.HTTPRedirectHandler):
    """Reject every broker redirect before credentials can change origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise AutopilotContractError("TOKEN_BROKER_REDIRECT_REJECTED")


@dataclass(frozen=True)
class TokenBrokerConfig:
    url: str
    host: str
    secret: str
    vercel_bypass_secret: str


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


def load_token_broker_config() -> TokenBrokerConfig:
    raw_url = _require_env("AUTOPILOT_TOKEN_BROKER_URL")
    parsed = urllib.parse.urlsplit(raw_url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or TOKEN_BROKER_HOST_PATTERN.fullmatch(host) is None
        or parsed.path != TOKEN_BROKER_PATH
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or raw_url != f"https://{host}{TOKEN_BROKER_PATH}"
    ):
        raise AutopilotContractError("TOKEN_BROKER_ORIGIN_INVALID")

    secret = _require_env("AUTOPILOT_TOKEN_BROKER_SECRET")
    bypass = _require_env("AUTOPILOT_VERCEL_BYPASS_SECRET")
    if not 43 <= len(secret) <= 512 or any(
        character in secret for character in "\r\n"
    ):
        raise AutopilotContractError("TOKEN_BROKER_SECRET_INVALID")
    if not 32 <= len(bypass) <= 512 or any(
        character in bypass for character in "\r\n"
    ):
        raise AutopilotContractError("VERCEL_BYPASS_SECRET_INVALID")
    return TokenBrokerConfig(
        url=raw_url,
        host=host,
        secret=secret,
        vercel_bypass_secret=bypass,
    )


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


def _validate_github_api_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != GITHUB_API_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
    ):
        raise AutopilotContractError("GITHUB_API_ORIGIN_INVALID")


def _github_get_json(url: str, *, not_found_code: str) -> Any:
    """Perform one credential-free, no-redirect, size-bounded GitHub GET."""

    _validate_github_api_url(url)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "bridge-school-autopilot-shadow/1.3",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        opener = urllib.request.build_opener(_RejectRedirects())
        with opener.open(request, timeout=15) as response:
            final_url = response.geturl()
            _validate_github_api_url(final_url)
            if final_url != url or response.status != 200:
                raise AutopilotContractError("GITHUB_API_RESPONSE_INVALID")
            raw = response.read(GITHUB_RESPONSE_LIMIT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise AutopilotContractError(not_found_code) from exc
        if exc.code in {403, 408, 429} or 500 <= exc.code <= 599:
            raise AutopilotRetryableError("GITHUB_API_TRANSIENT_ERROR") from exc
        raise AutopilotContractError("GITHUB_API_HTTP_ERROR") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AutopilotRetryableError("GITHUB_API_TRANSIENT_ERROR") from exc

    if len(raw) > GITHUB_RESPONSE_LIMIT_BYTES:
        raise AutopilotContractError("GITHUB_API_RESPONSE_TOO_LARGE")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutopilotContractError("GITHUB_API_JSON_INVALID") from exc


def _diagnostic_excerpt(value: Any, *, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if not isinstance(value, str):
        raise AutopilotContractError("GITHUB_CI_DIAGNOSTIC_INVALID")
    normalized = " ".join(value.split())
    if not normalized:
        return None, False
    return normalized[:limit], len(normalized) > limit


def fetch_github_pr_snapshot(goal_json: dict[str, Any]) -> dict[str, Any]:
    """Fetch one bounded public PR snapshot without credentials or mutation."""

    repository = goal_json["repository"]
    pr_number = goal_json["pr_number"]
    expected_head = goal_json["expected_head_sha"]
    if repository != GITHUB_REPOSITORY:
        raise AutopilotContractError("GITHUB_REPOSITORY_INVALID")
    url = f"https://{GITHUB_API_HOST}/repos/{repository}/pulls/{pr_number}"
    payload = _github_get_json(url, not_found_code="GITHUB_PR_NOT_FOUND")
    if not isinstance(payload, dict):
        raise AutopilotContractError("GITHUB_API_JSON_INVALID")

    head = payload.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    expected_html_url = f"https://github.com/{repository}/pull/{pr_number}"
    if payload.get("number") != pr_number or payload.get("html_url") != expected_html_url:
        raise AutopilotContractError("GITHUB_PR_IDENTITY_MISMATCH")
    if head_sha != expected_head:
        raise AutopilotContractError("GITHUB_PR_HEAD_CHANGED")
    if payload.get("state") != "open":
        raise AutopilotContractError("GITHUB_PR_NOT_OPEN")
    if goal_json["require_draft"] and payload.get("draft") is not True:
        raise AutopilotContractError("GITHUB_PR_NOT_DRAFT")
    mergeable = payload.get("mergeable")
    if mergeable is not None and not isinstance(mergeable, bool):
        raise AutopilotContractError("GITHUB_PR_MERGEABLE_INVALID")
    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, str) or len(updated_at) > 40:
        raise AutopilotContractError("GITHUB_PR_TIMESTAMP_INVALID")

    return {
        "repository": repository,
        "pr_number": pr_number,
        "state": "open",
        "draft": True,
        "head_sha": head_sha,
        "mergeable": mergeable,
        "updated_at": updated_at,
        "api_host": GITHUB_API_HOST,
        "http_method": "GET",
        "production_mutation": False,
        "model_calls": 0,
        "cost_actual_microusd": 0,
    }


def fetch_github_ci_snapshot(goal_json: dict[str, Any]) -> dict[str, Any]:
    """Inspect exact-head public GitHub checks without credentials or mutation."""

    repository = goal_json["repository"]
    pr_number = goal_json["pr_number"]
    expected_head = goal_json["expected_head_sha"]
    before = fetch_github_pr_snapshot(goal_json)

    checks_url = (
        f"https://{GITHUB_API_HOST}/repos/{repository}/commits/{expected_head}"
        f"/check-runs?filter=latest&per_page={GITHUB_CHECK_RUN_LIMIT}"
    )
    payload = _github_get_json(checks_url, not_found_code="GITHUB_CI_HEAD_NOT_FOUND")
    if not isinstance(payload, dict):
        raise AutopilotContractError("GITHUB_CI_RESPONSE_INVALID")
    total_count = payload.get("total_count")
    check_runs = payload.get("check_runs")
    if (
        isinstance(total_count, bool)
        or not isinstance(total_count, int)
        or total_count < 0
        or total_count > GITHUB_CHECK_RUN_LIMIT
        or not isinstance(check_runs, list)
        or len(check_runs) != total_count
    ):
        raise AutopilotContractError("GITHUB_CI_CHECK_SET_INCOMPLETE")

    parsed_checks: list[dict[str, Any]] = []
    conclusion_counts = {key: 0 for key in sorted(GITHUB_CHECK_CONCLUSIONS)}
    conclusion_counts["none"] = 0
    completed_count = 0
    pending_count = 0

    for raw_check in check_runs:
        if not isinstance(raw_check, dict):
            raise AutopilotContractError("GITHUB_CI_CHECK_INVALID")
        check_id = raw_check.get("id")
        name = raw_check.get("name")
        status = raw_check.get("status")
        conclusion = raw_check.get("conclusion")
        app = raw_check.get("app")
        output = raw_check.get("output")
        if (
            isinstance(check_id, bool)
            or not isinstance(check_id, int)
            or check_id < 1
            or not isinstance(name, str)
            or not 1 <= len(name) <= GITHUB_CHECK_NAME_LIMIT
            or raw_check.get("head_sha") != expected_head
            or status not in GITHUB_CHECK_STATUSES
            or (conclusion is not None and conclusion not in GITHUB_CHECK_CONCLUSIONS)
            or not isinstance(app, dict)
            or not isinstance(app.get("slug"), str)
            or not 1 <= len(app["slug"]) <= 100
            or not isinstance(output, dict)
        ):
            raise AutopilotContractError("GITHUB_CI_CHECK_INVALID")
        title, title_truncated = _diagnostic_excerpt(
            output.get("title"), limit=120
        )
        output_excerpt, output_truncated = _diagnostic_excerpt(
            output.get("summary"), limit=GITHUB_DIAGNOSTIC_TEXT_LIMIT
        )
        if status == "completed" and conclusion is not None:
            completed_count += 1
        else:
            pending_count += 1
        conclusion_counts[conclusion or "none"] += 1
        parsed_checks.append(
            {
                "app_slug": app["slug"],
                "check_run_id": check_id,
                "conclusion": conclusion,
                "name": name,
                "output_title": title,
                "output_summary_excerpt": output_excerpt,
                "output_truncated": title_truncated or output_truncated,
            }
        )

    parsed_checks.sort(key=lambda item: (item["name"], item["check_run_id"]))
    hard_failures = [
        item for item in parsed_checks if item["conclusion"] in GITHUB_HARD_FAILURES
    ]
    selected_failures = hard_failures[:GITHUB_FAILED_CHECK_LIMIT]
    failed_checks: list[dict[str, Any]] = []
    for item in selected_failures:
        annotation_url = (
            f"https://{GITHUB_API_HOST}/repos/{repository}/check-runs/"
            f"{item['check_run_id']}/annotations?per_page=2"
        )
        annotations = _github_get_json(
            annotation_url, not_found_code="GITHUB_CI_ANNOTATIONS_NOT_FOUND"
        )
        if not isinstance(annotations, list) or len(annotations) > 2:
            raise AutopilotContractError("GITHUB_CI_ANNOTATIONS_INVALID")
        annotation_level: str | None = None
        annotation_path: str | None = None
        annotation_excerpt: str | None = None
        if annotations:
            annotation = annotations[0]
            if not isinstance(annotation, dict):
                raise AutopilotContractError("GITHUB_CI_ANNOTATIONS_INVALID")
            annotation_level = annotation.get("annotation_level")
            annotation_path = annotation.get("path")
            if (
                annotation_level not in {"notice", "warning", "failure"}
                or not isinstance(annotation_path, str)
                or not 1 <= len(annotation_path) <= GITHUB_PATH_LIMIT
            ):
                raise AutopilotContractError("GITHUB_CI_ANNOTATIONS_INVALID")
            annotation_excerpt, _ = _diagnostic_excerpt(
                annotation.get("message"), limit=GITHUB_DIAGNOSTIC_TEXT_LIMIT
            )
            if annotation_excerpt is None:
                raise AutopilotContractError("GITHUB_CI_ANNOTATIONS_INVALID")
        failed_checks.append(
            {
                **item,
                "annotation_level": annotation_level,
                "annotation_path": annotation_path,
                "annotation_message_excerpt": annotation_excerpt,
                "annotations_truncated": len(annotations) > 1,
            }
        )

    after = fetch_github_pr_snapshot(goal_json)
    if before["head_sha"] != after["head_sha"]:
        raise AutopilotContractError("GITHUB_PR_HEAD_CHANGED")

    failure_codes = sorted(
        {f"CI_{item['conclusion'].upper()}" for item in hard_failures}
    )
    if hard_failures:
        overall_state = "FAIL"
    elif pending_count:
        overall_state = "PENDING"
    elif total_count:
        overall_state = "PASS"
    else:
        overall_state = "NO_CHECKS"

    return {
        "repository": repository,
        "pr_number": pr_number,
        "state": "open",
        "draft": True,
        "head_sha": expected_head,
        "updated_at": after["updated_at"],
        "check_total": total_count,
        "completed_count": completed_count,
        "pending_count": pending_count,
        "conclusion_counts": conclusion_counts,
        "overall_state": overall_state,
        "failure_codes": failure_codes,
        "failed_checks": failed_checks,
        "failed_checks_truncated": len(hard_failures) > GITHUB_FAILED_CHECK_LIMIT,
        "api_host": GITHUB_API_HOST,
        "http_method": "GET",
        "production_mutation": False,
        "model_calls": 0,
        "cost_actual_microusd": 0,
    }


def _validate_token_broker_result(
    payload: Any,
    *,
    request_payload: dict[str, Any],
    broker_host: str,
) -> dict[str, Any]:
    expected_keys = {
        "action_fingerprint",
        "base_sha",
        "branch_name",
        "commit_sha",
        "draft",
        "manifest_version",
        "merge_allowed",
        "operation_count",
        "production_mutation",
        "pull_request_number",
        "pull_request_url",
        "replayed",
        "repository",
        "status",
        "task_key",
        "token_exposed",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise AutopilotContractError("TOKEN_BROKER_RESPONSE_INVALID")
    pull_number = payload.get("pull_request_number")
    commit_sha = payload.get("commit_sha")
    replayed = payload.get("replayed")
    status_value = payload.get("status")
    expected_operation_count = 8 + 2 * len(request_payload["changes"])
    if (
        status_value not in {"created", "existing"}
        or type(replayed) is not bool
        or (status_value == "existing" and replayed is not True)
        or payload.get("repository") != request_payload["repository"]
        or payload.get("task_key") != request_payload["task_key"]
        or payload.get("action_fingerprint") != request_payload["action_fingerprint"]
        or payload.get("manifest_version") != 1
        or payload.get("base_sha") != request_payload["expected_base_sha"]
        or payload.get("branch_name") != request_payload["branch_name"]
        or not isinstance(commit_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None
        or type(pull_number) is not int
        or not 1 <= pull_number <= 1_000_000
        or payload.get("pull_request_url")
        != f"https://github.com/{request_payload['repository']}/pull/{pull_number}"
        or payload.get("draft") is not True
        or payload.get("token_exposed") is not False
        or payload.get("merge_allowed") is not False
        or payload.get("production_mutation") is not False
        or payload.get("operation_count") != expected_operation_count
    ):
        raise AutopilotContractError("TOKEN_BROKER_RESPONSE_INVALID")

    result = dict(payload)
    result.update(
        {
            "broker_host": broker_host,
            "http_method": "POST",
            "model_calls": 0,
            "cost_actual_microusd": 0,
        }
    )
    return result


def execute_bounded_draft_repair(goal_json: dict[str, Any]) -> dict[str, Any]:
    """Call only the pinned Preview broker and retain token-free evidence."""

    request_payload = build_draft_repair_broker_payload(goal_json)
    config = load_token_broker_config()
    encoded = json.dumps(
        request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > TOKEN_BROKER_REQUEST_LIMIT_BYTES:
        raise AutopilotContractError("TOKEN_BROKER_REQUEST_TOO_LARGE")
    request = urllib.request.Request(
        config.url,
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.secret}",
            "Content-Type": "application/json",
            "User-Agent": "bridge-school-autopilot-oracle/1.4",
            "X-Vercel-Protection-Bypass": config.vercel_bypass_secret,
        },
    )
    try:
        opener = urllib.request.build_opener(_RejectBrokerRedirects())
        with opener.open(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != config.url:
                raise AutopilotContractError("TOKEN_BROKER_RESPONSE_INVALID")
            raw = response.read(TOKEN_BROKER_RESPONSE_LIMIT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {408, 425, 429} or 500 <= exc.code <= 599:
            raise AutopilotRetryableError("TOKEN_BROKER_TRANSIENT_ERROR") from exc
        if exc.code == 409:
            raise AutopilotContractError("TOKEN_BROKER_PRECONDITION_FAILED") from exc
        raise AutopilotContractError("TOKEN_BROKER_HTTP_ERROR") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AutopilotRetryableError("TOKEN_BROKER_TRANSIENT_ERROR") from exc
    if len(raw) > TOKEN_BROKER_RESPONSE_LIMIT_BYTES:
        raise AutopilotContractError("TOKEN_BROKER_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutopilotContractError("TOKEN_BROKER_RESPONSE_INVALID") from exc
    return _validate_token_broker_result(
        payload, request_payload=request_payload, broker_host=config.host
    )


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

    if task.goal_type == "GITHUB_PR_READ_ONLY_V1":
        summary = fetch_github_pr_snapshot(task.goal_json)
        summary["task_id"] = task.task_id
        summary["task_kind"] = task.goal_type
        summary["runtime"] = "ORACLE_RESIDENT"
        _complete(
            config,
            task,
            evidence_class="GITHUB_PR_READ_ONLY_SNAPSHOT",
            summary=summary,
        )
        return

    if task.goal_type == "GITHUB_CI_READ_ONLY_V1":
        summary = fetch_github_ci_snapshot(task.goal_json)
        summary["task_id"] = task.task_id
        summary["task_kind"] = task.goal_type
        summary["runtime"] = "ORACLE_RESIDENT"
        _complete(
            config,
            task,
            evidence_class="GITHUB_CI_READ_ONLY_SNAPSHOT",
            summary=summary,
        )
        return

    if task.goal_type == "GITHUB_DRAFT_REPAIR_V1":
        summary = execute_bounded_draft_repair(task.goal_json)
        summary["task_id"] = task.task_id
        summary["task_kind"] = task.goal_type
        summary["runtime"] = "ORACLE_RESIDENT"
        _complete(
            config,
            task,
            evidence_class="GITHUB_DRAFT_REPAIR_EVIDENCE",
            summary=summary,
        )
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
    except AutopilotRetryableError as exc:
        resulting_state = fail_task(config, task, error_code=str(exc), retryable=True)
        LOGGER.warning(
            "task_transient_failure task_id=%s code=%s state=%s",
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
