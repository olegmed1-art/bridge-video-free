from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-container-evidence.yml"


def test_bounded_diagnostic_accepts_runtime_json_format() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "value = json.loads(line)" in text
    assert "set(value) == {'error_code', 'status'}" in text
    assert "re.fullmatch(r'UV_CONTAINER_[A-Z0-9_]+" in text
    assert "json.dumps(value, separators=(',', ':'), sort_keys=True)" in text


def test_evidence_workflow_remains_non_activating_and_media_free() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "UNIVERSAL_VIDEO_CONTAINER_ACTIVATE=0" in text
    assert "video_job_submitted=false" in text
    assert "workflow_dispatch:" in text
    assert "paths:" in text
