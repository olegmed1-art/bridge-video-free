#!/usr/bin/env python3
"""Independent finite-state verifier for the Phase 3B mutation manifest.

This checker intentionally does not call ``validate_repair_request`` and does
not share the policy's safety predicate.  It consumes the generated manifest
as data and proves the permitted write topology for the pilot canary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autopilot_phase3b.policy import (
    FileChange,
    RepairRequest,
    build_mutation_manifest,
    expected_branch_name,
)


TASK_KEY = "phase3b-independent-model-20260830"
REPOSITORY_PATH = "/repos/olegmed1-art/bridge-video-free"
MAIN_REF_PATH = f"{REPOSITORY_PATH}/git/ref/heads/main"


def _manifest() -> dict[str, object]:
    request = RepairRequest(
        task_key=TASK_KEY,
        repository="olegmed1-art/bridge-video-free",
        base_branch="main",
        expected_base_sha="a" * 40,
        branch_name=expected_branch_name(TASK_KEY),
        title="[Autopilot draft] independent finite-state canary",
        changes=(
            FileChange(
                path="docs/evidence/autopilot/phase3b-model-canary.md",
                operation="CREATE",
                content_utf8="Independent Phase 3B model canary.\n",
            ),
        ),
    )
    return build_mutation_manifest(request)


def verify(manifest: dict[str, object]) -> dict[str, object]:
    operations = manifest.get("operations")
    if not isinstance(operations, list) or not operations:
        raise SystemExit("PHASE3B_MODEL_OPERATIONS_INVALID")

    state = "START"
    object_writes = 0
    branch_writes = 0
    draft_pr_writes = 0
    main_reads = 0

    for operation in operations:
        if not isinstance(operation, dict):
            raise SystemExit("PHASE3B_MODEL_OPERATION_INVALID")
        method = operation.get("method")
        path = operation.get("path")
        purpose = operation.get("purpose")
        if method not in {"GET", "POST"} or not isinstance(path, str):
            raise SystemExit("PHASE3B_MODEL_METHOD_INVALID")
        if any(part in path for part in ("/merges", "/actions", "/deployments")):
            raise SystemExit("PHASE3B_MODEL_FORBIDDEN_ENDPOINT")
        if operation.get("force") is True:
            raise SystemExit("PHASE3B_MODEL_FORCE_PUSH")

        if method == "GET" and path == MAIN_REF_PATH:
            main_reads += 1
            state = "BASE_PREFLIGHT" if main_reads == 1 else "BASE_RECONFIRMED"
            continue

        if method == "GET" and path == f"{REPOSITORY_PATH}/git/commits/{'a' * 40}":
            if state != "BASE_PREFLIGHT" or operation.get("expect_commit_sha") != "a" * 40:
                raise SystemExit("PHASE3B_MODEL_BASE_TREE_LOOKUP_INVALID")
            continue

        if method == "GET" and "/contents/" in path:
            if state not in {"BASE_PREFLIGHT", "OBJECTS_BUILT"}:
                raise SystemExit("PHASE3B_MODEL_FILE_PREFLIGHT_ORDER")
            if operation.get("ref") != "a" * 40:
                raise SystemExit("PHASE3B_MODEL_FILE_PREFLIGHT_UNPINNED")
            continue

        if method == "POST" and path in {
            f"{REPOSITORY_PATH}/git/blobs",
            f"{REPOSITORY_PATH}/git/trees",
            f"{REPOSITORY_PATH}/git/commits",
        }:
            if state not in {"BASE_PREFLIGHT", "OBJECTS_BUILT"}:
                raise SystemExit("PHASE3B_MODEL_OBJECT_ORDER")
            object_writes += 1
            state = "OBJECTS_BUILT"
            continue

        if method == "POST" and path == f"{REPOSITORY_PATH}/git/refs":
            if state != "BASE_RECONFIRMED":
                raise SystemExit("PHASE3B_MODEL_REF_BEFORE_READBACK")
            ref = operation.get("ref")
            if not isinstance(ref, str) or not ref.startswith(
                "refs/heads/autopilot/repair/"
            ):
                raise SystemExit("PHASE3B_MODEL_REF_NAMESPACE_INVALID")
            branch_writes += 1
            state = "BRANCH_CREATED"
            continue

        if method == "POST" and path == f"{REPOSITORY_PATH}/pulls":
            if state != "BRANCH_CREATED":
                raise SystemExit("PHASE3B_MODEL_PR_ORDER")
            if (
                operation.get("base") != "main"
                or operation.get("draft") is not True
                or not str(operation.get("head", "")).startswith(
                    "autopilot/repair/"
                )
            ):
                raise SystemExit("PHASE3B_MODEL_PR_BOUNDARY_INVALID")
            draft_pr_writes += 1
            state = "DRAFT_PR_CREATED"
            continue

        raise SystemExit(f"PHASE3B_MODEL_UNKNOWN_OPERATION:{method}:{path}:{purpose}")

    if (
        state != "DRAFT_PR_CREATED"
        or main_reads != 2
        or object_writes < 3
        or branch_writes != 1
        or draft_pr_writes != 1
    ):
        raise SystemExit("PHASE3B_MODEL_TERMINAL_INVARIANT_FAILED")
    return {
        "assurance": "I2_INDEPENDENT_FINITE_STATE_CHECK",
        "branch_writes": branch_writes,
        "draft_pr_writes": draft_pr_writes,
        "main_reads": main_reads,
        "main_writes": 0,
        "merge_writes": 0,
        "delete_writes": 0,
        "force_pushes": 0,
        "result": "PASS",
    }


if __name__ == "__main__":
    print(json.dumps(verify(_manifest()), sort_keys=True))
