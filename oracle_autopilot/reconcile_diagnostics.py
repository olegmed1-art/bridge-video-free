"""Read-only runner probes. Never print DSNs, API bodies or exception messages."""
from __future__ import annotations

import os
import re
import urllib.error

import psycopg

from database.runtime_worker_preflight import EXPECTED_HOST, EXPECTED_PRINCIPAL
from .reconcile_db import normalize_dsn
from .database_target import expected_database
from .paused_reconcile import github


def error_code(exc):
    """Map untrusted diagnostics to a fixed, public-safe vocabulary."""
    state = getattr(exc, 'sqlstate', None)
    if isinstance(state, str) and re.fullmatch(r'[0-9A-Z]{5}', state):
        return 'SQLSTATE_' + state
    message = str(exc).lower()
    for needle, code in (
        ('endpoint is disabled', 'ENDPOINT_DISABLED'),
        ('endpoint not found', 'ENDPOINT_NOT_FOUND'),
        ('password authentication failed', 'AUTHENTICATION_FAILED'),
        ('invalid connection option', 'CONNECTION_OPTION_INVALID'),
        ('unsupported startup parameter', 'STARTUP_PARAMETER_UNSUPPORTED'),
        ('could not translate host name', 'DNS_FAILED'),
        ('name or service not known', 'DNS_FAILED'),
        ('connection refused', 'CONNECTION_REFUSED'),
        ('timeout', 'TIMEOUT'),
        ('ssl', 'TLS_ERROR'),
    ):
        if needle in message:
            return code
    return 'UNCLASSIFIED'


def probe(dsn, label, *, startup_options=False, gateway=False):
    stage = 'connect'
    kwargs = dict(connect_timeout=10, autocommit=True,
                  application_name='autopilot-reconcile-diagnostic')
    if startup_options:
        kwargs['options'] = '-c statement_timeout=10000 -c lock_timeout=3000'
    try:
        with psycopg.connect(dsn, **kwargs) as conn:
            stage = 'read_only_transaction'
            with conn.transaction():
                conn.execute('SET TRANSACTION READ ONLY')
                conn.execute("SET LOCAL statement_timeout = '10s'")
                conn.execute("SET LOCAL lock_timeout = '3s'")
                stage = 'select_one'
                assert conn.execute('SELECT 1').fetchone() == (1,)
                stage = 'identity'
                principal_ok, database_ok = conn.execute(
                    "SELECT current_user = %s, current_database() = %s",
                    (EXPECTED_PRINCIPAL, expected_database()),
                ).fetchone()
                print(f'db_diagnostic variant={label} principal_expected={principal_ok} database_expected={database_ok}')
                if not principal_ok or not database_ok:
                    print(f'db_diagnostic variant={label} result=FAIL code=IDENTITY_MISMATCH')
                    return False
                statements = (
                    ('paused_candidates', 'SELECT count(*) FROM autopilot.paused_reconcile_candidates(50)'),
                    ('progress_candidates', 'SELECT count(*) FROM autopilot.project_progress_candidates(50)'),
                ) if not gateway else (
                    ('paused_candidates', 'SELECT count(*) FROM autopilot_reconcile.paused_candidates(50)'),
                    ('progress_candidates', 'SELECT count(*) FROM autopilot_reconcile.progress_candidates(50)'),
                )
                if gateway:
                    stage = 'least_privilege'
                    internal, create, table_write = conn.execute(
                        "SELECT has_schema_privilege(current_user,'autopilot','USAGE'),"
                        "has_schema_privilege(current_user,'autopilot_reconcile','CREATE'),"
                        "has_table_privilege(current_user,(SELECT c.oid FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE "
                        "n.nspname='autopilot' AND c.relname='project_work_item'),'UPDATE')"
                    ).fetchone()
                    if internal is not False or create is not False or table_write is not False:
                        print(f'db_diagnostic variant={label} result=FAIL code=EXCESS_PRIVILEGE')
                        return False
                for name, sql in statements:
                    stage = name
                    count = conn.execute(sql).fetchone()[0]
                    print(f'db_diagnostic variant={label} stage={stage} count={count} result=PASS')
        print(f'db_diagnostic variant={label} result=PASS')
        return True
    except Exception as exc:
        print(f'db_diagnostic variant={label} stage={stage} result=FAIL code={error_code(exc)}')
        return False


def main():
    raw = os.environ.get('DATABASE_URL', '')
    if not raw:
        print('db_diagnostic result=FAIL code=DSN_MISSING')
        return 1
    try:
        normalized = normalize_dsn(raw)
    except (SystemExit, ValueError):
        print('db_diagnostic result=FAIL code=DSN_CONTRACT_INVALID')
        return 1
    print(f'db_diagnostic normalized_changed={normalized != raw}')
    ok = probe(normalized, 'production_gateway', gateway=True)
    try:
        head = github('git/ref/heads/main')['object']['sha']
        if not re.fullmatch('[0-9a-f]{40}', head):
            raise ValueError('invalid_head')
        checks = github(f'commits/{head}/check-runs?per_page=1')
        if not isinstance(checks.get('check_runs'), list):
            raise ValueError('invalid_checks')
        print('github_diagnostic stage=check_runs result=PASS')
    except Exception as exc:
        cause = exc.__cause__
        status = cause.code if isinstance(cause, urllib.error.HTTPError) else 0
        print(f'github_diagnostic stage=check_runs result=FAIL http_status={status}')
        ok = False
    return 0 if ok else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'db_diagnostic result=FAIL code={error_code(exc)}')
        raise SystemExit(1) from None
