"""Fail-closed parser and database ingress for ChatGPT role callbacks.

GitHub Actions supplies the authenticated event envelope.  This module still
pins the repository, mailbox, actor, and GitHub App before invoking the single
narrow database RPC.  It never accepts a caller-provided ``signature_ok`` bit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg


REPOSITORY = "olegmed1-art/bridge-video-free"
REPOSITORY_ID = 1_330_085_090
MAILBOX_PR = 1150
ACTOR_LOGIN = "olegmed1-art"
ACTOR_ID = 315_099_490
APP_SLUG = "chatgpt-codex-connector"
APP_ID = 1_144_995
ROLE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
DISPATCH_NOT_SENT = "AUTOPILOT_CALLBACK_DISPATCH_NOT_SENT"
DISPATCH_NOT_SENT_RETRY_DELAYS_SECONDS = (1, 2, 4, 8)
RESULT_FIELDS = (
    "dispatch_id",
    "dispatch_epoch",
    "role",
    "task_fingerprint",
    "target_pr",
    "status",
    "result_code",
    "target_head_sha",
    "summary",
    "target_chat_id",
    "message_id",
    "run_id",
    "executor_id",
)
PROOF_FIELDS = (
    "dispatch_id",
    "dispatch_epoch",
    "role",
    "task_fingerprint",
    "target_pr",
    "target_chat_id",
    "target_chat_name",
    "message_id",
    "run_id",
    "executor_id",
    "ui_visible",
    "run_state",
)


class CallbackContractError(RuntimeError):
    """The event is not the one narrow provider-authenticated callback."""


@dataclass(frozen=True)
class RoleCallback:
    provider_event_id: str
    dispatch_id: str
    dispatch_epoch: int
    role: str
    task_fingerprint: str
    target_pr: int
    status: str
    result_code: str
    target_head_sha: str
    summary: str
    target_chat_id: str
    message_id: str
    run_id: str
    executor_id: str
    payload_fingerprint: str


@dataclass(frozen=True)
class DeliveryProof:
    provider_event_id: str
    dispatch_id: str
    dispatch_epoch: int
    role: str
    task_fingerprint: str
    target_pr: int
    target_chat_id: str
    target_chat_name: str
    message_id: str
    run_id: str
    executor_id: str
    payload_fingerprint: str


def validate_callback_dsn(raw: str) -> str:
    value = raw.strip()
    parsed = urllib.parse.urlsplit(value)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.username != "autopilot_callback_login"
        or not parsed.password
        or not (parsed.hostname or "").lower().endswith(".neon.tech")
        or "-pooler." in (parsed.hostname or "").lower()
        or parsed.path != "/neondb"
        or parsed.fragment
        or query.get("sslmode", [""])[0] not in {"require", "verify-full"}
        or query.get("channel_binding") != ["require"]
    ):
        raise CallbackContractError("CALLBACK_DSN_INVALID")
    return value


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CallbackContractError(code)
    return value


def _parse_ordered_body(
    body: str, marker: str, fields: tuple[str, ...]
) -> dict[str, str]:
    lines = body.splitlines()
    if len(lines) != len(fields) + 1 or lines[0] != marker:
        raise CallbackContractError("CALLBACK_BODY_INVALID")
    values: dict[str, str] = {}
    for line, expected_key in zip(lines[1:], fields, strict=True):
        key, separator, value = line.partition("=")
        if separator != "=" or key != expected_key or not value:
            raise CallbackContractError("CALLBACK_BODY_INVALID")
        values[key] = value
    return values


def _validate_common(values: dict[str, str]) -> None:
    if (
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            values["dispatch_id"],
        )
        is None
    ):
        raise CallbackContractError("CALLBACK_DISPATCH_ID_INVALID")
    if re.fullmatch(r"[1-9][0-9]{0,6}", values["dispatch_epoch"]) is None:
        raise CallbackContractError("CALLBACK_EPOCH_INVALID")
    if ROLE_PATTERN.fullmatch(values["role"]) is None:
        raise CallbackContractError("CALLBACK_ROLE_INVALID")
    if re.fullmatch(r"[0-9a-f]{64}", values["task_fingerprint"]) is None:
        raise CallbackContractError("CALLBACK_TASK_FINGERPRINT_INVALID")
    if re.fullmatch(r"[1-9][0-9]{0,6}", values["target_pr"]) is None:
        raise CallbackContractError("CALLBACK_TARGET_INVALID")
    for key in ("target_chat_id", "message_id", "run_id"):
        if (
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                values[key],
            )
            is None
        ):
            raise CallbackContractError("CALLBACK_UI_PROOF_INVALID")
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", values["executor_id"])
        is None
    ):
        raise CallbackContractError("CALLBACK_EXECUTOR_INVALID")


def parse_issue_comment_event(event: object) -> RoleCallback | DeliveryProof:
    root = _mapping(event, "CALLBACK_EVENT_INVALID")
    repository = _mapping(root.get("repository"), "CALLBACK_REPOSITORY_INVALID")
    issue = _mapping(root.get("issue"), "CALLBACK_MAILBOX_INVALID")
    comment = _mapping(root.get("comment"), "CALLBACK_COMMENT_INVALID")
    actor = _mapping(comment.get("user"), "CALLBACK_ACTOR_INVALID")
    app = _mapping(comment.get("performed_via_github_app"), "CALLBACK_APP_INVALID")
    if root.get("action") != "created":
        raise CallbackContractError("CALLBACK_ACTION_INVALID")
    if (
        repository.get("full_name") != REPOSITORY
        or repository.get("id") != REPOSITORY_ID
    ):
        raise CallbackContractError("CALLBACK_REPOSITORY_INVALID")
    pull = issue.get("pull_request")
    if (
        issue.get("number") != MAILBOX_PR
        or not isinstance(pull, dict)
        or pull.get("url")
        != f"https://api.github.com/repos/{REPOSITORY}/pulls/{MAILBOX_PR}"
        or comment.get("issue_url")
        != f"https://api.github.com/repos/{REPOSITORY}/issues/{MAILBOX_PR}"
    ):
        raise CallbackContractError("CALLBACK_MAILBOX_INVALID")
    if (
        actor.get("login") != ACTOR_LOGIN
        or actor.get("id") != ACTOR_ID
        or comment.get("author_association") != "OWNER"
    ):
        raise CallbackContractError("CALLBACK_ACTOR_INVALID")
    if app.get("slug") != APP_SLUG or app.get("id") != APP_ID:
        raise CallbackContractError("CALLBACK_APP_INVALID")

    comment_id = comment.get("id")
    body = comment.get("body")
    if (
        isinstance(comment_id, bool)
        or not isinstance(comment_id, int)
        or comment_id < 1
    ):
        raise CallbackContractError("CALLBACK_COMMENT_INVALID")
    if not isinstance(body, str) or len(body.encode("utf-8")) > 1_024:
        raise CallbackContractError("CALLBACK_BODY_INVALID")
    marker = body.splitlines()[0] if body.splitlines() else ""
    fields = PROOF_FIELDS if marker == "AUTOPILOT_DELIVERY_PROOF_V1" else RESULT_FIELDS
    values = _parse_ordered_body(body, marker, fields)
    if marker not in {"AUTOPILOT_DELIVERY_PROOF_V1", "AUTOPILOT_RESULT_V1"}:
        raise CallbackContractError("CALLBACK_BODY_INVALID")
    _validate_common(values)
    # The database RPC binds the role to the dispatch and requires it to be an
    # enabled role_registry entry. Keep only the public identifier-shape gate
    # here so newly registered school roles reach that authoritative check.
    if marker == "AUTOPILOT_DELIVERY_PROOF_V1":
        if (
            values["ui_visible"] != "true"
            or values["run_state"] != "RUNNING"
            or len(values["target_chat_name"]) not in range(1, 81)
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in values["target_chat_name"]
            )
        ):
            raise CallbackContractError("CALLBACK_UI_PROOF_INVALID")
        return DeliveryProof(
            provider_event_id=f"github-comment:{comment_id}",
            dispatch_id=values["dispatch_id"],
            dispatch_epoch=int(values["dispatch_epoch"]),
            role=values["role"],
            task_fingerprint=values["task_fingerprint"],
            target_pr=int(values["target_pr"]),
            target_chat_id=values["target_chat_id"],
            target_chat_name=values["target_chat_name"],
            message_id=values["message_id"],
            run_id=values["run_id"],
            executor_id=values["executor_id"],
            payload_fingerprint=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        )
    if values["status"] not in {"SUCCEEDED", "BLOCKED"}:
        raise CallbackContractError("CALLBACK_STATUS_INVALID")
    if re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", values["result_code"]) is None:
        raise CallbackContractError("CALLBACK_RESULT_CODE_INVALID")
    if re.fullmatch(r"[0-9a-f]{40}", values["target_head_sha"]) is None:
        raise CallbackContractError("CALLBACK_HEAD_INVALID")
    summary = values["summary"]
    if (
        len(summary) > 160
        or any(ord(character) < 32 or ord(character) == 127 for character in summary)
        or re.search(r"(?i)(?:https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,})", summary)
        or re.search(
            r"(?i)(?:password|secret|token|api[_ -]?key|credential|private[_ -]?key)",
            summary,
        )
        or re.search(r"(?i)(?:[0-9a-f]{32,}|[a-z0-9+/]{40,}={0,2})", summary)
    ):
        raise CallbackContractError("CALLBACK_SUMMARY_INVALID")

    return RoleCallback(
        provider_event_id=f"github-comment:{comment_id}",
        dispatch_id=values["dispatch_id"],
        dispatch_epoch=int(values["dispatch_epoch"]),
        role=values["role"],
        task_fingerprint=values["task_fingerprint"],
        target_pr=int(values["target_pr"]),
        status=values["status"],
        result_code=values["result_code"],
        target_head_sha=values["target_head_sha"],
        summary=summary,
        target_chat_id=values["target_chat_id"],
        message_id=values["message_id"],
        run_id=values["run_id"],
        executor_id=values["executor_id"],
        payload_fingerprint=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )


def ingest_callback(dsn: str, callback: RoleCallback) -> tuple[bool, str]:
    """Invoke only the task-bound callback RPC using parameterized SQL."""

    with (
        psycopg.connect(
            validate_callback_dsn(dsn),
            connect_timeout=10,
            application_name="school-autopilot-github-callback",
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT accepted, resulting_state
              FROM autopilot.accept_role_dispatch_terminal_v2(
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
              )
            """,
            (
                callback.provider_event_id,
                callback.payload_fingerprint,
                True,
                REPOSITORY,
                MAILBOX_PR,
                ACTOR_LOGIN,
                ACTOR_ID,
                "OWNER",
                APP_SLUG,
                APP_ID,
                json.dumps(
                    {
                        "dispatch_id": callback.dispatch_id,
                        "dispatch_epoch": callback.dispatch_epoch,
                        "role": callback.role,
                        "task_fingerprint": callback.task_fingerprint,
                        "target_pr": callback.target_pr,
                        "status": callback.status,
                        "result_code": callback.result_code,
                        "target_head_sha": callback.target_head_sha,
                        "summary": callback.summary,
                        "target_chat_id": callback.target_chat_id,
                        "message_id": callback.message_id,
                        "run_id": callback.run_id,
                        "executor_id": callback.executor_id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        row = cursor.fetchone()
        connection.commit()
    if row is None:
        raise CallbackContractError("CALLBACK_RPC_EMPTY")
    return bool(row[0]), str(row[1])


def ingest_delivery_proof(dsn: str, proof: DeliveryProof) -> tuple[bool, str]:
    with (
        psycopg.connect(
            validate_callback_dsn(dsn),
            connect_timeout=10,
            application_name="school-autopilot-github-delivery-proof",
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT accepted, resulting_state
              FROM autopilot.accept_role_dispatch_delivery_proof(
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
              )
            """,
            (
                proof.provider_event_id,
                proof.payload_fingerprint,
                True,
                REPOSITORY,
                MAILBOX_PR,
                ACTOR_LOGIN,
                ACTOR_ID,
                "OWNER",
                APP_SLUG,
                APP_ID,
                json.dumps(
                    {
                        "dispatch_id": proof.dispatch_id,
                        "dispatch_epoch": proof.dispatch_epoch,
                        "role": proof.role,
                        "task_fingerprint": proof.task_fingerprint,
                        "target_pr": proof.target_pr,
                        "target_chat_id": proof.target_chat_id,
                        "target_chat_name": proof.target_chat_name,
                        "message_id": proof.message_id,
                        "run_id": proof.run_id,
                        "executor_id": proof.executor_id,
                        "ui_visible": True,
                        "run_state": "RUNNING",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        row = cursor.fetchone()
        connection.commit()
    if row is None:
        raise CallbackContractError("CALLBACK_RPC_EMPTY")
    return bool(row[0]), str(row[1])


def ingest_callback_with_retry(
    dsn: str,
    callback: RoleCallback,
    *,
    sleeper: Any = time.sleep,
) -> tuple[bool, str]:
    """Retry only the bounded outbox publish/mark race.

    Every other database or contract failure remains immediate and fail-closed.
    Duplicate delivery after an uncertain response is safe because the RPC is
    idempotent on the provider event identifier and payload fingerprint.
    """

    for attempt, delay in enumerate((*DISPATCH_NOT_SENT_RETRY_DELAYS_SECONDS, None)):
        try:
            return ingest_callback(dsn, callback)
        except psycopg.Error as exc:
            if DISPATCH_NOT_SENT not in str(exc) or delay is None:
                raise
            sleeper(delay)
    raise AssertionError("unreachable")


def main() -> None:
    event_path = Path(os.environ["GITHUB_EVENT_PATH"])
    callback = parse_issue_comment_event(
        json.loads(event_path.read_text(encoding="utf-8"))
    )
    if isinstance(callback, DeliveryProof):
        accepted, state = ingest_delivery_proof(
            os.environ["AUTOPILOT_CALLBACK_DATABASE_URL"], callback
        )
    else:
        accepted, state = ingest_callback_with_retry(
            os.environ["AUTOPILOT_CALLBACK_DATABASE_URL"], callback
        )
    print(json.dumps({"accepted": accepted, "resulting_state": state}, sort_keys=True))


if __name__ == "__main__":
    main()
