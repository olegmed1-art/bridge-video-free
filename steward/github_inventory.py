"""Read-only GitHub baseline for School Systems Steward.

The collector inventories repository metadata, branches, open work, workflows,
and Actions artifacts. It never mutates GitHub state. Detailed rows remain
runner-local; only bounded counts and a checksum belong in public logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

import requests

GITHUB_API = "https://api.github.com"
SCHEMA_VERSION = "school-systems-github-inventory-v1"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class GitHubInventoryError(RuntimeError):
    """Bounded failure when a complete GitHub baseline cannot be proven."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GitHubReadClient:
    """Minimal GET-only REST client with explicit pagination boundaries."""

    def __init__(
        self,
        token: str,
        *,
        session: requests.Session | Any | None = None,
        timeout_seconds: float = 30.0,
        api_base: str = GITHUB_API,
    ) -> None:
        token = str(token or "").strip()
        if not token:
            raise GitHubInventoryError("GITHUB_TOKEN_MISSING")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._token = token
        self._session = session or requests.Session()
        self._timeout = float(timeout_seconds)
        self._api_base = api_base.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "school-systems-steward",
        }

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        if not path.startswith("/"):
            raise ValueError("GitHub API path must start with /")
        try:
            response = self._session.get(
                self._api_base + path,
                headers=self.headers,
                params=dict(params or {}),
                timeout=self._timeout,
            )
        except Exception as exc:
            raise GitHubInventoryError("GITHUB_REQUEST_FAILED") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 404 and allow_404:
            return None
        remaining = str(getattr(response, "headers", {}).get("X-RateLimit-Remaining", ""))
        if status == 403 and remaining == "0":
            raise GitHubInventoryError("GITHUB_RATE_LIMIT_EXHAUSTED")
        if not 200 <= status < 300:
            raise GitHubInventoryError(f"GITHUB_HTTP_{status or 'UNKNOWN'}")
        try:
            return response.json()
        except Exception as exc:
            raise GitHubInventoryError("GITHUB_INVALID_JSON") from exc

    def get_paginated_list(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        object_key: str | None = None,
        page_size: int = 100,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be in [1,100]")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        rows: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            query = dict(params or {})
            query.update({"per_page": page_size, "page": page})
            payload = self.get_json(path, params=query)
            if object_key is None:
                page_rows = payload
            else:
                if not isinstance(payload, Mapping):
                    raise GitHubInventoryError("GITHUB_PAGE_NOT_OBJECT")
                page_rows = payload.get(object_key)
            if not isinstance(page_rows, list):
                raise GitHubInventoryError("GITHUB_PAGE_NOT_ARRAY")
            for raw in page_rows:
                if not isinstance(raw, Mapping):
                    raise GitHubInventoryError("GITHUB_ROW_NOT_OBJECT")
                rows.append(dict(raw))
            if len(page_rows) < page_size:
                return rows
        raise GitHubInventoryError("GITHUB_PAGINATION_LIMIT")


def _parse_repository(value: str) -> tuple[str, str, str]:
    repository = str(value or "").strip()
    if not _REPOSITORY_RE.fullmatch(repository):
        raise GitHubInventoryError("INVALID_REPOSITORY")
    owner, name = repository.split("/", 1)
    return repository, owner, name


def _parse_timestamp(value: Any, *, code: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise GitHubInventoryError(code)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GitHubInventoryError(code) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(then: datetime, now: datetime) -> int:
    seconds = (now - then).total_seconds()
    if seconds < 0:
        return 0
    return int(seconds // 86400)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _commit_date(client: GitHubReadClient, repository: str, sha: str) -> str:
    if not _SHA_RE.fullmatch(sha):
        raise GitHubInventoryError("GITHUB_INVALID_COMMIT_SHA")
    payload = client.get_json(f"/repos/{repository}/commits/{sha}")
    if not isinstance(payload, Mapping):
        raise GitHubInventoryError("GITHUB_COMMIT_NOT_OBJECT")
    commit = payload.get("commit")
    if not isinstance(commit, Mapping):
        raise GitHubInventoryError("GITHUB_COMMIT_MISSING")
    committer = commit.get("committer")
    author = commit.get("author")
    date_value = None
    if isinstance(committer, Mapping):
        date_value = committer.get("date")
    if not date_value and isinstance(author, Mapping):
        date_value = author.get("date")
    parsed = _parse_timestamp(date_value, code="GITHUB_COMMIT_DATE_INVALID")
    return parsed.isoformat()


def build_github_inventory(
    client: GitHubReadClient,
    *,
    repository: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a COMPLETE deterministic baseline or raise a bounded error."""
    repository, owner, name = _parse_repository(repository)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    repo_payload = client.get_json(f"/repos/{repository}")
    if not isinstance(repo_payload, Mapping):
        raise GitHubInventoryError("GITHUB_REPOSITORY_NOT_OBJECT")
    default_branch = str(repo_payload.get("default_branch") or "").strip()
    if not default_branch:
        raise GitHubInventoryError("GITHUB_DEFAULT_BRANCH_MISSING")

    raw_branches = client.get_paginated_list(f"/repos/{repository}/branches")
    raw_pulls = client.get_paginated_list(
        f"/repos/{repository}/pulls", params={"state": "open", "sort": "updated"}
    )
    raw_issues_all = client.get_paginated_list(
        f"/repos/{repository}/issues", params={"state": "open", "sort": "updated"}
    )
    raw_workflows = client.get_paginated_list(
        f"/repos/{repository}/actions/workflows", object_key="workflows"
    )
    raw_artifacts = client.get_paginated_list(
        f"/repos/{repository}/actions/artifacts", object_key="artifacts"
    )

    open_pr_heads: set[str] = set()
    pulls: list[dict[str, Any]] = []
    for raw in raw_pulls:
        number = raw.get("number")
        head = raw.get("head")
        if not isinstance(number, int) or not isinstance(head, Mapping):
            raise GitHubInventoryError("GITHUB_PR_INVALID")
        head_ref = str(head.get("ref") or "").strip()
        if not head_ref:
            raise GitHubInventoryError("GITHUB_PR_HEAD_MISSING")
        open_pr_heads.add(head_ref)
        updated = _parse_timestamp(raw.get("updated_at"), code="GITHUB_PR_DATE_INVALID")
        pulls.append(
            {
                "number": number,
                "title": str(raw.get("title") or ""),
                "draft": bool(raw.get("draft")),
                "head_ref": head_ref,
                "base_ref": str((raw.get("base") or {}).get("ref") or ""),
                "created_at": str(raw.get("created_at") or ""),
                "updated_at": updated.isoformat(),
                "inactive_days": _age_days(updated, now),
            }
        )

    issues: list[dict[str, Any]] = []
    for raw in raw_issues_all:
        if "pull_request" in raw:
            continue
        number = raw.get("number")
        if not isinstance(number, int):
            raise GitHubInventoryError("GITHUB_ISSUE_INVALID")
        updated = _parse_timestamp(raw.get("updated_at"), code="GITHUB_ISSUE_DATE_INVALID")
        labels_raw = raw.get("labels") or []
        if not isinstance(labels_raw, list):
            raise GitHubInventoryError("GITHUB_ISSUE_LABELS_INVALID")
        labels: list[str] = []
        for label in labels_raw:
            if isinstance(label, Mapping):
                value = str(label.get("name") or "").strip()
            else:
                value = str(label).strip()
            if value:
                labels.append(value)
        issues.append(
            {
                "number": number,
                "title": str(raw.get("title") or ""),
                "labels": sorted(labels, key=str.casefold),
                "created_at": str(raw.get("created_at") or ""),
                "updated_at": updated.isoformat(),
                "inactive_days": _age_days(updated, now),
            }
        )

    commit_dates: MutableMapping[str, str] = {}
    branches: list[dict[str, Any]] = []
    seen_branch_names: set[str] = set()
    for raw in raw_branches:
        branch_name = str(raw.get("name") or "").strip()
        commit = raw.get("commit")
        if not branch_name or not isinstance(commit, Mapping):
            raise GitHubInventoryError("GITHUB_BRANCH_INVALID")
        if branch_name in seen_branch_names:
            raise GitHubInventoryError("GITHUB_DUPLICATE_BRANCH")
        seen_branch_names.add(branch_name)
        sha = str(commit.get("sha") or "").lower()
        if sha not in commit_dates:
            commit_dates[sha] = _commit_date(client, repository, sha)
        committed = _parse_timestamp(
            commit_dates[sha], code="GITHUB_COMMIT_DATE_INVALID"
        )
        age = _age_days(committed, now)
        protected = bool(raw.get("protected"))
        excluded_reason = None
        if branch_name == default_branch:
            excluded_reason = "DEFAULT_BRANCH"
        elif branch_name in open_pr_heads:
            excluded_reason = "OPEN_PR"
        elif protected:
            excluded_reason = "PROTECTED"
        branches.append(
            {
                "name": branch_name,
                "sha": sha,
                "protected": protected,
                "committed_at": committed.isoformat(),
                "age_days": age,
                "open_pr": branch_name in open_pr_heads,
                "stale_candidate": excluded_reason is None and age >= 90,
                "stale_excluded_reason": excluded_reason,
            }
        )

    workflows: list[dict[str, Any]] = []
    for raw in raw_workflows:
        workflow_id = raw.get("id")
        if not isinstance(workflow_id, int):
            raise GitHubInventoryError("GITHUB_WORKFLOW_INVALID")
        workflows.append(
            {
                "id": workflow_id,
                "name": str(raw.get("name") or ""),
                "path": str(raw.get("path") or ""),
                "state": str(raw.get("state") or ""),
            }
        )

    artifacts: list[dict[str, Any]] = []
    for raw in raw_artifacts:
        artifact_id = raw.get("id")
        size = raw.get("size_in_bytes")
        if not isinstance(artifact_id, int) or not isinstance(size, int) or size < 0:
            raise GitHubInventoryError("GITHUB_ARTIFACT_INVALID")
        artifacts.append(
            {
                "id": artifact_id,
                "name": str(raw.get("name") or ""),
                "size_in_bytes": size,
                "expired": bool(raw.get("expired")),
                "created_at": str(raw.get("created_at") or ""),
                "expires_at": str(raw.get("expires_at") or ""),
            }
        )

    branches.sort(key=lambda row: row["name"].casefold())
    pulls.sort(key=lambda row: row["number"])
    issues.sort(key=lambda row: row["number"])
    workflows.sort(key=lambda row: (row["path"].casefold(), row["id"]))
    artifacts.sort(key=lambda row: row["id"])

    counts = {
        "branches": len(branches),
        "protected_branches": sum(1 for row in branches if row["protected"]),
        "open_pull_requests": len(pulls),
        "draft_pull_requests": sum(1 for row in pulls if row["draft"]),
        "open_issues": len(issues),
        "workflows": len(workflows),
        "active_workflows": sum(
            1 for row in workflows if row["state"] == "active"
        ),
        "artifacts": len(artifacts),
        "artifact_bytes": sum(row["size_in_bytes"] for row in artifacts),
        "branches_older_30_days": sum(1 for row in branches if row["age_days"] >= 30),
        "branches_older_90_days": sum(1 for row in branches if row["age_days"] >= 90),
        "branches_older_180_days": sum(1 for row in branches if row["age_days"] >= 180),
        "stale_branch_candidates_90_days": sum(
            1 for row in branches if row["stale_candidate"]
        ),
        "pull_requests_inactive_30_days": sum(
            1 for row in pulls if row["inactive_days"] >= 30
        ),
        "issues_inactive_30_days": sum(
            1 for row in issues if row["inactive_days"] >= 30
        ),
    }

    stable_payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "COMPLETE",
        "repository": {
            "full_name": repository,
            "owner": owner,
            "name": name,
            "default_branch": default_branch,
            "private": bool(repo_payload.get("private")),
            "archived": bool(repo_payload.get("archived")),
            "fork": bool(repo_payload.get("fork")),
        },
        "coverage": {
            "pagination_proven": True,
            "detailed_output_retained": False,
            "mutations_performed": False,
        },
        "counts": counts,
        "branches": branches,
        "open_pull_requests": pulls,
        "open_issues": issues,
        "workflows": workflows,
        "artifacts": artifacts,
    }
    manifest = dict(stable_payload)
    manifest["inventory_sha256"] = _canonical_sha256(stable_payload)
    manifest["generated_at"] = now.isoformat()
    return manifest


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_outputs(manifest: Mapping[str, Any], output_dir: Path) -> dict[str, Path]:
    if manifest.get("status") != "COMPLETE":
        raise GitHubInventoryError("REFUSE_NON_COMPLETE_MANIFEST")
    output_dir = Path(output_dir)
    manifest_path = output_dir / "github-inventory.json"
    summary_path = output_dir / "github-inventory-summary.txt"
    _atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    counts = manifest.get("counts") or {}
    repository = manifest.get("repository") or {}
    lines = [
        f"schema_version={manifest.get('schema_version')}",
        f"status={manifest.get('status')}",
        f"repository={repository.get('full_name')}",
        f"default_branch={repository.get('default_branch')}",
        f"private={int(bool(repository.get('private')))}",
        f"inventory_sha256={manifest.get('inventory_sha256')}",
    ]
    for key in sorted(counts):
        lines.append(f"{key}={counts[key]}")
    _atomic_write(summary_path, "\n".join(lines) + "\n")
    return {"manifest": manifest_path, "summary": summary_path}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only GitHub system inventory")
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", "olegmed1-art/bridge-video-free"),
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        client = GitHubReadClient(
            os.environ.get("GITHUB_TOKEN", ""),
            timeout_seconds=args.timeout_seconds,
        )
        manifest = build_github_inventory(client, repository=args.repository)
        write_outputs(manifest, args.output_dir)
    except GitHubInventoryError as exc:
        print(f"SCHOOL_STEWARD_GITHUB_AUDIT_FAIL code={exc.code}")
        return 2
    except Exception:
        print("SCHOOL_STEWARD_GITHUB_AUDIT_FAIL code=UNEXPECTED")
        return 3
    print("SCHOOL_STEWARD_GITHUB_AUDIT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
