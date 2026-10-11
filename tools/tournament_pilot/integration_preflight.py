"""PR2147-only integration guard; no network, credentials or database access."""
from collections import Counter
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import subprocess
import sys

BASELINE = "a0127356cd0ce30d0189e3208918e14b4b981141"
BASELINE_TREE = "5a712d367f4a1f06034b3fafe021b229646e73fe"
MAIN = "d7314c60644ccbbf2f269367810ccc3325b76d22"
REPOSITORY = "olegmed1-art/bridge-video-free"
BRANCH = "codex/shape-generator-v2-20261011"
WORKFLOW = ".github/workflows/tournament-shape-pilot.yml"
ALLOWED = {WORKFLOW, "tools/tournament_pilot/integration_preflight.py",
           "tools/tournament_pilot/test_integration_preflight.py",
           "bridge_school_api/db.py", "tests/test_api_dsn_contract.py",
           "tests/test_runtime_app_incident_probe.py"}
ARTIFACTS = ("tools/tournament_pilot/package.json", "tournament-first-binding-evidence.json")
MAX_ARTIFACT_BYTES = 131072
ASSESSMENT_VERSION = "tournament-call-assessment-v2"
ROW_BUDGET = {"public.source": 1, "public.knowledge_item": 2, "public.knowledge_version": 2,
              "public.knowledge_version_source": 2, "bidding.rule": 2, "bidding.rule_test": 8,
              "bidding.rule_test_run": 8, "ai.decision_position": 1, "public.canon_activation": 4,
              "bidding.runtime_activation": 4, "bidding.ingestion_run": 1, "bidding.ingestion_event": 5}
JOB_IF = ("github.event_name == 'push' || "
          "(github.event.pull_request.number == 2147 && "
          "github.event.pull_request.head.repo.full_name == github.repository && "
          "github.event.pull_request.head.ref == 'codex/shape-generator-v2-20261011')")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_event(event):
    pr = event["pull_request"]
    require(event["number"] == 2147 and pr["number"] == 2147, "PR2147_ONLY")
    require(event["repository"]["full_name"] == REPOSITORY, "REPOSITORY_MISMATCH")
    for side in ("head", "base"):
        require(pr[side]["repo"]["full_name"] == REPOSITORY, "FORK_NOT_ALLOWED")
        require(re.fullmatch(r"[0-9a-f]{40}", pr[side]["sha"]), "INVALID_SHA")
    require(pr["head"]["ref"] == BRANCH, "BRANCH_MISMATCH")
    require(pr["base"]["ref"] == "main" and pr["base"]["sha"] == MAIN, "BASE_CHANGED_REVIEW_REQUIRED")
    return pr["head"]["sha"]


def added_content(before, after):
    """Scan new material; the exact reviewed baseline already pins old bytes."""
    old, new = before.splitlines(), after.splitlines()
    matcher = SequenceMatcher(a=old, b=new, autojunk=False)
    return "\n".join(line for tag, _, _, start, end in matcher.get_opcodes()
                     if tag in {"insert", "replace"} for line in new[start:end])


def validate_delta(changes, contents):
    require(set(changes) == ALLOWED, "INTEGRATION_ONLY_DELTA_REQUIRED")
    require(all(status in {"A", "M"} for status in changes.values()), "DELETE_OR_RENAME_FORBIDDEN")
    patterns = (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
                r"AKIA[A-Z0-9]{16}", r"postgres(?:ql)?://[^\s]+",
                r"C:\\Users\\", r"Sentinel_[a-z0-9]+")
    for name in changes:
        require(not any(re.search(p, contents[name]) for p in patterns), "PRIVATE_MATERIAL: " + name)


