"""Audit publication surface and automatic workflows; no network or secrets."""
from fnmatch import fnmatchcase
from pathlib import Path
import re
import ast
import subprocess

import yaml

BASE = "f513afc4a1a4cae325b7baeeef5f4848dd29a7a5"
BRANCH = "review/canon-pilot-integration-f513-20261004"
WORKFLOW = ".github/workflows/canon-pilot-integration.yml"
API_FILES = {"pyproject.toml", ".vercelignore"}


def matches(value, patterns):
    included = False
    for pattern in patterns:
        negative = pattern.startswith("!")
        if fnmatchcase(value, pattern.lstrip("!")):
            included = not negative
    return included


def main():
    root = Path(__file__).resolve().parents[2]
    changed = subprocess.check_output(
        ["git","diff","--name-only",BASE], cwd=root, text=True).splitlines()
    changed += subprocess.check_output(
        ["git","ls-files","--others","--exclude-standard"], cwd=root, text=True).splitlines()
    changed = sorted(set(changed))
    assert changed, "No experiment changes"
    assert set(changed) == set(["tools/canon_auth/disposable_preflight.py","tools/canon_auth/resident_rehearsal.py","tools/canon_auth/rehearse_sql.py","tools/canon_auth/test_resident.py","tools/canon_auth/teacher_behavior.py",".github/workflows/canon-pilot-integration.yml","tools/tournament_pilot/bootstrap.py","tools/canon_auth/build_once.py","tools/canon_auth/test_build_once.py","tools/canon_auth/test_teacher_behavior.py","tools/canon_auth/INTEGRATION.md","tools/canon_auth/integration_checks.py"])
    assert all(p == ".vercelignore" or Path(p).suffix in {".py",".md",".yml",".toml"} for p in changed)
    forbidden = [r"-----BEGIN .*PRIVATE KEY", r"gh[pousr]_[A-Za-z0-9]{20,}",
                 r"github_pat_[A-Za-z0-9_]{20,}", r"AKIA[A-Z0-9]{16}",
                 r"postgres(?:ql)?://[^\s]+", r"C:\\Users\\", r"Sentinel_[a-z0-9]+"]
    for name in changed:
        content = (root / name).read_text(encoding="utf-8")
        # Scanner definitions above are not leaked private material.
        if name == "tools/canon_auth/integration_checks.py":
            # Remove only this audit's literal scanner-pattern assignment.
            node = next(n for n in ast.walk(ast.parse(content))
                        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name)
                            and t.id == "forbidden" for t in n.targets))
            lines = content.splitlines(keepends=True)
            content = "".join(lines[:node.lineno - 1] + lines[node.end_lineno:])
        if name == "tools/canon_auth/test_auth.py":
            # Exact f-string fixture, not an actual DSN or blanket file exclusion.
            content = content.replace("postgresql://{d.db.EXPECTED_PRINCIPAL}:{SECRET}@{host}/neondb?{query}", "synthetic-dsn-fixture")
        assert not any(re.search(pattern, content) for pattern in forbidden), name
    triggered = []
    for path in (root / ".github/workflows").glob("*.y*ml"):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        events = workflow.get("on", workflow.get(True, {}))
        if isinstance(events, str):
            events = {events: None}
        elif isinstance(events, list):
            events = dict.fromkeys(events)
        if "push" in events:
            config = events["push"] or {}
            branch_ok = ("branches" not in config or matches(BRANCH, config["branches"]))
            branch_ok &= not matches(BRANCH, config.get("branches-ignore", []))
            path_ok = ("paths" not in config or any(matches(p, config["paths"]) for p in changed))
            path_ok &= not ("paths-ignore" in config and all(matches(p, config["paths-ignore"]) for p in changed))
            if branch_ok and path_ok:
                triggered.append(path.relative_to(root).as_posix())
        if "create" in events:
            raise AssertionError(f"Branch-create side effect: {path.name}")
        if "workflow_run" in events:
            config = events["workflow_run"] or {}
            assert not matches("Canon pilot integration", config.get("workflows", ["*"])), path.name
    assert triggered == [WORKFLOW], triggered
    protected = [".github/workflows/native-maintenance-owner-attest.yml","tools/canon_auth/owner_probe.py","tools/canon_auth/resident_preflight.py","tools/canon_auth/test_owner_probe.py","tools/canon_auth/test_owner_resident.py","tools/canon_auth/installation_checks.py","tools/canon_auth/installation/manifest.json","tools/canon_auth/pilot_sql.py","tools/tournament_pilot/package.json","bridge_school_api/tournament_teacher.py","bridge_school_api/db.py","tools/canon_auth/vercel_validator.py","tools/canon_auth/preflight.py"]
    for name in protected:
        original = subprocess.check_output(["git", "show", BASE + ":" + name], cwd=root)
        assert (root / name).read_bytes() == original, name
    deployment = __import__("json").loads((root / "vercel.json").read_text())
    assert deployment["git"]["deploymentEnabled"] == {"main": True, "preview": False, "*": False}
    workflow = yaml.safe_load((root / WORKFLOW).read_text(encoding="utf-8"))
    text = (root / WORKFLOW).read_text(encoding="utf-8")
    assert "secrets." not in text and "id-token" not in text
    assert workflow["permissions"] == {"contents":"read"}
    assert len(workflow["jobs"]) == 1
    job = workflow["jobs"]["offline-consumer"]
    assert job["timeout-minutes"] == 10 and "environment" not in job
    rehearsal = next(s for s in job["steps"] if s.get("name") == "Staged SQL and authenticated API rehearsal")
    assert rehearsal["shell"] == "bash"  # Explicit bash enables -eo pipefail.
    assert "| tee" not in rehearsal["run"]
    assert "['status'] == 'PASS'" in rehearsal["run"]
    service = job["services"]["postgres"]
    assert set(job["services"]) == {"postgres"}
    assert service["image"] == "postgres:18"
    assert service["env"] == {"POSTGRES_DB":"tournament_rehearsal", "POSTGRES_USER":"postgres", "POSTGRES_HOST_AUTH_METHOD":"trust"}
    assert service["ports"] == ["127.0.0.1:55432:5432"]
    print(f"Publication scope: {len(changed)} acceptance files; baseline {BASE}")
    print("Automatic push workflow: canon-pilot-integration only; one job, 10-minute cap; no secrets; one loopback disposable PostgreSQL service")
    print("Qualified owner path, application/L1/SQL compiler and gates unchanged; expired live validator preserved; no production execution")


if __name__ == "__main__":
    main()
