"""Fixed root-readable DB routing inspection, never emitting a DSN or password."""
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit, unquote

UNITS = ('school-autopilot-production-light.service', 'school-autopilot-shadow.service',
         'school-autopilot-online-observer.service')


def safe_identifier(value):
    return value if re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', value or '') else 'REDACTED_OR_ABSENT'


def main():
    result = []
    for unit in UNITS:
        output = subprocess.run(['/usr/bin/systemctl', 'show', unit, '-p', 'MainPID',
                                 '-p', 'ActiveState', '-p', 'WorkingDirectory'],
                                check=True, capture_output=True, text=True, timeout=15).stdout
        props = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        pid = int(props['MainPID'])
        assert pid > 0 and props['ActiveState'] == 'active'
        # Inspect actual process environment, not just a possibly stale disk file.
        raw = Path(f'/proc/{pid}/environ').read_bytes()
        env = dict(s.decode().split('=', 1) for s in raw.split(b'\0') if b'=' in s)
        dsn = env.get('AUTOPILOT_DATABASE_URL', '')
        parsed = urlsplit(dsn)
        result.append({'unit': unit, 'active': True,
            'db_backend': safe_identifier(env.get('AUTOPILOT_DB_BACKEND', 'neon')),
            'db_host': safe_identifier(parsed.hostname),
            'db_name': safe_identifier(unquote(parsed.path.lstrip('/'))),
            'db_user': safe_identifier(unquote(parsed.username or '')),
            'postgres_target_code_present': (Path(props['WorkingDirectory'])/'oracle_autopilot/database_target.py').is_file()})
    print(json.dumps({'scope': 'READ_ONLY_LOCAL_ROUTING', 'services': result}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'audit': 'INCOMPLETE', 'error_type': type(exc).__name__}))
        sys.exit(2)
