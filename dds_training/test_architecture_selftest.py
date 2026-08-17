from __future__ import annotations

"""Meta-tests for the DDS test architecture itself.

These checks prevent a green result from a partial, self-modifying, supply-chain
floating, accidentally self-starting, or cache-fragile test system. They validate
the canonical manifest, prohibit network/API clients in the DDS Python layer,
enforce measured runtime coverage, verify read-only smoke workflows, keep the
historical pilot workflows archived/non-executable, protect immutable pilot
restoration for Stage 2, and require expensive DDS wheel bootstrap to be
separable from later evidence gates.
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
ARCHIVED_PILOT_WORKFLOWS = {
    "dds-training-pilot-start.yml",
    "dds-training-pilot-finish.yml",
}
PINNED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/cache/restore": "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
    "actions/cache/save": "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
}
EXPECTED_DDS_COMMIT = "37c8a79f4c67c55d1a309ccb66dd00cb58af464a"
PILOT_RUN_ID = "31909124383"
PILOT_ARTIFACT = "dds-pilot-10k-complete-state-31909124383"
PILOT_TGZ_SHA256 = "f472b1361c879e890ffeab0e9184736a7dcd39cbc3a4c33541eb7156df9176d6"
PILOT_RAW_SHA256 = "8a21cf06ab7ac424ee0f245ccf274e6d6f4f7135fa9b8c4c0e52c595c0da5996"
PILOT_DB_SHA256 = "c01cbe9e143198a083df8ab51ea2113ec71688b2f23d93dcfb4b1de9c29f2127"


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


def assert_pinned_actions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    floating = re.findall(r"uses:\s*(actions/[^@\s]+)@v\d+", text)
    assert not floating, (path.name, floating)
    for line in text.splitlines():
        stripped = line.strip()
        if "uses: actions/" not in stripped:
            continue
        match = re.search(r"uses:\s*(actions/[^@\s]+)@([0-9a-f]{40})", stripped)
        assert match, (path.name, stripped)
        action, sha = match.groups()
        expected = PINNED_ACTIONS.get(action)
        if expected is not None:
            assert sha == expected, (path.name, action, sha, expected)
    if "uses: actions/checkout@" in text:
        assert "persist-credentials: false" in text, path.name


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
    runtime_coverage = coverage.get("runtime_coverage", {})
    assert set(runtime_coverage) == {"fast", "dds"}, runtime_coverage
    for suite, thresholds in runtime_coverage.items():
        assert float(thresholds["minimum_overall_percent"]) > 0, suite
        assert float(thresholds["minimum_module_percent"]) > 0, suite
        ratio = float(thresholds["minimum_module_execution_ratio"])
        assert 0 < ratio <= 1, (suite, ratio)

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
    present_forbidden = sorted(name for name in forbidden_workflows if (workflows / name).exists())
    assert not present_forbidden, present_forbidden

    dds_workflows = sorted([*workflows.glob("dds*.yml"), *workflows.glob("dds*.yaml")])
    unsafe: dict[str, list[str]] = {}
    for path in dds_workflows:
        text = path.read_text(encoding="utf-8").lower()
        found = sorted(token for token in UNSAFE_WORKFLOW_TOKENS if token in text)
        if found:
            unsafe[path.name] = found
        assert_pinned_actions(path)
    assert not unsafe, unsafe

    fast_path = workflows / "dds-training-v23-smoke.yml"
    heavy_path = workflows / "dds-training-local-smoke.yml"
    prep_path = workflows / "dds-main-30k-prepare.yml"
    assert fast_path.is_file() and heavy_path.is_file() and prep_path.is_file()
    fast = fast_path.read_text(encoding="utf-8")
    heavy = heavy_path.read_text(encoding="utf-8")
    prep = prep_path.read_text(encoding="utf-8")
    fast_lower = fast.lower()
    heavy_lower = heavy.lower()
    prep_lower = prep.lower()

    assert "pull_request:" in fast
    assert "test_runner.py" in fast and "--suite fast" in fast
    assert "test_runner.py" in heavy and "--suite dds" in heavy
    assert "--report" in fast and "--report" in heavy
    assert "git diff --exit-code" in fast and "git diff --exit-code" in heavy
    assert "contents: read" in fast_lower and "contents: read" in heavy_lower
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in fast
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in heavy

    for workflow_name, text, suite in (
        (fast_path.name, fast, "fast"),
        (heavy_path.name, heavy, "dds"),
    ):
        assert "DDS_COVERAGE_ROOT" in text, workflow_name
        assert "DDS_COVERAGE_DIR" in text, workflow_name
        assert "coverage_report.py" in text, workflow_name
        assert f"--suite {suite}" in text, workflow_name
        assert "--fail-on-error" in text, workflow_name

    for workflow_name, text in ((fast_path.name, fast), (heavy_path.name, heavy)):
        runner_guard = text.index("python test_runner_selftest.py")
        architecture_guard = text.index("python test_architecture_selftest.py")
        manifest_check = text.index("--check-only")
        assert runner_guard < manifest_check, workflow_name
        assert architecture_guard < manifest_check, workflow_name

    archive_state: dict[str, dict[str, object]] = {}
    for name in ARCHIVED_PILOT_WORKFLOWS:
        path = workflows / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        header = workflow_header(text)
        automatic = asserted_trigger_names(header)
        assert not (automatic & FORBIDDEN_AUTOMATIC_TRIGGERS), (name, automatic)
        assert "workflow_dispatch:" in header, name
        assert "run_stage.py" not in text, name
        assert "--start" not in text, name
        assert "--open-sealed" not in text, name
        assert "contents: read" in header.lower(), name
        archive_state[name] = {
            "automatic_triggers": sorted(automatic),
            "manual_dispatch_only": True,
            "executable_training": False,
        }

    # Stage-2 preparation may run as a PR/readiness regression because it never starts
    # the mass evaluator. It must restore the exact immutable completed pilot artifact,
    # expand the same deterministic corpus, lock fresh TRAIN predictions before DDS
    # preflight, keep holdouts closed and upload a durable prepared-state artifact.
    assert "pull_request:" in prep and "workflow_dispatch:" in prep
    assert "actions: read" in prep_lower and "contents: read" in prep_lower
    assert "run_stage.py evaluate" not in prep
    assert "--start" not in prep
    for token in (PILOT_RUN_ID, PILOT_ARTIFACT, PILOT_TGZ_SHA256, PILOT_RAW_SHA256, PILOT_DB_SHA256):
        assert token in prep, token
    assert "run_stage.py prepare --stage main --out work/pilot" in prep
    assert "locked_predictions_main_train_adaptive.jsonl" in prep
    assert prep.index("locked_predictions_main_train_adaptive.jsonl") < prep.index("prepare_stage2.py --work work/pilot")
    assert "stage2_readiness.py" in prep and "--require main_train" in prep
    assert "assert ready['holdout']['ready'] is False" in prep
    assert "assert ready['skill_claim']['ready'] is False" in prep
    assert "assert db.execute(\"select count(*) from dds_results\").fetchone()[0] == 22497" in prep
    assert "dds-main-30k-prepared-state.tgz" in prep

    bootstrap = (dds_root / "bootstrap_linux.sh").read_text(encoding="utf-8")
    assert EXPECTED_DDS_COMMIT in bootstrap
    assert "DDS_RUN_PREFLIGHT" in bootstrap
    assert "DDS_REQUIRE_WHEEL_CACHE" in bootstrap

    # Expensive verified wheel construction is intentionally separated from later
    # preflight/test gates. That lets the workflow record .cache_rebuilt and save the
    # verified wheel even if a downstream evidence gate later fails.
    cold_bootstrap = "DDS_RUN_PREFLIGHT=0 bash dds_training/bootstrap_linux.sh"
    warm_bootstrap = "DDS_REQUIRE_WHEEL_CACHE=1 DDS_RUN_PREFLIGHT=0 bash dds_training/bootstrap_linux.sh"
    assert cold_bootstrap in heavy
    assert warm_bootstrap in heavy
    assert heavy.index(cold_bootstrap) < heavy.index("Record whether DDS wheel cache was rebuilt")
    assert heavy.index("Record whether DDS wheel cache was rebuilt") < heavy.index("Verify deterministic DDS golden preflight twice")
    assert heavy.count("preflight.py --quick") >= 2
    assert "dds-preflight-repeatability.json" in heavy
    assert "canonical_sha256" in heavy
    assert ".cache_rebuilt" in heavy
    assert "github.run_id" in heavy
    assert "hashFiles('dds_training/bootstrap_linux.sh')" in heavy
    assert "if: always() && steps.dds-cache-state.outputs.rebuilt == 'true'" in heavy

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
                "runtime_coverage_suites": sorted(runtime_coverage),
                "forbidden_network_imports": 0,
                "unsafe_self_modifying_test_workflows": 0,
                "floating_dds_action_dependencies": 0,
                "archived_pilot_workflows": archive_state,
                "main_30k_preparation_restores_immutable_pilot": True,
                "main_30k_preparation_mass_evaluation": False,
                "golden_preflight_repetitions_required": 2,
                "bootstrap_preflight_deferred_until_evidence_step": True,
                "verified_wheel_cache_replay_required": True,
                "verified_wheel_cache_saved_after_downstream_failure": True,
                "pinned_dds_commit": EXPECTED_DDS_COMMIT,
                "canonical_fast_workflow": fast_path.name,
                "canonical_dds_workflow": heavy_path.name,
                "canonical_main_preparation_workflow": prep_path.name,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
