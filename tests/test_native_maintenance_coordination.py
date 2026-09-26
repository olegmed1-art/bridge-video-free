"""Fault injection for real coordination readers; no live approval or credentials."""
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import copy
import time
import unittest
from unittest.mock import Mock, patch

from ops import native_maintenance_coordination as c
from ops.native_maintenance_workflow_pause import Refused, digest
from ops.native_maintenance_run_guard import RunBinding
from database import native_cli_permission_engine as engine
from test_native_maintenance_run_guard import FakeAPI
from test_native_maintenance_executor import HOLD, PLAN, TARGET

SCOPE = dict(version=1, source='a'*40, target=asdict(TARGET), hold=asdict(HOLD),
             manifest_digest='b'*64, workflow_plan_digest=digest(PLAN),
             origin_run=dict(run_id=123, attempt=2, job_id=456), execution_mode='staged_v1')
START = datetime(2026, 9, 27, tzinfo=timezone.utc)


def agreement_record(scope=SCOPE):
    now = time.time()
    stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return dict(version=1, owner='olegmed1-art',
                operation_digest=digest({k:v for k,v in scope.items() if k!='origin_run'}),
                not_before=stamp(now-10), expires_at=stamp(now+600), coverage=c.COVERAGE.copy(),
                evidence='CI_SYNTHETIC_DIRECTOR_COMMITMENT_NOT_A_LIVE_APPROVAL')


