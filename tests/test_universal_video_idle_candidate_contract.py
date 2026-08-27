from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / ".github/workflows/oracle-instance-idle-candidate.yml"
VIDEO = ROOT / ".github/workflows/oracle-universal-video-job.yml"
POWER = ROOT / ".github/workflows/oracle-instance-power.yml"


def test_terminal_video_work_dispatches_external_idle_candidate():
    text = VIDEO.read_text(encoding="utf-8")
    assert "permissions:\n  actions: write\n  contents: read\n  issues: write" in text
    assert "- name: Signal external idle candidate" in text
    assert "if: ${{ always() }}" in text
    assert "gh workflow run oracle-instance-idle-candidate.yml" in text
    assert '-f source_run_id="$GITHUB_RUN_ID"' in text


def test_idle_candidate_waits_and_fails_closed_before_bounded_stop():
    text = CANDIDATE.read_text(encoding="utf-8")
    assert "permissions:\n  actions: write\n  contents: read" in text
    assert "cancel-in-progress: true" in text
    assert "sleep 600" in text
    assert "assistant_lab.oracle_idle_snapshot()" in text
    assert "oracle-universal-video-job.yml/runs?per_page=100" in text
    assert '{"queued","in_progress","waiting","requested","pending"}' in text
    assert "active == 0 && uv_active == 0" in text
    assert "steps.idle.outputs.stop_allowed == 'true'" in text
    assert "gh workflow run oracle-instance-power.yml" in text
    assert "-f action=stop" in text
    assert "OCI_" not in text
    assert "ocid1." not in text


def test_downstream_power_boundary_remains_exact_and_idle_gated():
    text = POWER.read_text(encoding="utf-8")
    assert "ocid1.instance.oc1.eu-frankfurt-1.antheljtruoejaica7hj5oubnh2cctnjr7ti7llcgo6ho6wdvgvui6td7saq" in text
    assert "options: [status, start, stop]" in text
    assert "steps.idle.outputs.idle_state == 'IDLE'" in text
    assert "Stop exact instance only with IDLE proof" in text
