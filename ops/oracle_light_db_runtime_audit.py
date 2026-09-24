"""Fixed root-readable DB routing inspection, never emitting a DSN or password."""
import json
import os
import pwd
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit, unquote

UNITS = ('school-autopilot-production-light.service', 'school-autopilot-shadow.service',
         'school-autopilot-online-observer.service')


def safe_identifier(value):
    return value if re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', value or '') else 'REDACTED_OR_ABSENT'



# The child receives the live DSN only in its environment. Its args and output
# contain no password, host, or connection error message.
READ_ONLY_LOGIN = """import json, os, sys
try:
    import psycopg
except Exception:
    print(json.dumps({'error_code': 'DRIVER_UNAVAILABLE'}))
    sys.exit(2)
try:
    with psycopg.connect(os.environ['AUDIT_DATABASE_URL'], autocommit=True,
                         connect_timeout=10,
                         options='-c statement_timeout=5000 -c default_transaction_read_only=on') as conn:
        row = conn.execute("SELECT current_user, current_database(), current_setting('transaction_read_only')").fetchone()
except Exception as exc:
    if type(exc).__name__ == 'OperationalError':
        code = ('AUTHENTICATION_FAILED' if getattr(exc, 'sqlstate', None) == '28P01'
                or 'password authentication failed' in str(exc).lower()
                else 'CONNECT_FAILED')
    else:
        code = 'QUERY_FAILED'
    print(json.dumps({'error_code': code}))
    sys.exit(2)
print(json.dumps({'user': row[0], 'database': row[1], 'read_only': row[2]}))
"""


class AuditFailure(RuntimeError):
    pass


def verify_production_login(dsn):
    account = pwd.getpwnam('school-autopilot')
    def drop_to_service():
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    child = subprocess.run(
        ['/opt/bridge-school/school-autopilot/.venv/bin/python', '-c', READ_ONLY_LOGIN],
        env={'AUDIT_DATABASE_URL': dsn, 'PATH': '/usr/bin:/bin',
             'PYTHONDONTWRITEBYTECODE': '1'},
        cwd='/', preexec_fn=drop_to_service, capture_output=True, text=True,
        timeout=25, check=False)
    if len(child.stdout) >= 512:
        raise AuditFailure('LOGIN_OUTPUT_INVALID')
    try:
        proof = json.loads(child.stdout)
    except ValueError:
        raise AuditFailure('LOGIN_OUTPUT_INVALID') from None
    if child.returncode != 0:
        codes = {'DRIVER_UNAVAILABLE', 'AUTHENTICATION_FAILED', 'CONNECT_FAILED',
                 'QUERY_FAILED'}
        if (isinstance(proof, dict) and set(proof) == {'error_code'}
                and proof['error_code'] in codes):
            raise AuditFailure(proof['error_code'])
        raise AuditFailure('LOGIN_CHILD_FAILED')
    if proof != {'user': 'autopilot_light_worker_login',
                 'database': 'neondb', 'read_only': 'on'}:
        raise AuditFailure('LOGIN_IDENTITY_MISMATCH')

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
        service = {'unit': unit, 'active': True,
            'db_backend': safe_identifier(env.get('AUTOPILOT_DB_BACKEND', 'neon')),
            'db_host': safe_identifier(parsed.hostname),
            'db_name': safe_identifier(unquote(parsed.path.lstrip('/'))),
            'db_user': safe_identifier(unquote(parsed.username or '')),
            'postgres_target_code_present': (Path(props['WorkingDirectory'])/'oracle_autopilot/database_target.py').is_file()}
        if unit == UNITS[0]:
            assert service['db_backend'] == 'neon'
            assert service['db_host'] == 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
            assert service['db_name'] == 'neondb'
            assert service['db_user'] == 'autopilot_light_worker_login'
            verify_production_login(dsn)
            service['db_login_verified_read_only'] = True
        result.append(service)
    print(json.dumps({'scope': 'READ_ONLY_LOCAL_ROUTING', 'services': result}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'audit': 'INCOMPLETE', 'error_code': str(exc)}
                         if isinstance(exc, AuditFailure)
                         else {'audit': 'INCOMPLETE', 'error_type': type(exc).__name__}))
        sys.exit(2)
