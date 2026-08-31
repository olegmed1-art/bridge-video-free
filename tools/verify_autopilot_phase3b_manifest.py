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
    blob_results: set[str] = set()
    branch_writes = 0
    draft_pr_writes = 0
    main_reads = 0
    branch_preflights = 0

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
            if main_reads == 1 and state == "START":
                state = "BASE_PREFLIGHT"
            elif main_reads == 2 and state == "COMMIT_BUILT":
                state = "BASE_RECONFIRMED"
            else:
                raise SystemExit("PHASE3B_MODEL_MAIN_READ_ORDER")
            if operation.get("expect_sha") != "a" * 40:
                raise SystemExit("PHASE3B_MODEL_MAIN_READ_UNPINNED")
            continue

        if method == "GET" and path == f"{REPOSITORY_PATH}/git/commits/{'a' * 40}":
            if state != "BASE_PREFLIGHT" or operation.get("expect_commit_sha") != "a" * 40:
                raise SystemExit("PHASE3B_MODEL_BASE_TREE_LOOKUP_INVALID")
            state = "BASE_TREE_LOOKED_UP"
            continue

        if method == "GET" and path.startswith(
            f"{REPOSITORY_PATH}/git/ref/heads/autopilot/repair/"
        ):
            if (
                state != "BASE_TREE_LOOKED_UP"
                or operation.get("expect_absent") is not True
            ):
                raise SystemExit("PHASE3B_MODEL_BRANCH_PREFLIGHT_INVALID")
            branch_preflights += 1
            state = "BRANCH_ABSENT"
            continue

        if method == "GET" and "/contents/" in path:
            if state != "BRANCH_ABSENT":
                raise SystemExit("PHASE3B_MODEL_FILE_PREFLIGHT_ORDER")
            if operation.get("ref") != "a" * 40:
                raise SystemExit("PHASE3B_MODEL_FILE_PREFLIGHT_UNPINNED")
            if operation.get("expect_absent") is not True:
                raise SystemExit("PHASE3B_MODEL_CREATE_ABSENCE_UNBOUND")
            if "expect_blob_sha" in operation:
                raise SystemExit("PHASE3B_MODEL_CREATE_PRECONDITION_AMBIGUOUS")
            continue

        if method == "POST" and path == f"{REPOSITORY_PATH}/git/blobs":
            if state not in {"BRANCH_ABSENT", "BLOBS_BUILT"}:
                raise SystemExit("PHASE3B_MODEL_BLOB_ORDER")
            index = len(blob_results)
            result_key = operation.get("result_key")
            if (
                result_key != f"blob_{index}_sha"
                or operation.get("content_source")
                != f"request.changes[{index}].content_utf8"
                or not isinstance(operation.get("content_sha256"), str)
                or len(str(operation.get("content_sha256"))) != 64
            ):
                raise SystemExit("PHASE3B_MODEL_BLOB_BINDING_INVALID")
            blob_results.add(str(result_key))
            object_writes += 1
            state = "BLOBS_BUILT"
            continue

        if method == "POST" and path == f"{REPOSITORY_PATH}/git/trees":
            entries = operation.get("entries")
            if (
                state != "BLOBS_BUILT"
                or operation.get("base_tree_source")
                != "base_tree_lookup.tree.sha"
                or operation.get("result_key") != "tree_sha"
                or not isinstance(entries, list)
                or len(entries) != len(blob_results)
            ):
                raise SystemExit("PHASE3B_MODEL_TREE_BINDING_INVALID")
            for index, entry in enumerate(entries):
                if (
                    not isinstance(entry, dict)
                    or entry.get("mode") != "100644"
                    or entry.get("type") != "blob"
                    or entry.get("sha_source") != f"blob_{index}_sha"
                ):
                    raise SystemExit("PHASE3B_MODEL_TREE_ENTRY_INVALID")
            object_writes += 1
            state = "TREE_BUILT"
            continue

        if method == "POST" and path == f"{REPOSITORY_PATH}/git/commits":
            if (
                state != "TREE_BUILT"
                or operation.get("tree_sha_source") != "tree_sha"
                or operation.get("parent_sha") != "a" * 40
                or operation.get("result_key") != "commit_sha"
            ):
                raise SystemExit("PHASE3B_MODEL_COMMIT_BINDING_INVALID")
            object_writes += 1
            state = "COMMIT_BUILT"
            continue

        if method == "POST" and path == f"{REPOSITORY_PATH}/git/refs":
            if state != "BASE_RECONFIRMED":
                raise SystemExit("PHASE3B_MODEL_REF_BEFORE_READBACK")
            ref = operation.get("ref")
            if not isinstance(ref, str) or not ref.startswith(
                "refs/heads/autopilot/repair/"
            ):
                raise SystemExit("PHASE3B_MODEL_REF_NAMESPACE_INVALID")
            if operation.get("sha_source") != "commit_sha":
                raise SystemExit("PHASE3B_MODEL_REF_TARGET_UNBOUND")
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
        or branch_preflights != 1
        or object_writes != 3
        or blob_results != {"blob_0_sha"}
        or branch_writes != 1
        or draft_pr_writes != 1
    ):
        raise SystemExit("PHASE3B_MODEL_TERMINAL_INVARIANT_FAILED")
    return {
        "assurance": "I2_INDEPENDENT_FINITE_STATE_CHECK",
        "branch_writes": branch_writes,
        "branch_preflights": branch_preflights,
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
