from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOLD = ROOT / '.github/workflows/oracle-manual-hold.yml'
CANDIDATE = ROOT / '.github/workflows/oracle-instance-idle-candidate.yml'


def test_manual_hold_is_owner_bounded_and_duration_bounded():
    text = HOLD.read_text(encoding='utf-8')
    assert "github.actor == github.repository_owner" in text
    assert "startsWith(github.event.comment.body, '/oracle-hold ')" in text
    assert "hours must be between 0 and 12" in text
    assert "hours * 3600" in text
    assert "gh workflow run oracle-instance-power.yml" in text
    assert "-f action=start" in text


def test_hold_state_is_durable_and_release_is_fail_closed():
    text = HOLD.read_text(encoding='utf-8')
    assert "issues/751/comments?per_page=100" in text
    assert "[oracle-hold state] until_epoch=" in text
    assert "assistant_lab.oracle_idle_snapshot()" in text
    assert "oracle-universal-video-job.yml/runs?per_page=100" in text
    assert "sleep 600" in text
    assert "Recheck hold was not extended during grace" in text
    assert "-f action=stop" in text
    assert "OCI_" not in text
    assert "ocid1." not in text


def test_existing_idle_candidate_refuses_stop_during_active_hold():
    text = CANDIDATE.read_text(encoding='utf-8')
    assert "issues: read" in text
    assert "Check owner manual hold" in text
    assert "issues/751/comments?per_page=100" in text
    assert "automatic stop suppressed by owner manual hold" in text
    assert "steps.hold.outputs.active != 'true'" in text
    assert "steps.idle.outputs.stop_allowed == 'true'" in text
    assert "sleep 600" in text
