from pathlib import Path

WORKFLOW = Path(".github/workflows/dds3-runtime-container-proof.yml")


def test_dds3_runtime_container_workflow_is_triggerable_and_has_job():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch: {}" in source
    assert "pull_request:" in source
    assert "push:" in source
    assert "runtime-e2e:" in source
    assert "docker build -f dds3_runtime/Dockerfile" in source
    assert "DDS3_RUNTIME_E2E: PASS" in source
    assert "fallback_used'] is False" in source


def test_dds3_runtime_container_workflow_filters_cover_contract_test():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert ".github/workflows/dds3-runtime-container-proof.yml" in source
    assert "tests/test_dds3_runtime_container_workflow_contract.py" in source
