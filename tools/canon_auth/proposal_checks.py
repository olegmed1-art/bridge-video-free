"""Offline proposal checks; no network, secrets, production or real workflow edits."""
import ast
import hashlib
import json
import runpy
import shutil
import subprocess
import tempfile
import sys
import re
from pathlib import Path
from unittest.mock import patch

import yaml

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "tools/canon_auth/proposals/owner_probe"
BASE = "2c7445aecdfe7e657797fbc0ccc63bf89920a8b4"
BRANCH = "review/canon-owner-probe-offline-20261004"
REVIEW_WORKFLOW = ".github/workflows/canon-owner-proposal-review.yml"
OWNER = ".github/workflows/native-maintenance-owner-attest.yml"
PROBE = "tools/canon_auth/owner_probe.py"
workflow = (ROOT / "native-maintenance-owner-attest.yml.template").read_text()
value = yaml.safe_load(workflow)
events = value.get("on", value.get(True))
assert set(events) == {"pull_request", "workflow_dispatch"}
assert value["permissions"] == {"contents": "read"}
contract = value["jobs"]["contract"]
assert contract["timeout-minutes"] == 5
assert "fastapi==0.141.1" in str(contract["steps"])
assert "secrets." not in str(contract)
job = value["jobs"]["canon-owner-probe"]
assert job["timeout-minutes"] == 3 and job["environment"] == "database-production"
for guard in ("workflow_dispatch", "inputs.probe_scope == 'canon-readonly'",
              "github.triggering_actor == github.repository_owner",
              "inputs.expected_probe_sha == github.sha",
              "711ddd648fa74f2b903f9d7127dadc412f94b277",
              "refs/heads/test/canon-acceptance-cf6091-20261004"):
    assert guard in job["if"]
assert "services" not in job and "strategy" not in job
assert job["steps"][0]["with"]["persist-credentials"] is False
assert len([step for step in job["steps"] if "env" in step]) == 1
assert job["steps"][-1]["run"] == "python -m tools.canon_auth.owner_probe"
assert job["steps"][-1]["env"]["NATIVE_OWNER_DATABASE_URL"] == (
    "${{ secrets.LIGHT_MAINTENANCE_DATABASE_URL || secrets.NEON_DATABASE_URL }}")
for patch_file in ("workflow.patch", "publication-preflight.patch"):
    flags = ["--unidiff-zero"] if patch_file == "publication-preflight.patch" else []
    subprocess.run(["git", "apply", *flags, "--check", str(ROOT / patch_file)], cwd=REPO, check=True)
source = (REPO / PROBE).read_text()
for node in ast.walk(ast.parse(source)):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute":
        assert isinstance(node.args[0], ast.Constant)
        assert node.args[0].value.startswith(("SELECT", "SET TRANSACTION", "SET LOCAL"))
assert not any(word in source for word in ("pilot_sql", "revoke_on_failure", "SET ROLE", "GRANT "))

# Run the proposed actual scope audit on a temporary file tree, mocking ONLY
# git's changed-file inventory. Actual YAML/hash/workflow rules still execute.
with tempfile.TemporaryDirectory(prefix="canon-owner-probe-offline-") as temporary:
    temporary = Path(temporary)
    shutil.copytree(REPO / ".github/workflows", temporary / ".github/workflows")
    location = temporary / "tools/canon_auth"
    location.mkdir(parents=True)
    (temporary / OWNER).write_text(workflow)
    (temporary / PROBE).write_text(source)
    preflight = location / "preflight.py"
    preflight.write_text((ROOT / "preflight.py.template").read_text())
    audit = runpy.run_path(str(preflight))["main"]
    def inventory(args, **kwargs):
        return OWNER + "\n" + PROBE + "\n" if args[1] == "diff" else ""
    with patch("subprocess.check_output", inventory):
        audit()
        (temporary / OWNER).write_text(workflow + "\n# unreviewed delta\n")
        try:
            audit()
        except AssertionError as exc:
            assert "exact reviewed" in str(exc)
        else:
            raise AssertionError("Unreviewed owner workflow was admitted")

# Actual protected workflow and actual publication gate are byte-identical to base.
for protected in (OWNER, "tools/canon_auth/preflight.py"):
    assert subprocess.check_output(["git", "diff", BASE, "--", protected], cwd=REPO) == b""

files = [REPO / PROBE, REPO / "tools/canon_auth/test_owner_probe.py",
         Path(__file__).resolve(), REPO / REVIEW_WORKFLOW]
