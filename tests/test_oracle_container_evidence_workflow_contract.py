import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-container-evidence.yml"


def test_bounded_diagnostic_accepts_runtime_json_format() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'python ops/bounded_container_log_diagnostic.py "$RUNNER_TEMP/container.log"' in text
    assert "python - <<'PY'" not in text


def test_bounded_parser_canonicalizes_runtime_json_without_leaking_raw_log() -> None:
    path = ROOT / "ops/bounded_container_log_diagnostic.py"
    spec = importlib.util.spec_from_file_location("bounded_container_log_diagnostic", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.bounded_diagnostics(
        [
            "token=must-not-leak",
            '{"status": "FAILED", "error_code": "UV_CONTAINER_MODEL_UNAVAILABLE"}',
            '{"status":"FAILED","error_code":"OTHER_FAILURE"}',
        ]
    )

    assert result == ['{"error_code":"UV_CONTAINER_MODEL_UNAVAILABLE","status":"FAILED"}']


def test_evidence_workflow_remains_non_activating_and_media_free() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "UNIVERSAL_VIDEO_CONTAINER_ACTIVATE=0" in text
    assert "video_job_submitted=false" in text
    assert "workflow_dispatch:" in text
    assert "paths:" in text
