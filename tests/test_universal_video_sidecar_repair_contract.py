from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "ops/universal_video_sidecar_repair.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/oracle-universal-video-sidecar-repair.yml").read_text(encoding="utf-8")


def test_repair_is_fixed_no_media_sidecar_operation():
    assert "EXPECTED_RUNTIME_COMMIT='7e46f0327d6094400e0d35ec6af20408cc97683e'" in SCRIPT
    assert "assistant-lab.service" in SCRIPT
    assert "fallback_used" in SCRIPT
    assert "QUEUED_OR_RUNNING_JOB" in SCRIPT
    assert "runtime_import=deferred_to_authoritative_systemd_unit" in SCRIPT
    assert "IMPORT_PERMISSION" not in SCRIPT
    assert 'systemctl start "$SERVICE"' in SCRIPT
    assert "UNIVERSAL_VIDEO_SIDECAR_REPAIR_PASS" in SCRIPT
    assert "submit-base64" not in SCRIPT
    assert "drive_results" not in SCRIPT
    assert "WhisperModel" not in SCRIPT


def test_workflow_exposes_only_exact_owner_command_and_pinned_payload():
    assert "github.event.comment.user.login == 'olegmed1-art'" in WORKFLOW
    assert "github.event.comment.body == '/oracle uv-sidecar-repair'" in WORKFLOW
    assert "workflow_dispatch" not in WORKFLOW
    assert "ORACLE_HOST: 158.180.47.161" in WORKFLOW
    assert "ORACLE_USER: ubuntu" in WORKFLOW
    assert "EXPECTED_SCRIPT_BLOB: bd374addc702b666430fa83cc70f73d446fefa70" in WORKFLOW
    assert "git hash-object ops/universal_video_sidecar_repair.sh" in WORKFLOW
    assert "'sudo -n /bin/bash -s'" in WORKFLOW


def test_workflow_never_publishes_raw_ssh_output():
    assert 'cat "$RUNNER_TEMP/uv-sidecar-repair-raw.txt"' not in WORKFLOW
    assert 'cat "$safe"' in WORKFLOW
    assert "client_secret" not in WORKFLOW
    assert "refresh_token" not in WORKFLOW
