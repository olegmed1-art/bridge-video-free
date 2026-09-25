"""Explicit one-reservation native Codex reconciliation; never polls or allocates.

This entry point is deliberately separate from the resident worker.  Migration
0339's owner-only RPC grants and disabled switch must be reviewed before use.
An operator supplies one existing reservation ID and an isolated CLI profile.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import subprocess
import urllib.error
import urllib.request

import psycopg
from psycopg.rows import dict_row

from . import codex_cli_bridge as bridge
from .codex_cli_delivery import advance
from .codex_cli_queue import NativeAuthority, NativeQueue
from .worker import validate_neon_direct_dsn

REPOSITORY = 'olegmed1-art/bridge-video-free'
RESPONSE_LIMIT = 1_048_576
LIGHT_HOST = 'autopilot-lite-vnic'
LIGHT_UNIT = 'school-autopilot-production-light.service'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def read_pr(number, token, opener=None):
    if type(number) is not int or not 1 <= number <= 1_000_000:
        raise ValueError('NATIVE_PR_NUMBER_INVALID')
    if not token or len(token) > 4_096 or any(ord(char) < 33 for char in token):
        raise ValueError('NATIVE_GITHUB_TOKEN_INVALID')
    url = f'https://api.github.com/repos/{REPOSITORY}/pulls/{number}'
    request = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'User-Agent': 'school-autopilot-native-cli/1',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    opener = opener or urllib.request.build_opener(NoRedirect()).open
    with opener(request, 10) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError('NATIVE_GITHUB_PR_HTTP_INVALID')
        raw = response.read(RESPONSE_LIMIT + 1)
    if len(raw) > RESPONSE_LIMIT:
        raise ValueError('NATIVE_GITHUB_PR_TOO_LARGE')
    try:
        pr = bridge.parse(raw.decode('utf-8'))
        head, base = pr['head'], pr['base']
        if (type(pr['number']) is not int or pr['number'] != number
                or pr['state'] not in ('open', 'closed')
                or not bridge.SHA.fullmatch(head['sha'])
                or not isinstance(head['ref'], str)
                or head['repo']['full_name'] != REPOSITORY
                or base['repo']['full_name'] != REPOSITORY):
            raise ValueError('NATIVE_GITHUB_PR_IDENTITY_INVALID')
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError('NATIVE_GITHUB_PR_INVALID') from exc
    return pr


def require_live_hold():
    """Local unit readback is independent of the caller's environment.

    This is defense in depth; an atomic database-side canary admission is
    still required before this draft is enabled or merged.
    """
    if os.environ.get('AUTOPILOT_ADMISSION_MODE') != 'HOLD':
        raise ValueError('NATIVE_SERVICE_MUST_REMAIN_HELD')
    if os.uname().nodename != LIGHT_HOST:
        raise ValueError('NATIVE_HOST_IDENTITY_INVALID')
    status = subprocess.run(['systemctl', 'show', LIGHT_UNIT,
                             '-pActiveState', '-pSubState', '-pEnvironment'],
                            capture_output=True, text=True, check=True, timeout=15,
                            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    fields = dict(line.split('=', 1) for line in status.stdout.splitlines() if '=' in line)
    admission = [item for item in fields.get('Environment', '').split()
                 if item.startswith('AUTOPILOT_ADMISSION_MODE=')]
    if (fields.get('ActiveState') != 'active' or fields.get('SubState') != 'running'
            or admission != ['AUTOPILOT_ADMISSION_MODE=HOLD']):
        raise ValueError('NATIVE_SERVICE_MUST_REMAIN_HELD')


def one_step(dispatch_id, dsn, token, profile):
    if not bridge.UUID.fullmatch(dispatch_id):
        raise ValueError('NATIVE_DISPATCH_ID_INVALID')
    require_live_hold()
    username = 'school-autopilot' if profile == 'service' else 'ubuntu'
    if profile not in ('ubuntu', 'service') or os.geteuid() != pwd.getpwnam(username).pw_uid:
        raise ValueError('NATIVE_PROFILE_IDENTITY_INVALID')
    dsn = validate_neon_direct_dsn(dsn)
    bridge.configure_profile(profile)
    if bridge.health()['state'] != 'CLI_AUTH_READY':
        raise ValueError('NATIVE_CLI_AUTH_REQUIRED')

    def rpc(statement, parameters):
        with psycopg.connect(dsn, connect_timeout=10, row_factory=dict_row,
                             application_name='school-autopilot-native-cli-once') as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, parameters)
                row = cursor.fetchone()
                if row is None:
                    raise ValueError('NATIVE_QUEUE_ROW_MISSING')
                return row

    class CheckedQueue(NativeQueue):
        def begin_submission(self, request):
            require_live_hold()
            return super().begin_submission(request)

    queue = CheckedQueue(rpc)
    authority = NativeAuthority(queue, lambda number: read_pr(number, token))

    class CheckedProvider:
        lookup = staticmethod(bridge.lookup)
        collect = staticmethod(bridge.collect)

        @staticmethod
        def submit(request):
            # SQL begin has already recorded the irreversible one-shot intent.
            # On failed recheck leave UNKNOWN; never attempt a second create.
            require_live_hold()
            if not queue.current(request):
                raise ValueError('NATIVE_CANARY_REVOKED')
            return bridge.submit(request)

    return advance(dispatch_id, queue, authority, CheckedProvider)


def main():
    parser = argparse.ArgumentParser(description='Advance one existing native reservation once')
    parser.add_argument('--dispatch-id', required=True)
    parser.add_argument('--profile', choices=('ubuntu', 'service'), required=True)
    args = parser.parse_args()
    try:
        outcome = one_step(args.dispatch_id, os.environ['AUTOPILOT_DATABASE_URL'],
                           os.environ['GITHUB_TOKEN'], args.profile)
        print(json.dumps({'state': outcome['state'], 'dispatch_id': args.dispatch_id},
                         sort_keys=True))
    except Exception:
        # Do not print exception strings: drivers and providers may include secrets.
        print(json.dumps({'state': 'BLOCKED', 'code': 'NATIVE_STEP_FAILED'}))
        raise SystemExit(2)


if __name__ == '__main__':
    main()
