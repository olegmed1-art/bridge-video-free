from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/probe_uv003_operator_bootstrap.py"
WORKFLOW = ROOT / ".github/workflows/oracle-diana11-003-bootstrap-diagnostic.yml"


def load_module():
    spec = importlib.util.spec_from_file_location("uv003_bootstrap_probe", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_frozen_identity_and_read_only_surface():
    module = load_module()
    assert module.JOB_ID == "diana11-shadow-20260826-001"
    assert module.EXPECTED_COMMIT == "6a4e8248eedd00f849fcefd1bf41a51b26f5e7c6"
    assert module.EXPECTED_MODEL == "small"
    assert module.EXPECTED_PROCESSING_FINGERPRINT == (
        "371661d2a1858e576e2f618ddf504da724edc30089a9af88f9dd3a140ca30951"
    )
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "write_text",
        "write_bytes",
        "os.replace",
        "os.rename",
        "unlink(",
        "mkdir(",
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "submit-bridge",
        "publish-bridge",
        "requests.post",
        "requests.put",
        "requests.patch",
        "requests.delete",
        "GOOGLE_DRIVE",
        "ffmpeg",
        "faster_whisper",
    ):
        assert forbidden not in source
    assert 'print(f"UV003_BOOTSTRAP_DIAGNOSTIC={code}")' in source


def test_runtime_env_classification_is_coarse_and_deterministic():
    module = load_module()
    valid = (
        "UNIVERSAL_VIDEO_SOURCE_COMMIT=" + module.EXPECTED_COMMIT + "\n"
        "UNIVERSAL_VIDEO_WHISPER_MODEL=small\n"
    ).encode()
    assert module.parse_runtime_env(valid) == (module.EXPECTED_COMMIT, "small")
    assert module.parse_runtime_env(b"") == "ENV_STRUCTURE_INVALID"
    assert module.parse_runtime_env(b"A=1\x00B=2\n") == "ENV_STRUCTURE_INVALID"
    assert module.parse_runtime_env(b"\xff") == "ENV_ENCODING_INVALID"
    assert module.parse_runtime_env(b"NOT_AN_ASSIGNMENT\n") == "ENV_STRUCTURE_INVALID"
    assert module.parse_runtime_env(b"WHISPER_MODEL=small\n") == "ENV_SOURCE_PIN_MISSING"
    duplicate = valid + ("UNIVERSAL_VIDEO_SOURCE_COMMIT=" + module.EXPECTED_COMMIT + "\n").encode()
    assert module.parse_runtime_env(duplicate) == "ENV_SOURCE_PIN_MULTIPLE"


def test_external_code_vocabulary_is_fixed():
    module = load_module()
    expected = {
        "ROOT_REQUIRED",
        "SOURCE_LAYOUT",
        "SOURCE_HEAD_READ_FAILED",
        "SOURCE_HEAD_MISMATCH",
        "SOURCE_STATUS_FAILED",
        "SOURCE_DIRTY",
        "ENV_MISSING_OR_UNSAFE",
        "ENV_TOO_LARGE",
        "ENV_READ_FAILED",
        "ENV_ENCODING_INVALID",
        "ENV_STRUCTURE_INVALID",
        "ENV_SOURCE_PIN_MISSING",
        "ENV_SOURCE_PIN_MULTIPLE",
        "ENV_SOURCE_PIN_MISMATCH",
        "ENV_MODEL_MISMATCH",
        "PROCESSING_FINGERPRINT_MISMATCH",
        "RUNTIME_PYTHON_MISSING",
        "RECEIPT_READER_MISSING",
        "WORKER_USER_MISSING",
        "SERVICE_INACTIVE",
        "SPOOL_LAYOUT",
        "SPOOL_GUARD_FAILED",
        "JOB_ID_CONFLICT",
        "ROOT_CONTROL_UNSAFE",
        "PASS",
        "INTERNAL_FAILURE",
    }
    assert module.ALLOWED_CODES == expected


def test_workflow_is_request_only_and_cannot_execute_job():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch" not in source
    assert "ops/oracle-diana11-003-bootstrap-diagnostic-requests/*.json" in source
    assert "x['issue']==615" in source
    assert "x['probe']=='uv003-bootstrap-readonly-v1'" in source
    assert "sudo -n python3 /tmp/uv003-bootstrap-diagnostic.py" in source
    assert "sudo -n bash /tmp/uv003-operator-source.sh status-bridge" in source
    runtime = source.split("\n  diagnose:\n", 1)[1]
    for forbidden in (
        "submit-bridge",
        "publish-bridge",
        "readback-bridge",
        "actions/upload-artifact",
        "systemctl restart",
        "systemctl stop",
        "systemctl start",
    ):
        assert forbidden not in runtime

def test_runtime_python_symlink_resolves_to_bounded_executable(tmp_path):
    module = load_module()
    target = tmp_path / "python3"
    target.write_bytes(b"#!/bin/sh\nexit 0\n")
    target.chmod(0o755)
    link = tmp_path / "python"
    link.symlink_to(target.name)
    assert module.safe_executable_regular_target(link, maximum=1024)

    target.chmod(0o644)
    assert not module.safe_executable_regular_target(link, maximum=1024)
    target.chmod(0o755)
    target.write_bytes(b"x" * 1025)
    assert not module.safe_executable_regular_target(link, maximum=1024)

    link.unlink()
    link.symlink_to("missing-python")
    assert not module.safe_executable_regular_target(link, maximum=1024)
