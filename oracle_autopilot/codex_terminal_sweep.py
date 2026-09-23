"""Bounded pre-deadline readback of original Codex terminal comments.

Candidate enumeration is a read-only SECURITY DEFINER RPC. Every result uses
the existing authenticated terminal parser/head gate/database acceptance RPC.
No timeout, slot, retry, or outbox state is changed when nothing is found.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import quote

import psycopg

from oracle_autopilot.codex_terminal_readback import ingest_rest_readback, read_original_terminal
from oracle_autopilot.github_codex_callback import (
    CODEX_APP_ID, CODEX_BOT_ID, REPOSITORY, RESULT_MARKER,
    CallbackContractError, _default_open, _fetch_json,
    verify_pr_head,
)
from oracle_autopilot.github_role_callback import validate_callback_dsn


def load_candidates(dsn: str):
    with psycopg.connect(
        validate_callback_dsn(dsn), connect_timeout=10,
        application_name="school-autopilot-terminal-readback",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM autopilot.codex_terminal_readback_candidates()")
            candidates = cursor.fetchall()
    if len(candidates) > 6:
        raise CallbackContractError("CODEX_READBACK_CAPACITY_EXCEEDED")
    return candidates


def record_failure(dsn: str, dispatch_id, reason_code: str, comment_id=None):
    with psycopg.connect(
        validate_callback_dsn(dsn), connect_timeout=10,
        application_name="school-autopilot-readback-diagnostic",
    ) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT autopilot.record_codex_rest_readback_diagnostic(%s,%s,%s)",
            (dispatch_id, reason_code, comment_id),
        )
        connection.commit()


def find_original_comment(candidate, token: str, *, opener=_default_open):
    dispatch_id, pr_number, epoch, role, fingerprint, mode, since_at, deadline_at = candidate
    if not isinstance(since_at, datetime) or not isinstance(deadline_at, datetime):
        raise CallbackContractError("CODEX_READBACK_CANDIDATE_INVALID")
    if since_at.tzinfo is None or deadline_at.tzinfo is None:
        raise CallbackContractError("CODEX_READBACK_CANDIDATE_INVALID")
    # The SQL RPC already excludes expired/near-expired rows. Re-check before
    # GitHub reads; the acceptance RPC independently samples its own clock.
    if deadline_at <= datetime.now(timezone.utc):
        raise CallbackContractError("CODEX_READBACK_DEADLINE_EXPIRED")
    since = quote(since_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), safe=":-")
    base = f"https://api.github.com/repos/{REPOSITORY}/issues/{pr_number}/comments"
    matches = []
    for page in (1, 2):
        url = f"{base}?per_page=100&page={page}&since={since}"
        rows = _fetch_json(url, token, opener=opener, max_bytes=1_048_576,
                           error_code="CODEX_READBACK_PAGE_UNAVAILABLE")
        if not isinstance(rows, list) or len(rows) > 100:
            raise CallbackContractError("CODEX_READBACK_PAGE_INVALID")
        for row in rows:
            if not isinstance(row, dict):
                raise CallbackContractError("CODEX_READBACK_PAGE_INVALID")
            actor, app, body = row.get("user"), row.get("performed_via_github_app"), row.get("body")
            if (not isinstance(actor, dict) or actor.get("id") != CODEX_BOT_ID
                or not isinstance(app, dict) or app.get("id") != CODEX_APP_ID
                or not isinstance(body, str) or RESULT_MARKER not in body):
                continue
            # Parse only from a fresh exact-ID double read. A malformed bot
            # comment on this PR fails closed; it cannot become a receipt.
            try:
                terminal = read_original_terminal(
                    pr_number, row.get("id"), token, opener=opener,
                # GitHub comment timestamps have second precision while the
                # PostgreSQL publication timestamp has microseconds.
                not_before=since_at.replace(microsecond=0), not_after=deadline_at,
                )
            except CallbackContractError as exc:
                exc.comment_id = row.get("id")
                raise
            if (terminal.dispatch_id == str(dispatch_id)
                and terminal.dispatch_epoch == epoch and terminal.role == role
                and terminal.task_fingerprint == fingerprint and terminal.target_pr == pr_number):
                matches.append(terminal)
        if len(rows) < 100:
            break
    else:
        raise CallbackContractError("CODEX_READBACK_PAGE_LIMIT")
    if len(matches) > 1:
        error = CallbackContractError("CODEX_READBACK_DUPLICATE_TERMINAL")
        error.comment_id = matches[0].comment_id
        raise error
    return matches[0] if matches else None


def sweep(dsn: str, token: str, *, opener=_default_open):
    candidates = load_candidates(dsn)
    results = []
    for candidate in candidates:
        terminal = None
        try:
            terminal = find_original_comment(candidate, token, opener=opener)
            if terminal is None:
                results.append({"dispatch_id": str(candidate[0]), "result": "NO_TERMINAL"})
                continue
            verify_pr_head(terminal.target_pr, terminal.target_head_sha, token, opener=opener)
            accepted, state = ingest_rest_readback(dsn, terminal)
            results.append({"dispatch_id": str(candidate[0]), "result": "ACCEPTED" if accepted else "DUPLICATE", "state": state})
        except CallbackContractError as exc:
            raw = str(exc)
            reason = (
                "CODEX_REPAIR_HEAD_NOT_APPLIED"
                if raw == "CODEX_PR_HEAD_MISMATCH" and candidate[5] == "REPAIR"
                else raw if raw in {
                    "CODEX_READBACK_DUPLICATE_TERMINAL", "CODEX_READBACK_PAGE_LIMIT",
                    "CODEX_READBACK_PAGE_UNAVAILABLE", "CODEX_READBACK_COMMENT_CHANGED",
                    "CODEX_READBACK_COMMENT_OUTSIDE_WINDOW", "CODEX_READBACK_DEADLINE_EXPIRED",
                    "CODEX_PR_HEAD_MISMATCH",
                } else "CODEX_READBACK_INVALID_RESULT"
            )
            comment_id = terminal.comment_id if terminal else getattr(exc, "comment_id", None)
            record_failure(dsn, candidate[0], reason, comment_id)
            results.append({"dispatch_id": str(candidate[0]), "result": reason})
    return results


def main():
    print(json.dumps(sweep(os.environ["AUTOPILOT_CALLBACK_DATABASE_URL"], os.environ["GITHUB_TOKEN"]), sort_keys=True))


if __name__ == "__main__":
    main()
