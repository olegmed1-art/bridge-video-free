from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'ops/universal_video_sidecar_diagnostic.sh').read_text(encoding='utf-8')
WORKFLOW = (ROOT / '.github/workflows/oracle-universal-video-sidecar-diagnostic.yml').read_text(encoding='utf-8')


def test_diagnostic_is_read_only_and_fixed():
    assert "EXPECTED_RUNTIME_COMMIT='7e46f0327d6094400e0d35ec6af20408cc97683e'" in SCRIPT
    assert "universal-video.service" in SCRIPT
    assert "service_user_import" in SCRIPT
    assert "PYTHONDONTWRITEBYTECODE=1" in SCRIPT
    assert "systemctl start" not in SCRIPT
    assert "systemctl restart" not in SCRIPT
    assert "systemctl enable" not in SCRIPT
    assert "chmod " not in SCRIPT
    assert "chown " not in SCRIPT
    assert "submit-base64" not in SCRIPT
    assert "UNIVERSAL_VIDEO_SIDECAR_DIAGNOSTIC_PASS" in SCRIPT


def test_workflow_has_isolated_push_request_and_pinned_payload():
    assert "ops/oracle-universal-video-sidecar-diagnostic-requests/*.json" in WORKFLOW
    assert "expected exactly one sidecar diagnostic request" in WORKFLOW
    assert "assert x['operation'] == 'diagnose'" in WORKFLOW
    assert "EXPECTED_SCRIPT_BLOB: 0bbb421711f64f46c41182476cd7fa3122949d36" in WORKFLOW
    assert "git hash-object ops/universal_video_sidecar_diagnostic.sh" in WORKFLOW
    assert "ORACLE_HOST: 158.180.47.161" in WORKFLOW
    assert "ORACLE_USER: ubuntu" in WORKFLOW
    assert "oracle-instance-workload-mutation" in WORKFLOW


def test_workflow_publishes_only_bounded_diagnostic_fields():
    assert 'uv-sidecar-diagnostic-raw.txt' in WORKFLOW
    assert 'uv-sidecar-diagnostic-safe.txt' in WORKFLOW
    assert 'cat "$safe"' in WORKFLOW
    assert 'cat "$raw"' not in WORKFLOW
    assert "client_secret" not in WORKFLOW
    assert "refresh_token" not in WORKFLOW
