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
ROLES = frozenset({"RECOGNIZER", "VIDEO", "BOOKS", "KNOWLEDGE"})
FIELDS = (
    "dispatch_id",
    "dispatch_epoch",
    "role",
    "task_fingerprint",
    "target_pr",
    "status",
    "result_code",
    "target_head_sha",
    "summary",
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


def parse_issue_comment_event(event: object) -> RoleCallback:
    root = _mapping(event, "CALLBACK_EVENT_INVALID")
    repository = _mapping(root.get("repository"), "CALLBACK_REPOSITORY_INVALID")
    issue = _mapping(root.get("issue"), "CALLBACK_MAILBOX_INVALID")
    comment = _mapping(root.get("comment"), "CALLBACK_COMMENT_INVALID")
    actor = _mapping(comment.get("user"), "CALLBACK_ACTOR_INVALID")
    app = _mapping(
        comment.get("performed_via_github_app"), "CALLBACK_APP_INVALID"
    )
    if root.get("action") != "created":
        raise CallbackContractError("CALLBACK_ACTION_INVALID")
    if repository.get("full_name") != REPOSITORY or repository.get("id") != REPOSITORY_ID:
        raise CallbackContractError("CALLBACK_REPOSITORY_INVALID")
    pull = issue.get("pull_request")
    if (
        issue.get("number") != MAILBOX_PR
        or not isinstance(pull, dict)
        or pull.get("url") != f"https://api.github.com/repos/{REPOSITORY}/pulls/{MAILBOX_PR}"
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
    if isinstance(comment_id, bool) or not isinstance(comment_id, int) or comment_id < 1:
        raise CallbackContractError("CALLBACK_COMMENT_INVALID")
    if not isinstance(body, str) or len(body.encode("utf-8")) > 1_024:
        raise CallbackContractError("CALLBACK_BODY_INVALID")
    lines = body.splitlines()
    if len(lines) != len(FIELDS) + 1 or lines[0] != "AUTOPILOT_RESULT_V1":
        raise CallbackContractError("CALLBACK_BODY_INVALID")
    values: dict[str, str] = {}
    for line, expected_key in zip(lines[1:], FIELDS, strict=True):
        key, separator, value = line.partition("=")
        if separator != "=" or key != expected_key or not value:
            raise CallbackContractError("CALLBACK_BODY_INVALID")
        values[key] = value

    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", values["dispatch_id"]) is None:
        raise CallbackContractError("CALLBACK_DISPATCH_ID_INVALID")
    if re.fullmatch(r"[1-9][0-9]{0,6}", values["dispatch_epoch"]) is None:
        raise CallbackContractError("CALLBACK_EPOCH_INVALID")
    if values["role"] not in ROLES:
        raise CallbackContractError("CALLBACK_ROLE_INVALID")
    if re.fullmatch(r"[0-9a-f]{64}", values["task_fingerprint"]) is None:
        raise CallbackContractError("CALLBACK_TASK_FINGERPRINT_INVALID")
    if re.fullmatch(r"[1-9][0-9]{0,6}", values["target_pr"]) is None:
        raise CallbackContractError("CALLBACK_TARGET_INVALID")
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
        or re.search(r"(?i)(?:password|secret|token|api[_ -]?key|credential|private[_ -]?key)", summary)
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
        payload_fingerprint=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )


def ingest_callback(dsn: str, callback: RoleCallback) -> tuple[bool, str]:
    """Invoke only the task-bound callback RPC using parameterized SQL."""

    with psycopg.connect(
        validate_callback_dsn(dsn),
        connect_timeout=10,
        application_name="school-autopilot-github-callback",
    ) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT accepted, resulting_state
              FROM autopilot.accept_role_dispatch_callback(
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


def main() -> None:
    event_path = Path(os.environ["GITHUB_EVENT_PATH"])
    callback = parse_issue_comment_event(json.loads(event_path.read_text(encoding="utf-8")))
    accepted, state = ingest_callback(
        os.environ["AUTOPILOT_CALLBACK_DATABASE_URL"], callback
    )
    print(json.dumps({"accepted": accepted, "resulting_state": state}, sort_keys=True))


if __name__ == "__main__":
    main()
