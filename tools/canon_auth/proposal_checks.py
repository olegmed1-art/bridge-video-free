"""Offline source checks; no network, secrets, production or actual workflow edits."""
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "tools/canon_auth/proposals/owner_probe"
BASE = "7ae456e543ae584112a31dda3e63a8a8cab64d63"
BRANCH = "review/canon-owner-probe-offline-20261004"
REVIEW_WORKFLOW = ".github/workflows/canon-owner-proposal-review.yml"
OWNER = ".github/workflows/native-maintenance-owner-attest.yml"
PROBE = "tools/canon_auth/owner_probe.py"


def events(workflow):
    return workflow.get("on", workflow.get(True, {}))


def validate_template(workflow, original):
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
    job = value["jobs"]["canon-owner-probe"]
    assert job["timeout-minutes"] == 3 and job["environment"] == "database-production"
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
    assert job["steps"][0]["with"] == {"ref": "${{ github.sha }}", "fetch-depth": 0, "persist-credentials": False}
    assert len([step for step in job["steps"] if "env" in step]) == 1
    assert job["steps"][-1]["run"] == "python -m tools.canon_auth.owner_probe"
    assert job["steps"][-1]["env"] == {
        "NATIVE_OWNER_DATABASE_URL": "${{ secrets.LIGHT_MAINTENANCE_DATABASE_URL || secrets.NEON_DATABASE_URL }}",
        "GH_TOKEN": "${{ github.token }}", "EXPECTED_MAIN": "${{ inputs.expected_main_sha }}",
        "EXPECTED_PROBE_SHA": "${{ inputs.expected_probe_sha }}", "OWNER_PROBE_SCOPE": "${{ inputs.probe_scope }}"}


workflow = (ROOT / "native-maintenance-owner-attest.yml.template").read_text()
original = (REPO / OWNER).read_text()
validate_template(workflow, original)
for bad in (workflow.replace("refs/heads/main", "refs/heads/feature/unreviewed"),
            workflow.replace("contents: read", "contents: write"),
            workflow.replace("inputs.expected_main_sha == github.sha", "true"),
            workflow.replace("github.triggering_actor == github.repository_owner", "true")):
    try:
        validate_template(bad, original)
    except AssertionError:
        pass
    else:
        raise AssertionError("Unreviewed template guard was admitted")
subprocess.run(["git", "apply", "--check", str(ROOT / "workflow.patch")], cwd=REPO, check=True)
assert not (ROOT / "preflight.py.template").exists()
assert not (ROOT / "publication-preflight.patch").exists()

subprocess.run(["git", "diff", "--check", BASE], cwd=REPO, check=True)

# No modifications to either live protection boundary.
for protected in (OWNER, "tools/canon_auth/preflight.py"):
    assert subprocess.check_output(["git", "diff", BASE, "--", protected], cwd=REPO) == b""
for path in (REPO / PROBE, REPO / "tools/canon_auth/resident_preflight.py"):
    source = path.read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute":
            assert isinstance(node.args[0], ast.Constant)
            assert node.args[0].value.strip().startswith(("SELECT", "SET TRANSACTION", "SET LOCAL"))
    assert not any(word in source for word in ("pilot_sql", "revoke_on_failure", "SET ROLE", "GRANT "))
assert 'BRANCH = "main"' in (REPO / PROBE).read_text()
assert 'MAIN = ' not in (REPO / PROBE).read_text()

files = [REPO / PROBE, REPO / "tools/canon_auth/resident_preflight.py",
         REPO / "tools/canon_auth/test_owner_probe.py", REPO / "tools/canon_auth/test_owner_resident.py",
         Path(__file__).resolve(), REPO / REVIEW_WORKFLOW]
files += sorted(path for path in ROOT.iterdir() if path.is_file() and path.name != "manifest.json")
expected = {path.relative_to(REPO).as_posix(): hashlib.sha256(path.read_text().encode()).hexdigest()
            for path in files}
manifest_path = ROOT / "manifest.json"
if "--write-manifest" in sys.argv:
    manifest_path.write_text(json.dumps({"source_base": BASE, "execution_branch": "main", "content_sha256": expected}, indent=2) + "\n")
else:
    manifest = json.loads(manifest_path.read_text())
    assert manifest["source_base"] == BASE and manifest["execution_branch"] == "main"
    assert manifest["content_sha256"] == expected

changed = subprocess.check_output(["git", "diff", "--name-only", BASE], cwd=REPO, text=True).splitlines()
changed += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO, text=True).splitlines()
assert set(changed) <= set(expected) | {manifest_path.relative_to(REPO).as_posix()}, changed
for path in files:
    text = path.read_text().replace('r"-----BEGIN .*PRIVATE KEY"', "scanner-definition")
    for forbidden in (r"-----BEGIN .*PRIVATE KEY", r"gh[pousr]_[A-Za-z0-9]{20,}",
                      r"github_pat_[A-Za-z0-9_]{20,}", r"AKIA[A-Z0-9]{16}",
                      r"postgres(?:ql)?://[^\s]+", r"C:\\Users\\"):
        assert not re.search(forbidden, text), path.name

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
        assert not matches("Canon owner proposal synthetic review", (hooks["workflow_run"] or {}).get("workflows", ["*"])), path.name
assert triggered == [REVIEW_WORKFLOW], triggered
vercel = json.loads((REPO / "vercel.json").read_text())
assert vercel["git"]["deploymentEnabled"]["*"] is False
assert vercel["git"]["deploymentEnabled"].get(BRANCH, False) is False
print(json.dumps({"status": "PASS", "sha": subprocess.check_output(["git", "rev-parse", "HEAD"],
                 cwd=REPO, text=True).strip(), "owner_probe_executed": False, "production_mutations": False,
                 "active_owner_workflow_unchanged": True, "active_preflight_unchanged": True,
                 "manifest_verified": True, "execution_branch": "main", "changed_files": len(set(changed))}, sort_keys=True))
