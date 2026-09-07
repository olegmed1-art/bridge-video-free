#!/usr/bin/env python3
"""Validate one immutable, owner-authored Issue #881 pre-canary approval."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


REPOSITORY = "olegmed1-art/bridge-video-free"
DIRECTOR_LOGIN = "olegmed1-art"
DIRECTOR_ID = 315099490
ISSUE_NUMBER = 881
WORKFLOW_PATH = ".github/workflows/issue-881-authoritative-external-evidence.yml"
RECEIPT_PREFIX = "ISSUE881_PRECANARY_DIRECTOR_GO_V1\n"
RUN_NAME_PREFIX = "issue881-precanary"
MAX_RECEIPT_AGE_SECONDS = 900
SHA_RE = re.compile(r"[0-9a-f]{40}")
NONCE_RE = re.compile(r"[0-9a-f]{64}")
RUN_ID_RE = re.compile(r"[1-9][0-9]{7,19}")


class OneShotValidationError(ValueError):
    """The approval or run set is missing, reused, ambiguous, or stale."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OneShotValidationError(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "duplicate receipt key")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    _require(path.is_file() and not path.is_symlink(), "unsafe JSON input")
    _require(0 < path.stat().st_size <= 2_000_000, "JSON input size is unsafe")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OneShotValidationError("invalid JSON input") from exc


