#!/usr/bin/env python3
"""Read-only source guard for a future recurring Oracle Autopilot backup.

This deliberately does not create an archive or authorize a cutover. A backup
runner must still verify the complete GitHub writer inventory and restoration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit

ROUTE = Path('/var/lib/bridge-autopilot-tunnel/route.json')
UNITS = (
    'school-autopilot-production-light.service',
    'school-autopilot-shadow.service',
    'school-autopilot-online-observer.service',
)
EXPECTED = {
    'school-autopilot-production-light.service': 'autopilot',
    'school-autopilot-shadow.service': 'autopilot_shadow',
    'school-autopilot-online-observer.service': 'autopilot_shadow',
}


def route_state() -> dict:
    descriptor = os.open(ROUTE, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o644 or info.st_size > 4096:
            raise ValueError('route file identity')
        state = json.loads(source.read(4097))
    if set(state) != {'version', 'backend', 'database', 'epoch'} or state['version'] != 1 or state['database'] != 'autopilot' or type(state['epoch']) is not int:
        raise ValueError('route schema')
    return state


def service_state(unit: str) -> dict:
    output = subprocess.run(
        ['/usr/bin/systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState'],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout
    values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    pid = int(values['MainPID'])
    if pid <= 0 or values['ActiveState'] != 'active':
        raise ValueError('inactive unit')
    raw = Path(f'/proc/{pid}/environ').read_bytes()
    env = dict(item.decode().split('=', 1) for item in raw.split(b'\0') if b'=' in item)
    parsed = urlsplit(env.get('AUTOPILOT_DATABASE_URL', ''))
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    if parsed.scheme not in {'postgres', 'postgresql'} or not parsed.password or parsed.fragment:
        raise ValueError('invalid service connection URI')
    if (set(query) - {'sslmode', 'channel_binding', 'sslrootcert', 'connect_timeout', 'application_name'}
            or any(len(values) != 1 for values in query.values())
            or query.get('sslmode') != ['verify-full'] or query.get('channel_binding') != ['require']):
        raise ValueError('unpinned service connection URI')
    if (env.get('AUTOPILOT_PG_HOST') != parsed.hostname
            or env.get('AUTOPILOT_PG_DATABASE') != unquote(parsed.path.lstrip('/'))
            or env.get('AUTOPILOT_PG_PORT') != str(parsed.port)):
        raise ValueError('service target does not match pinned configuration')
    return {'backend': env.get('AUTOPILOT_DB_BACKEND', 'neon'),
            'host': parsed.hostname, 'port': parsed.port,
            'database': unquote(parsed.path.lstrip('/'))}


def validate(state: dict, services: dict) -> None:
    if state['backend'] != 'postgresql' or state['epoch'] < 1:
        raise ValueError('route has not moved to PostgreSQL')
    if set(services) != set(EXPECTED):
        raise ValueError('incomplete service inventory')
    for unit, expected_database in EXPECTED.items():
        actual = services[unit]
        if actual != {'backend': 'postgresql', 'host': '127.0.0.1',
                      'port': 55432, 'database': expected_database}:
            raise ValueError('service database route mismatch: ' + unit)


def main() -> int:
    try:
        if os.geteuid() != 0 or subprocess.run(['hostname'], capture_output=True, text=True,
                                                  check=True, timeout=5).stdout.strip() != 'autopilot-lite-vnic':
            raise ValueError('host or principal')
        if not os.path.ismount('/srv/autopilot-data'):
            raise ValueError('data mount unavailable')
        state = route_state()
        services = {unit: service_state(unit) for unit in UNITS}
        validate(state, services)
    except (OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'backup_source': 'NOT_READY', 'error_type': type(exc).__name__,
                          'scope': 'local_route_and_three_services_only'}))
        return 2
    print(json.dumps({'backup_source': 'LOCAL_ROUTE_READY', 'route_epoch': state['epoch'],
                      'database': 'autopilot', 'scope': 'local_route_and_three_services_only',
                      'production_backup_created': False}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
