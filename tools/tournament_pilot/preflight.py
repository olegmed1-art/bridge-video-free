"""Audit publication surface and automatic workflows; no network or secrets."""
from fnmatch import fnmatchcase
from pathlib import Path
import re
import subprocess

import yaml

BASE = "1440920191e1778fb9a9ba24e6701937a1a7459c"
BRANCH = "test/tournament-shape-pilot-20261004"
WORKFLOW = ".github/workflows/tournament-shape-pilot.yml"
API_FILES = {"bridge_school_api/tournament_teacher.py", "bridge_school_api/ai_teacher.py"}


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
    assert all(p == WORKFLOW or p in API_FILES or p.startswith("tools/tournament_pilot/") for p in changed)
    assert all(Path(p).suffix in {".py",".json",".md",".yml",".html",".mjs"} for p in changed)
    forbidden = [r"-----BEGIN .*PRIVATE KEY", r"gh[pousr]_[A-Za-z0-9]{20,}",
                 r"github_pat_[A-Za-z0-9_]{20,}", r"AKIA[A-Z0-9]{16}",
                 r"postgres(?:ql)?://[^\s]+", r"C:\\Users\\", r"Sentinel_[a-z0-9]+"]
    for name in changed:
        content = (root / name).read_text(encoding="utf-8")
        # Scanner definitions above are not leaked private material.
        if name.endswith("preflight.py"):
            continue
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
            assert not matches("Tournament shape pilot", config.get("workflows", ["*"])), path.name
    assert triggered == [WORKFLOW], triggered
    workflow = yaml.safe_load((root / WORKFLOW).read_text(encoding="utf-8"))
    text = (root / WORKFLOW).read_text(encoding="utf-8")
    assert "secrets." not in text and "id-token" not in text
    assert workflow["permissions"] == {"contents":"read"}
    assert len(workflow["jobs"]) == 1
    job = workflow["jobs"]["offline-consumer"]
    assert job["timeout-minutes"] <= 20 and "environment" not in job
    rehearsal = next(s for s in job["steps"] if s.get("name") == "Authenticated screen, SQL gates and rollback")
    assert rehearsal["shell"] == "bash"  # Explicit bash enables -eo pipefail.
    assert "| tee" not in rehearsal["run"]
    assert "['status'] == 'PASS'" in rehearsal["run"]
    service = job["services"]["postgres"]
    assert set(job["services"]) == {"postgres"}
    assert service["image"] == "postgres:18"
    assert service["env"] == {"POSTGRES_DB":"tournament_rehearsal", "POSTGRES_USER":"postgres", "POSTGRES_HOST_AUTH_METHOD":"trust"}
    assert service["ports"] == ["127.0.0.1:55432:5432"]
    print(f"Publication scope: {len(changed)} minimal experiment files; baseline {BASE}")
    print("Automatic push workflow: tournament-shape-pilot only; one job, 10-minute cap; no secrets; one loopback disposable PostgreSQL service")
    print("Only existing teacher route and its bounded adapters allowed outside experiment; L1/SQL gates and deployment unchanged")


if __name__ == "__main__":
    main()