def _timestamp(value: Any) -> float:
    _require(isinstance(value, str), "invalid receipt timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OneShotValidationError("invalid receipt timestamp") from exc
    _require(parsed.tzinfo is not None, "receipt timestamp has no timezone")
    return parsed.timestamp()


def expected_run_name(exact_sha: str, receipt_id: int) -> str:
    return f"{RUN_NAME_PREFIX}/{exact_sha}/receipt-{receipt_id}"


def render_receipt(
    *, exact_sha: str, approval_nonce: str, recover_container_from_run: str
) -> str:
    """Render the only accepted, deliberately non-media Director receipt body."""

    _require(SHA_RE.fullmatch(exact_sha) is not None, "invalid exact SHA")
    _require(NONCE_RE.fullmatch(approval_nonce) is not None, "invalid approval nonce")
    _require(
        not recover_container_from_run
        or RUN_ID_RE.fullmatch(recover_container_from_run) is not None,
        "invalid recovery run",
    )
    payload = {
        "approval_nonce": approval_nonce,
        "drive_write_performed": False,
        "exact_sha": exact_sha,
        "media_processing": False,
        "one_run": True,
        "queue_mutation": False,
        "recover_container_from_run": recover_container_from_run,
        "rerun": False,
        "scope": "bounded-external-precanary-recreation",
        "vercel_production_deploy": False,
        "workflow": WORKFLOW_PATH,
    }
    return RECEIPT_PREFIX + json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _flatten_run_pages(value: Any) -> list[dict[str, Any]]:
    pages = value if isinstance(value, list) else [value]
    _require(pages and all(isinstance(page, dict) for page in pages), "invalid run pages")
    runs: list[dict[str, Any]] = []
    for page in pages:
        items = page.get("workflow_runs")
        _require(isinstance(items, list), "invalid workflow run page")
        _require(all(isinstance(item, dict) for item in items), "invalid workflow run")
        runs.extend(items)
    return runs


def validate_one_shot(
    comment: Any,
    run_pages: Any,
    *,
    exact_sha: str,
    receipt_id: int,
    approval_nonce: str,
    recover_container_from_run: str,
    current_run_id: int,
    current_run_attempt: int,
    now: float,
) -> dict[str, Any]:
    """Bind one fresh owner comment to exactly one first-attempt workflow run."""

    _require(SHA_RE.fullmatch(exact_sha) is not None, "invalid exact SHA")
    _require(NONCE_RE.fullmatch(approval_nonce) is not None, "invalid approval nonce")
    _require(type(receipt_id) is int and receipt_id > 0, "invalid receipt id")
    _require(type(current_run_id) is int and current_run_id > 0, "invalid current run id")
    _require(current_run_attempt == 1, "workflow reruns are forbidden")
    _require(isinstance(comment, dict), "invalid approval comment")
    _require(comment.get("id") == receipt_id, "approval comment id mismatch")
    _require(
        comment.get("issue_url")
        == f"https://api.github.com/repos/{REPOSITORY}/issues/{ISSUE_NUMBER}",
        "approval comment belongs to the wrong issue",
    )
    user = comment.get("user")
    _require(
        isinstance(user, dict)
        and user.get("id") == DIRECTOR_ID
        and user.get("login") == DIRECTOR_LOGIN,
        "approval comment is not Director-owned",
    )
    _require(comment.get("author_association") == "OWNER", "approver is not repository owner")
    created_at = comment.get("created_at")
    _require(comment.get("updated_at") == created_at, "edited approval comments are forbidden")
    created = _timestamp(created_at)
    _require(created <= now + 30, "approval comment is future-dated")
    _require(0 <= now - created < MAX_RECEIPT_AGE_SECONDS, "approval comment is stale")

    body = comment.get("body")
    _require(isinstance(body, str) and body.startswith(RECEIPT_PREFIX), "wrong receipt schema")
    raw_payload = body[len(RECEIPT_PREFIX) :]
    try:
        payload = json.loads(raw_payload, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, OneShotValidationError) as exc:
        raise OneShotValidationError("invalid receipt JSON") from exc
    _require(type(payload) is dict, "receipt payload is not an object")
    expected_body = render_receipt(
        exact_sha=exact_sha,
        approval_nonce=approval_nonce,
        recover_container_from_run=recover_container_from_run,
    )
    _require(body == expected_body, "receipt body is not exact or canonical")

    runs = _flatten_run_pages(run_pages)
    run_name = expected_run_name(exact_sha, receipt_id)
    workflow_runs = [
        run
        for run in runs
        if run.get("path") == WORKFLOW_PATH and run.get("event") == "workflow_dispatch"
    ]
    receipt_runs = [run for run in workflow_runs if run.get("display_title") == run_name]
    _require(len(receipt_runs) == 1, "approval receipt was not used exactly once")
    current = receipt_runs[0]
    repository = current.get("repository")
    actor = current.get("actor")
    triggering_actor = current.get("triggering_actor")
    _require(current.get("id") == current_run_id, "receipt is bound to another run")
    _require(current.get("run_attempt") == 1, "recorded workflow rerun is forbidden")
    _require(current.get("head_sha") == exact_sha, "workflow run head does not match exact SHA")
    _require(
        isinstance(repository, dict) and repository.get("full_name") == REPOSITORY,
        "workflow run belongs to the wrong repository",
    )
    _require(
        isinstance(actor, dict) and actor.get("login") == DIRECTOR_LOGIN,
        "workflow run was not dispatched by the Director",
    )
    _require(
        isinstance(triggering_actor, dict)
        and triggering_actor.get("login") == DIRECTOR_LOGIN,
        "workflow run triggering actor is not the Director",
    )

    exact_sha_prefix = f"{RUN_NAME_PREFIX}/{exact_sha}/receipt-"
    same_sha_runs = [
        run for run in workflow_runs if str(run.get("display_title", "")).startswith(exact_sha_prefix)
    ]
    allowed_ids = {current_run_id}
    if recover_container_from_run:
        recovery_id = int(recover_container_from_run)
        allowed_ids.add(recovery_id)
        prior = [run for run in same_sha_runs if run.get("id") == recovery_id]
        _require(len(prior) == 1, "recovery source run is missing or ambiguous")
        _require(
            prior[0].get("status") == "completed" and prior[0].get("conclusion") == "failure",
            "recovery source run is not a completed failure",
        )
        _require(prior[0].get("run_attempt") == 1, "recovery from a rerun is forbidden")
    _require(
        {run.get("id") for run in same_sha_runs} == allowed_ids
        and len(same_sha_runs) == len(allowed_ids),
        "another pre-canary run already exists for this exact SHA",
    )

    return {
        "exact_sha": exact_sha,
        "receipt_id": receipt_id,
        "receipt_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "run_id": current_run_id,
        "run_attempt": 1,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    render = subparsers.add_parser("render")
    verify = subparsers.add_parser("verify")
    for item in (render, verify):
        item.add_argument("--exact-sha", required=True)
        item.add_argument("--approval-nonce", required=True)
        item.add_argument("--recover-container-from-run", default="")
    verify.add_argument("--comment-json", type=Path, required=True)
    verify.add_argument("--runs-json", type=Path, required=True)
    verify.add_argument("--receipt-id", type=int, required=True)
    verify.add_argument("--current-run-id", type=int, required=True)
    verify.add_argument("--current-run-attempt", type=int, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "render":
            print(
                render_receipt(
                    exact_sha=args.exact_sha,
                    approval_nonce=args.approval_nonce,
                    recover_container_from_run=args.recover_container_from_run,
                )
            )
            return 0
        result = validate_one_shot(
            _read_json(args.comment_json),
            _read_json(args.runs_json),
            exact_sha=args.exact_sha,
            receipt_id=args.receipt_id,
            approval_nonce=args.approval_nonce,
            recover_container_from_run=args.recover_container_from_run,
            current_run_id=args.current_run_id,
            current_run_attempt=args.current_run_attempt,
            now=dt.datetime.now(dt.timezone.utc).timestamp(),
        )
        print(
            "UNIVERSAL_VIDEO_PRECANARY_ONE_SHOT "
            f"receipt_id={result['receipt_id']} "
            f"receipt_sha256={result['receipt_sha256']} "
            f"exact_sha={result['exact_sha']} run_id={result['run_id']} "
            "run_attempt=1 result=PASS"
        )
        return 0
    except OneShotValidationError as exc:
        print(f"UNIVERSAL_VIDEO_PRECANARY_ONE_SHOT_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
