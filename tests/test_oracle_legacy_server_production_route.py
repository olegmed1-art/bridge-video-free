import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTING = ROOT / 'ops/universal-video-runtime-routing.json'
WORKFLOW = ROOT / '.github/workflows/bridge-video-3.1-server.yml'
REMOTE = ROOT / 'ops/run_bridge_video_oracle_legacy_remote.sh'


def test_oracle_legacy_server_is_canary_only_before_cutover():
    routing = json.loads(ROUTING.read_text(encoding='utf-8'))
    route = routing['routes']['oracle_legacy_server']
    assert routing['active_production_route'] == 'github_actions_legacy'
    assert route['compute_location'] == 'oracle_server'
    assert route['production_default'] is False
    assert route['runtime_family'] == 'legacy_r26'
    assert routing['cutover']['server_legacy_candidate']['state'] == 'CANARY_ALLOWED_NOT_ACTIVE'
    assert routing['cutover']['server_legacy_candidate']['rollback_route'] == 'github_actions_legacy'


def test_server_workflow_fails_closed_and_uses_pinned_ssh():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'python ops/validate_universal_video_runtime_routing.py --require-active oracle_legacy_server' in text
    assert 'ops/oracle-legacy-server-canary-requests/*.txt' in text
    assert "canary requires isolated output folder" in text
    assert "canary database persistence forbidden" in text
    assert 'StrictHostKeyChecking=yes' in text
    assert 'EXPECTED_FINGERPRINT: SHA256:' in text
    assert 'concurrency:' in text and 'oracle-instance-workload-mutation' in text
    assert text.index('Resolve request and fail-closed route gate') < text.index('GOOGLE_DRIVE_OAUTH_JSON')
    assert "bash '$release/ops/run_bridge_video_oracle_legacy_remote.sh'" in text


def test_remote_runtime_preserves_legacy_revision_and_identity_contracts():
    text = REMOTE.read_text(encoding='utf-8')
    assert 'bridge_runtime_hardening_r26' in text
    assert "stable_job_id('drive'" in text
    assert 'check_completed_job.py' in text
    assert 'bridge_worker_3_1_free.py' in text
    assert 'run_drive_3_1_free_oidc.py' in text
    assert 'route_drive_job_outputs.py' in text
    assert 'persist_completed_drive_job' in text
    assert 'compute":"oracle_legacy_server' in text
    assert 'GOOGLE_DRIVE_OAUTH_JSON=' in text
