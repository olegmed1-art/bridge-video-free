from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/universal_video_diana11_shadow_diagnostic.sh"
WORKFLOW = ROOT / ".github/workflows/oracle-diana11-shadow-preflight-diagnostic.yml"


def test_diagnostic_is_syntax_valid_and_read_only():
    text = SCRIPT.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    for forbidden in (
        "ffmpeg",
        "ffprobe",
        "faster_whisper",
        "WhisperModel",
        "enqueue",
        "submit-bridge",
        "submit_for",
        "drive_results",
        "GOOGLE_DRIVE_OAUTH",
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "service start",
        "service stop",
        "spool/inbox/",
    ):
        assert forbidden not in text
    assert "UV003_DIAG_STATUS=PASS" in text
    assert "UV003_DIAG_STATUS=FAIL" in text
    assert "UNEXPECTED_DIAGNOSTIC_FAILURE" in text
    assert "MAX_PREFLIGHT_OUTPUT_BYTES=65536" in text
    assert "ulimit -f 128" in text
    assert "timeout --signal=KILL 60" in text


def test_failure_vocabulary_is_bounded():
    text = SCRIPT.read_text(encoding="utf-8")
    codes = set(re.findall(r"emit_fail ([A-Z0-9_]+)", text))
    assert codes == {
        "NOT_ROOT",
        "EXPECTED_REVISION_INVALID",
        "RUNTIME_ENV_UNSAFE",
        "SOURCE_CHECKOUT_UNSAFE",
        "SPOOL_ROOT_UNSAFE",
        "RUNTIME_HEAD_INVALID",
        "RUNTIME_HEAD_MISMATCH",
        "GIT_STATUS_UNREADABLE",
        "RUNTIME_CHECKOUT_DIRTY",
        "RUNTIME_ENV_PARSE_FAILED",
        "RUNTIME_ENV_REVISION_INVALID",
        "RUNTIME_ENV_REVISION_MISMATCH",
        "RUNTIME_MODEL_INVALID",
        "FRESH_ID_CONFLICT",
        "PREFLIGHT_TARGET_UNSAFE",
        "PREFLIGHT_SUDOERS_UNSAFE",
        "PREFLIGHT_SUDOERS_INVALID",
        "OCARUN_IDENTITY_MISSING",
        "PREFLIGHT_ROOT_COMMAND_FAILED",
        "PREFLIGHT_ROOT_OUTPUT_INVALID",
        "PREFLIGHT_SUDO_COMMAND_FAILED",
        "PREFLIGHT_SUDO_OUTPUT_INVALID",
        "UNEXPECTED_DIAGNOSTIC_FAILURE",
    }
    assert 'printf \'UV003_DIAG_CODE=%s\\n\' "$1"' in text
    assert "echo \"ERROR:" not in text


def test_workflow_filters_remote_output_before_issue_comment():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "oracle-diana11-shadow-preflight-diagnostic-requests" in workflow
    assert "x['issue']==566" in workflow
    assert "UV003_DIAG_CODE=" in workflow
    assert "re.fullmatch" in workflow
    assert "raw" in workflow
    assert "safe" in workflow
    assert "gh issue comment" in workflow
    assert "No submit, ASR/media processing, retry, Drive publication, or production promotion" in workflow
    for forbidden in (
        "actions/upload-artifact",
        "submit-bridge",
        "universal-video-diana11 submit",
        "drive_results",
        "GOOGLE_DRIVE_OAUTH",
    ):
        assert forbidden not in workflow


if __name__ == "__main__":
    test_diagnostic_is_syntax_valid_and_read_only()
    test_failure_vocabulary_is_bounded()
    test_workflow_filters_remote_output_before_issue_comment()
