"""Synthetic contracts for the actual job gate and inline scope precheck.
No secrets, network or project-module execution.

Run from the repository root:
python -m pytest -q tests/test_autopilot_paused_reconcile_workflow_contract.py

The evaluator intentionally supports only the string/boolean expression subset
used here. Unknown syntax fails the test; it is not a full Actions emulator.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PATH = Path(".github/workflows/autopilot-paused-reconcile.yml")
WORKFLOW = yaml.load(PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
JOB = WORKFLOW["jobs"]["reconcile"]
REPO = "olegmed1-art/bridge-video-free"
SHA = "a" * 40
UPSTREAMS = (
    "Issue 881 Exact Canary Contract CI",
    "Issue 881 Current-Main Authoritative CI",
)
ALL_UPSTREAMS = (*UPSTREAMS, "Bridge School Database CI", "Autopilot publication security CI")


def evaluate(expression, github):
    """Read the actual YAML predicate; never eval code or call project modules."""
    tree = ast.parse(expression.replace("&&", " and ").replace("||", " or "), mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id == "github":
            return github
        if isinstance(node, ast.Attribute):
            parent = visit(node.value)
            return parent.get(node.attr, "") if isinstance(parent, dict) else ""
        if isinstance(node, ast.BoolOp):
            values = [visit(value) for value in node.values]
            if not all(type(value) is bool for value in values):
                raise AssertionError("only boolean operands are supported")
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = visit(node.left), visit(node.comparators[0])
            # GitHub's string equality is case-insensitive; missing properties
            # evaluate to an empty string. Null also coerces to empty here.
            left = "" if left is None else left
            right = "" if right is None else right
            if not isinstance(left, str) or not isinstance(right, str):
                raise AssertionError("only string comparisons are supported")
            equal = left.casefold() == right.casefold()
            if isinstance(node.ops[0], ast.Eq):
                return equal
            if isinstance(node.ops[0], ast.NotEq):
                return not equal
        raise AssertionError("unsupported Actions predicate syntax: " + ast.dump(node))

    result = visit(tree)
    assert type(result) is bool
    return result


def context(event="workflow_run", upstream=UPSTREAMS[0]):
    value = {
        "repository": REPO,
        "ref": "refs/heads/main",
        "sha": SHA,
        "event_name": event,
        "event": {},
    }
    if event == "workflow_run":
        value["event"] = {
            "action": "completed",
            "workflow_run": {
                "name": upstream,
                "repository": {"full_name": REPO},
                "head_repository": {"full_name": REPO},
                "event": "workflow_dispatch",
                "head_branch": "main",
                "head_sha": SHA,
                "status": "completed",
                "conclusion": "success",
            },
        }
    return value


PRECHECK = JOB["steps"][0]
EXPECTED_PRECHECK_ENV = {
    "EVENT_NAME": "${{ github.event_name }}",
    "EVENT_REPOSITORY": "${{ github.repository }}",
    "EVENT_REF": "${{ github.ref }}",
    "EVENT_SHA": "${{ github.sha }}",
    "EVENT_ACTION": "${{ github.event.action }}",
    "UPSTREAM_REPOSITORY": "${{ github.event.workflow_run.repository.full_name }}",
    "UPSTREAM_HEAD_REPOSITORY": "${{ github.event.workflow_run.head_repository.full_name }}",
    "UPSTREAM_EVENT": "${{ github.event.workflow_run.event }}",
    "UPSTREAM_BRANCH": "${{ github.event.workflow_run.head_branch }}",
    "UPSTREAM_SHA": "${{ github.event.workflow_run.head_sha }}",
    "UPSTREAM_STATUS": "${{ github.event.workflow_run.status }}",
    "UPSTREAM_CONCLUSION": "${{ github.event.workflow_run.conclusion }}",
    "UPSTREAM_NAME": "${{ github.event.workflow_run.name }}",
}


def precheck_source():
    lines = PRECHECK["run"].splitlines()
    assert lines[0] == "python -I -B - <<'PY'"
    assert lines[-1] == "PY"
    return "\n".join(lines[1:-1]) + "\n"


def precheck_environment(github):
    values = {}
    for name, expression in PRECHECK["env"].items():
        assert expression.startswith("${{") and expression.endswith("}}")
        fields = expression[3:-2].strip().split(".")
        assert fields[0] == "github"
        value = github
        for field in fields[1:]:
            value = value.get(field, "") if isinstance(value, dict) else ""
        assert value is None or isinstance(value, str)
        values[name] = value or ""
    return values


def run_precheck(github, *, environment=None, cwd=None):
    # Only synthetic metadata is inherited; no test-runner credentials or tokens.
    values = precheck_environment(github) if environment is None else environment
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", precheck_source()],
        env={"LANG": "C.UTF-8", **values}, cwd=cwd,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=5, check=False,
    )


def precheck_allowed(github):
    result = run_precheck(github)
    assert result.stdout == b""
    if result.returncode == 0:
        assert result.stderr == b""
        return True
    assert result.returncode == 1
    assert result.stderr == b"AUTOPILOT_EVENT_SCOPE_REJECTED\n"
    return False


def allowed(value):
    return evaluate(JOB["if"], value) and precheck_allowed(value)


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_direct_main_paths_remain_allowed(event):
    assert allowed(context(event))


@pytest.mark.parametrize("upstream", UPSTREAMS)
def test_successful_main_dispatch_at_exact_event_sha_is_allowed(upstream):
    assert allowed(context(upstream=upstream))


@pytest.mark.parametrize("upstream", ALL_UPSTREAMS)
@pytest.mark.parametrize("event", ["pull_request", "pull_request_target"])
def test_pr_completion_is_rejected_even_with_main_branch_and_same_sha(upstream, event):
    value = context(upstream=upstream)
    value["event"]["workflow_run"]["event"] = event
    assert not allowed(value)


@pytest.mark.parametrize("event", [
    "push", "schedule", "workflow_run", "workflow_call", "repository_dispatch", "",
])
def test_no_unreviewed_upstream_event_is_admitted(event):
    value = context()
    value["event"]["workflow_run"]["event"] = event
    assert not allowed(value)


@pytest.mark.parametrize("event", ["pull_request", "pull_request_target", "push", "repository_dispatch", ""])
def test_no_unreviewed_direct_event_is_admitted(event):
    assert not allowed(context(event))


@pytest.mark.parametrize("event", ["workflow_run", "schedule", "workflow_dispatch"])
@pytest.mark.parametrize("field,value", [
    ("repository", "other/bridge-video-free"),
    ("ref", "refs/heads/topic"),
    ("ref", "refs/tags/main"),
    ("sha", ""),
])
def test_outer_repository_ref_and_sha_are_required(event, field, value):
    candidate = context(event)
    candidate[field] = value
    assert not allowed(candidate)


@pytest.mark.parametrize("field,value", [
    ("repository", {"full_name": "other/bridge-video-free"}),
    ("head_repository", {"full_name": "other/bridge-video-free"}),
    ("repository", {}),
    ("head_repository", {}),
    ("head_branch", "topic"),
    ("head_branch", ""),
    ("head_sha", "b" * 40),
    ("head_sha", ""),
    ("head_sha", None),
    ("head_branch", None),
    ("event", None),
    ("name", None),
    ("status", None),
    ("conclusion", None),
    ("repository", None),
    ("head_repository", None),
    ("status", "in_progress"),
    ("status", ""),
    ("conclusion", "failure"),
    ("conclusion", "cancelled"),
    ("conclusion", "skipped"),
    ("conclusion", "timed_out"),
    ("conclusion", "neutral"),
    ("conclusion", "action_required"),
    ("conclusion", ""),
    ("name", "Unreviewed workflow"),
    ("name", ALL_UPSTREAMS[2]),
    ("name", ALL_UPSTREAMS[3]),
])
def test_untrusted_incomplete_stale_or_unsuccessful_run_is_rejected(field, value):
    candidate = context()
    candidate["event"]["workflow_run"][field] = value
    assert not allowed(candidate)


@pytest.mark.parametrize("field", list(context()["event"]["workflow_run"]))
def test_every_required_upstream_field_fails_closed_when_missing(field):
    candidate = context()
    del candidate["event"]["workflow_run"][field]
    assert not allowed(candidate)


@pytest.mark.parametrize("payload", [{}, {"action": "completed"}, {"workflow_run": {}}])
def test_missing_event_data_fails_closed(payload):
    candidate = context()
    candidate["event"] = payload
    assert not allowed(candidate)


def test_non_completion_fails_closed():
    candidate = context()
    candidate["event"]["action"] = "requested"
    assert not allowed(candidate)


def test_regression_reproduces_old_guard_without_running_a_workflow():
    candidate = context()
    candidate["event"]["workflow_run"].update(
        event="pull_request", head_branch="topic", head_sha="b" * 40, conclusion="failure",
    )
    old_guard = "github.repository == 'olegmed1-art/bridge-video-free' && github.ref == 'refs/heads/main'"
    assert evaluate(old_guard, candidate)
    assert not allowed(candidate)


def test_workflow_trigger_and_permissions_contract():
    triggers = WORKFLOW["on"]
    assert set(triggers) == {"workflow_dispatch", "schedule", "workflow_run"}
    assert triggers["schedule"] == [{"cron": "37 * * * *"}]
    assert triggers["workflow_run"] == {
        "workflows": list(ALL_UPSTREAMS),
        "types": ["completed"],
        "branches": ["main"],
    }
    assert WORKFLOW["permissions"] == {
        "contents": "read", "pull-requests": "read", "checks": "read",
    }
    assert WORKFLOW["concurrency"] == {
        "group": "autopilot-paused-evidence-reconciliation",
        "cancel-in-progress": "false",
    }
    assert set(WORKFLOW["jobs"]) == {"reconcile"}
    assert "env" not in WORKFLOW and "env" not in JOB
    assert JOB["runs-on"] == "ubuntu-24.04"
    assert JOB["timeout-minutes"] == "20"
    assert "continue-on-error" not in JOB


def test_exact_checkout_and_secret_steps_remain_bounded():
    steps = JOB["steps"]
    assert len(steps) == 6
    steps = steps[1:]
    assert steps[0] == {
        "name": "Checkout admitted main SHA",
        "uses": "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
        "with": {"ref": "${{ github.sha }}", "fetch-depth": "1", "persist-credentials": "false"},
    }
    assert steps[1] == {
        "name": "Install bounded reconciliation dependencies",
        "run": "python -m pip install 'psycopg[binary]==3.3.4'",
    }
    base_env = {
        "DATABASE_URL": "${{ secrets.BRIDGE_WORKER_DATABASE_URL }}",
        "SSH_PRIVATE_KEY": "${{ secrets.ORACLE_SSH_PRIVATE_KEY }}",
        "GH_TOKEN": "${{ github.token }}",
    }
    assert steps[2] == {
        "name": "Verify connection, bounded RPC access, and check-runs",
        "id": "preflight", "env": base_env,
        "run": "python -m ops.github_autopilot_db_route diagnostics",
    }
    for step, command, name in (
        (steps[3], "reconcile", "Reconcile at most three paused items from fresh evidence"),
        (steps[4], "next-step", "Admit bounded read-only next steps"),
    ):
        assert step == {
            "name": name, "if": "${{ false }}",
            "env": {**base_env, "REPOSITORY": "${{ github.repository }}"},
            "run": "python -m ops.github_autopilot_db_route " + command,
        }


@pytest.mark.parametrize("branch", ["Main", "MAIN", "mAin"])
@pytest.mark.parametrize("event", ["workflow_dispatch", "workflow_run", "schedule"])
def test_case_variant_ref_passes_coarse_expression_but_stops_before_checkout(branch, event):
    candidate = context(event)
    candidate["ref"] = "refs/heads/" + branch
    assert evaluate(JOB["if"], candidate)  # Reproduce the reviewed scope edge.
    assert not precheck_allowed(candidate)
    assert not allowed(candidate)


@pytest.mark.parametrize("field,value", [
    ("head_branch", "Main"),
    ("head_branch", "MAIN"),
    ("repository", {"full_name": REPO.upper()}),
    ("head_repository", {"full_name": REPO.upper()}),
    ("head_sha", SHA.upper()),
    ("status", "COMPLETED"),
    ("conclusion", "SUCCESS"),
    ("event", "WORKFLOW_DISPATCH"),
    ("name", UPSTREAMS[0].lower()),
])
def test_upstream_case_variants_also_require_the_exact_precheck(field, value):
    candidate = context()
    candidate["event"]["workflow_run"][field] = value
    assert evaluate(JOB["if"], candidate)
    assert not precheck_allowed(candidate)


@pytest.mark.parametrize("field,value", [
    ("repository", REPO.upper()),
    ("event_name", "WORKFLOW_DISPATCH"),
    ("sha", SHA.upper()),
    ("sha", "x" * 40),
    ("sha", "a" * 39),
    ("sha", "a" * 41),
])
def test_precheck_outer_fields_are_case_sensitive_and_sha_is_canonical(field, value):
    candidate = context("workflow_dispatch")
    candidate[field] = value
    assert evaluate(JOB["if"], candidate)
    assert not precheck_allowed(candidate)


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
@pytest.mark.parametrize("key", ["EVENT_REPOSITORY", "EVENT_REF", "EVENT_SHA", "EVENT_NAME"])
def test_precheck_direct_required_metadata_missing_fails_closed(event, key):
    candidate = context(event)
    environment = precheck_environment(candidate)
    del environment[key]
    result = run_precheck(candidate, environment=environment)
    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == b"AUTOPILOT_EVENT_SCOPE_REJECTED\n"


@pytest.mark.parametrize("key", [
    "EVENT_REPOSITORY", "EVENT_REF", "EVENT_SHA", "EVENT_NAME", "EVENT_ACTION",
    "UPSTREAM_REPOSITORY", "UPSTREAM_HEAD_REPOSITORY", "UPSTREAM_EVENT",
    "UPSTREAM_BRANCH", "UPSTREAM_SHA", "UPSTREAM_STATUS", "UPSTREAM_CONCLUSION",
    "UPSTREAM_NAME",
])
def test_precheck_upstream_required_metadata_missing_fails_closed(key):
    candidate = context()
    environment = precheck_environment(candidate)
    del environment[key]
    result = run_precheck(candidate, environment=environment)
    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == b"AUTOPILOT_EVENT_SCOPE_REJECTED\n"


def test_precheck_is_first_secret_free_inline_and_cannot_continue_on_error():
    assert set(PRECHECK) == {"name", "shell", "timeout-minutes", "env", "run"}
    assert PRECHECK["name"] == "Validate case-sensitive event scope"
    assert PRECHECK["shell"] == "bash"
    assert PRECHECK["timeout-minutes"] == "1"
    assert PRECHECK["env"] == EXPECTED_PRECHECK_ENV
    assert "secrets." not in str(PRECHECK)
    assert "github.token" not in str(PRECHECK)
    assert "${{" not in PRECHECK["run"]
    tree = ast.parse(precheck_source())
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(isinstance(node, ast.Import) for node in imports)
    assert sorted(alias.name for node in imports for alias in node.names) == ["os", "re"]
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


def test_precheck_ignores_project_modules_in_working_directory(tmp_path):
    # Even a local file with a standard-library name must not be imported.
    (tmp_path / "re.py").write_text("raise RuntimeError('PROJECT_CODE_IMPORTED')\n", encoding="utf-8")
    result = run_precheck(context("workflow_dispatch"), cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout == result.stderr == b""
