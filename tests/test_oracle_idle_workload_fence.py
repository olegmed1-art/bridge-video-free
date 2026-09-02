from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHARED_FENCE = "oracle-instance-workload-mutation"


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
    assert f"group: {SHARED_FENCE}" in power
    assert "cancel-in-progress: false" in power
