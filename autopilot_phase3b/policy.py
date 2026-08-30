"""Fail-closed policy for the first autonomous GitHub draft repair.

The policy produces a declarative mutation manifest only.  It never performs
HTTP requests, reads credentials, invokes a shell, merges a pull request, or
changes the default branch.  A future credentialed adapter must accept only a
manifest produced and revalidated by this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal


REPOSITORY = "olegmed1-art/bridge-video-free"
BASE_BRANCH = "main"
BRANCH_PREFIX = "autopilot/repair/"
MAX_FILES = 3
MAX_FILE_BYTES = 16_384
MAX_TOTAL_BYTES = 32_768
ALLOWED_PATH_PATTERNS = (
    re.compile(r"oracle_autopilot/[A-Za-z0-9_/-]+\.py"),
    re.compile(r"tests/test_oracle_autopilot_[A-Za-z0-9_/-]+\.py"),
    re.compile(r"docs/evidence/autopilot/[A-Za-z0-9_.-]+\.md"),
)
FORBIDDEN_PATH_PREFIXES = (
    ".github/",
    "database/",
    "deploy/",
    "ops/",
)


class DraftRepairPolicyError(RuntimeError):
    """A permanent Phase 3B policy violation."""


@dataclass(frozen=True)
class FileChange:
    path: str
    operation: Literal["CREATE", "UPDATE"]
    content_utf8: str
    expected_blob_sha: str | None = None


@dataclass(frozen=True)
class RepairRequest:
    task_key: str
    repository: str
    base_branch: str
    expected_base_sha: str
    branch_name: str
    title: str
    changes: tuple[FileChange, ...]
    require_draft: bool = True
    allow_merge: bool = False
    allow_force_push: bool = False
    production_mutation: bool = False


def expected_branch_name(task_key: str) -> str:
    digest = hashlib.sha256(task_key.encode("utf-8")).hexdigest()[:16]
    return f"{BRANCH_PREFIX}{digest}"


def _canonical_payload(request: RepairRequest) -> dict[str, object]:
    return {
        "allow_force_push": request.allow_force_push,
        "allow_merge": request.allow_merge,
        "base_branch": request.base_branch,
        "branch_name": request.branch_name,
        "changes": [
            {
                "content_sha256": hashlib.sha256(
                    change.content_utf8.encode("utf-8")
                ).hexdigest(),
                "expected_blob_sha": change.expected_blob_sha,
                "operation": change.operation,
                "path": change.path,
            }
            for change in request.changes
        ],
        "expected_base_sha": request.expected_base_sha,
        "production_mutation": request.production_mutation,
        "repository": request.repository,
        "require_draft": request.require_draft,
        "task_key": request.task_key,
        "title": request.title,
    }


def repair_fingerprint(request: RepairRequest) -> str:
    validate_repair_request(request)
    encoded = json.dumps(
        _canonical_payload(request), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_path(path: str) -> None:
    if (
        not path
        or len(path) > 200
        or path.startswith(("/", "\\"))
        or "\\" in path
        or "//" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(path.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES)
        or not any(pattern.fullmatch(path) for pattern in ALLOWED_PATH_PATTERNS)
    ):
        raise DraftRepairPolicyError("PHASE3B_PATH_NOT_ALLOWED")


def validate_repair_request(request: RepairRequest) -> None:
    if request.repository != REPOSITORY:
        raise DraftRepairPolicyError("PHASE3B_REPOSITORY_INVALID")
    if request.base_branch != BASE_BRANCH:
        raise DraftRepairPolicyError("PHASE3B_BASE_BRANCH_INVALID")
    if not re.fullmatch(r"[0-9a-f]{40}", request.expected_base_sha):
        raise DraftRepairPolicyError("PHASE3B_BASE_SHA_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", request.task_key):
        raise DraftRepairPolicyError("PHASE3B_TASK_KEY_INVALID")
    if request.branch_name != expected_branch_name(request.task_key):
        raise DraftRepairPolicyError("PHASE3B_BRANCH_INVALID")
    if (
        not 1 <= len(request.title) <= 120
        or not request.title.startswith("[Autopilot draft] ")
        or any(ord(character) < 32 or ord(character) == 127 for character in request.title)
    ):
        raise DraftRepairPolicyError("PHASE3B_TITLE_INVALID")
    if (
        request.require_draft is not True
        or request.allow_merge is not False
        or request.allow_force_push is not False
        or request.production_mutation is not False
    ):
        raise DraftRepairPolicyError("PHASE3B_SAFETY_FLAGS_INVALID")
    if not 1 <= len(request.changes) <= MAX_FILES:
        raise DraftRepairPolicyError("PHASE3B_FILE_COUNT_INVALID")

    seen_paths: set[str] = set()
    total_bytes = 0
    for change in request.changes:
        _validate_path(change.path)
        if change.path in seen_paths:
            raise DraftRepairPolicyError("PHASE3B_DUPLICATE_PATH")
        seen_paths.add(change.path)
        if change.operation not in {"CREATE", "UPDATE"}:
            raise DraftRepairPolicyError("PHASE3B_OPERATION_INVALID")
        if not isinstance(change.content_utf8, str) or "\x00" in change.content_utf8:
            raise DraftRepairPolicyError("PHASE3B_CONTENT_INVALID")
        content_bytes = change.content_utf8.encode("utf-8")
        if not content_bytes or len(content_bytes) > MAX_FILE_BYTES:
            raise DraftRepairPolicyError("PHASE3B_FILE_SIZE_INVALID")
        total_bytes += len(content_bytes)
        if change.operation == "CREATE":
            if change.expected_blob_sha is not None:
                raise DraftRepairPolicyError("PHASE3B_CREATE_PRECONDITION_INVALID")
        elif not isinstance(change.expected_blob_sha, str) or not re.fullmatch(
            r"[0-9a-f]{40}", change.expected_blob_sha
        ):
            raise DraftRepairPolicyError("PHASE3B_UPDATE_PRECONDITION_INVALID")
    if total_bytes > MAX_TOTAL_BYTES:
        raise DraftRepairPolicyError("PHASE3B_TOTAL_SIZE_INVALID")


def build_mutation_manifest(request: RepairRequest) -> dict[str, object]:
    """Return the only write sequence Phase 3B may ever execute.

    The manifest deliberately contains no token and no arbitrary URL.  The
    future adapter must re-read ``main`` immediately before creating the ref.
    It may create Git objects, one namespaced branch, and one draft PR only.
    """

    validate_repair_request(request)
    fingerprint = repair_fingerprint(request)
    repo_path = f"/repos/{REPOSITORY}"
    operations: list[dict[str, object]] = [
        {
            "method": "GET",
            "path": f"{repo_path}/git/ref/heads/{BASE_BRANCH}",
            "expect_sha": request.expected_base_sha,
            "purpose": "base_preflight",
        },
        {
            "method": "GET",
            "path": f"{repo_path}/git/commits/{request.expected_base_sha}",
            "expect_commit_sha": request.expected_base_sha,
            "purpose": "base_tree_lookup",
        },
    ]
    for index, change in enumerate(request.changes):
        if change.operation == "UPDATE":
            operations.append(
                {
                    "method": "GET",
                    "path": f"{repo_path}/contents/{change.path}",
                    "expect_blob_sha": change.expected_blob_sha,
                    "ref": request.expected_base_sha,
                    "purpose": f"file_preflight_{index}",
                }
            )
        operations.append(
            {
                "method": "POST",
                "path": f"{repo_path}/git/blobs",
                "body_shape": {"encoding": "utf-8"},
                "content_sha256": hashlib.sha256(
                    change.content_utf8.encode("utf-8")
                ).hexdigest(),
                "purpose": f"create_blob_{index}",
            }
        )
    operations.extend(
        [
            {
                "method": "POST",
                "path": f"{repo_path}/git/trees",
                "base_tree_source": "base_tree_lookup.tree.sha",
                "file_mode": "100644",
                "paths": [change.path for change in request.changes],
                "purpose": "create_tree",
            },
            {
                "method": "POST",
                "path": f"{repo_path}/git/commits",
                "parent_sha": request.expected_base_sha,
                "message": f"autopilot: bounded repair {fingerprint[:16]}",
                "purpose": "create_commit",
            },
            {
                "method": "GET",
                "path": f"{repo_path}/git/ref/heads/{BASE_BRANCH}",
                "expect_sha": request.expected_base_sha,
                "purpose": "base_readback",
            },
            {
                "method": "POST",
                "path": f"{repo_path}/git/refs",
                "ref": f"refs/heads/{request.branch_name}",
                "force": False,
                "purpose": "create_namespaced_branch",
            },
            {
                "method": "POST",
                "path": f"{repo_path}/pulls",
                "base": BASE_BRANCH,
                "head": request.branch_name,
                "draft": True,
                "title": request.title,
                "purpose": "create_draft_pr",
            },
        ]
    )
    if any(
        operation["method"] not in {"GET", "POST"}
        or operation["path"].endswith("/merges")
        or operation.get("force") is True
        or (
            operation["method"] == "POST"
            and operation["path"].endswith("/git/refs")
            and operation.get("ref") == "refs/heads/main"
        )
        for operation in operations
    ):
        raise DraftRepairPolicyError("PHASE3B_UNSAFE_MANIFEST")
    return {
        "version": 1,
        "fingerprint": fingerprint,
        "repository": REPOSITORY,
        "expected_base_sha": request.expected_base_sha,
        "branch_name": request.branch_name,
        "draft_only": True,
        "merge_allowed": False,
        "force_push_allowed": False,
        "production_mutation": False,
        "credential_included": False,
        "operations": operations,
    }
