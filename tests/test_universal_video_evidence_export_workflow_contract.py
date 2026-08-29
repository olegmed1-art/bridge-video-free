from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-evidence-export.yml"


def test_export_workflow_has_a_fixed_read_only_remote_surface():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ops/oracle-universal-video-evidence-export-requests/*.json" in text
    assert "expected exactly one evidence export request" in text
    assert "assert set(wrapper)=={'request_id','issue','export'}" in text
    assert "assert wrapper['issue']==819" in text
    assert "sudo -n /usr/local/sbin/universal-video-evidence-export" in text
    assert "install -d -m 0750 -o root" not in text
    assert "runuser -u universal-video" not in text
    assert "chown root:universal-video" not in text
    assert "publication_state']=='NOT_PUBLISHED'" in text
    assert "school_canon_changed'] is False" in text


def test_raw_remote_output_is_never_published_or_logged():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'uv-export-raw.txt' in text
    assert 'uv-export-safe.json' in text
    assert 'cat "$RUNNER_TEMP/uv-export-raw.txt"' not in text
    assert 'cat "$RUNNER_TEMP/uv-export-safe.json"' in text
    assert "No transcript text, raw command output, media" in text
    assert "transcript.jsonl" in text
    assert "transcript.txt" in text
    assert "bridge_positions_profiled_shadow.jsonl" in text


def test_workflow_cannot_start_compute_or_promote_results():
    text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "universal-video submit",
        "spool/inbox",
        "systemctl start universal-video",
        "workflow_dispatch",
        "canonical_promotion_allowed'] is True",
        "school_canon_changed'] is True",
    )
    for token in forbidden:
        assert token not in text
    assert "External DDS3 non-regression" in text
    assert "fallback_used') is False" in text


def test_oci_run_command_crosses_only_the_exact_bounded_sudo_surface():
    text = WORKFLOW.read_text(encoding="utf-8")
    remote = text.split('remote="$(cat <<EOF', 1)[1].split("EOF", 1)[0]
    assert remote.count("sudo -n") == 1
    assert "sudo -n /usr/local/sbin/universal-video-evidence-export" in remote
    assert "git -C" not in remote
    for root_only in ("install -o root", "install -d", "chown ", "runuser "):
        assert root_only not in remote
