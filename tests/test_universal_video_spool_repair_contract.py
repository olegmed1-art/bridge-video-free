from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-job.yml"


def test_spool_operations_use_unprivileged_service_principal():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '--execution-user "$execution_user"' in text
    assert "--execution-user root" not in text
    assert "universal-video 2>&1" in text
    assert "UNIVERSAL_VIDEO_SPOOL_SERVICE_WRITE_PASS" in text
    assert "sudo -n /usr/local/sbin/universal-video-spool-repair" not in text


def test_submit_and_status_remain_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'submit_cmd="/usr/local/sbin/universal-video submit-base64' in text
    assert 'status_cmd="/usr/local/sbin/universal-video status' in text
    assert 'submit_cmd="sudo ' not in text
    assert 'status_cmd="sudo ' not in text
    assert "TECHNICAL_CONFORMANT) echo ORACLE_UNIVERSAL_VIDEO_JOB_TECHNICAL_CONFORMANCE_PASS; exit 0" in text
    assert "REVIEW|NONCONFORMANT|CONFLICT|FAILED) exit 1" in text
