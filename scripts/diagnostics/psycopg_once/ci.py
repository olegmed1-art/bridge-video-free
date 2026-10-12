"""No-secret offline compatibility and public-metadata prerequisite gate."""
import io
import json
import os
from pathlib import Path
import re
import sys
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import probe
from protocol import valid_offline

WORKFLOW_NAME = "Incident 1911 native one call"
SHA = re.compile(r"[0-9a-f]{40}\Z")


def event_allowed(event, stage):
    expected = probe.PREFLIGHT_BRANCH if stage == "preflight" else probe.LIVE_BRANCH
    return (type(event) is dict and event.get("ref_type") == "branch" and event.get("ref") == expected and
            event.get("repository", {}).get("full_name") == probe.REPO)


def validate_preflight(runs, jobs, sha):
    if type(runs) is not dict or runs.get("total_count") != 1 or len(runs.get("workflow_runs", [])) != 1:
        return False
    run = runs["workflow_runs"][0]
    expected = {"head_sha": sha, "head_branch": probe.PREFLIGHT_BRANCH, "event": "create", "run_number": 1,
                "run_attempt": 1, "status": "completed", "conclusion": "success", "name": WORKFLOW_NAME,
                "path": ".github/workflows/" + probe.WORKFLOW}
    if any(run.get(k) != v for k, v in expected.items()) or run.get("repository", {}).get("full_name") != probe.REPO:
        return False
    if type(run.get("id")) is not int or run["id"] <= 0:
        return False
    rows = jobs.get("jobs", [])
    if type(rows) is not list or jobs.get("total_count") != len(rows) or len(rows) not in (1, 2):
        return False
    actual = {}
    for job in rows:
        if job.get("name") in actual or job.get("head_sha") != sha or job.get("run_attempt") != 1 or job.get("status") != "completed":
            return False
        actual[job.get("name")] = job.get("conclusion")
    if actual not in ({"Offline preflight": "success"}, {"Offline preflight": "success", "Live once": "skipped"}):
        return False
    passed = next(x for x in rows if x["name"] == "Offline preflight")
    steps = {x.get("name"): x.get("conclusion") for x in passed.get("steps", [])}
    return all(steps.get(s) == "success" for s in ("Bootstrap pinned public artifacts", "Offline compatibility without network"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("redirect_rejected")


def public_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "incident-1911-preflight-gate", "Accept": "application/vnd.github+json"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=5) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("public_metadata")
        data = response.read(524289)
    if len(data) > 524288:
        raise ValueError("metadata_size")
    return json.loads(data)


def gate(env, root=ROOT, fetch=public_json):
    if not probe.context_allowed(env, "live") or env.get("BRIDGE_APP_DATABASE_URL"):
        raise ValueError("gate_context")
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
    if not event_allowed(event, "live"):
        raise ValueError("event")
    sha = env["GITHUB_SHA"]
    offline = json.loads((root / "offline-result.json").read_text())
    if not valid_offline(offline, sha):
        raise ValueError("local_offline_gate")
    base = "https://api.github.com/repos/" + probe.REPO
    runs = fetch(base + "/actions/workflows/" + probe.WORKFLOW + "/runs?branch=" + probe.PREFLIGHT_BRANCH + "&event=create&per_page=10")
    # Validate the id before constructing the second fixed-domain URL.
    if runs.get("total_count") != 1 or len(runs.get("workflow_runs", [])) != 1:
        raise ValueError("run_count")
    run_id = runs["workflow_runs"][0].get("id")
    if type(run_id) is not int or not 0 < run_id < 10**15:
        raise ValueError("run_id")
    jobs = fetch(base + "/actions/runs/" + str(run_id) + "/jobs?filter=all&per_page=100")
    if not validate_preflight(runs, jobs, sha):
        raise ValueError("preflight_not_proven")
    result = {"schema": "incident-1911-gate-v2", "head_sha": sha, "preflight_passed": True}
    (root / "stage-gate.json").write_text(json.dumps(result), encoding="ascii")
    return result


def offline(original_namespace, expected_uid, sha, root=ROOT):
    if (sys.platform != "linux" or not SHA.fullmatch(sha) or os.getuid() == 0 or
            os.getuid() != expected_uid or dict(os.environ) != {"LC_ALL": "C.UTF-8"} or os.readlink("/proc/self/ns/net") == original_namespace):
        raise ValueError("offline_isolation")
    devices = {line.split(":", 1)[0].strip() for line in Path("/proc/net/dev").read_text().splitlines() if ":" in line}
    if devices - {"lo"} or len(Path("/proc/net/route").read_text().splitlines()) > 1:
        raise ValueError("offline_network")
    if "NoNewPrivs:\t1" not in Path("/proc/self/status").read_text():
        raise ValueError("offline_privileges")
    attempts = [0]

    def audit(event, args):
        if event.startswith("socket."):
            attempts[0] += 1
            raise RuntimeError("offline_socket_prohibited")

    sys.addaudithook(audit)
    _, versions = probe.runtime(root)
    suite = unittest.defaultTestLoader.discover(str(root), pattern="test_*.py")
    captured = io.StringIO()
    tests = unittest.TextTestRunner(stream=captured, verbosity=0).run(suite)
    result = {"schema": "incident-1911-offline-v2", "head_sha": sha,
              "status": "pass" if tests.wasSuccessful() and tests.testsRun >= 40 and attempts[0] == 0 else "fail",
              "tests": tests.testsRun, "failures": len(tests.failures), "errors": len(tests.errors),
              "network_namespace_isolated": True, "nonroot": True, "secret_present": False,
              "socket_attempts": attempts[0], "native_connect_calls": 0, "runtime": versions}
    (root / "offline-result.json").write_text(json.dumps(result), encoding="ascii")
    return result


def main():
    try:
        if len(sys.argv) == 5 and sys.argv[1] == "offline":
            value = offline(sys.argv[2], int(sys.argv[3]), sys.argv[4])
            print(json.dumps(value, separators=(",", ":")))
            return 0 if value["status"] == "pass" else 1
        if sys.argv[1:] == ["gate"]:
            print(json.dumps(gate(os.environ), separators=(",", ":")))
            return 0
    except BaseException:
        print('{"status":"ci_gate_failed"}')
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
