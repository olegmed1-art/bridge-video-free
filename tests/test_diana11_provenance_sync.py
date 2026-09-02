from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/universal_video_diana11_provenance_sync.sh"
WORKFLOW = ROOT / ".github/workflows/oracle-diana11-provenance-sync.yml"


def test_sync_script_is_syntax_valid_and_narrow():
    text = SCRIPT.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    assert "readonly SERVICE='universal-video.service'" in text
    assert "readonly RUNTIME_ENV=\"$BASE_DIR/universal-video.env\"" in text
    assert "UNIVERSAL_VIDEO_SOURCE_COMMIT=" in text
    assert "queue_empty || emit_fail JOB_QUEUE_NOT_EMPTY" in text
    assert "queue_empty || emit_fail JOB_QUEUE_CHANGED" in text
    assert "systemctl stop \"$SERVICE\"" in text
    assert "systemctl start \"$SERVICE\"" in text
    assert "SERVICE_PROCESS_ENV_MISMATCH" in text
    assert "ROLLBACK_FAILED" in text
    for forbidden in (
        "assistant-lab.service",
        "dds3.service",
        "ffmpeg",
        "ffprobe",
        "faster_whisper",
        "WhisperModel",
        "enqueue",
        "submit-bridge",
        "submit_for",
        "drive_results",
        "GOOGLE_DRIVE_OAUTH",
        "systemctl restart",
        "apt-get",
        "pip install",
    ):
        assert forbidden not in text


def test_only_non_secret_revision_key_is_rewritten():
    text = SCRIPT.read_text(encoding="utf-8")
    rewrite = text.split("if ! RUNTIME_ENV=", 1)[1].split("then\n  emit_fail ENV_REWRITE_FAILED", 1)[0]
    assert "UNIVERSAL_VIDEO_SOURCE_COMMIT=" in rewrite
    assert "UNIVERSAL_VIDEO_WHISPER_MODEL=" not in rewrite
    assert "WHISPER_MODEL=" not in rewrite
    assert "GOOGLE" not in rewrite
    assert "SECRET" not in rewrite
    assert "TOKEN" not in rewrite
    assert "PASSWORD" not in rewrite


def test_failure_vocabulary_is_bounded():
    text = SCRIPT.read_text(encoding="utf-8")
    codes = set(re.findall(r"emit_fail ([A-Z0-9_]+)", text))
    assert codes == {
        "NOT_ROOT",
        "EXPECTED_REVISION_INVALID",
        "RUNTIME_ENV_UNSAFE",
        "SOURCE_CHECKOUT_UNSAFE",
        "SPOOL_ROOT_UNSAFE",
        "SERVICE_NOT_ACTIVE",
        "JOB_QUEUE_NOT_EMPTY",
        "SOURCE_HEAD_INVALID",
        "SOURCE_HEAD_MISMATCH",
        "SOURCE_TRACKED_DIRTY",
        "GIT_STATUS_UNREADABLE",
        "SOURCE_UNTRACKED_DIRTY",
        "RUNTIME_ENV_PARSE_FAILED",
        "RUNTIME_ENV_REVISION_INVALID",
        "RUNTIME_MODEL_INVALID",
        "SERVICE_PROCESS_UNSAFE",
        "SERVICE_PROCESS_ENV_UNREADABLE",
        "SERVICE_PROCESS_REVISION_INVALID",
        "SERVICE_PROCESS_MODEL_INVALID",
        "SERVICE_PROCESS_ENV_MISMATCH",
        "SERVICE_STOP_FAILED",
        "JOB_QUEUE_CHANGED",
        "ENV_REWRITE_FAILED",
        "SERVICE_START_FAILED",
        "UNEXPECTED_SYNC_FAILURE",
    }
    assert "code='ROLLBACK_FAILED'" in text
    assert "UV003_SYNC_CODE=ALREADY_ALIGNED" in text
    assert "UV003_SYNC_CODE=PROVENANCE_ALIGNED" in text


def test_workflow_filters_evidence_and_never_exposes_raw_output():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "oracle-diana11-provenance-sync-requests" in workflow
    assert "x['issue']==566" in workflow
    assert "UV003_SYNC_CODE=" in workflow
    assert "re.fullmatch" in workflow
    assert "uv003-provenance-safe.txt" in workflow
    assert "No job submit, ASR/media processing, Drive publication, or production promotion" in workflow
    for forbidden in (
        "actions/upload-artifact",
        "submit-bridge",
        "universal-video-diana11 submit",
        "drive_results",
        "GOOGLE_DRIVE_OAUTH",
    ):
        assert forbidden not in workflow


if __name__ == "__main__":
    test_sync_script_is_syntax_valid_and_narrow()
    test_only_non_secret_revision_key_is_rewritten()
    test_failure_vocabulary_is_bounded()
    test_workflow_filters_evidence_and_never_exposes_raw_output()
