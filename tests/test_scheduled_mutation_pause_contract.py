from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_artifact_cleanup_is_manual_only_during_pause():
    text = read("artifact-cleanup.yml")
    assert "if: github.event_name != 'schedule'" in text
    assert "if: github.event_name == 'workflow_dispatch'" in text


def test_ai_queue_worker_is_manual_only_during_pause():
    text = read("bridge-ai-queue-worker.yml")
    assert "if: github.event_name == 'workflow_dispatch'" in text


def test_dds3_monitors_skip_scheduled_execution_during_pause():
    production = read("dds3-production-health-monitor.yml")
    oracle = read("oracle-ben-dds3-health-monitor.yml")
    assert "if: github.event_name != 'schedule'" in production
    assert "if: github.event_name != 'schedule'" in oracle
    assert "if: ${{ github.event_name == 'workflow_dispatch' }}" in oracle


def test_oracle_power_reconciliation_is_dormant_during_pause():
    text = read("oracle-manual-hold.yml")
    assert "if: ${{ github.event_name == 'schedule' && false }}" in text


def test_video_watchdog_skips_scheduled_execution_during_pause():
    text = read("oracle-universal-video-job.yml")
    assert "if: github.event_name != 'schedule'" in text
    assert "github.event_name != 'schedule' && needs.validate.outputs.should_execute == 'true'" in text
