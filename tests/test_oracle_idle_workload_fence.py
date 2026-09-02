from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHARED_FENCE = "oracle-instance-workload-mutation"

POWER_GROUP = (
    "${{ github.event_name == 'workflow_dispatch' && "
    "'oracle-instance-workload-mutation' || (github.event_name == "
    "'issue_comment' && github.actor == github.repository_owner && "
    "contains(fromJSON('[\"/oracle-instance status\",\"/oracle-instance start\","
    "\"/oracle-instance stop\"]'), github.event.comment.body)) && "
    "'oracle-instance-workload-mutation' || "
    "format('oracle-instance-power-noop-{0}', github.run_id) }}"
)

MASS_LAUNCH_GROUP = (
    "${{ github.event_name == 'pull_request_target' && "
    "github.event.pull_request.head.repo.full_name == github.repository && "
    "github.event.pull_request.user.login == github.repository_owner && "
    "github.event.pull_request.base.ref == 'main' && "
    "github.event.pull_request.changed_files == 1 && "
    "'oracle-instance-workload-mutation' || "
    "format('oracle-dds3-pilot10k-launch-noop-{0}', github.run_id) }}"
)

MASS_OPERATOR_GROUP = (
    "${{ github.event_name == 'issue_comment' && "
    "github.event.comment.user.login == github.repository_owner && "
    "contains(fromJSON('[\"/dds3-pilot10k start\",\"/dds3-pilot10k status\","
    "\"/dds3-main30k deploy\",\"/dds3-main30k stage\","
    "\"/dds3-main30k start\",\"/dds3-main30k reconcile\"]'), "
    "github.event.comment.body) && 'oracle-instance-workload-mutation' || "
    "format('oracle-dds3-pilot10k-operator-noop-{0}', github.run_id) }}"
)

EXPECTED_PRODUCERS = {
    "oracle-diana11-002-job.yml": (
        {"pull_request", "push"},
        "${{ github.event_name == 'pull_request' && "
        "format('oracle-diana11-002-pr-{0}', github.event.pull_request.number) || "
        "'oracle-instance-workload-mutation' }}",
    ),
    "oracle-diana11-003-one-shadow-execution.yml": (
        {"pull_request", "push"},
        "${{ github.event_name == 'pull_request' && "
        "format('oracle-diana11-003-pr-{0}', github.event.pull_request.number) || "
        "'oracle-instance-workload-mutation' }}",
    ),
}

EXPECTED_RESEARCH_PRODUCERS = {
    "research-job-dds3-canary.yml": (
        {"pull_request", "issue_comment"},
        "${{ github.event_name == 'pull_request' && "
        "format('research-job-dds3-pr-{0}', github.event.pull_request.number) || "
        "(github.event_name == 'issue_comment' && "
        "github.event.comment.user.login == 'olegmed1-art' && "
        "github.event.comment.body == '/research-job canary-dds3-idempotency') && "
        "'oracle-instance-workload-mutation' || "
        "format('research-job-dds3-noop-{0}', github.run_id) }}",
    ),
    "research-job-neon-operator.yml": (
        {"pull_request", "issue_comment"},
        "${{ github.event_name == 'pull_request' && "
        "format('research-job-neon-pr-{0}', github.event.pull_request.number) || "
        "(github.event_name == 'issue_comment' && "
        "github.event.comment.user.login == 'olegmed1-art' && "
        "github.event.comment.body == '/research-job migrate-neon-and-canary') && "
        "'oracle-instance-workload-mutation' || "
        "format('research-job-neon-noop-{0}', github.run_id) }}",
    ),
}


def _workflow_text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _workflow_contract(name: str) -> tuple[set[str], dict[str, object]]:
    document = yaml.safe_load(_workflow_text(name))
    # YAML 1.1 parsers treat the unquoted GitHub Actions key `on` as boolean.
    triggers = document.get("on", document.get(True))
    assert isinstance(triggers, dict), name
    concurrency = document.get("concurrency")
    assert isinstance(concurrency, dict), name
    return set(triggers), concurrency


def _assert_workflow_mapping(name: str, events: set[str], group: str) -> None:
    actual_events, concurrency = _workflow_contract(name)
    assert actual_events == events, name
    assert concurrency == {"group": group, "cancel-in-progress": False}, name


def _workflows_containing(marker: str) -> set[str]:
    return {
        path.name
        for path in WORKFLOWS.glob("*.yml")
        if marker in path.read_text(encoding="utf-8")
    }


def test_all_submit_bridge_oracle_producers_have_exact_event_to_fence_mapping() -> None:
    assert _workflows_containing(" submit-bridge") == set(EXPECTED_PRODUCERS)
    for name, (events, group) in EXPECTED_PRODUCERS.items():
        _assert_workflow_mapping(name, events, group)


def test_stop_consumer_has_exact_non_cancelling_event_to_fence_mapping() -> None:
    _assert_workflow_mapping(
        "oracle-instance-power.yml",
        {"workflow_dispatch", "issue_comment"},
        POWER_GROUP,
    )


def test_research_job_canaries_have_exact_event_to_fence_mapping() -> None:
    assert _workflows_containing("research_runtime import enqueue") == set(
        EXPECTED_RESEARCH_PRODUCERS
    )
    for name, (events, group) in EXPECTED_RESEARCH_PRODUCERS.items():
        _assert_workflow_mapping(name, events, group)


def test_dds3_mass_launchers_have_exact_event_to_fence_mapping() -> None:
    expected = {
        "oracle-dds3-pilot10k-launch.yml": ({"pull_request_target"}, MASS_LAUNCH_GROUP),
        "oracle-dds3-pilot10k-operator.yml": ({"issue_comment"}, MASS_OPERATOR_GROUP),
    }
    assert _workflows_containing("systemctl start --no-block dds3-mass@") == set(expected)
    for name, (events, group) in expected.items():
        _assert_workflow_mapping(name, events, group)
        assert SHARED_FENCE in group
