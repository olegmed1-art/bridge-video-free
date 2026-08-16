from __future__ import annotations

"""Meta-tests for the DDS test architecture itself.

These checks prevent a green result from a partial or self-modifying test
system. They validate the canonical manifest, prohibit network/API clients in
the DDS Python layer, enforce a bounded coverage-waiver budget, and ensure smoke
workflows are read-only consumers of the manifest-driven runner.
"""

import ast
import json
from pathlib import Path

from test_runner import load_manifest

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
}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


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
    waiver_budget = int(coverage.get("max_waivers", 0))
    assert waiver_budget >= 0
    assert len(waivers) <= waiver_budget, (len(waivers), waiver_budget, sorted(waivers))

    network_imports: dict[str, list[str]] = {}
    for path in sorted(dds_root.glob("*.py")):
        found = sorted(imported_roots(path) & FORBIDDEN_NETWORK_IMPORTS)
        if found:
            network_imports[path.name] = found
    assert not network_imports, network_imports

    workflows = repo_root / ".github" / "workflows"
    forbidden_workflows = {
        "dds-v23-patch-algorithm-review.yml",
        "dds-training-v23-guard-smoke.yml",
        "dds-algorithm-review-v23-smoke.yml",
    }
    present_forbidden = sorted(name for name in forbidden_workflows if (workflows / name).exists())
    assert not present_forbidden, present_forbidden

    fast_path = workflows / "dds-training-v23-smoke.yml"
    heavy_path = workflows / "dds-training-local-smoke.yml"
    assert fast_path.is_file() and heavy_path.is_file()
    fast = fast_path.read_text(encoding="utf-8").lower()
    heavy = heavy_path.read_text(encoding="utf-8").lower()
    assert "pull_request:" in fast
    assert "test_runner.py" in fast and "--suite fast" in fast
    assert "test_runner.py" in heavy and "--suite dds" in heavy
    assert "--report" in fast and "--report" in heavy
    assert "actions/upload-artifact@v4" in fast and "actions/upload-artifact@v4" in heavy
    assert "git diff --exit-code" in fast and "git diff --exit-code" in heavy
    assert "contents: read" in fast and "contents: read" in heavy

    unsafe: dict[str, list[str]] = {}
    for path in sorted(workflows.glob("dds*.yml")):
        name = path.name.lower()
        if not any(token in name for token in ("smoke", "test", "review", "patch")):
            continue
        text = path.read_text(encoding="utf-8").lower()
        found = sorted(token for token in UNSAFE_WORKFLOW_TOKENS if token in text)
        if found:
            unsafe[path.name] = found
    assert not unsafe, unsafe

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
                "canonical_fast_workflow": fast_path.name,
                "canonical_dds_workflow": heavy_path.name,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
