#!/usr/bin/env python3
"""Validate exact-head independent review evidence for Issue #881."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SHA_RE = re.compile(r"[0-9a-f]{40}")
OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
CODEX_BOT_LOGIN = "chatgpt-codex-connector[bot]"
CODEX_CLEAN_LINE_RE = re.compile(
    r"^Codex Review: Didn't find any major issues\\."
    r"(?:[ \\t]+[^\\r\\n]{1,160})?[ \\t]*$",
    re.MULTILINE,
)

REVIEWED_COMMIT_LINE_RE = re.compile(
    r"^\*\*Reviewed commit:\*\* `([0-9a-f]{10}|[0-9a-f]{40})`[ \t]*$",
    re.MULTILINE,
)
REVIEWED_COMMIT_CLAIM = "**Reviewed commit:**"
MAX_JSON_BYTES = 5_000_000


class ReviewEvidenceError(ValueError):
    """Exact-head independent review evidence is missing or ambiguous."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewEvidenceError(message)


def _read_json(path: Path) -> Any:
    _require(path.is_file() and not path.is_symlink(), "unsafe review JSON input")
    _require(
        0 < path.stat().st_size <= MAX_JSON_BYTES,
        "review JSON input size is unsafe",
    )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewEvidenceError("invalid review JSON input") from exc


def _flatten_pages(value: Any, *, label: str) -> list[dict[str, Any]]:
    _require(isinstance(value, list), f"invalid {label} pages")
    if all(isinstance(item, dict) for item in value):
        return list(value)
    _require(
        all(isinstance(page, list) for page in value),
        f"invalid {label} page",
    )
    items = [item for page in value for item in page]
    _require(
        all(isinstance(item, dict) for item in items),
        f"invalid {label} item",
    )
    return items


def _login(item: dict[str, Any]) -> str:
    user = item.get("user")
    if not isinstance(user, dict):
        return ""
    login = user.get("login")
    return login if isinstance(login, str) else ""


def _is_exact_clean_receipt(comment: dict[str, Any], exact_sha: str) -> bool:
    if _login(comment) != CODEX_BOT_LOGIN:
        return False
    body = comment.get("body")
    if not isinstance(body, str) or CODEX_CLEAN_LINE_RE.search(body) is None:
        return False
    if body.count(REVIEWED_COMMIT_CLAIM) != 1:
        return False
    reviewed_tokens = REVIEWED_COMMIT_LINE_RE.findall(body)
    if len(reviewed_tokens) != 1:
        return False
    token = reviewed_tokens[0]
    return token == exact_sha or (len(token) == 10 and exact_sha.startswith(token))


def validate_review_evidence(
    review_pages: Any,
    comment_pages: Any,
    *,
    exact_sha: str,
    owner_login: str,
) -> dict[str, int | str]:
    """Require Codex review evidence and final independent assurance at one head."""

    _require(SHA_RE.fullmatch(exact_sha) is not None, "invalid reviewed SHA")
    _require(OWNER_RE.fullmatch(owner_login) is not None, "invalid repository owner")
    reviews = _flatten_pages(review_pages, label="review")
    comments = _flatten_pages(comment_pages, label="comment")

    codex_review_count = sum(
        1
        for review in reviews
        if review.get("commit_id") == exact_sha
        and _login(review) == CODEX_BOT_LOGIN
        and review.get("state") in {"COMMENTED", "APPROVED"}
    )
    codex_clean_count = sum(
        1 for comment in comments if _is_exact_clean_receipt(comment, exact_sha)
    )
    _require(
        codex_review_count > 0 or codex_clean_count > 0,
        "exact head has neither a Codex review object nor an exact clean Codex bot receipt",
    )

    latest_by_reviewer: dict[str, tuple[str, str]] = {}
    for review in reviews:
        login = _login(review)
        submitted_at = review.get("submitted_at")
        state = review.get("state")
        if (
            review.get("commit_id") != exact_sha
            or not login
            or login == owner_login
            or not isinstance(submitted_at, str)
            or not isinstance(state, str)
        ):
            continue
        previous = latest_by_reviewer.get(login)
        if previous is None or submitted_at > previous[0]:
            latest_by_reviewer[login] = (submitted_at, state)
    approval_count = sum(
        1 for _submitted_at, state in latest_by_reviewer.values() if state == "APPROVED"
    )
    assurance_count = approval_count + codex_clean_count
    _require(
        assurance_count > 0,
        "reviewed head has no current independent approval or exact clean Codex bot receipt",
    )

    return {
        "approval_count": approval_count,
        "assurance_count": assurance_count,
        "codex_clean_count": codex_clean_count,
        "codex_review_count": codex_review_count,
        "exact_sha": exact_sha,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--reviews-json", type=Path, required=True)
    verify.add_argument("--comments-json", type=Path, required=True)
    verify.add_argument("--exact-sha", required=True)
    verify.add_argument("--owner-login", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = validate_review_evidence(
            _read_json(args.reviews_json),
            _read_json(args.comments_json),
            exact_sha=args.exact_sha,
            owner_login=args.owner_login,
        )
    except ReviewEvidenceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "ISSUE881_CODEX_REVIEW_GATE "
        f"exact_sha={result['exact_sha']} "
        f"review_objects={result['codex_review_count']} "
        f"clean_receipts={result['codex_clean_count']} "
        f"approvals={result['approval_count']} result=PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
