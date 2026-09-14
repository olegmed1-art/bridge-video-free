from __future__ import annotations

"""One-shot read-only live canary for Global School Observer (#1157).

This script intentionally performs only HTTP GET requests against GitHub and
runs the deterministic observer evaluator over a tiny normalized snapshot. It
has no external write path and emits one local JSON receipt.
"""

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from autopilot_app.observer import evaluate_snapshot


_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_INSTALL_RECEIPT_RE = re.compile(r"(?m)^## AUTOPILOT_INSTALLED\b")


def _github_get(url: str, token: str) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "global-school-observer-read-only-canary",
        },
        method="GET",
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed GitHub API origin below
        if response.status != 200:
            raise RuntimeError(f"GITHUB_READ_FAILED:{response.status}")
        return json.load(response)


def _evidence_ref(payload: dict[str, Any], fallback: str) -> str:
    value = payload.get("html_url")
    return value if isinstance(value, str) and value else fallback


def _last_issue_comments(repo: str, issue: dict[str, Any], token: str) -> list[dict[str, Any]]:
    count = issue.get("comments")
    if not isinstance(count, int) or count < 0:
        raise RuntimeError("ISSUE_COMMENT_COUNT_INVALID")
    page = max(1, math.ceil(count / 100))
    url = f"https://api.github.com/repos/{repo}/issues/881/comments?per_page=100&page={page}"
    payload = _github_get(url, token)
    if not isinstance(payload, list):
        raise RuntimeError("ISSUE_COMMENTS_PAYLOAD_INVALID")
    return [item for item in payload if isinstance(item, dict)]


def build_live_snapshot(repo: str, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    base = f"https://api.github.com/repos/{repo}"
    issue_881 = _github_get(f"{base}/issues/881", token)
    issue_1157 = _github_get(f"{base}/issues/1157", token)
    pr_1158 = _github_get(f"{base}/pulls/1158", token)

    if not all(isinstance(item, dict) for item in (issue_881, issue_1157, pr_1158)):
        raise RuntimeError("GITHUB_CONTROL_PAYLOAD_INVALID")

    comments = _last_issue_comments(repo, issue_881, token)
    install_receipt = next(
        (
            item
            for item in reversed(comments)
            if isinstance(item.get("body"), str) and _INSTALL_RECEIPT_RE.search(item["body"])
        ),
        None,
    )
    if install_receipt is None:
        raise RuntimeError("AUTOPILOT_INSTALLED_RECEIPT_NOT_FOUND")

    if not pr_1158.get("merged_at"):
        raise RuntimeError("OBSERVER_CORE_PR_NOT_MERGED")

    tasks = [
        {
            "task_id": "autopilot-installed",
            "status": "SUCCESS",
            "owner": "AUTOPILOT_INSTALLER",
            "updated_at": install_receipt.get("created_at"),
            "evidence_refs": [
                _evidence_ref(install_receipt, f"issue:{repo}#881:AUTOPILOT_INSTALLED")
            ],
        },
        {
            "task_id": "global-school-observer-core",
            "status": "SUCCESS",
            "owner": "OBSERVER",
            "updated_at": pr_1158.get("merged_at"),
            "evidence_refs": [_evidence_ref(pr_1158, f"pr:{repo}#1158")],
        },
        {
            "task_id": "global-school-observer-activation-tracker",
            "status": "OPEN",
            "owner": "OBSERVER",
            "updated_at": issue_1157.get("updated_at"),
            "evidence_refs": [_evidence_ref(issue_1157, f"issue:{repo}#1157")],
        },
    ]

    snapshot = {"tasks": tasks, "operations": []}
    source_state = {
        "issue_881_updated_at": issue_881.get("updated_at"),
        "issue_1157_updated_at": issue_1157.get("updated_at"),
        "pr_1158_merged_at": pr_1158.get("merged_at"),
        "autopilot_installed_receipt": _evidence_ref(
            install_receipt, f"issue:{repo}#881:AUTOPILOT_INSTALLED"
        ),
    }
    return snapshot, source_state


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    output_path = Path(os.environ.get("OBSERVER_CANARY_OUTPUT", "observer-canary.json"))

    if not _REPO_RE.fullmatch(repo):
        raise RuntimeError("GITHUB_REPOSITORY_INVALID")
    if not token:
        raise RuntimeError("GITHUB_TOKEN_MISSING")

    observed_at = datetime.now(timezone.utc)
    snapshot, source_state = build_live_snapshot(repo, token)
    findings = evaluate_snapshot(snapshot, now=observed_at)

    receipt = {
        "schema": "GLOBAL_SCHOOL_OBSERVER_CANARY_V1",
        "mode": "READ_ONLY_CANARY",
        "external_mutation_allowed": False,
        "repo": repo,
        "observed_at": observed_at.isoformat(),
        "source_state": source_state,
        "finding_count": len(findings),
        "findings": findings,
    }
    output_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
