"""Bounded Light pilot entrypoint; never enters the legacy scheduler.

The root controller installs source-pinned permit/admission files only after
independent live preflight and the scoped operator window. This process cannot
approve, renew, replace, or recover those files. HOLD denies all provider calls,
including ACK/collection of an already-created task.
"""
import json
import os
from pathlib import Path
from .light_native_control import root_bytes
import time

from database import native_cli_permission_engine as engine
from ops.native_permission_hold_guard import EXPECTED_TARGET
from ops.oracle_autopilot_source_preflight import connection_parameters
from . import codex_cli_bridge as bridge
from .light_native_pilot import Claim, Permit, Session, REPOSITORY, require
from .light_native_provider import LightProvider

CONTROL = Path('/etc/bridge-school/light-native-pilot')
RELEASES = bridge.LIGHT_ROOT / 'releases'
CLAIMS = bridge.LIGHT_ROOT / 'runtime/native-single-pilot'
ROLE = 'autopilot_light_worker_login'



def admitted():
    return (os.environ.get('AUTOPILOT_ADMISSION_MODE') == 'PILOT'
            and root_bytes(CONTROL / 'admission', 16) == b'PILOT\n')


def release_source():
    root = Path(__file__).absolute().parent.parent
    source = root.name
    require(bridge.SHA.fullmatch(source) and root.parent == RELEASES
            and Path.cwd() == root and root.resolve() == root, 'PILOT_RELEASE_PATH')
    # The controller validates the full source manifest. Verify the loaded file
    # itself remains root-controlled here.
    root_bytes(Path(__file__).absolute(), 65536, private=False)
    require(os.getuid() != 0 and os.uname().nodename == 'autopilot-lite-vnic', 'PILOT_RUNTIME_IDENTITY')
    return source


def runtime_parameters(raw):
    parsed = connection_parameters(raw, ROLE)
    parsed.update(options='', application_name='school-autopilot-native-single-pilot',
                  gssencmode='disable', autocommit=True)
    return parsed


def runtime_identity(conn):
    expected = EXPECTED_TARGET['neon']
    target = engine.Target('neondb', ROLE, ROLE, ROLE, engine.NeonBinding(**expected))
    engine.identity(conn, target)
    flags = conn.execute('SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls '
                         'FROM pg_catalog.pg_roles WHERE rolname=current_user').fetchone()
    require(flags == (False, False, False, False, False), 'PILOT_RUNTIME_ROLE_FLAGS')


def read_pr(number):
    from .worker import _github_get_json
    return _github_get_json(f'https://api.github.com/repos/{REPOSITORY}/pulls/{number}',
                            not_found_code='PILOT_PR_MISSING')


def execute():
    import psycopg
    source = release_source()
    require(admitted(), 'PILOT_NOT_ADMITTED')
    accepted = root_bytes(CONTROL / 'accepted-sha256', 65).decode('ascii').strip()
    reader = lambda: root_bytes(CONTROL / 'permit.json', 65536)
    permit = Permit(reader(), accepted, reader, source)
    provider = LightProvider(permit.target)
    # One connection for the entire attempt, with no transparent reconnect.
    with Claim(CLAIMS) as claim, psycopg.connect(**runtime_parameters(
            os.environ.get('AUTOPILOT_DATABASE_URL', ''))) as conn:
        runtime_identity(conn)
        session = Session(permit, claim, conn, read_pr, provider, admission=admitted)
        session.reserve()
        while True:
            result = session.step()
            if result['state'] == 'DONE':
                return result
            time.sleep(10)


def main():
    try:
        result = execute()
        print(json.dumps(dict(audit='LIGHT_NATIVE_SINGLE_PILOT_TERMINAL',
            dispatch_id=result['dispatch_id'], provider_task_id=result['provider_task_id'],
            terminal_sha256=bridge.digest(bridge.canonical(result['terminal'])))), flush=True)
    except BaseException:
        # Never log driver/CLI exceptions: they may contain secrets or task text.
        print('{"audit":"LIGHT_NATIVE_SINGLE_PILOT_QUARANTINED"}', flush=True)
    # An always-restarting service must not turn a quarantine into a retry loop.
    # Only the external reviewed controller can restart or restore HOLD.
    while True:
        time.sleep(30)


if __name__ == '__main__':
    main()
