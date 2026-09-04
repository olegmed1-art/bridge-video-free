import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-batch-intake.yml"
REQUEST_SCHEMA = ROOT / "ops/universal-video-batch-request.schema.json"
INTAKE_SCHEMA = ROOT / "ops/universal-video-batch-intake.schema.json"


def test_batch_transport_is_durable_bounded_and_project_neutral():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ops/oracle-universal-video-batch-requests/*.json" in text
    assert "expected exactly one batch request" in text
    assert "validate_intake_request" in text
    assert "resume-batch-base64 '$REQUEST_KEY' '$PAYLOAD'" in text
    assert "universal-video-batch-status-v1" in text
    assert "reconciled_existing" in text
    assert "ORACLE_UNIVERSAL_VIDEO_BATCH_INTAKE_PASS" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "StrictHostKeyChecking=no" not in text
    assert "/run/lock/oracle-workload-mutation.lock" in text
    assert "github.sha" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 360" in text
    assert "EXECUTION_DEADLINE_EPOCH=$(( $(date +%s) + 21000 ))" in text
    assert text.count("timeout \"$ssh_timeout\" ssh") == 1
    assert "remaining=$(( EXECUTION_DEADLINE_EPOCH - now - 300 ))" in text
    assert 'ssh_timeout="$(budget_max 4800)"' in text
    assert "for attempt in 1 2 3" in text
    assert '--max-wait-seconds "$(budget_max 600)"' in text
    assert "recover_instance_after_transport_loss" in text
    assert "enqueue_rc == 255 && attempt < 3" in text
    assert "ServerAliveCountMax=2" in text
    preflight = text.split("- name: Resolve pinned SSH transport", 1)[0]
    assert "STARTING)" in preflight
    assert "STOPPING)" in preflight
    assert '--wait-for-state STOPPED --max-wait-seconds "$(budget_max 600)"' in preflight
    assert "'CANARY_REVIEW'" in text
    ssh_setup = text.split("- name: Resolve pinned SSH transport", 1)[1].split(
        "- name: Enqueue metadata", 1
    )[0]
    assert "recover_instance_after_scan_loss" in ssh_setup
    assert "for attempt in 1 2 3" in ssh_setup
    assert "scan_rc == 3 && attempt < 3" in ssh_setup
    assert "STOPPING)" in ssh_setup
    assert "sleep 60" not in text
    assert "project" not in json.loads(INTAKE_SCHEMA.read_text(encoding="utf-8"))["properties"]


def test_outer_request_can_only_target_the_exact_oracle_host():
    schema = json.loads(REQUEST_SCHEMA.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["host"]["const"] == "158.180.47.161"
    assert schema["properties"]["user"]["const"] == "ubuntu"
    assert schema["properties"]["expected_host_fingerprint"]["const"].startswith("SHA256:")
    assert "command" not in schema["properties"]


def test_batch_cleanup_failure_is_retained_as_bounded_evidence():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "enqueue_rc=$?" in text
    assert 'echo "UV_BATCH_ERROR_CODE=$intake_code"' in text
    assert "UV_BATCH_ERROR_CODE=UV_BATCH_ENQUEUE_FAILED" in text
    for code in (
        "UV_INTAKE_DISK_FULL",
        "UV_INTAKE_READ_ONLY",
        "UV_INTAKE_PERMISSION_DENIED",
        "UV_INTAKE_CONTRACT_INVALID",
        "UV_INTAKE_IO_FAILED",
        "UV_INTAKE_EXECUTION_FAILED",
        "UV_INTAKE_CLEANUP_FAILED",
        "UV_BATCH_INTAKE_INVALID",
        "UV_BATCH_INTAKE_FAILED",
    ):
        assert code in text
    assert 'value.get("error_code") in allowed' in text
    assert 'echo "$result"' not in text
