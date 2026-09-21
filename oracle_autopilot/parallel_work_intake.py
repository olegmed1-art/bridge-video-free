"""Release-controlled, fail-closed intake for independent Autopilot work.

The resident worker has no authority to invent work from arbitrary pull
requests.  This manifest is shipped with a reviewed worker release and is the
only source accepted by the bounded database intake function.  Every item is
repository-only, explicitly scoped, and independent by construction.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SCHEMA_VERSION = "AUTOPILOT_PARALLEL_WORK_V1"
REPOSITORY = "olegmed1-art/bridge-video-free"
MAX_ITEMS = 5
ALLOWED_PRIORITIES = frozenset({0, 10, 20, 30})
REQUIRED_FORBIDDEN_ACTIONS = frozenset(
    {
        "canon_mutation",
        "credential_access",
        "deploy",
        "drive_write",
        "force_push",
        "main_write",
        "merge",
        "neon_write",
        "paid_action",
        "production_write",
        "real_media_processing",
        "server_write",
    }
)
WORK_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,139}")
ROLE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
TASK_KIND_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
PATH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def _task_spec(
    *,
    repair_policy: str,
    expected_changed_files: list[str],
    required_checks: list[str],
) -> dict[str, Any]:
    return {
        "execution_scope": "REPOSITORY",
        "parallel_safe": True,
        "production_mutation": False,
        "repair_policy": repair_policy,
        "expected_changed_files": expected_changed_files,
        "required_checks": required_checks,
        "forbidden_actions": sorted(REQUIRED_FORBIDDEN_ACTIONS),
    }


PARALLEL_WORK_MANIFEST: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "repository": REPOSITORY,
    "source": "REVIEWED_WORKER_RELEASE",
    "items": [
        {
            "work_key": "video-queue-pr1059-current-head-audit-20260921",
            "role": "VIDEO_QUEUE",
            "task_kind": "REPOSITORY_AUDIT",
            "objective": (
                "Audit PR #1059 on its live exact head against current main. "
                "Verify repository code and CI evidence only; do not access or "
                "write Drive, production, Neon, servers, credentials, or media."
            ),
            "target_pr": 1059,
            "priority": 20,
            "task_spec_json": _task_spec(
                repair_policy="DISABLED",
                expected_changed_files=[],
                required_checks=[
                    "all exact-head GitHub checks complete successfully",
                    "repository-only terminal evidence",
                ],
            ),
        },
        {
            "work_key": "video-pr1599-current-head-audit-20260921",
            "role": "VIDEO",
            "task_kind": "REPOSITORY_AUDIT",
            "objective": (
                "Audit PR #1599 on its live exact head against current main and "
                "resolve repository-only documentation defects if required."
            ),
            "target_pr": 1599,
            "priority": 20,
            "task_spec_json": _task_spec(
                repair_policy="BOUNDED",
                expected_changed_files=[
                    "docs/architecture/video31_baseline_v1_audit_2026-09-15.md"
                ],
                required_checks=[
                    "Issue 881 Exact Canary Contract CI",
                    "Issue 881 Current-Main Authoritative CI",
                    "Deployment architecture contract",
                    "Secret gate",
                ],
            ),
        },
        {
            "work_key": "autopilot-pr1683-current-head-audit-20260921",
            "role": "AUTOPILOT",
            "task_kind": "REPOSITORY_AUDIT",
            "objective": (
                "Audit PR #1683 on its live exact head against current main. "
                "Review migration and lifecycle contracts without applying any "
                "migration or changing production."
            ),
            "target_pr": 1683,
            "priority": 10,
            "task_spec_json": _task_spec(
                repair_policy="DISABLED",
                expected_changed_files=[],
                required_checks=[
                    "Bridge School Database CI",
                    "Issue 881 Exact Pre-Canary Evidence",
                    "Migration Namespace Guard",
                    "Secret gate",
                ],
            ),
        },
        {
            "work_key": "security-pr1736-current-head-audit-20260921",
            "role": "SECURITY",
            "task_kind": "REPOSITORY_SECURITY_AUDIT",
            "objective": (
                "Audit PR #1736 on its live exact head for bounded publication "
                "safety and repair only the explicitly assigned repository files "
                "if required."
            ),
            "target_pr": 1736,
            "priority": 10,
            "task_spec_json": _task_spec(
                repair_policy="BOUNDED",
                expected_changed_files=[
                    "oracle_autopilot/github_codex_publication.py",
                    "tests/test_oracle_autopilot_github_codex_publication.py",
                ],
                required_checks=[
                    "Autopilot publication security CI",
                    "Issue 881 Exact Canary Contract CI",
                    "Issue 881 Exact Pre-Canary Evidence",
                    "Oracle Autopilot Lite shadow CI",
                    "Secret gate",
                ],
            ),
        },
    ],
}


class ParallelWorkManifestError(ValueError):
    """The release manifest is not safe to send to the database intake RPC."""


def _string_list(value: Any, *, allow_empty: bool) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_MANIFEST_LIST_INVALID")
    if any(not isinstance(item, str) or not item for item in value):
        raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_MANIFEST_LIST_INVALID")
    if len(value) != len(set(value)):
        raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_MANIFEST_LIST_DUPLICATE")
    return value


def validate_parallel_work_manifest(manifest: Any) -> None:
    """Validate the complete static manifest before any database call."""

    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "repository",
        "source",
        "items",
    }:
        raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_MANIFEST_SHAPE_INVALID")
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["repository"] != REPOSITORY
        or manifest["source"] != "REVIEWED_WORKER_RELEASE"
    ):
        raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_MANIFEST_IDENTITY_INVALID")
    items = manifest["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_MANIFEST_COUNT_INVALID")

    work_keys: set[str] = set()
    roles: set[str] = set()
    targets: set[int] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "work_key",
            "role",
            "task_kind",
            "objective",
            "target_pr",
            "priority",
            "task_spec_json",
        }:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_ITEM_SHAPE_INVALID")
        work_key = item["work_key"]
        role = item["role"]
        task_kind = item["task_kind"]
        objective = item["objective"]
        target_pr = item["target_pr"]
        priority = item["priority"]
        if not isinstance(work_key, str) or WORK_KEY_RE.fullmatch(work_key) is None:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_WORK_KEY_INVALID")
        if not isinstance(role, str) or ROLE_RE.fullmatch(role) is None:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_ROLE_INVALID")
        if not isinstance(task_kind, str) or TASK_KIND_RE.fullmatch(task_kind) is None:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_TASK_KIND_INVALID")
        if (
            not isinstance(objective, str)
            or not 1 <= len(objective) <= 1000
            or any(ord(character) < 32 for character in objective)
        ):
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_OBJECTIVE_INVALID")
        if type(target_pr) is not int or not 1 <= target_pr <= 1_000_000:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_TARGET_INVALID")
        if type(priority) is not int or priority not in ALLOWED_PRIORITIES:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_PRIORITY_INVALID")
        if work_key in work_keys or role in roles or target_pr in targets:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_DUPLICATE_INVALID")
        work_keys.add(work_key)
        roles.add(role)
        targets.add(target_pr)

        spec = item["task_spec_json"]
        if not isinstance(spec, dict) or set(spec) != {
            "execution_scope",
            "parallel_safe",
            "production_mutation",
            "repair_policy",
            "expected_changed_files",
            "required_checks",
            "forbidden_actions",
        }:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_SPEC_SHAPE_INVALID")
        if (
            spec["execution_scope"] != "REPOSITORY"
            or spec["parallel_safe"] is not True
            or spec["production_mutation"] is not False
            or spec["repair_policy"] not in {"DISABLED", "BOUNDED"}
        ):
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_SCOPE_INVALID")
        files = _string_list(
            spec["expected_changed_files"],
            allow_empty=spec["repair_policy"] == "DISABLED",
        )
        if spec["repair_policy"] == "DISABLED" and files:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_DISABLED_REPAIR_FILES")
        if any(
            PATH_RE.fullmatch(path) is None
            or ".." in path.split("/")
            or path.startswith(
                (
                    ".github/",
                    "database/migrations/",
                    "database/rollbacks/",
                    "deploy/",
                    "docs/canon/",
                )
            )
            for path in files
        ):
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_REPAIR_PATH_INVALID")
        _string_list(spec["required_checks"], allow_empty=False)
        forbidden = _string_list(spec["forbidden_actions"], allow_empty=False)
        if set(forbidden) != REQUIRED_FORBIDDEN_ACTIONS:
            raise ParallelWorkManifestError("AUTOPILOT_PARALLEL_FORBIDDEN_SET_INVALID")


def canonical_manifest_text() -> str:
    validate_parallel_work_manifest(PARALLEL_WORK_MANIFEST)
    return json.dumps(
        PARALLEL_WORK_MANIFEST,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def manifest_sha256() -> str:
    return hashlib.sha256(canonical_manifest_text().encode("utf-8")).hexdigest()
