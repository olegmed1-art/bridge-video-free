"""Negative source-selection checks for the future production backup guard."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import oracle_light_production_backup_preflight as guard


def service(uri):
    with patch.object(guard.subprocess, 'run', return_value=SimpleNamespace(stdout='MainPID=42\nActiveState=active\n')):
        with patch.object(Path, 'read_bytes', return_value=(
            'AUTOPILOT_DB_BACKEND=postgresql\0'
            'AUTOPILOT_PG_HOST=127.0.0.1\0AUTOPILOT_PG_PORT=55432\0'
            'AUTOPILOT_PG_DATABASE=autopilot\0AUTOPILOT_DATABASE_URL=' + uri + '\0'
        ).encode()):
            return guard.service_state(guard.UNITS[0])


def test_selection():
    state = {'version': 1, 'backend': 'postgresql', 'database': 'autopilot', 'epoch': 1}
    services = {unit: {'backend': 'postgresql', 'host': '127.0.0.1',
                       'port': 55432, 'database': database}
                for unit, database in guard.EXPECTED.items()}
    guard.validate(state, services)
    for altered_state, altered_services in (
        ({**state, 'backend': 'neon'}, services),
        (state, {**services, guard.UNITS[0]: {**services[guard.UNITS[0]], 'database': 'autopilot_candidate_20260922'}}),
        (state, {unit: value for unit, value in services.items() if unit != guard.UNITS[1]}),
    ):
        try:
            guard.validate(altered_state, altered_services)
        except ValueError:
            pass
        else:
            raise AssertionError('unsafe backup source accepted')


def test_connection_overrides():
    valid = 'postgresql://worker:secret@127.0.0.1:55432/autopilot?sslmode=verify-full&channel_binding=require'
    assert service(valid + '&connect_timeout=10&application_name=backup-preflight')['database'] == 'autopilot'
    for uri in (valid + '&host=neon.example',
                valid + '&sslmode=disable',
                valid.replace('/autopilot?', '/autopilot_candidate_20260922?') + '&dbname=autopilot',
                valid.replace('sslmode=verify-full', 'sslmode=verify-full&sslmode=disable')):
        try:
            service(uri)
        except ValueError:
            pass
        else:
            raise AssertionError('libpq override accepted')


if __name__ == '__main__':
    test_selection()
    test_connection_overrides()
    print('backup source guard negative cases: PASS')
