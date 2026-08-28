from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / ".github/workflows/oracle-instance-idle-candidate.yml"
VIDEO = ROOT / ".github/workflows/oracle-universal-video-job.yml"
POWER = ROOT / ".github/workflows/oracle-instance-power.yml"
AUTO = ROOT / ".github/workflows/oracle-instance-power-auto.yml"
EPOCH_PROBE = ROOT / ".github/workflows/oracle-epoch-readonly-probe.yml"


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
    assert '-f idle_source_run_id="$SOURCE_RUN_ID"' in text
    assert "OCI_" not in text
    assert "ocid1." not in text


def test_downstream_power_boundary_remains_exact_and_idle_gated():
    text = POWER.read_text(encoding="utf-8")
    assert "ocid1.instance.oc1.eu-frankfurt-1.antheljtruoejaica7hj5oubnh2cctnjr7ti7llcgo6ho6wdvgvui6td7saq" in text
    assert "options: [status, start, stop]" in text
    assert "idle_source_run_id:" in text
    assert "actions: read" in text
    assert "group: oracle-instance-workload-mutation" in text
    assert "Revalidate automatic stop epoch" in text
    assert 'row.get("status")!="completed"' in text
    assert 'r.get("event")!="pull_request"' in text
    assert "steps.epoch.outputs.epoch_state == 'CURRENT'" in text
    assert "Refuse stale automatic stop" in text
    assert "steps.idle.outputs.idle_state == 'IDLE'" in text
    assert "Stop exact instance only with IDLE proof" in text
    assert "controller: manual only" in text


def test_video_and_power_mutations_share_a_non_cancelling_lock():
    video = VIDEO.read_text(encoding="utf-8")
    power = POWER.read_text(encoding="utf-8")
    assert "'oracle-instance-workload-mutation'" in video
    assert "oracle-universal-video-pr-{0}" in video
    assert "group: oracle-instance-workload-mutation" in power
    for text in (video, power):
        assert "cancel-in-progress: false" in text


def test_video_watchdog_is_rare_and_refuses_durable_receipt_replay():
    text = VIDEO.read_text(encoding="utf-8")
    assert "cron: '17 * * * *'" in text
    assert "cron: '*/5 * * * *'" not in text
    assert "should_execute: ${{ steps.request.outputs.should_execute }}" in text
    assert "gh api --paginate --slurp" in text
    assert "issues/$issue/comments?per_page=100" in text
    assert 'marker="Universal Video / $profile / $job_id"' in text
    assert "durable request receipt present; replay refused" in text
    assert "needs.validate.outputs.should_execute == 'true'" in text


def test_oracle_power_auto_controller_has_no_timer_or_push_trigger():
    text = AUTO.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "\n  schedule:" not in text
    assert "\n  push:" not in text
    assert "cron:" not in text
    assert "manually dispatched bounded action" in text


def test_epoch_probe_is_read_only_and_owner_bounded():
    text = EPOCH_PROBE.read_text(encoding="utf-8")
    assert "github.actor == github.repository_owner" in text
    assert "startsWith(github.event.comment.body, '/oracle-epoch ')" in text
    assert "actions: read" in text
    assert "contents: read" in text
    assert "issues: write" in text
    assert "oracle-universal-video-job.yml/runs?per_page=100" in text
    assert 'row.get("status")!="completed"' in text
    assert '"STALE" if newer else "CURRENT"' in text
    assert "OCI_" not in text
    assert "ocid1." not in text
    assert "instance action" not in text
    assert "gh workflow run oracle-instance-power.yml" not in text