def validate_workflow(workflow, text):
    require("secrets." not in text and "id-token" not in text, "NO_SECRETS_OR_OIDC")
    require(workflow["permissions"] == {"contents": "read"}, "READ_ONLY_PERMISSIONS")
    require("env" not in workflow and "defaults" not in workflow, "NO_AMBIENT_ENVIRONMENT")
    events = workflow.get("on", workflow.get(True))
    require(set(events) == {"push", "pull_request"}, "NO_EXTRA_TRIGGERS")
    require(events["push"]["branches"] == ["test/tournament-shape-pilot-20261004"], "LEGACY_BRANCH_CHANGED")
    require(events["pull_request"]["branches"] == ["main"], "PR_BASE_FILTER")
    require(set(workflow["jobs"]) == {"offline-consumer"}, "ONE_JOB_ONLY")
    job = workflow["jobs"]["offline-consumer"]
    require(" ".join(job["if"].split()) == JOB_IF, "EXACT_PR_JOB_GATE")
    require(job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == 10, "BOUNDED_STANDARD_RUNNER")
    require(not {"env", "environment", "container", "strategy"}.intersection(job), "NO_PRIVILEGED_JOB_CONTEXT")
    require(set(job["services"]) == {"postgres"}, "ONE_DISPOSABLE_SERVICE")
    service = job["services"]["postgres"]
    require(service["image"] == "postgres:18", "POSTGRES_IMAGE")
    require(service["ports"] == ["127.0.0.1:55432:5432"], "LOOPBACK_ONLY")
    require(service["env"] == {"POSTGRES_DB": "tournament_rehearsal", "POSTGRES_USER": "postgres",
                               "POSTGRES_HOST_AUTH_METHOD": "trust"}, "SYNTHETIC_DATABASE_ONLY")
    steps = job["steps"]
    require(all("env" not in step for step in steps), "NO_STEP_CREDENTIALS")
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    require(checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}, "CLEAN_FULL_CHECKOUT")
    old = next(s for s in steps if s.get("name") == "Exact SHA and isolation preflight")
    require(old["if"] == "github.event_name == 'push'", "PRESERVE_LEGACY_PREFLIGHT")
    gate = next(s for s in steps if s.get("name") == "PR2147 exact revision and isolation preflight")
    require(gate["if"] == "github.event_name == 'pull_request'", "PR_PREFLIGHT_REQUIRED")
    rehearsal = next(s for s in steps if s.get("name") == "Authenticated screen, SQL gates and rollback")
    require(rehearsal["shell"] == "bash" and "| tee" not in rehearsal["run"], "FAIL_CLOSED_REHEARSAL")
    require("['status'] == 'PASS'" in rehearsal["run"], "REHEARSAL_RESULT_REQUIRED")
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@"))
    require(upload["with"]["retention-days"] == 7, "BOUNDED_RETENTION")
    require(tuple(upload["with"]["path"].split()) == ARTIFACTS, "EXACT_ARTIFACTS_ONLY")
    limit = next(s for s in steps if s.get("name") == "Bound and verify evidence before upload")
    require(limit["run"] == "python -m tools.tournament_pilot.integration_preflight evidence", "EVIDENCE_LIMIT_REQUIRED")
    require(steps.index(limit) < steps.index(upload), "VERIFY_BEFORE_UPLOAD")


