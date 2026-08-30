from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-job.yml"


def test_spool_operations_use_exact_installed_bounded_ssh_surface():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "oci instance-agent command" not in text
    assert "timeout 180 ssh" in text
    assert "StrictHostKeyChecking=yes" in text
    assert '"$ORACLE_USER@$ORACLE_HOST" "$command" 2>/dev/null' in text
    assert "sudo -n -u ocarun sudo -n /usr/local/sbin/universal-video-spool-repair" in text
    assert "UNIVERSAL_VIDEO_SPOOL_RUNTIME_REPAIR_PASS" in text


def test_submit_and_status_remain_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'submit_cmd="sudo -n -u ocarun sudo -n /usr/local/sbin/universal-video submit-drive-base64' in text
    assert 'status_cmd="sudo -n -u ocarun sudo -n /usr/local/sbin/universal-video status' in text
    assert "PRE_SUBMIT_ERROR_CODE=UV_SUBMIT_COMMAND_FAILED" not in text
    assert "code='UV_SUBMIT_COMMAND_FAILED'" in text
    assert "PRE_SUBMIT_ERROR_CODE=UV_STATUS_COMMAND_FAILED" in text
    assert "TECHNICAL_CONFORMANT) echo ORACLE_UNIVERSAL_VIDEO_JOB_TECHNICAL_CONFORMANCE_PASS; exit 0" in text
    assert "REVIEW|NONCONFORMANT|CONFLICT|FAILED)" in text
    assert 'PRE_SUBMIT_ERROR_CODE=UV_JOB_TERMINAL_$last' in text
