"""Fixed root-readable DB routing inspection, never emitting a DSN or password."""
import json
import hmac
import os
import pwd
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from urllib.parse import urlsplit, unquote

UNITS = ('school-autopilot-production-light.service', 'school-autopilot-shadow.service',
         'school-autopilot-online-observer.service')
LIGHT_ENV_FILE = Path('/etc/school-autopilot-production-light.env')
PIN_ENV_FILE = Path('/opt/bridge-school/school-autopilot-production-light/releases/'
                    '3244f4d4b17ce99e58c342f01e4715436a09622b/ops/autopilot/broker-hold.env')
PIN_KEYS = frozenset({'AUTOPILOT_TOKEN_BROKER_URL',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_SOURCE_SHA',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_ARTIFACT_SHA256',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_POLICY_SHA256',
    'AUTOPILOT_TOKEN_BROKER_EXPECTED_PROVENANCE_SHA256'})


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


def live_credential_matches_disk(dsn, path=LIGHT_ENV_FILE):
    """Attest the current systemd secret source without printing its contents."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as file:
        metadata = os.fstat(file.fileno())
        if not (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
                and stat.S_IMODE(metadata.st_mode) == 0o600
                and metadata.st_size <= 262144):
            raise AuditFailure('ENV_FILE_UNTRUSTED')
        raw = file.read(262145)
        if len(raw) > 262144:
            raise AuditFailure('ENV_FILE_UNTRUSTED')
    found = []
    for line in raw.decode().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise AuditFailure('ENV_FILE_INVALID')
        try:
            tokens = shlex.split(value, comments=False, posix=True)
        except ValueError:
            raise AuditFailure('ENV_FILE_INVALID') from None
        if len(tokens) != 1:
            raise AuditFailure('ENV_FILE_INVALID')
        if key == 'AUTOPILOT_DATABASE_URL':
            found.append(tokens[0])
    if len(found) != 1:
        raise AuditFailure('ENV_FILE_INVALID')
    return hmac.compare_digest(found[0].encode('utf-8'), dsn.encode('utf-8'))


def environment_source_layout(output):
    """Reduce systemd's file list to non-secret facts; never echo an unknown path."""
    entries = output.splitlines()
    expected = f'EnvironmentFiles={LIGHT_ENV_FILE} (ignore_errors=no)'
    pin = f'EnvironmentFiles={PIN_ENV_FILE} (ignore_errors=no)'
    return {'entries': len(entries), 'expected_primary': expected in entries,
            'expected_pin': pin in entries,
            'unknown_entries': sum(entry not in (expected, pin) for entry in entries)}


def verify_broker_pin_file(path=PIN_ENV_FILE):
    """A recognized second EnvironmentFile must contain broker pins only."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o644 and info.st_size <= 4096):
            raise AuditFailure('PIN_FILE_UNTRUSTED')
        raw = file.read(4097)
        if len(raw) > 4096:
            raise AuditFailure('PIN_FILE_UNTRUSTED')
    keys = []
    for line in raw.decode().splitlines():
        key, separator, value = line.partition('=')
        if not separator or key not in PIN_KEYS:
            raise AuditFailure('PIN_FILE_INVALID')
        try:
            tokens = shlex.split(value, comments=False, posix=True)
        except ValueError:
            raise AuditFailure('PIN_FILE_INVALID') from None
        if len(tokens) != 1:
            raise AuditFailure('PIN_FILE_INVALID')
        keys.append(key)
    if len(keys) != len(PIN_KEYS) or set(keys) != PIN_KEYS:
        raise AuditFailure('PIN_FILE_INVALID')


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
            source = subprocess.run(['/usr/bin/systemctl', 'show', unit,
                                     '-p', 'EnvironmentFiles'],
                                    check=True, capture_output=True, text=True,
                                    timeout=15).stdout
            layout = environment_source_layout(source)
            acceptable = ({'entries': 1, 'expected_primary': True,
                           'expected_pin': False, 'unknown_entries': 0},
                          {'entries': 2, 'expected_primary': True,
                           'expected_pin': True, 'unknown_entries': 0})
            if layout not in acceptable:
                print(json.dumps({'audit': 'ENV_SOURCE_LAYOUT', **layout}), flush=True)
                raise AuditFailure('ENV_SOURCE_DRIFT')
            if layout['expected_pin']:
                verify_broker_pin_file()
            matches = live_credential_matches_disk(dsn)
            print(json.dumps({'audit': 'SOURCE_ATTESTED',
                              'live_credential_matches_disk': matches}), flush=True)
            if not matches:
                raise AuditFailure('LIVE_ENV_DRIFT')
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
