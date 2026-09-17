"""Offline owner-bound verifier for guarded bounded publication permits.

This module is deliberately not imported by the callback workflow or worker.
It reads three immutable GitHub records (owner command, Codex publication
request, and a separate direct owner approval), re-fetches them before any
permit issue, and calls the owner-only SQL issuer only in explicit ``issue``
mode.  It never publishes repository content itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import asdict
from typing import Any

import psycopg

from oracle_autopilot.github_codex_callback import (
    OWNER_ID,
    OWNER_LOGIN,
    REPOSITORY,
    REPOSITORY_ID,
    TIMESTAMP_PATTERN,
    CallbackContractError,
)
from oracle_autopilot import github_codex_publication as publication

APPROVAL_MARKER = "AUTOPILOT_PUBLICATION_APPROVAL_V1"
APPROVAL_FIELDS = {
    "command_comment_id",
    "publication_comment_id",
    "payload_sha256",
    "dispatch_id",
    "dispatch_epoch",
    "role",
    "task_fingerprint",
    "target_pr",
    "expected_head_sha",
}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CallbackContractError(code)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _positive_bigint(value: Any, code: str) -> int:
    require(type(value) is int and 1 <= value <= 9_223_372_036_854_775_807, code)
    return value


def _issue_number(comment: dict[str, Any]) -> int:
    url = comment.get("issue_url")
    prefix = f"https://api.github.com/repos/{REPOSITORY}/issues/"
    require(isinstance(url, str) and url.startswith(prefix), "PERMIT_COMMENT_TARGET_INVALID")
    suffix = url[len(prefix):]
    require(re.fullmatch(r"[1-9][0-9]{0,6}", suffix) is not None,
            "PERMIT_COMMENT_TARGET_INVALID")
    return int(suffix)


def _event(comment: dict[str, Any], target_pr: int) -> dict[str, Any]:
    return {
        "action": "created",
        "repository": {"id": REPOSITORY_ID, "full_name": REPOSITORY},
        "issue": {
            "number": target_pr,
            "pull_request": {
                "url": f"https://api.github.com/repos/{REPOSITORY}/pulls/{target_pr}"
            },
        },
        "comment": comment,
    }


def stable_record(comment: dict[str, Any]) -> dict[str, Any]:
    user = comment.get("user")
    require(isinstance(user, dict), "PERMIT_RECORD_INVALID")
    app = comment.get("performed_via_github_app")
    if app is not None:
        require(isinstance(app, dict), "PERMIT_RECORD_INVALID")
        app_record: dict[str, Any] | None = {"id": app.get("id"), "slug": app.get("slug")}
    else:
        app_record = None
    created_at, updated_at = comment.get("created_at"), comment.get("updated_at")
    require(isinstance(created_at, str) and TIMESTAMP_PATTERN.fullmatch(created_at) is not None,
            "PERMIT_RECORD_TIME_INVALID")
    require(isinstance(updated_at, str) and TIMESTAMP_PATTERN.fullmatch(updated_at) is not None,
            "PERMIT_RECORD_TIME_INVALID")
    body = comment.get("body")
    require(isinstance(body, str) and len(body.encode()) <= 65_536, "PERMIT_RECORD_BODY_INVALID")
    return {
        "id": _positive_bigint(comment.get("id"), "PERMIT_RECORD_ID_INVALID"),
        "issue_url": comment.get("issue_url"),
        "created_at": created_at,
        "updated_at": updated_at,
        "author_association": comment.get("author_association"),
        "user": {"id": user.get("id"), "login": user.get("login")},
        "performed_via_github_app": app_record,
        "body": body,
    }


def parse_approval(comment: dict[str, Any], target_pr: int) -> dict[str, Any]:
    require(_issue_number(comment) == target_pr, "PERMIT_APPROVAL_TARGET_INVALID")
    record = stable_record(comment)
    require(record["user"] == {"id": OWNER_ID, "login": OWNER_LOGIN}
            and record["author_association"] == "OWNER",
            "PERMIT_APPROVAL_ACTOR_INVALID")
    # A copied envelope mediated by an App is not a direct owner approval.
    require(record["performed_via_github_app"] is None, "PERMIT_APPROVAL_APP_MEDIATED")
    require(record["created_at"] == record["updated_at"], "PERMIT_APPROVAL_EDITED")
    lines = record["body"].splitlines()
    require(len(lines) == 2 and lines[0] == APPROVAL_MARKER, "PERMIT_APPROVAL_BODY_INVALID")
    try:
        value = json.loads(lines[1], object_pairs_hook=publication.strict_object)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CallbackContractError("PERMIT_APPROVAL_JSON_INVALID") from exc
    require(isinstance(value, dict) and set(value) == APPROVAL_FIELDS,
            "PERMIT_APPROVAL_FIELDS_INVALID")
    return value


def _fetch_comment(github: Any, comment_id: int) -> dict[str, Any]:
    result = github.call(f"issues/comments/{_positive_bigint(comment_id, 'PERMIT_COMMENT_ID_INVALID')}")
    require(isinstance(result, dict), "PERMIT_RECORD_INVALID")
    return result


def verify(github: Any, command_comment_id: int, publication_comment_id: int,
           approval_comment_id: int) -> dict[str, Any]:
    ids = {command_comment_id, publication_comment_id, approval_comment_id}
    require(len(ids) == 3, "PERMIT_COMMENT_REUSE_INVALID")

    publication_comment = _fetch_comment(github, publication_comment_id)
    target_pr = _issue_number(publication_comment)
    publication_event = _event(publication_comment, target_pr)
    request = publication.parse_publication_event(publication_event)
    require(request.comment_id == publication_comment_id, "PERMIT_PUBLICATION_ID_INVALID")

    command_comment = _fetch_comment(github, command_comment_id)
    command = publication.bind_command(request, publication_event, command_comment)
    require(command.comment_id == command_comment_id, "PERMIT_COMMAND_ID_INVALID")

    approval_comment = _fetch_comment(github, approval_comment_id)
    approval = parse_approval(approval_comment, target_pr)
    expected_approval = {
        "command_comment_id": command.comment_id,
        "publication_comment_id": request.comment_id,
        "payload_sha256": request.fingerprint,
        "dispatch_id": command.dispatch_id,
        "dispatch_epoch": command.dispatch_epoch,
        "role": command.role,
        "task_fingerprint": command.task_fingerprint,
        "target_pr": command.target_pr,
        "expected_head_sha": command.expected_head_sha,
    }
    require(approval == expected_approval, "PERMIT_APPROVAL_BINDING_INVALID")

    first_records = {
        "command": stable_record(command_comment),
        "publication": stable_record(publication_comment),
        "approval": stable_record(approval_comment),
    }
    # Re-fetch every record immediately before a permit can be issued. Any edit,
    # deletion, identity change or copied replacement fails closed.
    second_comments = {
        "command": _fetch_comment(github, command_comment_id),
        "publication": _fetch_comment(github, publication_comment_id),
        "approval": _fetch_comment(github, approval_comment_id),
    }
    second_records = {key: stable_record(value) for key, value in second_comments.items()}
    require(second_records == first_records, "PERMIT_RECORD_CHANGED")
    # Reparse the complete second snapshot as a final semantic readback.
    second_event = _event(second_comments["publication"], target_pr)
    second_publication = publication.parse_publication_event(second_event)
    second_command = publication.bind_command(second_publication, second_event,
                                               second_comments["command"])
    second_approval = parse_approval(second_comments["approval"], target_pr)
    require(second_publication == request and second_command == command
            and second_approval == approval, "PERMIT_RECORD_CHANGED")

    evidence = {
        "dispatch_id": command.dispatch_id,
        "dispatch_epoch": command.dispatch_epoch,
        "role": command.role,
        "task_fingerprint": command.task_fingerprint,
        "target_pr": command.target_pr,
        "expected_head_sha": command.expected_head_sha,
        "command_comment_id": command.comment_id,
        "publication_comment_id": request.comment_id,
        "approval_comment_id": approval_comment_id,
        "payload_sha256": request.fingerprint,
        "provenance_evidence_sha256": digest(first_records),
    }
    return {"command": asdict(command), "evidence": evidence,
            "record_sha256": {key: digest(value) for key, value in first_records.items()}}


def issue(cursor: Any, verification: dict[str, Any], ttl_seconds: int = 600) -> dict[str, Any]:
    require(type(ttl_seconds) is int and 180 <= ttl_seconds <= 900, "PERMIT_TTL_INVALID")
    cursor.execute("SELECT autopilot.issue_codex_publication_permit(%s::jsonb,%s)",
                   (canonical(verification["evidence"]), ttl_seconds))
    row = cursor.fetchone()
    require(row is not None and isinstance(row[0], dict) and row[0].get("state") == "ISSUED",
            "PERMIT_ISSUE_FAILED")
    return row[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("verify", "issue"))
    parser.add_argument("command_comment_id", type=int)
    parser.add_argument("publication_comment_id", type=int)
    parser.add_argument("approval_comment_id", type=int)
    parser.add_argument("--ttl-seconds", type=int, default=600)
    args = parser.parse_args()
    github = publication.GitHub(os.environ["GITHUB_TOKEN"])
    verified = verify(github, args.command_comment_id, args.publication_comment_id,
                      args.approval_comment_id)
    if args.action == "verify":
        print(canonical({"state": "VERIFIED", "evidence": verified["evidence"],
                         "record_sha256": verified["record_sha256"]}))
        return
    # Operator-only path. No workflow/service in this PR invokes this entry point.
    with psycopg.connect(os.environ["AUTOPILOT_OWNER_DATABASE_URL"], connect_timeout=10,
                         application_name="school-autopilot-publication-permit-owner") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '15s'")
            result = issue(cursor, verified, args.ttl_seconds)
        connection.commit()
    print(canonical(result))


if __name__ == "__main__":
    main()
