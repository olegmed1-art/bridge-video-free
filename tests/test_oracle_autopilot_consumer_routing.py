from pathlib import Path
import re
from ops.github_autopilot_db_route import TARGETS


EXPECTED = {
    'autopilot-chatgpt-role-callback.yml': ['role-callback'],
    'autopilot-codex-event-callback.yml': ['codex-ack','codex-terminal','codex-publication'],
    'autopilot-paused-reconcile.yml': ['diagnostics','reconcile','next-step'],
    'autopilot-mailbox-pre-rotation.yml': ['mailbox'],
    'autopilot-reconcile-diagnostic.yml': ['diagnostics'],
    'database-health-monitor.yml': ['health'],
}


def test_all_known_continuous_and_manual_read_consumers_are_leased():
    covered=set()
    for name,selectors in EXPECTED.items():
        text=(Path('.github/workflows')/name).read_text()
        actual=re.findall(r'python -m ops\.github_autopilot_db_route ([a-z-]+)',text)
        assert actual==selectors
        assert "github.ref == 'refs/heads/main'" in text
        assert 'secrets.ORACLE_SSH_PRIVATE_KEY' in text
        assert 'timeout-minutes: 20' in text
        covered.update(actual)
    assert covered==set(TARGETS)


def test_shared_neon_secrets_and_original_health_checks_remain():
    text=Path('.github/workflows/database-health-monitor.yml').read_text()
    assert 'python database/runtime_worker_preflight.py' in text
    assert 'python database/runtime_app_http_preflight.py' in text
    assert 'BRIDGE_HEALTH_DATABASE_URL: ${{ secrets.BRIDGE_HEALTH_DATABASE_URL }}' in text
    assert 'AUTOPILOT_HEALTH_DATABASE_URL: ${{ secrets.BRIDGE_HEALTH_DATABASE_URL }}' in text
    assert TARGETS['health'][:3]==('AUTOPILOT_HEALTH_DATABASE_URL','bridge_school_health_principal','database.runtime_health_preflight')


def test_continuous_receivers_do_not_bypass_the_route_wrapper():
    forbidden=['python -m oracle_autopilot.github_role_callback',
               'python -m oracle_autopilot.github_codex_callback',
               'python -m oracle_autopilot.github_codex_publication',
               'python -m oracle_autopilot.paused_reconcile',
               'python -m oracle_autopilot.next_step_reconcile']
    for name in EXPECTED:
        text=(Path('.github/workflows')/name).read_text()
        assert not any(command in text for command in forbidden)
