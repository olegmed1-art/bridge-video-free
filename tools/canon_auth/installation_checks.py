"""Audit the actual installation delta; never connect, dispatch or read secrets."""
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "tools/canon_auth/installation"
BASE = "4f2c7a98d0b7e1e5f48ac781332442becea302dd"
BRANCH = "review/canon-owner-installation-20261004"
REVIEW_WORKFLOW = ".github/workflows/canon-owner-installation-review.yml"
OWNER = ".github/workflows/native-maintenance-owner-attest.yml"
PROBE = "tools/canon_auth/owner_probe.py"


def events(workflow):
    return workflow.get("on", workflow.get(True, {}))


def validate_installed_workflow(workflow, original):
    value = yaml.safe_load(workflow)
    old = yaml.safe_load(original)
    assert set(events(value)) == {"pull_request", "workflow_dispatch"}
    assert value["permissions"] == old["permissions"] == {"contents": "read"}
    assert set(value["jobs"]) == {"contract", "attest", "canon-owner-probe"}
    inputs = events(value)["workflow_dispatch"]["inputs"]
    assert inputs["expected_main_sha"] == events(old)["workflow_dispatch"]["inputs"]["expected_main_sha"]
    assert inputs["probe_scope"]["default"] == "maintenance"
    assert inputs["probe_scope"]["options"] == ["maintenance", "canon-readonly"]
    maintenance = dict(value["jobs"]["attest"])
    guard = maintenance.pop("if")
    old_maintenance = dict(old["jobs"]["attest"])
    old_guard = old_maintenance.pop("if")
    assert maintenance == old_maintenance
    # Keep every original main/owner/manual/exact SHA check, adding scope only.
    assert re.sub(r"\s+", " ", guard.replace("inputs.probe_scope == 'maintenance' &&", "")).strip() == re.sub(r"\s+", " ", old_guard).strip()
    contract = value["jobs"]["contract"]
    assert contract["timeout-minutes"] == 5
    assert "environment" not in contract and "secrets." not in str(contract)
    assert set(contract) == {"runs-on", "timeout-minutes", "steps"}
    old_steps = old["jobs"]["contract"]["steps"]
    assert contract["runs-on"] == old["jobs"]["contract"]["runs-on"]
    assert contract["steps"][0] == {**old_steps[0], "with": {**old_steps[0]["with"], "fetch-depth": 0}}
    assert contract["steps"][1] == old_steps[1]
    assert contract["steps"][2] == {"run": "python -m pip install --disable-pip-version-check 'psycopg[binary]==3.3.4' pytest==8.4.2 PyYAML==6.0.2"}
    assert contract["steps"][3] == {"run": "python -m pytest -q tools/canon_auth/test_owner_probe.py tools/canon_auth/test_owner_resident.py tools/canon_auth/test_installed_owner_workflow.py"}
    assert contract["steps"][4:] == old_steps[3:]
    job = value["jobs"]["canon-owner-probe"]
    assert job["timeout-minutes"] == 3 and job["environment"] == "database-production"
    assert job["needs"] == "contract" and job["runs-on"] == old["jobs"]["attest"]["runs-on"]
    assert set(job) == {"needs", "if", "environment", "runs-on", "timeout-minutes", "steps"}
    for guard in ("github.repository == 'olegmed1-art/bridge-video-free'",
                  "github.ref == 'refs/heads/main'",
                  "github.actor == github.repository_owner",
                  "github.triggering_actor == github.repository_owner",
                  "github.event_name == 'workflow_dispatch'",
                  "inputs.probe_scope == 'canon-readonly'",
                  "inputs.expected_probe_sha == github.sha",
                  "inputs.expected_main_sha == github.sha"):
        assert guard in job["if"]
    assert "||" not in job["if"]
    assert job["steps"][0] == {**old_steps[0], "with": {"ref": "${{ github.sha }}", "fetch-depth": 0, "persist-credentials": False}}
    assert len([step for step in job["steps"] if "env" in step]) == 1
    assert job["steps"][-1]["run"] == "python -m tools.canon_auth.owner_probe"
    assert job["steps"][-1]["env"] == {
        "NATIVE_OWNER_DATABASE_URL": "${{ secrets.LIGHT_MAINTENANCE_DATABASE_URL || secrets.NEON_DATABASE_URL }}",
        "GH_TOKEN": "${{ github.token }}", "EXPECTED_MAIN": "${{ inputs.expected_main_sha }}",
        "EXPECTED_PROBE_SHA": "${{ inputs.expected_probe_sha }}", "OWNER_PROBE_SCOPE": "${{ inputs.probe_scope }}"}

    assert len(job["steps"]) == 4
    assert job["steps"][1] == old_steps[1]
    assert job["steps"][2] == old_steps[2]
    assert set(job["steps"][-1]) == {"name", "env", "run"}


def baseline():
    return subprocess.check_output(["git", "show", BASE + ":" + OWNER], cwd=REPO, text=True)


