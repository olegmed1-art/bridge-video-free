"""Authenticated GitHub ingress for the Slavik -> Codex Cloud event cycle.

The command comment is accepted only when it was created by the repository
owner through the pinned ChatGPT Codex Connector GitHub App.  Delivery becomes
real either when the immutable Codex bot account adds an ``eyes`` reaction or
when that same pinned bot/app returns an exact-bound terminal while the outbox
is still ``PUBLISHED`` and its delivery deadline is open.  The latter path does
not fabricate ACK provenance.  Both paths remain database-bound to repository,
dispatch/epoch, role, target PR/head, fingerprint, state, and deadlines.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg

from oracle_autopilot.github_role_callback import (
    CallbackContractError,
    RETAINED_MAILBOX_PRS,
    validate_callback_dsn,
)


REPOSITORY = "olegmed1-art/bridge-video-free"
REPOSITORY_ID = 1_330_085_090
OWNER_LOGIN = "olegmed1-art"
OWNER_ID = 315_099_490
CODEX_APP_SLUG = "chatgpt-codex-connector"
CODEX_APP_ID = 1_144_995
CODEX_BOT_LOGIN = "chatgpt-codex-connector[bot]"
CODEX_BOT_ID = 199_175_422
COMMAND_MARKER = "SLAVIK_CODEX_DISPATCH_V1"
TASK_COMMAND_PREFIX = "@codex execute this task"
RESULT_MARKER = "AUTOPILOT_CODEX_RESULT_V1"
GENERIC_FAILURE_BODY = "Codex couldn't complete this request. Try again later."
GENERIC_FAILURE_LOOKBACK_SECONDS = 600
GENERIC_FAILURE_MAX_DELAY_SECONDS = 300
ACK_RETRY_DELAYS_SECONDS = (0, 1, 2, 4, 8, 15, 30, 60)
COMMAND_FIELDS = (
    "dispatch_id",
    "dispatch_pr",
    "dispatch_epoch",
    "role",
    "task_fingerprint",
    "target_pr",
    "expected_head_sha",
    "mode",
    "execution_scope",
    "can_repair",
    "task_kind",
    "objective",
    "task_spec_json",
)
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
)
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
ROLE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")
POSITIVE_INTEGER_PATTERN = re.compile(r"[1-9][0-9]{0,6}")
TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
)
VIEW_TASK_PATTERN = re.compile(
    r"\[View task →\]\(https://chatgpt\.com/s/cd_[A-Za-z0-9_-]{1,128}\)"
)


@dataclass(frozen=True)
class CodexCommand:
    comment_id: int
    command_pr: int
    created_at: str
    dispatch_id: str
    dispatch_pr: int
    dispatch_epoch: int
    role: str
    task_fingerprint: str
    target_pr: int
    expected_head_sha: str
    mode: str
    execution_scope: str
    can_repair: bool
    task_kind: str
    objective: str
    task_spec: dict[str, Any]


@dataclass(frozen=True)
class CodexAck:
    command: CodexCommand
    reaction_id: int
    reaction_created_at: str

    @property
    def delivery_id(self) -> str:
        return f"github-codex-ack:{self.reaction_id}"

    @property
    def body(self) -> dict[str, Any]:
        command = self.command
        return {
            "ack_created_at": self.reaction_created_at,
            "ack_reaction_id": self.reaction_id,
            "command_comment_id": command.comment_id,
            "command_created_at": command.created_at,
            "command_pr": command.command_pr,
            "dispatch_epoch": command.dispatch_epoch,
            "dispatch_id": command.dispatch_id,
            "dispatch_pr": command.dispatch_pr,
            "expected_head_sha": command.expected_head_sha,
            "mode": command.mode,
            "role": command.role,
            "target_pr": command.target_pr,
            "task_fingerprint": command.task_fingerprint,
        }

    @property
    def payload_fingerprint(self) -> str:
        return _json_sha256(self.body)


@dataclass(frozen=True)
class CodexTerminal:
    comment_id: int
    event_pr: int
    dispatch_id: str
    dispatch_epoch: int
    role: str
    task_fingerprint: str
    target_pr: int
    status: str
    result_code: str
    target_head_sha: str
    summary: str

    @property
    def delivery_id(self) -> str:
        return f"github-codex-result:{self.comment_id}"

    @property
    def body(self) -> dict[str, Any]:
        return {
            "dispatch_epoch": self.dispatch_epoch,
            "dispatch_id": self.dispatch_id,
            "result_code": self.result_code,
            "role": self.role,
            "status": self.status,
            "summary": self.summary,
            "target_head_sha": self.target_head_sha,
            "target_pr": self.target_pr,
            "task_fingerprint": self.task_fingerprint,
        }

    @property
    def payload_fingerprint(self) -> str:
        return _json_sha256(self.body)


def _json_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CallbackContractError(code)
    return value


def _positive_integer(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000_000:
        raise CallbackContractError(code)
    return value


def _positive_bigint(value: object, code: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 9_223_372_036_854_775_807
    ):
        raise CallbackContractError(code)
    return value


def _validate_repository_event(event: object) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _mapping(event, "CODEX_EVENT_INVALID")
    repository = _mapping(root.get("repository"), "CODEX_REPOSITORY_INVALID")
    issue = _mapping(root.get("issue"), "CODEX_PR_INVALID")
    comment = _mapping(root.get("comment"), "CODEX_COMMENT_INVALID")
    if root.get("action") != "created":
        raise CallbackContractError("CODEX_ACTION_INVALID")
    if repository.get("id") != REPOSITORY_ID or repository.get("full_name") != REPOSITORY:
        raise CallbackContractError("CODEX_REPOSITORY_INVALID")
    pr_number = _positive_integer(issue.get("number"), "CODEX_PR_INVALID")
    pull = _mapping(issue.get("pull_request"), "CODEX_PR_INVALID")
    if (
        pull.get("url") != f"https://api.github.com/repos/{REPOSITORY}/pulls/{pr_number}"
        or comment.get("issue_url")
        != f"https://api.github.com/repos/{REPOSITORY}/issues/{pr_number}"
    ):
        raise CallbackContractError("CODEX_PR_INVALID")
    return issue, comment


def _parse_ordered_lines(
    lines: list[str], start: int, fields: tuple[str, ...], code: str
) -> dict[str, str]:
    if len(lines) < start + len(fields):
        raise CallbackContractError(code)
    values: dict[str, str] = {}
    for line, expected in zip(lines[start : start + len(fields)], fields, strict=True):
        key, separator, value = line.partition("=")
        if separator != "=" or key != expected or not value:
            raise CallbackContractError(code)
        values[key] = value
    return values


def _validate_binding_fields(values: dict[str, str], *, head_field: str) -> None:
    if UUID_PATTERN.fullmatch(values["dispatch_id"]) is None:
        raise CallbackContractError("CODEX_DISPATCH_ID_INVALID")
    if POSITIVE_INTEGER_PATTERN.fullmatch(values["dispatch_epoch"]) is None:
        raise CallbackContractError("CODEX_EPOCH_INVALID")
    if ROLE_PATTERN.fullmatch(values["role"]) is None:
        raise CallbackContractError("CODEX_ROLE_INVALID")
    if FINGERPRINT_PATTERN.fullmatch(values["task_fingerprint"]) is None:
        raise CallbackContractError("CODEX_TASK_FINGERPRINT_INVALID")
    if POSITIVE_INTEGER_PATTERN.fullmatch(values["target_pr"]) is None:
        raise CallbackContractError("CODEX_TARGET_PR_INVALID")
    if SHA_PATTERN.fullmatch(values[head_field]) is None:
        raise CallbackContractError("CODEX_HEAD_INVALID")


def parse_command_event(event: object) -> CodexCommand:
    issue, comment = _validate_repository_event(event)
    actor = _mapping(comment.get("user"), "CODEX_COMMAND_ACTOR_INVALID")
    app = _mapping(comment.get("performed_via_github_app"), "CODEX_COMMAND_APP_INVALID")
    if (
        actor.get("login") != OWNER_LOGIN
        or actor.get("id") != OWNER_ID
        or comment.get("author_association") != "OWNER"
    ):
        raise CallbackContractError("CODEX_COMMAND_ACTOR_INVALID")
    if app.get("slug") != CODEX_APP_SLUG or app.get("id") != CODEX_APP_ID:
        raise CallbackContractError("CODEX_COMMAND_APP_INVALID")

    comment_id = _positive_bigint(comment.get("id"), "CODEX_COMMENT_INVALID")
    created_at = comment.get("created_at")
    body = comment.get("body")
    if not isinstance(created_at, str) or TIMESTAMP_PATTERN.fullmatch(created_at) is None:
        raise CallbackContractError("CODEX_COMMAND_TIME_INVALID")
    if not isinstance(body, str) or len(body.encode()) > 16_384:
        raise CallbackContractError("CODEX_COMMAND_BODY_INVALID")
    lines = body.splitlines()
    # Prefer an explicit task verb so the GitHub integration does not route an
    # envelope-only request to ordinary PR review. Accept in-flight legacy
    # commands without admitting arbitrary mention/review prefixes.
    if (
        len(lines) < 3
        or lines[0] not in {"@codex", TASK_COMMAND_PREFIX}
        or lines[1:3] != ["", COMMAND_MARKER]
    ):
        raise CallbackContractError("CODEX_COMMAND_BODY_INVALID")
    if sum(line == COMMAND_MARKER for line in lines) != 1:
        raise CallbackContractError("CODEX_COMMAND_BODY_INVALID")
    values = _parse_ordered_lines(lines, 3, COMMAND_FIELDS, "CODEX_COMMAND_BODY_INVALID")
    _validate_binding_fields(values, head_field="expected_head_sha")
    if POSITIVE_INTEGER_PATTERN.fullmatch(values["dispatch_pr"]) is None:
        raise CallbackContractError("CODEX_DISPATCH_PR_INVALID")
    if values["mode"] not in {"READ_ONLY", "REPAIR", "VERIFY"}:
        raise CallbackContractError("CODEX_MODE_INVALID")
    if values["execution_scope"] not in {
        "REPOSITORY",
        "READ_ONLY_EXTERNAL",
        "OWNER_GATED",
    }:
        raise CallbackContractError("CODEX_SCOPE_INVALID")
    if values["can_repair"] not in {"true", "false"}:
        raise CallbackContractError("CODEX_REPAIR_POLICY_INVALID")
    if ROLE_PATTERN.fullmatch(values["task_kind"]) is None:
        raise CallbackContractError("CODEX_TASK_KIND_INVALID")
    objective = values["objective"]
    if not 1 <= len(objective) <= 1_000 or any(ord(char) < 32 for char in objective):
        raise CallbackContractError("CODEX_OBJECTIVE_INVALID")
    try:
        task_spec = json.loads(values["task_spec_json"])
    except (TypeError, ValueError) as exc:
        raise CallbackContractError("CODEX_TASK_SPEC_INVALID") from exc
    if not isinstance(task_spec, dict) or len(json.dumps(task_spec).encode()) > 4_096:
        raise CallbackContractError("CODEX_TASK_SPEC_INVALID")

    event_pr = int(issue["number"])
    target_pr = int(values["target_pr"])
    dispatch_pr = int(values["dispatch_pr"])
    # GitHub identity and the exact envelope are authenticated here.  The
    # database RPC remains authoritative for the current outbox mailbox and
    # dispatch PR, so this parser admits only the bounded mailbox history or
    # the envelope's own dispatch PR before that canonical check.
    if event_pr not in RETAINED_MAILBOX_PRS and event_pr != dispatch_pr:
        raise CallbackContractError("CODEX_COMMAND_PR_INVALID")
    return CodexCommand(
        comment_id=comment_id,
        command_pr=event_pr,
        created_at=created_at,
        dispatch_id=values["dispatch_id"],
        dispatch_pr=dispatch_pr,
        dispatch_epoch=int(values["dispatch_epoch"]),
        role=values["role"],
        task_fingerprint=values["task_fingerprint"],
        target_pr=target_pr,
        expected_head_sha=values["expected_head_sha"],
        mode=values["mode"],
        execution_scope=values["execution_scope"],
        can_repair=values["can_repair"] == "true",
        task_kind=values["task_kind"],
        objective=objective,
        task_spec=task_spec,
    )


def parse_terminal_event(event: object) -> CodexTerminal:
    issue, comment = _validate_repository_event(event)
    actor = _mapping(comment.get("user"), "CODEX_RESULT_ACTOR_INVALID")
    app = _mapping(comment.get("performed_via_github_app"), "CODEX_RESULT_APP_INVALID")
    if (
        actor.get("login") != CODEX_BOT_LOGIN
        or actor.get("id") != CODEX_BOT_ID
        or comment.get("author_association") != "NONE"
    ):
        raise CallbackContractError("CODEX_RESULT_ACTOR_INVALID")
    if app.get("slug") != CODEX_APP_SLUG or app.get("id") != CODEX_APP_ID:
        raise CallbackContractError("CODEX_RESULT_APP_INVALID")

    comment_id = _positive_bigint(comment.get("id"), "CODEX_COMMENT_INVALID")
    body = comment.get("body")
    if not isinstance(body, str) or len(body.encode()) > 65_536:
        raise CallbackContractError("CODEX_RESULT_BODY_INVALID")
    lines = body.splitlines()
    marker_indexes = [index for index, line in enumerate(lines) if line == RESULT_MARKER]
    if len(marker_indexes) != 1:
        raise CallbackContractError("CODEX_RESULT_BODY_INVALID")
    marker_index = marker_indexes[0]
    values = _parse_ordered_lines(
        lines, marker_index + 1, RESULT_FIELDS, "CODEX_RESULT_BODY_INVALID"
    )
    suffix = lines[marker_index + len(RESULT_FIELDS) + 1 :]
    while suffix and suffix[0] == "":
        suffix.pop(0)
    while suffix and suffix[-1] == "":
        suffix.pop()
    if suffix and (
        len(suffix) != 1 or VIEW_TASK_PATTERN.fullmatch(suffix[0].strip()) is None
    ):
        raise CallbackContractError("CODEX_RESULT_BODY_INVALID")

    _validate_binding_fields(values, head_field="target_head_sha")
    if values["status"] not in {"SUCCEEDED", "BLOCKED"}:
        raise CallbackContractError("CODEX_RESULT_STATUS_INVALID")
    if CODE_PATTERN.fullmatch(values["result_code"]) is None:
        raise CallbackContractError("CODEX_RESULT_CODE_INVALID")
    summary = values["summary"]
    if (
        not 1 <= len(summary) <= 160
        or any(ord(char) < 32 or ord(char) == 127 for char in summary)
        or re.search(r"(?i)(?:https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,})", summary)
        or re.search(
            r"(?i)(?:password|secret|token|api[_ -]?key|credential|private[_ -]?key)",
            summary,
        )
        or re.search(r"(?i)(?:[0-9a-f]{32,}|[a-z0-9+/]{40,}={0,2})", summary)
    ):
        raise CallbackContractError("CODEX_RESULT_SUMMARY_INVALID")
    return CodexTerminal(
        comment_id=comment_id,
        event_pr=int(issue["number"]),
        dispatch_id=values["dispatch_id"],
        dispatch_epoch=int(values["dispatch_epoch"]),
        role=values["role"],
        task_fingerprint=values["task_fingerprint"],
        target_pr=int(values["target_pr"]),
        status=values["status"],
        result_code=values["result_code"],
        target_head_sha=values["target_head_sha"],
        summary=summary,
    )


def _stable_comment_record(comment: dict[str, Any]) -> dict[str, Any]:
    actor = _mapping(comment.get("user"), "CODEX_RESULT_ACTOR_INVALID")
    app = _mapping(comment.get("performed_via_github_app"), "CODEX_RESULT_APP_INVALID")
    return {
        "id": comment.get("id"),
        "body": comment.get("body"),
        "created_at": comment.get("created_at"),
        "updated_at": comment.get("updated_at"),
        "issue_url": comment.get("issue_url"),
        "author_association": comment.get("author_association"),
        "actor_login": actor.get("login"),
        "actor_id": actor.get("id"),
        "app_slug": app.get("slug"),
        "app_id": app.get("id"),
    }


def _fetch_json(
    url: str,
    token: str,
    *,
    opener: Callable[[urllib.request.Request, int], Any],
    max_bytes: int,
    error_code: str,
) -> Any:
    request = _github_request(url, token)
    try:
        with opener(request, 10) as response:
            if response.status != 200 or response.geturl() != url:
                raise CallbackContractError(error_code)
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise CallbackContractError(error_code) from exc
    except urllib.error.URLError as exc:
        raise CallbackContractError(error_code) from exc
    if len(raw) > max_bytes:
        raise CallbackContractError(error_code)
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise CallbackContractError(error_code) from exc


def resolve_generic_failure_terminal(
    event: object,
    token: str,
    *,
    opener: Callable[[urllib.request.Request, int], Any] | None = None,
) -> CodexTerminal:
    """Bind one exact pinned provider failure to its immediately prior command."""

    if opener is None:
        opener = _default_open
    issue, failure = _validate_repository_event(event)
    actor = _mapping(failure.get("user"), "CODEX_RESULT_ACTOR_INVALID")
    app = _mapping(failure.get("performed_via_github_app"), "CODEX_RESULT_APP_INVALID")
    if (
        actor.get("login") != CODEX_BOT_LOGIN
        or actor.get("id") != CODEX_BOT_ID
        or failure.get("author_association") != "NONE"
    ):
        raise CallbackContractError("CODEX_RESULT_ACTOR_INVALID")
    if app.get("slug") != CODEX_APP_SLUG or app.get("id") != CODEX_APP_ID:
        raise CallbackContractError("CODEX_RESULT_APP_INVALID")
    if failure.get("body") != GENERIC_FAILURE_BODY:
        raise CallbackContractError("CODEX_GENERIC_FAILURE_BODY_INVALID")

    failure_id = _positive_bigint(failure.get("id"), "CODEX_COMMENT_INVALID")
    created_at = failure.get("created_at")
    updated_at = failure.get("updated_at")
    if (
        not isinstance(created_at, str)
        or TIMESTAMP_PATTERN.fullmatch(created_at) is None
        or updated_at != created_at
    ):
        raise CallbackContractError("CODEX_GENERIC_FAILURE_TIME_INVALID")
    failure_time = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    since = (failure_time - timedelta(seconds=GENERIC_FAILURE_LOOKBACK_SECONDS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    event_pr = _positive_integer(issue.get("number"), "CODEX_PR_INVALID")
    comments_url = (
        f"https://api.github.com/repos/{REPOSITORY}/issues/{event_pr}/comments"
        f"?per_page=100&since={urllib.parse.quote(since, safe=':-')}"
    )
    comments = _fetch_json(
        comments_url,
        token,
        opener=opener,
        max_bytes=1_048_576,
        error_code="CODEX_GENERIC_FAILURE_COMMENTS_INVALID",
    )
    if not isinstance(comments, list) or not 2 <= len(comments) < 100:
        raise CallbackContractError("CODEX_GENERIC_FAILURE_COMMENTS_INVALID")
    comment_rows = [
        _mapping(comment, "CODEX_GENERIC_FAILURE_COMMENTS_INVALID")
        for comment in comments
    ]
    for comment in comment_rows:
        comment_created_at = comment.get("created_at")
        if (
            not isinstance(comment_created_at, str)
            or TIMESTAMP_PATTERN.fullmatch(comment_created_at) is None
        ):
            raise CallbackContractError("CODEX_GENERIC_FAILURE_COMMENTS_INVALID")
        _positive_bigint(comment.get("id"), "CODEX_GENERIC_FAILURE_COMMENTS_INVALID")
    comment_rows.sort(key=lambda comment: (comment["created_at"], comment["id"]))
    indexes = [index for index, comment in enumerate(comment_rows) if comment.get("id") == failure_id]
    if len(indexes) != 1 or indexes[0] == 0:
        raise CallbackContractError("CODEX_GENERIC_FAILURE_ADJACENCY_INVALID")
    current = comment_rows[indexes[0]]
    if _stable_comment_record(current) != _stable_comment_record(failure):
        raise CallbackContractError("CODEX_GENERIC_FAILURE_CHANGED")
    previous = comment_rows[indexes[0] - 1]
    command_event = dict(_mapping(event, "CODEX_EVENT_INVALID"))
    command_event["comment"] = previous
    command = parse_command_event(command_event)
    command_time = datetime.strptime(command.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    delay = (failure_time - command_time).total_seconds()
    if (
        command.comment_id >= failure_id
        or delay < 0
        or delay > GENERIC_FAILURE_MAX_DELAY_SECONDS
    ):
        raise CallbackContractError("CODEX_GENERIC_FAILURE_ADJACENCY_INVALID")

    command_url = (
        f"https://api.github.com/repos/{REPOSITORY}/issues/comments/{command.comment_id}"
    )
    failure_url = f"https://api.github.com/repos/{REPOSITORY}/issues/comments/{failure_id}"
    fresh_command = _mapping(
        _fetch_json(
            command_url,
            token,
            opener=opener,
            max_bytes=65_536,
            error_code="CODEX_GENERIC_FAILURE_READBACK_INVALID",
        ),
        "CODEX_GENERIC_FAILURE_READBACK_INVALID",
    )
    fresh_failure = _mapping(
        _fetch_json(
            failure_url,
            token,
            opener=opener,
            max_bytes=65_536,
            error_code="CODEX_GENERIC_FAILURE_READBACK_INVALID",
        ),
        "CODEX_GENERIC_FAILURE_READBACK_INVALID",
    )
    if (
        _stable_comment_record(fresh_command) != _stable_comment_record(previous)
        or _stable_comment_record(fresh_failure) != _stable_comment_record(failure)
    ):
        raise CallbackContractError("CODEX_GENERIC_FAILURE_CHANGED")

    return CodexTerminal(
        comment_id=failure_id,
        event_pr=event_pr,
        dispatch_id=command.dispatch_id,
        dispatch_epoch=command.dispatch_epoch,
        role=command.role,
        task_fingerprint=command.task_fingerprint,
        target_pr=command.target_pr,
        status="BLOCKED",
        result_code="CODEX_PROVIDER_GENERIC_FAILURE",
        target_head_sha=command.expected_head_sha,
        summary="Task blocked. See the pinned provider failure comment.",
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _default_open(request: urllib.request.Request, timeout: int) -> Any:
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _github_request(url: str, token: str) -> urllib.request.Request:
    if not token or len(token) > 4_096 or any(ord(char) < 33 for char in token):
        raise CallbackContractError("CODEX_GITHUB_TOKEN_INVALID")
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "school-autopilot-codex-callback/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def verify_pr_head(
    pr_number: int,
    expected_head_sha: str,
    token: str,
    *,
    opener: Callable[[urllib.request.Request, int], Any] = _default_open,
) -> None:
    # This is the assigned work PR, which may be stacked on an integration
    # branch. The dispatch PR's main-only policy is enforced by the publisher
    # and event bridge, not by this target identity/head check.
    if not 1 <= pr_number <= 1_000_000 or SHA_PATTERN.fullmatch(expected_head_sha) is None:
        raise CallbackContractError("CODEX_PR_HEAD_REQUEST_INVALID")
    url = f"https://api.github.com/repos/{REPOSITORY}/pulls/{pr_number}"
    request = _github_request(url, token)
    try:
        with opener(request, 10) as response:
            if response.status != 200 or response.geturl() != url:
                raise CallbackContractError("CODEX_PR_HEAD_HTTP_INVALID")
            raw = response.read(1_048_577)
    except urllib.error.HTTPError as exc:
        raise CallbackContractError("CODEX_PR_HEAD_HTTP_INVALID") from exc
    except urllib.error.URLError as exc:
        raise CallbackContractError("CODEX_PR_HEAD_HTTP_UNAVAILABLE") from exc
    if len(raw) > 1_048_576:
        raise CallbackContractError("CODEX_PR_HEAD_RESPONSE_TOO_LARGE")
    try:
        payload = _mapping(json.loads(raw), "CODEX_PR_HEAD_RESPONSE_INVALID")
        head = _mapping(payload.get("head"), "CODEX_PR_HEAD_RESPONSE_INVALID")
    except (TypeError, ValueError) as exc:
        raise CallbackContractError("CODEX_PR_HEAD_RESPONSE_INVALID") from exc
    if (
        payload.get("number") != pr_number
        or payload.get("state") != "open"
        or head.get("sha") != expected_head_sha
    ):
        raise CallbackContractError("CODEX_PR_HEAD_MISMATCH")


def fetch_codex_ack(
    command: CodexCommand,
    token: str,
    *,
    opener: Callable[[urllib.request.Request, int], Any] = _default_open,
    sleeper: Callable[[float], Any] = time.sleep,
    delays: tuple[int, ...] = ACK_RETRY_DELAYS_SECONDS,
) -> CodexAck:
    url = (
        f"https://api.github.com/repos/{REPOSITORY}/issues/comments/"
        f"{command.comment_id}/reactions?content=eyes&per_page=100"
    )
    request = _github_request(url, token)
    for delay in delays:
        if delay:
            sleeper(delay)
        try:
            with opener(request, 10) as response:
                if response.status != 200 or response.geturl() != url:
                    raise CallbackContractError("CODEX_ACK_HTTP_INVALID")
                raw = response.read(262_145)
        except urllib.error.HTTPError as exc:
            raise CallbackContractError("CODEX_ACK_HTTP_INVALID") from exc
        except urllib.error.URLError as exc:
            raise CallbackContractError("CODEX_ACK_HTTP_UNAVAILABLE") from exc
        if len(raw) > 262_144:
            raise CallbackContractError("CODEX_ACK_RESPONSE_TOO_LARGE")
        try:
            reactions = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise CallbackContractError("CODEX_ACK_RESPONSE_INVALID") from exc
        if not isinstance(reactions, list):
            raise CallbackContractError("CODEX_ACK_RESPONSE_INVALID")
        for value in reactions:
            reaction = _mapping(value, "CODEX_ACK_RESPONSE_INVALID")
            actor = _mapping(reaction.get("user"), "CODEX_ACK_RESPONSE_INVALID")
            reaction_id = reaction.get("id")
            created_at = reaction.get("created_at")
            if (
                reaction.get("content") == "eyes"
                and actor.get("login") == CODEX_BOT_LOGIN
                and actor.get("id") == CODEX_BOT_ID
                and isinstance(reaction_id, int)
                and not isinstance(reaction_id, bool)
                and reaction_id > 0
                and isinstance(created_at, str)
                and TIMESTAMP_PATTERN.fullmatch(created_at) is not None
            ):
                return CodexAck(command, reaction_id, created_at)
    raise CallbackContractError("CODEX_ACK_NOT_FOUND")


def ingest_ack(dsn: str, ack: CodexAck) -> tuple[bool, str]:
    body = ack.body
    with (
        psycopg.connect(
            validate_callback_dsn(dsn),
            connect_timeout=10,
            application_name="school-autopilot-codex-ack",
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT accepted, resulting_state
              FROM autopilot.accept_role_dispatch_codex_ack(
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
              )
            """,
            (
                ack.delivery_id,
                ack.payload_fingerprint,
                True,
                REPOSITORY,
                ack.command.command_pr,
                OWNER_LOGIN,
                OWNER_ID,
                "OWNER",
                CODEX_APP_SLUG,
                CODEX_APP_ID,
                CODEX_BOT_LOGIN,
                CODEX_BOT_ID,
                json.dumps(body, sort_keys=True, separators=(",", ":")),
            ),
        )
        row = cursor.fetchone()
        connection.commit()
    if row is None:
        raise CallbackContractError("CODEX_ACK_RPC_EMPTY")
    return bool(row[0]), str(row[1])


