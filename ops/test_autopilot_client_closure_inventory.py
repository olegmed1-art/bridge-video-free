"""Exercise drift detection and prove a static pass cannot authorize cutover."""
import tempfile
from pathlib import Path
from unittest.mock import patch

import autopilot_client_closure_inventory as checker


def main():
    assert checker.inventory()['errors'] == []
    with tempfile.TemporaryDirectory() as temporary:
        workflows = Path(temporary)
        for name in checker.OWNER_WORKFLOWS:
            (workflows / name).write_text('secrets.NEON_DATABASE_URL')
        for name, targets in checker.ROUTED_WORKFLOWS.items():
            target = workflows / name
            target.write_text((target.read_text() if target.exists() else '') +
                              ''.join(f'\npython -m ops.github_autopilot_db_route {route}'
                                      for route in targets))
        with patch.object(checker, 'WORKFLOWS', workflows):
            assert checker.inventory()['errors'] == []
            callback = workflows / 'autopilot-chatgpt-role-callback.yml'
            callback.write_text(callback.read_text().replace('role-callback', 'diagnostics'))
            assert any(error.get('group') == 'workflow_route_targets' and
                       error.get('workflow') == callback.name and
                       error.get('added') == ['diagnostics'] and
                       error.get('removed') == ['role-callback']
                       for error in checker.inventory()['errors'])
            callback.write_text(callback.read_text().replace('diagnostics', 'role-callback'))
            (workflows / 'new-autopilot-writer.yml').write_text('secrets.NEON_DATABASE_URL')
            assert checker.inventory()['errors'][0]['added'] == ['new-autopilot-writer.yml']
            (workflows / 'new-autopilot-writer.yml').unlink()
            (workflows / 'autopilot-chatgpt-role-callback.yml').unlink()
            assert any('autopilot-chatgpt-role-callback.yml' in error.get('removed', [])
                       for error in checker.inventory()['errors'])
    assert checker.inventory()['cutover_ready'] is False
    assert checker.inventory()['live_sessions_verified'] is False
    print('AUTOPILOT_CLIENT_CLOSURE_STATIC_INVENTORY_PASS')


if __name__ == '__main__':
    main()
