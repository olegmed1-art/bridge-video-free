"""Bounded recovery of one original Codex terminal comment after a missed event.

This entrypoint does not manufacture a provider event or a terminal result.  It
re-reads the immutable GitHub comment, then uses the existing event receiver and
database RPC.  A late or conflicting receipt still fails at the RPC boundary.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import psycopg

from oracle_autopilot.github_codex_callback import (
    CODEX_BOT_ID,
    CODEX_BOT_LOGIN,
    CODEX_APP_ID,
    CODEX_APP_SLUG,
    GENERIC_FAILURE_BODY,
    REPOSITORY,
    REPOSITORY_ID,
    TIMESTAMP_PATTERN,
    CallbackContractError,
    _default_open,
    _fetch_json,
    _mapping,
    _positive_bigint,
    _positive_integer,
    _stable_comment_record,
    parse_terminal_event,
    resolve_generic_failure_terminal,
    verify_pr_head,
)
from oracle_autopilot.github_role_callback import validate_callback_dsn


def ingest_rest_readback(dsn: str, terminal):
    """Keep REST proof separate from the event receiver's signature assertion."""
    with psycopg.connect(
        validate_callback_dsn(dsn), connect_timeout=10,
        application_name="school-autopilot-codex-rest-readback",
    ) as connection, connection.cursor() as cursor:
        cursor.execute("""
            SELECT accepted, resulting_state
              FROM autopilot.accept_role_dispatch_codex_rest_readback(
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
              )
        """, (
            terminal.delivery_id, terminal.payload_fingerprint,
            True,  # authenticated GitHub REST double read, not webhook signature
            REPOSITORY, terminal.event_pr,
            CODEX_BOT_LOGIN, CODEX_BOT_ID, "NONE", CODEX_APP_SLUG, CODEX_APP_ID,
            json.dumps(terminal.body, sort_keys=True, separators=(",", ":")),
        ))
        row = cursor.fetchone()
        connection.commit()
    if row is None:
        raise CallbackContractError("CODEX_REST_READBACK_RPC_EMPTY")
    return bool(row[0]), str(row[1])


def read_original_terminal(pr_number: int, comment_id: int, token: str, *,
                           opener=_default_open, not_before=None, not_after=None):
    pr_number = _positive_integer(pr_number, "CODEX_READBACK_PR_INVALID")
    comment_id = _positive_bigint(comment_id, "CODEX_READBACK_COMMENT_INVALID")
    base = f"https://api.github.com/repos/{REPOSITORY}"
    issue = _mapping(_fetch_json(
        f"{base}/issues/{pr_number}", token, opener=opener,
        max_bytes=65_536, error_code="CODEX_READBACK_ISSUE_UNAVAILABLE",
    ), "CODEX_READBACK_ISSUE_INVALID")
    pull = _mapping(issue.get("pull_request"), "CODEX_READBACK_PR_INVALID")
    if issue.get("number") != pr_number or pull.get("url") != f"{base}/pulls/{pr_number}":
        raise CallbackContractError("CODEX_READBACK_PR_INVALID")
    url = f"{base}/issues/comments/{comment_id}"
    first = _mapping(_fetch_json(
        url, token, opener=opener, max_bytes=131_072,
        error_code="CODEX_READBACK_COMMENT_UNAVAILABLE",
    ), "CODEX_READBACK_COMMENT_INVALID")
    second = _mapping(_fetch_json(
        url, token, opener=opener, max_bytes=131_072,
        error_code="CODEX_READBACK_COMMENT_UNAVAILABLE",
    ), "CODEX_READBACK_COMMENT_INVALID")
    if _stable_comment_record(first) != _stable_comment_record(second):
        raise CallbackContractError("CODEX_READBACK_COMMENT_CHANGED")
    if (
        second.get("id") != comment_id
        or second.get("issue_url") != f"{base}/issues/{pr_number}"
        or second.get("updated_at") != second.get("created_at")
    ):
        raise CallbackContractError("CODEX_READBACK_COMMENT_INVALID")
    created_at = second.get("created_at")
    if not isinstance(created_at, str) or TIMESTAMP_PATTERN.fullmatch(created_at) is None:
        raise CallbackContractError("CODEX_READBACK_COMMENT_TIME_INVALID")
    created = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if (not_before is not None and created < not_before
        or not_after is not None and created > not_after):
        raise CallbackContractError("CODEX_READBACK_COMMENT_OUTSIDE_WINDOW")
    actor = _mapping(second.get("user"), "CODEX_READBACK_ACTOR_INVALID")
    app = _mapping(second.get("performed_via_github_app"), "CODEX_READBACK_APP_INVALID")
    if (actor.get("id"), actor.get("login")) != (CODEX_BOT_ID, CODEX_BOT_LOGIN):
        raise CallbackContractError("CODEX_READBACK_ACTOR_INVALID")
    if (app.get("id"), app.get("slug")) != (CODEX_APP_ID, CODEX_APP_SLUG):
        raise CallbackContractError("CODEX_READBACK_APP_INVALID")
    event = {
        "action": "created",
        "repository": {"id": REPOSITORY_ID, "full_name": REPOSITORY},
        "issue": issue,
        "comment": second,
    }
    if second.get("body") == GENERIC_FAILURE_BODY:
        return resolve_generic_failure_terminal(event, token, opener=opener)
    return parse_terminal_event(event)


def main() -> None:
    if len(sys.argv) != 3 or not all(arg.isascii() and arg.isdecimal() for arg in sys.argv[1:]):
        raise CallbackContractError("CODEX_READBACK_INPUT_INVALID")
    token = os.environ["GITHUB_TOKEN"]
    terminal = read_original_terminal(int(sys.argv[1]), int(sys.argv[2]), token)
    verify_pr_head(terminal.target_pr, terminal.target_head_sha, token)
    accepted, state = ingest_rest_readback(os.environ["AUTOPILOT_CALLBACK_DATABASE_URL"], terminal)
    print(json.dumps({"accepted": accepted, "resulting_state": state}, sort_keys=True))


if __name__ == "__main__":
    main()