def main():
    workflow = (REPO / OWNER).read_text()
    validate_installed_workflow(workflow, baseline())
    subprocess.run(["git", "diff", "--check", BASE], cwd=REPO, check=True)
    assert subprocess.check_output(["git", "diff", BASE, "--", "tools/canon_auth/preflight.py"], cwd=REPO) == b""
    assert subprocess.check_output(["git", "diff", BASE, "--", "ops/native_maintenance_owner_attest.py"], cwd=REPO) == b""
    assert subprocess.check_output(["git", "diff", BASE, "--", OWNER], cwd=REPO) != b""
    for path in (REPO / PROBE, REPO / "tools/canon_auth/resident_preflight.py"):
        source = path.read_text()
        assert not any(word in source for word in ("pilot_sql", "revoke_on_failure", "SET ROLE", "GRANT "))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "transaction":
                    assert any(k.arg == "force_rollback" and isinstance(k.value, ast.Constant)
                               and k.value.value is True for k in node.keywords)
                if node.func.attr == "execute":
                    assert isinstance(node.args[0], ast.Constant)
                    sql = node.args[0].value
                    assert sql.strip().startswith(("SELECT", "SET TRANSACTION", "SET LOCAL"))
                    assert not re.search(r"(?<![.\w])(current_setting|current_database|has_[a-z_]+_privilege|unnest)\s*\(", sql)
                    assert "::text[]" not in sql and "gates(uuid)" not in sql

    files = [OWNER, REVIEW_WORKFLOW, PROBE, "tools/canon_auth/resident_preflight.py",
             "tools/canon_auth/test_owner_probe.py", "tools/canon_auth/test_owner_resident.py",
             "tools/canon_auth/installation_checks.py", "tools/canon_auth/test_installed_owner_workflow.py",
             "tools/canon_auth/installation/README.md"]
    expected = {name: hashlib.sha256((REPO / name).read_text().encode()).hexdigest() for name in files}
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["installation_base"] == BASE and manifest["execution_branch"] == "main"
    assert manifest["content_sha256"] == expected
    changed = subprocess.check_output(["git", "diff", "--name-only", BASE], cwd=REPO, text=True).splitlines()
    changed += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO, text=True).splitlines()
    assert set(changed) == set(files) | {manifest_path.relative_to(REPO).as_posix()}, changed
    for name in files:
        text = (REPO / name).read_text().replace('r"-----BEGIN .*PRIVATE KEY"', "scanner-definition")
        for forbidden in (r"-----BEGIN .*PRIVATE KEY", r"gh[pousr]_[A-Za-z0-9]{20,}",
                          r"github_pat_[A-Za-z0-9_]{20,}", r"AKIA[A-Z0-9]{16}",
                          r"postgres(?:ql)?://[^\s]+", r"C:\\Users\\"):
            assert not re.search(forbidden, text), name

    review_text = (REPO / REVIEW_WORKFLOW).read_text()
    review = yaml.safe_load(review_text)
    assert review["permissions"] == {"contents": "read"}
    assert set(events(review)) == {"push"}
    assert events(review)["push"]["branches"] == [BRANCH]
    assert "secrets." not in review_text and "id-token" not in review_text
    assert len(review["jobs"]) == 1
    job = next(iter(review["jobs"].values()))
    assert job["timeout-minutes"] <= 10 and "environment" not in job and "services" not in job
    assert "python -m tools.canon_auth.owner_probe" not in review_text
    from .preflight import matches
    triggered = []
    for path in (REPO / ".github/workflows").glob("*.y*ml"):
        hooks = events(yaml.safe_load(path.read_text()))
        if isinstance(hooks, str): hooks = {hooks: None}
        if isinstance(hooks, list): hooks = {name: None for name in hooks}
        assert "create" not in hooks, path.name
        if "push" in hooks:
            config = hooks["push"] or {}
            branch_ok = matches(BRANCH, config.get("branches", ["*"]))
            branch_ok &= not matches(BRANCH, config.get("branches-ignore", []))
            path_ok = "paths" not in config or any(matches(name, config["paths"]) for name in changed)
            path_ok &= not ("paths-ignore" in config and all(matches(name, config["paths-ignore"]) for name in changed))
            if branch_ok and path_ok: triggered.append(path.relative_to(REPO).as_posix())
        if "workflow_run" in hooks:
            assert not matches("Canon owner installation synthetic review", (hooks["workflow_run"] or {}).get("workflows", ["*"])), path.name
    assert triggered == [REVIEW_WORKFLOW], triggered
    vercel = json.loads((REPO / "vercel.json").read_text())
    assert vercel["git"]["deploymentEnabled"]["*"] is False
    assert vercel["git"]["deploymentEnabled"].get(BRANCH, False) is False
    print(json.dumps({"status": "PASS", "sha": subprocess.check_output(["git", "rev-parse", "HEAD"],
                     cwd=REPO, text=True).strip(), "installed_workflow_verified": True,
                     "existing_maintenance_preserved": True, "active_owner_workflow_changed": True,
                     "publication_gate_unchanged": True, "owner_probe_executed": False,
                     "production_mutations": False, "manifest_verified": True,
                     "changed_files": len(set(changed))}, sort_keys=True))


if __name__ == "__main__":
    main()