files += sorted(path for path in ROOT.iterdir() if path.is_file() and path.name != "manifest.json")
expected = {path.relative_to(REPO).as_posix(): hashlib.sha256(path.read_text().encode()).hexdigest()
            for path in files}
manifest_path = ROOT / "manifest.json"
if "--write-manifest" in sys.argv:
    manifest_path.write_text(json.dumps({"base_candidate": BASE, "frozen_main":
        "711ddd648fa74f2b903f9d7127dadc412f94b277", "content_sha256": expected}, indent=2) + "\n")
else:
    manifest = json.loads(manifest_path.read_text())
    assert manifest["base_candidate"] == BASE and manifest["content_sha256"] == expected

changed = subprocess.check_output(["git", "diff", "--name-only", BASE], cwd=REPO, text=True).splitlines()
changed += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO, text=True).splitlines()
assert set(changed) <= set(expected) | {manifest_path.relative_to(REPO).as_posix()}, changed
for path in files:
    text = path.read_text()
    # Exact code literal is a scanner definition, not a PEM header.
    text = text.replace('r"-----BEGIN .*PRIVATE KEY"', "scanner-definition")
    text = text.replace("postgresql://{d.db.EXPECTED_PRINCIPAL}:{SECRET}@{host}/neondb?{query}", "synthetic-dsn-fixture")
    for forbidden in (r"-----" + r"BEGIN .*PRIVATE KEY", r"gh[pousr]_[A-Za-z0-9]{20,}",
                      r"github_pat_[A-Za-z0-9_]{20,}", r"AKIA[A-Z0-9]{16}",
                      r"postgres(?:ql)?://[^\s]+", r"C:\\Users\\"):
        assert not re.search(forbidden, text), path.name
    assert not re.search(r"(?:misty-poetry|br-aged-mud|ep-noisy-pine)-[A-Za-z0-9]+", text), path.name

# Review CI is synthetic-only: no secret refs, environment, dispatch, owner launch.
review_text = (REPO / REVIEW_WORKFLOW).read_text()
review = yaml.safe_load(review_text)
assert review["permissions"] == {"contents": "read"}
review_events = review.get("on", review.get(True))
assert set(review_events) == {"push"}
assert review_events["push"]["branches"] == [BRANCH]
assert "secrets." not in review_text and "id-token" not in review_text
assert len(review["jobs"]) == 1
review_job = next(iter(review["jobs"].values()))
assert review_job["timeout-minutes"] <= 10
assert "environment" not in review_job and "services" not in review_job
assert "python -m tools.canon_auth.owner_probe" not in review_text
# Reconcile all automatic workflow events for this exact review branch/delta.
from .preflight import matches
triggered = []
for path in (REPO / ".github/workflows").glob("*.y*ml"):
    workflow = yaml.safe_load(path.read_text())
    events = workflow.get("on", workflow.get(True, {}))
    if isinstance(events, str): events = {events: None}
    if isinstance(events, list): events = dict.fromkeys(events)
    assert "create" not in events, path.name
    if "push" in events:
        config = events["push"] or {}
        branch_ok = ("branches" not in config or matches(BRANCH, config["branches"]))
        branch_ok &= not matches(BRANCH, config.get("branches-ignore", []))
        path_ok = ("paths" not in config or any(matches(name, config["paths"]) for name in changed))
        path_ok &= not ("paths-ignore" in config and all(matches(name, config["paths-ignore"]) for name in changed))
        if branch_ok and path_ok: triggered.append(path.relative_to(REPO).as_posix())
    if "workflow_run" in events:
        assert not matches("Canon owner proposal synthetic review", (events["workflow_run"] or {}).get("workflows", ["*"])), path.name
assert triggered == [REVIEW_WORKFLOW], triggered
vercel = json.loads((REPO / "vercel.json").read_text())
assert vercel["git"]["deploymentEnabled"]["*"] is False
assert vercel["git"]["deploymentEnabled"].get(BRANCH, False) is False
print(json.dumps({"status": "PASS", "sha": subprocess.check_output(["git", "rev-parse", "HEAD"],
                 cwd=REPO, text=True).strip(), "owner_probe_executed": False,
                 "production_mutations": False, "active_owner_workflow_unchanged": True,
                 "active_preflight_unchanged": True, "manifest_verified": True,
                 "workflow_scope_hash_verified": True, "changed_files": len(set(changed))}, sort_keys=True))
