from __future__ import annotations

"""Meta-tests for the DDS test architecture itself.

These checks prevent a green result from a partial, self-modifying, or
accidentally self-starting test system.  They validate the canonical manifest,
prohibit network/API clients in the DDS Python layer, enforce the coverage
contract, verify read-only smoke workflows, and keep every mass DDS workflow
manual and explicitly authorized.
"""

import ast
import json
import re
from pathlib import Path

from test_runner import _recursive_files, load_manifest

FORBIDDEN_NETWORK_IMPORTS = {
    "aiohttp",
    "anthropic",
    "httpx",
    "openai",
    "requests",
    "urllib3",
}
UNSAFE_WORKFLOW_TOKENS = {
    "contents: write",
    "git push",
    "git commit",
    "rm -f .github/workflows",
    "pull_request_target:",
}
FORBIDDEN_AUTOMATIC_TRIGGERS = {
    "push",
    "pull_request",
    "pull_request_target",
    "schedule",
    "repository_dispatch",
}
MASS_WORKFLOWS = {
    "dds-training-pilot-start.yml": "ПИЛОТ-10К-TRAIN-ОДОБРЕН",
    "dds-training-pilot-finish.yml": "ПИЛОТ-10К-ФИНИШ-ОДОБРЕН",
}
CURRENT_ACTION_TOKENS = {
    "actions/checkout@v7",
    "actions/setup-python@v7",
}
EXPECTED_DDS_COMMIT = "37c8a79f4c67c55d1a309ccb66dd00cb58af464a"


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def workflow_header(text: str) -> str:
    marker = "\njobs:"
    if marker not in text:
        raise AssertionError("Workflow has no jobs block")
    return text.split(marker, 1)[0]


def asserted_trigger_names(header: str) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(
            r"(?m)^\s{1,6}(push|pull_request|pull_request_target|schedule|repository_dispatch):",
            header,
        )
    }


