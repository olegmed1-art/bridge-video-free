"""Exercise guards read from the installed workflow, without any dispatch."""
import json
import re

import pytest
import yaml

from . import installation_checks as c


@pytest.fixture(scope="module")
def installed():
    text = (c.REPO / c.OWNER).read_text()
    return text, yaml.safe_load(text), c.baseline()


def event(**changes):
    context = {
        "github.repository": "olegmed1-art/bridge-video-free",
        "github.repository_owner": "olegmed1-art",
        "github.ref": "refs/heads/main", "github.actor": "olegmed1-art",
        "github.triggering_actor": "olegmed1-art", "github.event_name": "workflow_dispatch",
        "github.sha": "a" * 40, "inputs.expected_main_sha": "a" * 40,
        "inputs.expected_probe_sha": "a" * 40, "inputs.probe_scope": "canon-readonly"}
    context.update(changes)
    return context


def admitted(expression, context):
    """Evaluate only the equality/AND syntax actually present in this YAML."""
    assert "||" not in expression
    clauses = expression.split("&&")
    def resolve(token):
        token = token.strip()
        if re.fullmatch(r"'[^']*'", token):
            return token[1:-1]
        assert re.fullmatch(r"(github|inputs)[.][a-z_]+", token)
        return context.get(token)
    for clause in clauses:
        left, right = clause.split("==")
        if resolve(left) != resolve(right):
            return False
    return True


def test_actual_installed_workflow_preserves_maintenance_and_validates_new_mode(installed):
    text, value, original = installed
    c.validate_installed_workflow(text, original)
    assert set(c.events(value)) == {"pull_request", "workflow_dispatch"}
    assert "push" not in c.events(value)
    assert c.events(value)["workflow_dispatch"]["inputs"]["probe_scope"]["default"] == "maintenance"


def test_exact_main_canon_event_selects_only_new_job(installed):
    _, value, _ = installed
    assert admitted(value["jobs"]["canon-owner-probe"]["if"], event())
    assert not admitted(value["jobs"]["attest"]["if"], event())


@pytest.mark.parametrize("changes", [
    {"github.ref": "refs/heads/review/canon-owner-installation-20261004"},
    {"github.ref": "refs/pull/2104/merge"},
    {"github.event_name": "pull_request"},
    {"github.event_name": "push"},
    {"github.actor": "other"},
    {"github.triggering_actor": "other"},
    {"github.repository": "other/repository"},
    {"inputs.expected_main_sha": "b" * 40},
    {"inputs.expected_probe_sha": "b" * 40},
    {"inputs.probe_scope": "maintenance"},
    {"inputs.probe_scope": ""},
])
def test_installed_canon_job_rejects_wrong_event_context(installed, changes):
    _, value, _ = installed
    assert not admitted(value["jobs"]["canon-owner-probe"]["if"], event(**changes))


def test_default_maintenance_selects_original_job_and_keeps_original_environment(installed):
    _, value, original = installed
    context = event(**{"inputs.probe_scope": c.events(value)["workflow_dispatch"]["inputs"]["probe_scope"]["default"]})
    assert admitted(value["jobs"]["attest"]["if"], context)
    assert not admitted(value["jobs"]["canon-owner-probe"]["if"], context)
    assert admitted(yaml.safe_load(original)["jobs"]["attest"]["if"], context)
    assert value["jobs"]["attest"]["environment"] == yaml.safe_load(original)["jobs"]["attest"]["environment"]


@pytest.mark.parametrize("guard", [
    "github.ref == 'refs/heads/main'",
    "github.triggering_actor == github.repository_owner",
    "inputs.expected_main_sha == github.sha",
    "inputs.expected_probe_sha == github.sha",
])
def test_removing_installed_guard_is_detected(installed, guard):
    text, _, original = installed
    with pytest.raises(AssertionError):
        c.validate_installed_workflow(text.replace(guard, "true"), original)


def test_unreviewed_extra_owner_step_and_permissions_are_refused(installed):
    text, _, original = installed
    value = yaml.safe_load(text)
    value["jobs"]["canon-owner-probe"]["steps"].insert(2, {"run": "echo unreviewed"})
    with pytest.raises(AssertionError):
        c.validate_installed_workflow(yaml.safe_dump(value), original)


def test_manifest_hashes_the_actual_owner_workflow(installed):
    import hashlib
    text, _, _ = installed
    manifest = json.loads((c.ROOT / "manifest.json").read_text())
    assert manifest["content_sha256"][c.OWNER] == hashlib.sha256(text.encode()).hexdigest()
    assert manifest["installation_base"] == c.BASE

def test_changed_installed_checkout_action_is_refused(installed):
    text, _, original = installed
    value = yaml.safe_load(text)
    value["jobs"]["canon-owner-probe"]["steps"][0]["uses"] = "unreviewed/checkout@main"
    with pytest.raises(AssertionError):
        c.validate_installed_workflow(yaml.safe_dump(value), original)


@pytest.mark.parametrize("job_id", ["attest", "canon-owner-probe"])
def test_repository_owner_fallback_is_refused(installed, job_id):
    text, _, original = installed
    value = yaml.safe_load(text)
    owner_envs = [step["env"] for step in value["jobs"][job_id]["steps"]
                  if "NATIVE_OWNER_DATABASE_URL" in step.get("env", {})]
    assert len(owner_envs) == 1
    owner_envs[0]["NATIVE_OWNER_DATABASE_URL"] = (
        "${{ secrets.LIGHT_MAINTENANCE_DATABASE_URL || secrets.NEON_DATABASE_URL }}")
    with pytest.raises(AssertionError):
        c.validate_installed_workflow(yaml.safe_dump(value), original)
