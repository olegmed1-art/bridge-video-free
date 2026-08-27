from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-job.yml"


def test_spool_repair_uses_default_agent_and_bounded_sudo():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--execution-user root" not in text
    assert "repair_cmd='sudo -n /bin/sh -ceu" in text
    assert "for d in inbox running done failed results" in text
    assert "UNIVERSAL_VIDEO_SPOOL_RUNTIME_REPAIR_PASS" in text


def test_submit_and_status_remain_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'submit_cmd="sudo -n /usr/local/sbin/universal-video submit-base64' in text
    assert 'status_cmd="sudo -n /usr/local/sbin/universal-video status' in text
    assert "TECHNICAL_CONFORMANT) echo ORACLE_UNIVERSAL_VIDEO_JOB_TECHNICAL_CONFORMANCE_PASS; exit 0" in text
    assert "REVIEW|NONCONFORMANT|CONFLICT|FAILED) exit 1" in text