def assert_current_actions(path: Path, *, cache: bool = False, artifact: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    for token in CURRENT_ACTION_TOKENS:
        assert token in text, (path.name, token)
    assert "actions/checkout@v4" not in text, path.name
    assert "actions/setup-python@v5" not in text, path.name
    if cache:
        assert "actions/cache/restore@v6" in text, path.name
        assert "actions/cache/save@v6" in text, path.name
        assert "actions/cache/restore@v4" not in text, path.name
        assert "actions/cache/save@v4" not in text, path.name
    if artifact:
        assert "actions/upload-artifact@v7" in text, path.name
        assert "actions/upload-artifact@v4" not in text, path.name


def main() -> None:
    dds_root = Path(__file__).resolve().parent
    repo_root = dds_root.parent
    manifest_path = dds_root / "test_matrix.json"
    manifest, specs = load_manifest(manifest_path, root=dds_root)
    assert manifest["schema"] == "dds-test-manifest-v1"
    assert len(specs) >= 20, len(specs)
    assert {spec.suite for spec in specs} == {"fast", "dds"}

    coverage = manifest["coverage"]
    waivers = dict(coverage.get("waivers", {}))
    waiver_budget = int(coverage["max_waivers"])
    assert len(waivers) <= waiver_budget, (len(waivers), waiver_budget, sorted(waivers))

    network_imports: dict[str, list[str]] = {}
    for path in _recursive_files(dds_root, "*.py"):
        found = sorted(imported_roots(path) & FORBIDDEN_NETWORK_IMPORTS)
        if found:
            network_imports[path.relative_to(dds_root).as_posix()] = found
    assert not network_imports, network_imports

    workflows = repo_root / ".github" / "workflows"
    forbidden_workflows = {
        "dds-v23-patch-algorithm-review.yml",
        "dds-training-v23-guard-smoke.yml",
        "dds-algorithm-review-v23-smoke.yml",
    }
    present_forbidden = sorted(
        name for name in forbidden_workflows if (workflows / name).exists()
    )
    assert not present_forbidden, present_forbidden

    # Every DDS workflow is inspected, not only files whose name happens to
    # contain "smoke" or "test".
    unsafe: dict[str, list[str]] = {}
    for path in sorted([*workflows.glob("dds*.yml"), *workflows.glob("dds*.yaml")]):
        text = path.read_text(encoding="utf-8").lower()
        found = sorted(token for token in UNSAFE_WORKFLOW_TOKENS if token in text)
        if found:
            unsafe[path.name] = found
    assert not unsafe, unsafe

    fast_path = workflows / "dds-training-v23-smoke.yml"
    heavy_path = workflows / "dds-training-local-smoke.yml"
    assert fast_path.is_file() and heavy_path.is_file()
    fast = fast_path.read_text(encoding="utf-8")
    heavy = heavy_path.read_text(encoding="utf-8")
    fast_lower = fast.lower()
    heavy_lower = heavy.lower()

    assert "pull_request:" in fast
    assert "test_runner.py" in fast and "--suite fast" in fast
    assert "test_runner.py" in heavy and "--suite dds" in heavy
    assert "--report" in fast and "--report" in heavy
    assert "git diff --exit-code" in fast and "git diff --exit-code" in heavy
    assert "contents: read" in fast_lower and "contents: read" in heavy_lower
    assert "actions/upload-artifact@v7" in fast and "actions/upload-artifact@v7" in heavy

    # The core guards run directly before trusting the manifest they govern.
    for workflow_name, text in ((fast_path.name, fast), (heavy_path.name, heavy)):
        runner_guard = text.index("python test_runner_selftest.py")
        architecture_guard = text.index("python test_architecture_selftest.py")
        manifest_check = text.index("--check-only")
        assert runner_guard < manifest_check, workflow_name
        assert architecture_guard < manifest_check, workflow_name

    for mass_name in MASS_WORKFLOWS:
        assert mass_name in fast, f"Fast workflow does not watch {mass_name}"

    assert_current_actions(fast_path, artifact=True)
    assert_current_actions(heavy_path, cache=True, artifact=True)

    # Golden solver smoke is required twice with byte-independent canonical JSON
    # and matching SHA-256 evidence.
    assert heavy.count("preflight.py --quick") >= 2
    assert "dds-preflight-repeatability.json" in heavy
    assert "canonical_sha256" in heavy
    assert ".cache_rebuilt" in heavy
    assert "github.run_id" in heavy
    assert EXPECTED_DDS_COMMIT in (dds_root / "bootstrap_linux.sh").read_text(
        encoding="utf-8"
    )

    mass_safety: dict[str, dict[str, object]] = {}
    for name, approval_token in MASS_WORKFLOWS.items():
        path = workflows / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        header = workflow_header(text)
        automatic = asserted_trigger_names(header)
        assert not (automatic & FORBIDDEN_AUTOMATIC_TRIGGERS), (name, automatic)
        assert "workflow_dispatch:" in header, name
        assert "approval_token:" in header, name
        assert approval_token in text, name
        assert "Verify explicit human authorization" in text, name
        assert "--start" in text, name
        assert "DDS_TRAINING_CONFIRM: YES" in text, name
        assert "contents: read" in header.lower(), name
        assert_current_actions(
            path,
            cache=(name == "dds-training-pilot-finish.yml"),
            artifact=True,
        )
        mass_safety[name] = {
            "automatic_triggers": sorted(automatic),
            "approval_token_present": True,
            "manual_dispatch_only": True,
        }

    print(
        json.dumps(
            {
                "ok": True,
                "manifest_tests": len(specs),
                "manifest_suites": sorted({spec.suite for spec in specs}),
                "orphan_selftests": 0,
                "unmapped_production_modules": 0,
                "coverage_waivers": sorted(waivers),
                "coverage_waiver_budget": waiver_budget,
                "forbidden_network_imports": 0,
                "unsafe_self_modifying_test_workflows": 0,
                "mass_workflows": mass_safety,
                "golden_preflight_repetitions_required": 2,
                "pinned_dds_commit": EXPECTED_DDS_COMMIT,
                "canonical_fast_workflow": fast_path.name,
                "canonical_dds_workflow": heavy_path.name,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