def ingest_terminal(dsn: str, terminal: CodexTerminal) -> tuple[bool, str]:
    with (
        psycopg.connect(
            validate_callback_dsn(dsn),
            connect_timeout=10,
            application_name="school-autopilot-codex-result",
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT accepted, resulting_state
              FROM autopilot.accept_role_dispatch_codex_terminal(
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
              )
            """,
            (
                terminal.delivery_id,
                terminal.payload_fingerprint,
                True,
                REPOSITORY,
                terminal.event_pr,
                CODEX_BOT_LOGIN,
                CODEX_BOT_ID,
                "NONE",
                CODEX_APP_SLUG,
                CODEX_APP_ID,
                json.dumps(terminal.body, sort_keys=True, separators=(",", ":")),
            ),
        )
        row = cursor.fetchone()
        connection.commit()
    if row is None:
        raise CallbackContractError("CODEX_RESULT_RPC_EMPTY")
    return bool(row[0]), str(row[1])


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"ack", "terminal"}:
        raise CallbackContractError("CODEX_CALLBACK_MODE_INVALID")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    dsn = os.environ["AUTOPILOT_CALLBACK_DATABASE_URL"]
    if sys.argv[1] == "ack":
        command = parse_command_event(event)
        token = os.environ["GITHUB_TOKEN"]
        verify_pr_head(command.target_pr, command.expected_head_sha, token)
        ack = fetch_codex_ack(command, token)
        accepted, state = ingest_ack(dsn, ack)
    else:
        comment = _mapping(event.get("comment"), "CODEX_COMMENT_INVALID")
        terminal = (
            resolve_generic_failure_terminal(event, os.environ["GITHUB_TOKEN"])
            if comment.get("body") == GENERIC_FAILURE_BODY
            else parse_terminal_event(event)
        )
        verify_pr_head(
            terminal.target_pr, terminal.target_head_sha, os.environ["GITHUB_TOKEN"]
        )
        accepted, state = ingest_terminal(dsn, terminal)
    print(json.dumps({"accepted": accepted, "resulting_state": state}, sort_keys=True))


if __name__ == "__main__":
    main()
