from __future__ import annotations

from dataclasses import replace

import pytest

from autopilot_phase3b.policy import (
    DraftRepairPolicyError,
    FileChange,
    RepairRequest,
    build_mutation_manifest,
    expected_branch_name,
    repair_fingerprint,
    validate_repair_request,
)


TASK_KEY = "phase3b-canary-20260830"


def _request(**overrides):
    values = {
        "task_key": TASK_KEY,
        "repository": "olegmed1-art/bridge-video-free",
        "base_branch": "main",
        "expected_base_sha": "a" * 40,
        "branch_name": expected_branch_name(TASK_KEY),
        "title": "[Autopilot draft] bounded repair canary",
        "changes": (
            FileChange(
                path="docs/evidence/autopilot/phase3b-canary.md",
                operation="CREATE",
                content_utf8="Phase 3B bounded draft canary.\n",
            ),
        ),
    }
    values.update(overrides)
    return RepairRequest(**values)


def test_valid_request_is_deterministic_and_secret_free():
    request = _request()
    validate_repair_request(request)
    assert repair_fingerprint(request) == repair_fingerprint(request)
    manifest = build_mutation_manifest(request)
    assert manifest["branch_name"].startswith("autopilot/repair/")
    assert manifest["draft_only"] is True
    assert manifest["merge_allowed"] is False
    assert manifest["force_push_allowed"] is False
    assert manifest["credential_included"] is False
    assert all(op["method"] in {"GET", "POST"} for op in manifest["operations"])
    assert not any(
        op["path"].endswith("/merges") or op.get("force") is True
        for op in manifest["operations"]
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("repository", "other/repo", "REPOSITORY_INVALID"),
        ("base_branch", "release", "BASE_BRANCH_INVALID"),
        ("expected_base_sha", "main", "BASE_SHA_INVALID"),
        ("branch_name", "main", "BRANCH_INVALID"),
        ("require_draft", False, "SAFETY_FLAGS_INVALID"),
        ("allow_merge", True, "SAFETY_FLAGS_INVALID"),
        ("allow_force_push", True, "SAFETY_FLAGS_INVALID"),
        ("production_mutation", True, "SAFETY_FLAGS_INVALID"),
    ],
)
def test_top_level_escape_attempts_fail_closed(field, value, code):
    with pytest.raises(DraftRepairPolicyError, match=code):
        validate_repair_request(replace(_request(), **{field: value}))


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/unsafe.yml",
        "database/migrations/9999_unsafe.sql",
        "deploy/oracle-autopilot/service",
        "ops/unsafe.py",
        "../main.py",
        "/etc/passwd",
        "oracle_autopilot/../../main.py",
        "README.md",
    ],
)
def test_forbidden_paths_fail_closed(path):
    bad_change = FileChange(path=path, operation="CREATE", content_utf8="x\n")
    with pytest.raises(DraftRepairPolicyError, match="PATH_NOT_ALLOWED"):
        validate_repair_request(replace(_request(), changes=(bad_change,)))


def test_update_requires_exact_blob_precondition():
    update = FileChange(
        path="oracle_autopilot/worker.py",
        operation="UPDATE",
        content_utf8="safe = True\n",
    )
    with pytest.raises(DraftRepairPolicyError, match="UPDATE_PRECONDITION_INVALID"):
        validate_repair_request(replace(_request(), changes=(update,)))
    validate_repair_request(
        replace(_request(), changes=(replace(update, expected_blob_sha="b" * 40),))
    )


def test_file_and_total_size_limits_are_enforced():
    oversized = FileChange(
        path="oracle_autopilot/large.py",
        operation="CREATE",
        content_utf8="x" * 16_385,
    )
    with pytest.raises(DraftRepairPolicyError, match="FILE_SIZE_INVALID"):
        validate_repair_request(replace(_request(), changes=(oversized,)))

    four = tuple(
        FileChange(
            path=f"oracle_autopilot/file_{index}.py",
            operation="CREATE",
            content_utf8="x\n",
        )
        for index in range(4)
    )
    with pytest.raises(DraftRepairPolicyError, match="FILE_COUNT_INVALID"):
        validate_repair_request(replace(_request(), changes=four))


def test_title_control_characters_are_rejected():
    with pytest.raises(DraftRepairPolicyError, match="TITLE_INVALID"):
        validate_repair_request(
            replace(_request(), title="[Autopilot draft] unsafe\nsecond line")
        )


def test_manifest_rechecks_main_before_creating_namespaced_ref():
    manifest = build_mutation_manifest(_request())
    operations = manifest["operations"]
    ref_index = next(
        index
        for index, operation in enumerate(operations)
        if operation.get("purpose") == "create_namespaced_branch"
    )
    assert operations[ref_index - 1] == {
        "method": "GET",
        "path": "/repos/olegmed1-art/bridge-video-free/git/ref/heads/main",
        "expect_sha": "a" * 40,
        "purpose": "base_readback",
    }
    assert operations[ref_index]["ref"].startswith("refs/heads/autopilot/repair/")
    assert operations[ref_index]["force"] is False


def test_policy_layer_has_no_network_secret_or_process_primitives():
    source = open("autopilot_phase3b/policy.py", encoding="utf-8").read()
    for forbidden in (
        "import urllib",
        "from urllib",
        "import requests",
        "from requests",
        "Authorization",
        "GITHUB_TOKEN",
        "PRIVATE_KEY",
        "getenv",
        "subprocess",
        "os.system",
    ):
        assert forbidden not in source