def validate_evidence(document):
    require(document["status"] == "PASS" and document["authenticated_api"] is True, "HTTP_NOT_VERIFIED")
    require(document["auth_missing"] == 401 and document["auth_wrong"] == 403, "AUTH_NOT_VERIFIED")
    require(document["screen_lifecycle"] == ["ABSTAIN"] * 3, "FULL_CALL_MUST_ABSTAIN")
    require(document["screen_shape_lifecycle"] == ["MATCH", "UNKNOWN", "MATCH"], "REVOCATION_NOT_VERIFIED")
    require(document["total_pilot_rows"] == 40 and document["row_budget"] == ROW_BUDGET
            and all(type(v) is int for v in document["row_budget"].values()), "ROW_BUDGET")
    require(document["extra_synthetic_school_fixture"] == 1 and document["other_position"] == "ABSTAIN", "FIXTURE_BOUNDARY")
    require(document["assessment_version"] == ASSESSMENT_VERSION, "ASSESSMENT_VERSION_CHANGED")
    for name in ("teacher_output_writes", "search_runs", "final_decisions"):
        require(type(document[name]) is int and document[name] == 0, "UNEXPECTED_WRITES")
    require(document["reactivation_extends_expiry"] is False and document["ttl_hours"] == 24, "TTL_CHANGED")
    cases = document["semantic_cases"]
    require(len(cases) == 18, "MISSING_SEMANTIC_CASES")
    expected = {"positive": ("PARTIAL", "MATCH"), "minor_orientation": ("PARTIAL", "MATCH"),
                "wrong_singleton": ("PARTIAL", "MISMATCH"), "singleton_boundary": ("PARTIAL", "MISMATCH"),
                "short_minor": ("PARTIAL", "MISMATCH"), "hidden_context": ("ABSTAIN", "UNKNOWN"),
                "wrong_turn": ("ABSTAIN", "UNKNOWN"), "interference": ("ABSTAIN", "UNKNOWN"),
                "missing_hand": ("ABSTAIN", "UNKNOWN")}
    observed = Counter((c["case"], c["observed"], c["expected"], c["shape_status"], c["assessment_version"]) for c in cases)
    required = Counter({(name, status, status, shape, ASSESSMENT_VERSION): 2
                        for name, (status, shape) in expected.items()})
    require(observed == required, "EXACT_SEMANTIC_CASES_REQUIRED")


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def verify_artifacts(root):
    paths = [root / name for name in ARTIFACTS]
    require(all(p.is_file() and not p.is_symlink() for p in paths), "MISSING_OR_LINKED_ARTIFACT")
    require(sum(p.stat().st_size for p in paths) <= MAX_ARTIFACT_BYTES, "ARTIFACT_SIZE_LIMIT")
    validate_evidence(json.loads(paths[1].read_text(encoding="utf-8")))


def main():
    root = Path(__file__).resolve().parents[2]
    if sys.argv[1:] == ["evidence"]:
        verify_artifacts(root)
        print("Evidence PASS: authenticated HTTP/SQL, 40 synthetic rows, no output writes; artifacts <=128 KiB")
        return
    require(not sys.argv[1:], "UNEXPECTED_ARGUMENTS")
    require(os.environ.get("GITHUB_EVENT_NAME") == "pull_request", "PR_EVENT_REQUIRED")
    head = validate_event(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")))
    require(git(root, "rev-parse", BASELINE + "^{tree}") == BASELINE_TREE, "BASELINE_TREE_CHANGED")
    subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, head], cwd=root, check=True)
    require(git(root, "rev-parse", "HEAD^{tree}") == git(root, "rev-parse", head + "^{tree}"), "MERGE_TREE_DIFFERS")
    require(not git(root, "status", "--porcelain"), "DIRTY_CHECKOUT")
    changes = {}
    for line in git(root, "diff", "--no-renames", "--name-status", BASELINE, head).splitlines():
        status, name = line.split("\t")
        changes[name] = status
    validate_delta(changes, {name: added_content(
        git(root, "show", BASELINE + ":" + name) if status == "M" else "",
        (root / name).read_text(encoding="utf-8")) for name, status in changes.items()})
    for name in ("DATABASE_URL", "NEON_DATABASE_URL", "BRIDGE_API_TOKEN", "VERCEL_OIDC_TOKEN"):
        require(not os.environ.get(name), "UNEXPECTED_RUNTIME_CREDENTIAL: " + name)
    import yaml
    text = (root / WORKFLOW).read_text(encoding="utf-8")
    validate_workflow(yaml.safe_load(text), text)
    print(json.dumps({"status": "PASS", "baseline": BASELINE, "head": head,
                      "checkout": git(root, "rev-parse", "HEAD"), "main": MAIN,
                      "delta": sorted(changes), "database": "disposable loopback only"}))


if __name__ == "__main__":
    main()
