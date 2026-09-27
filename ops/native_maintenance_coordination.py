"""Live drain observations and scoped operator coordination; no production CLI.

An independently accepted director agreement and recorded prior host identities
are inputs, never inferred from a successful diagnostic or a supplied Boolean.
The runtime must retain route/DB fences: drain observations are not admission locks.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from database import native_cli_permission_engine as engine
from ops import native_maintenance_lifetime as lifetime
from ops.native_maintenance_workflow_pause import digest, require, validate_plan
from ops.native_maintenance_workflow_api import source_matches
from ops.native_maintenance_run_guard import RunBinding
from ops.native_maintenance_supervisor import PriorSupervisors
from ops import oracle_light_active_hold_attest as hold

NONTERMINAL = ('queued', 'in_progress', 'waiting', 'pending', 'requested')
COVERAGE = ['direct_owner_sql', 'host_administration', 'workflow_administration', 'workflow_reruns']


class Agreement:
    """Validate independently approved bytes, not their own self-computed hash.

    accepted_digest must come from the separate review/owner acceptance path.
    No helper in this module mints approvals or extends a supplied window.
    """
    def __init__(self, record, accepted_digest, scope):
        require(type(record) is dict and set(record) == {
            'version', 'owner', 'operation_digest', 'not_before', 'expires_at', 'coverage', 'evidence'},
            'COORDINATION_AGREEMENT_SHAPE')
        require(re.fullmatch('[0-9a-f]{64}', accepted_digest or '')
                and digest(record) == accepted_digest, 'COORDINATION_AGREEMENT_NOT_ACCEPTED')
        spec = {k: v for k, v in scope.items() if k != 'origin_run'}
        require(record['version'] == 1 and type(record['version']) is int
                and record['owner'] == 'olegmed1-art' and record['coverage'] == COVERAGE
                and record['operation_digest'] == digest(spec)
                and type(record['evidence']) is str and 0 < len(record['evidence']) <= 1024,
                'COORDINATION_AGREEMENT_SCOPE')
        self.record = json.loads(json.dumps(record))
        self.accepted = accepted_digest
        self.scope = digest(scope)
        self.start, self.end = [self.timestamp(record[k]) for k in ('not_before', 'expires_at')]
        require(0 < self.end - self.start <= 1800, 'COORDINATION_AGREEMENT_DURATION')
        now = time.time()
        require(self.start <= now < self.end, 'COORDINATION_AGREEMENT_NOT_CURRENT')
        self.deadline = time.monotonic() + self.end - now
        self.last_wall = now
        self.failed = False

    @staticmethod
    def timestamp(value):
        require(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value),
                'COORDINATION_AGREEMENT_TIME')
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()

    def assert_held(self, scope):
        require(not self.failed, 'COORDINATION_AGREEMENT_ALREADY_FAILED')
        try:
            now = time.time()
            require(scope == self.scope and digest(self.record) == self.accepted,
                    'COORDINATION_AGREEMENT_CHANGED')
            require(self.start <= now < self.end and now >= self.last_wall
                    and time.monotonic() < self.deadline, 'COORDINATION_AGREEMENT_EXPIRED')
            self.last_wall = now
        except BaseException:
            self.failed = True
            raise


class WorkflowDrain:
    def __init__(self, reader, plan, accepted_plan_digest):
        validate_plan(plan)
        require(digest(plan) == accepted_plan_digest, 'DRAIN_PLAN_NOT_ACCEPTED')
        self.reader = reader
        self.plan = json.loads(json.dumps(plan))
        self.accepted = accepted_plan_digest

    def assert_drained(self):
        require(digest(self.plan) == self.accepted, 'DRAIN_PLAN_CHANGED')
        # No branch/source/date filter: old revisions and historical reruns count.
        # Only zero is accepted. A nonzero or paginated result refuses immediately;
        # there is no need to traverse pages after finding any unfinished run.
        for row in self.plan['workflows']:
            for status in NONTERMINAL:
                result = self.reader.get('/actions/workflows/' + str(row['id'])
                                         + '/runs?status=' + status + '&per_page=100')
                require(type(result) is dict and type(result.get('total_count')) is int
                        and result['total_count'] == 0 and result.get('workflow_runs') == [],
                        'WORKFLOW_NOT_DRAINED')


class OwnedConnections:
    """Register verified connection objects; never accept caller-supplied PIDs."""
    def __init__(self, connect, target, *, approved_hold=None):
        self.connect, self.target = connect, target
        require(approved_hold is None or type(approved_hold) is hold.HoldIdentity, 'DRAIN_HOLD_IDENTITY_REQUIRED')
        self.approved_hold = approved_hold
        self.live = {}

    @staticmethod
    def backend_identity(conn):
        # Neon rewrites BackendKeyData for cancellation routing. Obtain the real
        # server identity only through this already verified connection's SQL.
        row = conn.execute('SELECT pid,backend_start FROM pg_catalog.pg_stat_activity '
                           'WHERE pid=pg_backend_pid()').fetchone()
        require(type(row) is tuple and len(row) == 2 and type(row[0]) is int
                and row[0] > 0 and isinstance(row[1], datetime), 'DRAIN_BACKEND_IDENTITY')
        return row

    def assert_owned(self, conn, identity, wire_pid):
        require(not conn.closed and conn.info.backend_pid == wire_pid,
                'DRAIN_OWNED_BACKEND_LOST')
        require(self.backend_identity(conn) == identity, 'DRAIN_OWNED_BACKEND_CHANGED')

    @contextmanager
    def open(self):
        with self.connect() as conn:
            require(conn.autocommit and not conn.closed, 'DRAIN_OWNED_CONNECTION_REQUIRED')
            engine.identity(conn, self.target)
            row = self.backend_identity(conn)
            wire_pid = conn.info.backend_pid
            require(type(wire_pid) is int and id(conn) not in self.live
                    and row not in [r for _, r, _ in self.live.values()], 'DRAIN_BACKEND_IDENTITY')
            self.live[id(conn)] = (conn, row, wire_pid)
            try:
                yield conn
            finally:
                self.live.pop(id(conn), None)

    def assert_drained(self):
        with self.open() as observer:
            # An exact PID+backend_start match prevents PID reuse. Null/invisible
            # foreign statistics refuse; application_name is never an exclusion.
            own = set()
            for conn, identity, wire_pid in self.live.values():
                self.assert_owned(conn, identity, wire_pid)
                own.add(identity)
            with observer.transaction():
                observer.execute('SET TRANSACTION READ ONLY')
                observer.execute("SET LOCAL statement_timeout='3s'")
                rows = observer.execute("SELECT pid,backend_start,usename,backend_type,state,xact_start,"
                                        "backend_xid::text,backend_xmin::text FROM pg_catalog.pg_stat_activity "
                                        "WHERE datname=current_database() AND backend_type<>'autovacuum worker'").fetchall()
                require(all(type(row) is tuple and len(row) == 8 and type(row[0]) is int
                            and isinstance(row[1], datetime) for row in rows)
                        and len({row[0] for row in rows}) == len(rows), 'DATABASE_ACTIVITY_INCOMPLETE')
                foreign = [row for row in rows if row[:2] not in own]
                require({row[:2] for row in rows if row[:2] in own} == own, 'DRAIN_OWNED_BACKEND_MISSING')
                if foreign:
                    require(self.target.recipient == 'autopilot_light_worker_login'
                            and self.approved_hold is not None and len(foreign) == 1
                            and foreign[0][2:] == (self.target.recipient, 'client backend', 'idle', None, None, None),
                            'DATABASE_NOT_DRAINED')
                    require(hold.attest() == self.approved_hold, 'DRAIN_HOLD_CHANGED')
                    state = observer.execute("SELECT jsonb_build_object("
                        "'config',(SELECT jsonb_agg(to_jsonb(c)) FROM autopilot.native_cli_config c),"
                        "'receipts',(SELECT count(*) FROM autopilot.native_cli_receipt),"
                        "'nonterminal_tasks',(SELECT count(*) FROM autopilot.task WHERE status NOT IN "
                        "('OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED')))").fetchone()[0]
                    engine.dormant(state)
                    require(hold.attest() == self.approved_hold, 'DRAIN_HOLD_CHANGED')
                require(observer.execute('SELECT count(*) FROM pg_catalog.pg_prepared_xacts '
                                         'WHERE database=current_database()').fetchone() == (0,),
                        'DATABASE_PREPARED_TRANSACTION_PRESENT')
                for conn, identity, wire_pid in self.live.values():
                    self.assert_owned(conn, identity, wire_pid)




class Operator:
    """Combine the actual observations; defaults never grant coordination."""
    def __init__(self, *, scope, agreement, workflows, prior_hosts, connections, source_transport, run):
        require(isinstance(agreement, Agreement) and isinstance(workflows, WorkflowDrain)
                and isinstance(prior_hosts, PriorSupervisors) and isinstance(connections, OwnedConnections)
                and isinstance(run, RunBinding),
                'COORDINATION_COMPONENTS_REQUIRED')
        require(scope['workflow_plan_digest'] == digest(workflows.plan)
                and scope['source'] == workflows.plan['source']
                and scope['target'] == asdict(connections.target), 'COORDINATION_SCOPE_MISMATCH')
        require(connections.approved_hold is None or asdict(connections.approved_hold) == scope['hold'],
                'COORDINATION_HOLD_MISMATCH')
        run.assert_running()
        require(run.source == scope['source'], 'COORDINATION_RUN_SOURCE')
        self.run = run
        self.run_identity = (run.run_id, run.attempt, run.job_id)
        origin = scope['origin_run']
        if run.run_id != origin['run_id']:
            prefix = 'bridge-native-ro-' + scope['source'][:12] + '-' + str(origin['run_id']) + '-' + str(origin['attempt']) + '-'
            require(any(row['unit'].startswith(prefix) for row in prior_hosts.records),
                    'COORDINATION_ORIGIN_HOST_EVIDENCE_REQUIRED')
        self.scope = digest(scope)
        self.source = scope['source']
        self.target_digest = digest(scope['target'])
        self.plan_digest = scope['workflow_plan_digest']
        self.agreement, self.workflows, self.hosts, self.connections = agreement, workflows, prior_hosts, connections
        self.transport = source_transport
        self.failed = False
        self.assert_held(self.scope)

    def assert_local(self, scope):
        """Continuity only: callers still need fresh source/run observations."""
        require(not self.failed, 'COORDINATION_ALREADY_FAILED')
        try:
            require(scope == self.scope, 'COORDINATION_SCOPE_CHANGED')
            require(digest(asdict(self.connections.target)) == self.target_digest
                    and digest(self.workflows.plan) == self.plan_digest, 'COORDINATION_COMPONENT_CHANGED')
            require((self.run.run_id, self.run.attempt, self.run.job_id) == self.run_identity
                    and self.run.source == self.source and not self.run.failed
                    and time.monotonic() < self.run.deadline, 'COORDINATION_RUN_CHANGED')
            self.run.assert_current()
            self.agreement.assert_held(scope)
        except BaseException:
            self.failed = True
            raise

    def assert_held(self, scope):
        try:
            self.assert_local(scope)
            source_matches(self.transport, self.source)
            self.assert_local(scope)
        except BaseException:
            self.failed = True
            raise

    def assert_drained(self, scope):
        try:
            self.assert_held(scope)
            self.observe_drained(scope)
            self.assert_held(scope)
        except BaseException:
            self.failed = True
            raise

    def observe_drained(self, scope):
        """Read-only drain chain; no independent run/source admission authority."""
        try:
            self.assert_local(scope)
            self.workflows.assert_drained()
            self.hosts.assert_drained()
            self.connections.assert_drained()
            self.assert_local(scope)
        except BaseException:
            self.failed = True
            raise
