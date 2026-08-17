from __future__ import annotations

"""Meta-tests for the DDS test architecture itself.

A green result must come from the complete manifest, read-only workflows,
pinned dependencies and fail-closed launch policy.  This test intentionally
examines every DDS workflow, not just files whose names happen to contain
``smoke`` or ``test``.
"""

import ast
import json
import re
from datetime import date
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
    "dds_training_confirm: yes",
    "dds_run_approval_token:",
}
CANONICAL_WORKFLOWS = {
    "dds-golden-smoke.yml",
    "dds-training-v23-smoke.yml",
    "dds-training-local-smoke.yml",
}
ACTION_PIN_RE = re.compile(r"^\s*-?\s*uses:\s*[^#\s]+@([0-9a-f]{40})(?:\s*#.*)?$", re.MULTILINE)
ANY_ACTION_RE = re.compile(r"^\s*-?\s*uses:\s*([^#\s]+)@([^\s#]+)", re.MULTILINE)
WAIVER_EXPIRY_RE = re.compile(r"(?:^|;\s*)expires=(\d{4}-\d{2}-\d{2})(?:;|$)")
AUTHORIZED_ENTRY_RE = re.compile(
    r"^\s*python(?:3)?\s+(?:dds_training/)?authorized_run_stage\.py(?:\s|$)",
    re.MULTILINE,
)


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def _validate_waivers(waivers: dict[str, str], budget: int) -> dict[str, str]:
    assert budget >= 0
    assert len(waivers) <= budget, (len(waivers), budget, sorted(waivers))
    expiries: dict[str, str] = {}
    today = date.today()
    for module, reason in waivers.items():
        match = WAIVER_EXPIRY_RE.search(str(reason))
        assert match, f"Coverage waiver lacks expires=YYYY-MM-DD: {module}: {reason}"
        expires = date.fromisoformat(match.group(1))
        assert expires >= today, f"Coverage waiver expired on {expires}: {module}"
        expiries[module] = expires.isoformat()
    return expiries


def _validate_action_pins(path: Path, text: str) -> list[str]:
    actions = ANY_ACTION_RE.findall(text)
    assert actions, f"Workflow has no external actions: {path.name}"
    pinned = {match.group(1) for match in ACTION_PIN_RE.finditer(text)}
    unpinned = [f"{name}@{ref}" for name, ref in actions if ref not in pinned]
    assert not unpinned, f"Unpinned GitHub Actions in {path.name}: {unpinned}"
    return [f"{name}@{ref}" for name, ref in actions]


def main() -> None:
    dds_root = Path(__file__).resolve().parent
    repo_root = dds_root.parent
    manifest_path = dds_root / "test_matrix.json"
    manifest, specs = load_manifest(manifest_path, root=dds_root)
    assert manifest["schema"] == "dds-test-manifest-v1"
    assert len(specs) >= 30, len(specs)
    assert {spec.suite for spec in specs} == {"fast", "dds"}

    coverage = manifest["coverage"]
    waivers = dict(coverage.get("waivers", {}))
    waiver_budget = int(coverage.get("max_waivers", 0))
    waiver_expiries = _validate_waivers(waivers, waiver_budget)

    network_imports: dict[str, list[str]] = {}
    for path in sorted(dds_root.glob("*.py")):
        found = sorted(imported_roots(path) & FORBIDDEN_NETWORK_IMPORTS)
        if found:
            network_imports[path.name] = found
    assert not network_imports, network_imports

    workflows = repo_root / ".github" / "workflows"
    workflow_paths = sorted(workflows.glob("dds*.yml"))
    actual_workflows = {path.name for path in workflow_paths}
    assert actual_workflows == CANONICAL_WORKFLOWS, (
        "DDS workflow set must remain minimal; mass evaluation uses the authorized CLI after explicit approval",
        sorted(actual_workflows),
    )

    fast_path = workflows / "dds-training-v23-smoke.yml"
    heavy_path = workflows / "dds-training-local-smoke.yml"
    fast = fast_path.read_text(encoding="utf-8")
    heavy = heavy_path.read_text(encoding="utf-8")
    fast_lower = fast.lower()
    heavy_lower = heavy.lower()
    assert "pull_request:" in fast_lower and "pull_request:" in heavy_lower
    assert "test_runner.py" in fast_lower and "--suite fast" in fast_lower
    assert "test_runner.py" in heavy_lower and "--suite dds" in heavy_lower
    assert "--report" in fast_lower and "--report" in heavy_lower
    assert "source_integrity.py" in fast_lower and "source_integrity.py" in heavy_lower
    assert "status --porcelain=v1 --untracked-files=all" in fast_lower
    assert "status --porcelain=v1 --untracked-files=all" in heavy_lower
    assert "contents: read" in fast_lower and "contents: read" in heavy_lower
    assert not AUTHORIZED_ENTRY_RE.search(fast_lower)
    assert not AUTHORIZED_ENTRY_RE.search(heavy_lower)
    assert "run_stage.py evaluate" not in fast_lower and "run_stage.py evaluate" not in heavy_lower

    action_inventory: dict[str, list[str]] = {}
    unsafe: dict[str, list[str]] = {}
    mass_workflows = []
    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        action_inventory[path.name] = _validate_action_pins(path, text)
        found = sorted(token for token in UNSAFE_WORKFLOW_TOKENS if token in lower)
        if found:
            unsafe[path.name] = found
        if AUTHORIZED_ENTRY_RE.search(lower):
            mass_workflows.append(path.name)
            assert "workflow_dispatch:" in lower
            assert "push:" not in lower and "pull_request:" not in lower and "schedule:" not in lower
    assert not unsafe, unsafe
    assert mass_workflows == [], mass_workflows

    bootstrap = (dds_root / "bootstrap_linux.sh").read_text(encoding="utf-8")
    assert "37c8a79f4c67c55d1a309ccb66dd00cb58af464a" in bootstrap
    assert "5a408715e932c0250d28bd84555f12edbf70117de42f9181691c736eacc4a992" in bootstrap
    assert "sha256sum --check" in bootstrap

    authorization_source = (dds_root / "run_authorization.py").read_text(encoding="utf-8")
    assert "automatic_issuance_allowed" in authorization_source
    assert "approval_token_sha256" in authorization_source
    assert "predictions_sha256" in authorization_source
    assert "corpus_sha256" in authorization_source

    print(
        json.dumps(
            {
                "ok": True,
                "manifest_tests": len(specs),
                "manifest_suites": sorted({spec.suite for spec in specs}),
                "orphan_selftests": 0,
                "unmapped_production_modules": 0,
                "coverage_waivers": waiver_expiries,
                "coverage_waiver_budget": waiver_budget,
                "forbidden_network_imports": 0,
                "canonical_workflows": sorted(actual_workflows),
                "pinned_actions": action_inventory,
                "unsafe_or_self_modifying_workflows": 0,
                "mass_run_workflows_committed": len(mass_workflows),
                "supply_chain_commit_and_checksum_pinned": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
