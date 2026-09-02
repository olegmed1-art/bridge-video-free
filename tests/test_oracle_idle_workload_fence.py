from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHARED_FENCE = "oracle-instance-workload-mutation"
POWER_GROUP = (
    "group: ${{ github.event_name == 'workflow_dispatch' && "
    "'oracle-instance-workload-mutation' || (github.event_name == "
    "'issue_comment' && github.actor == github.repository_owner && "
    "contains(fromJSON('[\"/oracle-instance status\",\"/oracle-instance start\","
    "\"/oracle-instance stop\"]'), github.event.comment.body)) && "
    "'oracle-instance-workload-mutation' || "
    "format('oracle-instance-power-noop-{0}', github.run_id) }}"
)


def _workflow_text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_all_submit_bridge_oracle_producers_share_stop_fence() -> None:
    producers = {
        path.name: path.read_text(encoding="utf-8")
        for path in WORKFLOWS.glob("*.yml")
        if " submit-bridge" in path.read_text(encoding="utf-8")
    }
    assert producers
    for name, text in producers.items():
        assert SHARED_FENCE in text, name
        assert "github.event_name == 'pull_request'" in text, name
        assert "github.event.pull_request.number" in text, name
        assert "cancel-in-progress: false" in text, name


def test_stop_consumer_uses_same_non_cancelling_fence() -> None:
    power = _workflow_text("oracle-instance-power.yml")
    group_lines = [line.strip() for line in power.splitlines() if line.strip().startswith("group:")]
    assert group_lines == [POWER_GROUP]
    assert "cancel-in-progress: false" in power


def test_research_job_production_canaries_share_stop_fence() -> None:
    producers = {
        path.name: path.read_text(encoding="utf-8")
        for path in WORKFLOWS.glob("*.yml")
        if "research_runtime import enqueue" in path.read_text(encoding="utf-8")
    }
    assert producers
    for name, workflow in producers.items():
        assert SHARED_FENCE in workflow, name
        assert "github.event_name == 'pull_request'" in workflow, name
        assert "github.event.pull_request.number" in workflow, name
        assert "github.event.comment.body == '/research-job " in workflow, name
        assert "noop-{0}" in workflow, name
        assert "github.run_id" in workflow, name
        assert "cancel-in-progress: false" in workflow, name
