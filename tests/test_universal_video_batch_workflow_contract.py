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
    assert "enqueue-batch-base64" in text
    assert "ORACLE_UNIVERSAL_VIDEO_BATCH_INTAKE_PASS" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "StrictHostKeyChecking=no" not in text
    assert "oracle-instance-workload-mutation" in text
    assert "cancel-in-progress: false" in text
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
