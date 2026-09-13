#!/usr/bin/env python3
"""Independent static verifier for the isolated token-broker boundary."""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GITHUB_SOURCE = ROOT / "broker_app" / "github.py"
MAIN_SOURCE = ROOT / "broker_app" / "main.py"
POLICY_SOURCE = ROOT / "broker_app" / "policy.py"


def _assignments(tree: ast.AST) -> dict[str, object]:
    values: dict[str, object] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            try:
                values[target.id] = ast.literal_eval(node.value)
            except (TypeError, ValueError):
                continue
    return values


def verify() -> dict[str, object]:
    github_text = GITHUB_SOURCE.read_text(encoding="utf-8")
    main_text = MAIN_SOURCE.read_text(encoding="utf-8")
    policy_text = POLICY_SOURCE.read_text(encoding="utf-8")
    github_tree = ast.parse(github_text)
    main_tree = ast.parse(main_text)
    policy_tree = ast.parse(policy_text)
    assignments = _assignments(github_tree)
    policy_assignments = _assignments(policy_tree)

    expected_permissions = {
        "checks": "read",
        "contents": "write",
        "pull_requests": "write",
    }
    if assignments.get("GITHUB_API_URL") != "https://api.github.com":
        raise SystemExit("BROKER_GITHUB_ORIGIN_INVALID")
    if assignments.get("REPOSITORY_OWNER") != "olegmed1-art":
        raise SystemExit("BROKER_OWNER_INVALID")
    if assignments.get("REPOSITORY_NAME") != "bridge-video-free":
        raise SystemExit("BROKER_REPOSITORY_INVALID")
    if assignments.get("TOKEN_PERMISSIONS") != expected_permissions:
        raise SystemExit("BROKER_PERMISSIONS_INVALID")
    if assignments.get("BROKER_POLICY_VERSION") != "physical-no-merge-v1":
        raise SystemExit("BROKER_POLICY_VERSION_INVALID")
    expected_exact_operations = {
        ("GET", "/repos/olegmed1-art/bridge-video-free/git/ref/heads/main"),
        ("POST", "/repos/olegmed1-art/bridge-video-free/git/blobs"),
        ("POST", "/repos/olegmed1-art/bridge-video-free/git/trees"),
        ("POST", "/repos/olegmed1-art/bridge-video-free/git/commits"),
        ("POST", "/repos/olegmed1-art/bridge-video-free/git/refs"),
        ("POST", "/repos/olegmed1-art/bridge-video-free/pulls"),
    }
    if set(assignments.get("_ALLOWED_EXACT_OPERATIONS", ())) != expected_exact_operations:
        raise SystemExit("BROKER_TYPED_OPERATION_SET_INVALID")
    forbidden_parts = set(assignments.get("_FORBIDDEN_ENDPOINT_PARTS", ()))
    if not {
        "/merge", "/merges", "/actions", "/deployments", "/rulesets"
    }.issubset(forbidden_parts):
        raise SystemExit("BROKER_EXPLICIT_REJECTIONS_MISSING")
    if policy_assignments.get("REPOSITORY") != "olegmed1-art/bridge-video-free":
        raise SystemExit("BROKER_POLICY_REPOSITORY_INVALID")
    if policy_assignments.get("BASE_BRANCH") != "main":
        raise SystemExit("BROKER_POLICY_BASE_INVALID")
    if policy_assignments.get("BRANCH_PREFIX") != "autopilot/repair/":
        raise SystemExit("BROKER_POLICY_BRANCH_INVALID")
    if (
        policy_assignments.get("MAX_FILES") != 3
        or policy_assignments.get("MAX_FILE_BYTES") != 16_384
        or policy_assignments.get("MAX_TOTAL_BYTES") != 32_768
    ):
        raise SystemExit("BROKER_POLICY_SIZE_INVALID")

    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(github_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".")[0]
        for node in ast.walk(github_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    if imports & {"requests", "httpx", "subprocess", "socket"}:
        raise SystemExit("BROKER_UNBOUNDED_PRIMITIVE")
    if "_authorize_github_operation(method=method, path=path)" not in github_text:
        raise SystemExit("BROKER_TYPED_DISPATCH_NOT_ENFORCED")
    if "urllib.request.Request(" in main_text:
        raise SystemExit("BROKER_MAIN_RAW_HTTP_PRIMITIVE")
    if "execute_bounded_draft_repair" not in main_text:
        raise SystemExit("BROKER_BOUNDED_EXECUTOR_MISSING")
    if "issue_installation_token" in main_text:
        raise SystemExit("BROKER_RAW_TOKEN_ROUTE_PRESENT")

    routes: set[tuple[str, str]] = set()
    for node in ast.walk(main_tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            if (
                isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "app"
                and decorator.func.attr in {"get", "post", "put", "patch", "delete"}
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                routes.add((decorator.func.attr.upper(), decorator.args[0].value))
    if routes != {
        ("GET", "/healthz"),
        ("POST", "/v1/github/draft-repair"),
    }:
        raise SystemExit("BROKER_ROUTE_SURFACE_INVALID")
    if '"production_mutations_enabled": False' not in main_text:
        raise SystemExit("BROKER_PRODUCTION_GUARD_MISSING")
    if 'os.getenv("VERCEL_ENV", "") != "preview"' not in main_text:
        raise SystemExit("BROKER_PREVIEW_RUNTIME_GUARD_MISSING")
    if '"raw_installation_token_exposed": False' not in main_text:
        raise SystemExit("BROKER_RAW_TOKEN_GUARD_MISSING")
    for required in (
        '"broker_policy_version": BROKER_POLICY_VERSION',
        '"source_revision": _source_revision()',
        '"source_attested": _source_revision() != "UNATTESTED"',
        '"artifact_sha256": _artifact_sha256()',
        '"artifact_attested": provenance is not None',
        '"policy_sha256": broker_policy_sha256()',
        '"broker_policy_version": BROKER_POLICY_VERSION',
        '"broker_source_sha": provenance["source_sha"]',
        '"broker_artifact_sha256": provenance["artifact_sha256"]',
        '"broker_policy_sha256": provenance["policy_sha256"]',
        '"broker_provenance_sha256": provenance["provenance_sha256"]',
        '"merge_endpoint_enabled": False',
        '"ref_update_delete_enabled": False',
        "_require_source_attestation()",
        'service_root / "uv.lock"',
    ):
        if required not in main_text:
            raise SystemExit("BROKER_ATTESTATION_GUARD_MISSING")

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(pyproject["project"]["dependencies"])
    if dependencies != {
        "cryptography==46.0.0",
        "fastapi==0.141.1",
        "pydantic>=2.12,<3",
        "uvicorn[standard]==0.52.2",
    }:
        raise SystemExit("BROKER_DEPENDENCIES_INVALID")
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    for package in ("cryptography", "fastapi", "pydantic", "uvicorn"):
        if f'name = "{package}"' not in lock_text:
            raise SystemExit("BROKER_DEPENDENCY_LOCK_INVALID")

    vercel = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    if (
        vercel.get("framework") != "fastapi"
        or vercel.get("regions") != ["fra1"]
        or set(vercel.get("functions", {})) != {"broker_app/main.py"}
        or vercel["functions"]["broker_app/main.py"].get("maxDuration") != 30
    ):
        raise SystemExit("BROKER_VERCEL_BOUNDARY_INVALID")

    return {
        "assurance": "I2_INDEPENDENT_STATIC_CONTRACT",
        "delete_routes": 0,
        "github_origin": "api.github.com",
        "merge_routes": 0,
        "policy_version": "physical-no-merge-v1",
        "permissions": expected_permissions,
        "production_mutations": 0,
        "raw_token_responses": 0,
        "repository": "olegmed1-art/bridge-video-free",
        "result": "PASS",
        "write_route_count": 1,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