class Database:
    def __init__(self):
        self.active = {}
        self.next_pid = 10
        self.foreign = []
        self.prepared = 0
        self.state = dict(config=[dict(enabled=False)], receipts=0, nonterminal_tasks=0)
        self.drift = None

    @contextmanager
    def connect(self):
        self.next_pid += 1
        pid = self.next_pid
        conn = NS(autocommit=True, closed=False, info=NS(backend_pid=pid), transaction=nullcontext)
        self.active[pid] = conn
        def execute(sql):
            if 'WHERE pid=pg_backend_pid()' in sql:
                return NS(fetchone=lambda: (pid, START))
            if 'backend_type<>' in sql:
                rows = [(p, START, 'neondb_owner', 'client backend', 'idle', None, None, None) for p in self.active]
                if self.drift:
                    rows = self.drift(rows)
                return NS(fetchall=lambda: rows + self.foreign)
            if 'pg_prepared_xacts' in sql:
                return NS(fetchone=lambda: (self.prepared,))
            if 'jsonb_build_object' in sql:
                return NS(fetchone=lambda: (self.state,))
            return NS()
        conn.execute = execute
        try:
            yield conn
        finally:
            conn.closed = True
            del self.active[pid]


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.connections = c.OwnedConnections(self.db.connect, TARGET, approved_hold=HOLD)
        self.identity = patch.object(engine, 'identity').start()
        self.attest = patch.object(c.hold, 'attest', return_value=HOLD).start()
        self.addCleanup(patch.stopall)

    def test_agreement_requires_independent_digest_scope_and_current_window(self):
        record = agreement_record()
        for accepted, scope in [(None, SCOPE), ('e'*64, SCOPE), (digest(record), {**SCOPE,'source':'f'*40})]:
            with self.subTest(accepted=accepted), self.assertRaises((Refused, TypeError)):
                c.Agreement(record, accepted, scope)
        agreement = c.Agreement(record, digest(record), SCOPE)
        agreement.assert_held(digest(SCOPE))
        with patch.object(c.time, 'monotonic', return_value=agreement.deadline+1), self.assertRaises(Refused):
            agreement.assert_held(digest(SCOPE))
        with self.assertRaises(Refused):
            agreement.assert_held(digest(SCOPE))

    def test_agreement_changes_and_clock_rollback_latch(self):
        for fault in ('scope','record','clock'):
            record = agreement_record()
            agreement = c.Agreement(record, digest(record), SCOPE)
            if fault=='record': agreement.record['evidence']='changed'
            with patch.object(c.time,'time',return_value=agreement.last_wall-1 if fault=='clock' else agreement.last_wall):
                with self.assertRaises(Refused):
                    agreement.assert_held('c'*64 if fault=='scope' else digest(SCOPE))
            self.assertTrue(agreement.failed)

    def test_workflows_include_all_historical_nonterminal_statuses(self):
        reader = Mock()
        reader.get.return_value = dict(total_count=0,workflow_runs=[])
        drain = c.WorkflowDrain(reader, PLAN, digest(PLAN))
        drain.assert_drained()
        self.assertEqual(reader.get.call_count, len(PLAN['workflows'])*5)
        self.assertTrue(all('branch=' not in x.args[0] and 'head_sha=' not in x.args[0] for x in reader.get.call_args_list))
        for bad in [dict(total_count=1,workflow_runs=[]),dict(total_count=0,workflow_runs=[{}]),{},dict(total_count=False,workflow_runs=[])]:
            reader.get.return_value=bad
            with self.assertRaises(Refused): drain.assert_drained()

    def test_all_owned_connections_are_verified_and_close(self):
        with self.connections.open():
            with self.connections.open():
                self.connections.assert_drained()
        self.assertEqual(self.connections.live,{})
        self.assertEqual(self.db.active,{})
        self.assertEqual(self.identity.call_count,3)

    def test_only_one_idle_light_without_transaction_is_permitted(self):
        idle=(99,START,'autopilot_light_worker_login','client backend','idle',None,None,None)
        self.db.foreign=[idle]
        self.connections.assert_drained()
        for index,value in [(2,'other'),(3,'logical replication worker'),(4,'active'),(5,START),(6,'1'),(7,'1'),(1,None)]:
            changed=list(idle); changed[index]=value; self.db.foreign=[tuple(changed)]
            with self.subTest(index=index), self.assertRaises(Refused): self.connections.assert_drained()
        self.db.foreign=[idle,(100,*idle[1:])]
        with self.assertRaises(Refused): self.connections.assert_drained()
        self.db.foreign=[idle]
        self.connections.approved_hold=None
        with self.assertRaises(Refused): self.connections.assert_drained()

    def test_idle_exception_requires_unchanged_hold_and_dormant_database(self):
        self.db.foreign=[(99,START,'autopilot_light_worker_login','client backend','idle',None,None,None)]
        self.attest.return_value=None
        with self.assertRaises(Refused): self.connections.assert_drained()
        self.attest.return_value=HOLD
        self.db.state['config']=[dict(enabled=True)]
        with self.assertRaises(engine.Refused): self.connections.assert_drained()
        self.db.state['config']=[dict(enabled=False)]
        self.db.prepared=1
        with self.assertRaises(Refused): self.connections.assert_drained()

    def test_reused_owned_pid_or_missing_stats_are_not_exclusions(self):
        for transform in [lambda rows:[],lambda rows:[(r[0],datetime(2025,1,1,tzinfo=timezone.utc),*r[2:]) for r in rows]]:
            self.db.drift=transform
            with self.assertRaises(Refused): self.connections.assert_drained()

    def test_prior_supervisor_still_running_or_replaced_refuses(self):
        record=dict(unit='bridge-native-ro-'+('a'*12)+'-122-2-'+('b'*16)+'.service',invocation='c'*32,cgroup_inode=10)
        host=c.PriorSupervisors([record],digest([record]))
        def result(state='inactive',pid='0',invocation='c'*32):
            return NS(returncode=0,stdout=f'LoadState=loaded\nActiveState={state}\nMainPID={pid}\nInvocationID={invocation}\n'.encode())
        with patch.object(c.lifetime,'ctl',return_value=result()) as ctl, patch.object(c.lifetime,'empty',return_value=True):
            host.assert_drained()
            for bad in (result('active','12'),result(invocation='d'*32)):
                ctl.return_value=bad
                with self.assertRaises(Refused): host.assert_drained()

    def test_operator_composes_observers_and_latches_foreign_backend(self):
        api=FakeAPI(); run=RunBinding(api.source,123,2,api)
        record=agreement_record(); agreement=c.Agreement(record,digest(record),SCOPE)
        reader=Mock(); reader.get.return_value=dict(total_count=0,workflow_runs=[])
        source=Mock(); source.request.return_value=api.main
        operator=c.Operator(scope=SCOPE,agreement=agreement,
                            workflows=c.WorkflowDrain(reader,PLAN,digest(PLAN)),
                            prior_hosts=c.PriorSupervisors([],digest([])), connections=self.connections,
                            source_transport=source,run=run)
        operator.assert_drained(digest(SCOPE))
        self.db.foreign=[(99,START,'unknown','client backend','idle',None,None,None)]
        with self.assertRaises(Refused): operator.assert_drained(digest(SCOPE))
        self.db.foreign=[]
        with self.assertRaises(Refused): operator.assert_held(digest(SCOPE))

    def test_cross_run_requires_prior_origin_host_record(self):
        scope=copy.deepcopy(SCOPE); scope['origin_run']['run_id']=122
        record=agreement_record(scope); api=FakeAPI(); run=RunBinding(api.source,123,2,api)
        reader=Mock(); source=Mock(); source.request.return_value=api.main
        with self.assertRaisesRegex(Refused,'ORIGIN_HOST'):
            c.Operator(scope=scope,agreement=c.Agreement(record,digest(record),scope),
                       workflows=c.WorkflowDrain(reader,PLAN,digest(PLAN)),prior_hosts=c.PriorSupervisors([],digest([])),
                       connections=self.connections,source_transport=source,run=run)


if __name__=='__main__': unittest.main()
