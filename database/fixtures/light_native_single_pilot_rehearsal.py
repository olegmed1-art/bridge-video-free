"""Disposable PG18 only: real shared admission and RPCs, synthetic provider.

No network Cloud task, GitHub publication or Neon identity is claimed here.
The fixture uses the existing disposable database guard and runs last in CI.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from database.fixtures.native_cli_commit_rehearsal import connection, require
from oracle_autopilot import codex_cli_bridge as bridge
from oracle_autopilot.light_native_pilot import Claim, Permit, Session
from oracle_autopilot import light_native_preflight as preflight


def main():
    with connection() as conn:
        # This is the final test in an ephemeral database. Earlier concurrency
        # fixtures deliberately commit work; clear only disposable task state
        # so the shared queue can select this fixture deterministically.
        conn.execute('TRUNCATE autopilot.project_work_item,autopilot.task CASCADE')
        conn.execute('UPDATE autopilot.project_planner_state SET enabled=true')
        # Reuse the shared-admission fixture rather than inserting a fake native
        # reservation. Its synthetic publication ID is disposable SQL data only.
        source = Path('database/tests/339_autopilot_native_cli_receipts.sql').read_text()
        fixture = source[source.index('CREATE FUNCTION pg_temp.native_fixture'):source.index('CREATE FUNCTION pg_temp.native_reject')]
        conn.execute(fixture)
        conn.execute("UPDATE autopilot.role_registry SET enabled=true,can_repair=false WHERE role_id='AUTOPILOT'")
        conn.execute("UPDATE autopilot.native_cli_config SET enabled=true,cutover_at=clock_timestamp()-interval '1 hour'")
        row = conn.execute("SELECT pg_temp.native_fixture('single-pilot-pg18')").fetchone()[0]
        outbox = conn.execute('SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=%s::uuid',
                              (row['id'],)).fetchone()[0]
        dispatch = {key: outbox[key] for key in ('dispatch_id', 'expected_head_sha', 'mode', 'target_pr', 'task_fingerprint')}
        dispatch.update(branch='codex/single-pilot-pg18', assignment=row['assignment'])
        now = int(time.time())
        def disposable_identity(actual, _):
            require(actual is conn and actual.execute('SELECT current_database(),session_user,current_user').fetchone()
                    == ('bridge_school_ci', 'postgres', 'postgres'), 'DISPOSABLE_IDENTITY_REQUIRED')
        conn.read_only = True
        with patch.object(preflight.engine, 'identity', disposable_identity):
            evidence = preflight.observe(conn, dispatch, issued_at=now, expires_at=now+300, agreement_sha256='f'*64)
        conn.read_only = False
        value = dict(version=1, source='c'*40, dispatch=dispatch, environment_id='a'*32,
            environment_evidence_sha256='d'*64, issued_at=now, expires_at=now+300, owner_preflight=evidence)
        raw = bridge.canonical(value).encode()
        permit = Permit(raw, bridge.digest(raw.decode()), lambda: raw, 'c'*40)
        class Provider:
            creates = 0
            creation = None
            request = None
            def lookup(self, request, *, target): return deepcopy(self.creation)
            def submit(self, request, *, target):
                self.creates += 1
                self.request = deepcopy(request)
                self.creation = dict(state='SUBMITTED', provider_task_id='task_e_single_pilot_pg18',
                                     prompt_sha256=bridge.digest(bridge.prompt_for(request)))
                return deepcopy(self.creation)
            def collect(self, dispatch_id, *, target):
                report = {key: self.request[key] for key in ('dispatch_id', 'expected_head_sha', 'target_pr', 'task_fingerprint')}
                report.update(status='SUCCEEDED', result_code='VERIFIED', summary='Disposable provider fixture.')
                return dict(state='RESULT_RETRIEVED', provider_task_id=self.creation['provider_task_id'],
                            report=report, report_sha256=bridge.digest(bridge.canonical(report)))
        provider = Provider()
        def read_pr(number):
            repo = dict(full_name='olegmed1-art/bridge-video-free')
            return dict(number=number, state='open', head=dict(sha=dispatch['expected_head_sha'],
                        ref=dispatch['branch'], repo=repo), base=dict(repo=repo))
        with tempfile.TemporaryDirectory() as directory, Claim(directory) as claim:
            session = Session(permit, claim, conn, read_pr, provider, admission=lambda: True)
            request = session.reserve()
            result = session.step()
            require(result['state'] == 'DONE' and provider.creates == 1, 'SINGLE_PILOT_NOT_TERMINAL')
            require(session.step() == result and provider.creates == 1, 'SINGLE_PILOT_DUPLICATED')
            actual = conn.execute('SELECT status,goal_json FROM autopilot.task WHERE task_id=%s::uuid',
                                  (row['task_id'],)).fetchone()
            require(actual[0] == 'DONE' and bridge.digest(bridge.canonical(actual[1])) == evidence['goal_json_sha256'],
                    'SINGLE_PILOT_TASK_CHANGED')
            require(conn.execute('SELECT count(*) FROM autopilot.native_cli_receipt WHERE dispatch_id=%s::uuid',
                                 (row['id'],)).fetchone()[0] == 1, 'SINGLE_PILOT_RECEIPT_COUNT')
        print('LIGHT_NATIVE_SINGLE_PILOT_REAL_PG18_PASS')


if __name__ == '__main__':
    main()
