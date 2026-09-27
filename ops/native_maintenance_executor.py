"""One journalled permission session; separate, reconciled workflow restoration.

Requires real operator coordination, run binding and supervised host lifetime.
None is inferred from a flag or supplied by a permissive default. No CLI/token
loading/service activation is provided. Production assembly must supply these
dependencies through the reviewed runtime, never the CI stubs.
"""
from dataclasses import asdict
import json

from ops.native_maintenance_workflow_api import WorkflowAPI
from ops.native_maintenance_workflow_pause import WorkflowPause, digest, require
from ops.native_permission_hold_guard import HoldMaintenanceGuard


class ExecutionError(RuntimeError):
    def __init__(self, outcome='UNKNOWN'):
        self.outcome = outcome
        super().__init__('NATIVE_EXECUTION_REQUIRES_RECONCILIATION_' + outcome)


def permission_session(*args, **kwargs):
    # Lazy import permits contract tests without pretending to have a DB driver.
    from database.native_cli_maintenance_session import execute
    return execute(*args, **kwargs)


def operation_scope(*, target, operation, manifest_digest, plan_digest, source,
                    expected_route, approved_hold, origin_run=None,
                    staged=False, observed_admission=False):
    """Canonical identity only; constructing it supplies no approval."""
    require(type(staged) is bool and type(observed_admission) is bool
            and (not observed_admission or staged), 'EXECUTOR_MODE_INVALID')
    require(operation in ('apply', 'rollback'), 'EXECUTOR_OPERATION_INVALID')
    bound = dict(version=1, target=asdict(target), operation=operation,
                 manifest_digest=manifest_digest, workflow_plan_digest=plan_digest,
                 source=source, route=dict(expected_route), hold=asdict(approved_hold))
    if staged:
        bound['execution_mode'] = 'staged_v1'
    if observed_admission:
        bound['admission_mode'] = 'observed_v1'
    if origin_run is not None:
        require(type(origin_run) is dict and set(origin_run) == {'run_id', 'attempt', 'job_id'}
                and all(type(v) is int and v > 0 for v in origin_run.values()), 'EXECUTOR_RUN_IDENTITY')
        bound['origin_run'] = dict(origin_run)
    return json.loads(json.dumps(bound))


class SessionWindow:
    def __init__(self, executor):
        self.executor = executor

    def assert_held(self, target, operation):
        ex = self.executor
        require(asdict(target) == ex.scope['target'] and operation == ex.scope['operation'],
                'EXECUTOR_SESSION_SCOPE_CHANGED')
        ex._assert_window()
        ex.pause.assert_paused()
        ex.operator.assert_drained(ex.scope_digest)
        ex._assert_window()


class ObservedSessionWindow(SessionWindow):
    """One no-effect observation chain per call; never a cached admission lease."""
    def __init__(self, executor):
        from ops.native_maintenance_coordination import Operator
        from ops.native_maintenance_run_guard import RunBinding
        require(isinstance(executor.operator, Operator) and isinstance(executor.run, RunBinding)
                and executor.operator.run is executor.run, 'OBSERVED_ADMISSION_RUNTIME_REQUIRED')
        super().__init__(executor)

    def local(self):
        ex = self.executor
        require(not ex.failed, 'EXECUTOR_ALREADY_FAILED')
        ex.operation_journal.assert_live()
        ex.pause_journal.assert_live()
        ex.lifetime.assert_alive()
        ex.run.assert_current()
        require(ex._run_identity() == ex.current_run and ex.run.source == ex.scope['source'],
                'EXECUTOR_RUN_CHANGED')
        ex.operator.assert_local(ex.scope_digest)

    def observe_hold(self, target, operation, approved_hold):
        from ops.oracle_light_active_hold_attest import attest
        ex = self.executor
        try:
            require(asdict(target) == ex.scope['target'] and operation == ex.scope['operation']
                    and asdict(approved_hold) == ex.scope['hold'], 'EXECUTOR_SESSION_SCOPE_CHANGED')
            self.local()
            ex.pause.observe_paused()
            ex.operator.observe_drained(ex.scope_digest)
            require(attest() == approved_hold, 'HOLD_IDENTITY_CHANGED')
            # Detect re-enable during drain/HOLD before the final authenticated
            # run/source/job observation. Neither read chain can perform effects.
            ex.pause.observe_paused()
            ex.run.assert_running()
            self.local()
        except BaseException:
            ex.failed = True
            raise


class MaintenanceExecutor:
    """Compose the real API/pause/HOLD/session components with durable intent.

operator must implement assert_held(scope_digest) and assert_drained(scope_digest).
These cover actual scoped privileged-writer/admin coordination, existing jobs and
remote/backends, not just an empty GitHub list. lifetime.assert_alive() must bind
the reviewed independent supervisor. run is the authenticated RunBinding.

Fresh construction under a different authenticated run may restore after
independent reconciliation, but may NEVER repeat the original DB session.
"""
    def __init__(self, *, target, operation, manifest_path, manifest_digest,
                 expected_route, approved_hold, workflow_plan, plan_digest,
                 api_token, pause_journal, operation_journal, run, operator, lifetime, checkpoint,
                 staged=False, observed_admission=False):
        require(type(staged) is bool, 'EXECUTOR_MODE_INVALID')
        require(type(observed_admission) is bool and (not observed_admission or staged),
                'EXECUTOR_ADMISSION_MODE_INVALID')
        self.staged = staged
        require(operation in ('apply', 'rollback'), 'EXECUTOR_OPERATION_INVALID')
        require(digest(workflow_plan) == plan_digest, 'EXECUTOR_PLAN_MISMATCH')
        require(all(callable(getattr(operator, n, None)) for n in ('assert_local', 'assert_held', 'assert_drained'))
                and callable(getattr(lifetime, 'assert_alive', None))
                and all(callable(getattr(run, n, None)) for n in ('assert_current', 'assert_running'))
                and callable(getattr(checkpoint, 'sync', None)), 'EXECUTOR_RUNTIME_REQUIRED')
        require(pause_journal.root.resolve() != operation_journal.root.resolve(), 'SEPARATE_JOURNALS_REQUIRED')
        self.target, self.manifest_path = target, manifest_path
        self.run, self.operator, self.lifetime = run, operator, lifetime
        self.checkpoint = checkpoint
        self.operation_journal, self.pause_journal = operation_journal, pause_journal
        self.failed, self.phase = False, 'idle'
        self.run.assert_running()
        self.lifetime.assert_alive()
        self.current_run = self._run_identity()
        require(run.source == workflow_plan['source'], 'EXECUTOR_SOURCE_MISMATCH')
        bound = operation_scope(target=target, operation=operation, manifest_digest=manifest_digest,
                                plan_digest=plan_digest, source=run.source, expected_route=expected_route,
                                approved_hold=approved_hold, staged=staged,
                                observed_admission=observed_admission)
        if staged:
            require(callable(getattr(checkpoint, 'accept_resume', None)), 'EXECUTOR_RESUME_REQUIRED')
        if operation_journal.records:
            first = operation_journal.records[0]['event']
            require(type(first) is dict and set(first) == {'kind', 'scope'} and first['kind'] == 'BOUND',
                    'EXECUTOR_RECORD_INVALID')
            previous = first['scope']
            require(type(previous) is dict
                    and {k: v for k, v in previous.items() if k != 'origin_run'} == bound
                    and type(previous.get('origin_run')) is dict
                    and set(previous['origin_run']) == {'run_id', 'attempt', 'job_id'}
                    and all(type(v) is int and v > 0 for v in previous['origin_run'].values()),
                    'EXECUTOR_SCOPE_CHANGED')
            bound = previous
        else:
            bound['origin_run'] = self.current_run
        self.scope = json.loads(json.dumps(bound))
        self.scope_digest = digest(self.scope)
        self.operator.assert_held(self.scope_digest)
        if not operation_journal.records:
            operation_journal.append({'kind': 'BOUND', 'scope': self.scope})
        self._replay()
        window = ObservedSessionWindow(self) if observed_admission else SessionWindow(self)
        self.hold = HoldMaintenanceGuard(target, operation, approved_hold, writer_guard=window)
        api = WorkflowAPI(api_token, workflow_plan, plan_digest, mutation_guard=self)
        self.pause = WorkflowPause(workflow_plan, plan_digest, api, pause_journal, self,
                                   operation_scope_digest=self.scope_digest)

    def _run_identity(self):
        result = {'run_id': self.run.run_id, 'attempt': self.run.attempt, 'job_id': self.run.job_id}
        require(all(type(v) is int and v > 0 for v in result.values()), 'EXECUTOR_RUN_IDENTITY')
        return result

    def _replay(self):
        state = 'bound'
        session_run = None if self.staged else self.scope['origin_run']
        for record in self.operation_journal.records[1:]:
            event = record['event']
            require(type(event) is dict and set(event) == {'kind', 'run', 'outcome'}, 'EXECUTOR_EVENT_SHAPE')
            require(type(event['run']) is dict and set(event['run']) == {'run_id', 'attempt', 'job_id'}
                    and all(type(v) is int and v > 0 for v in event['run'].values()), 'EXECUTOR_EVENT_RUN')
            kind, outcome = event['kind'], event['outcome']
            if kind == 'PREPARED':
                require(self.staged and state == 'bound' and outcome == 'UNKNOWN'
                        and event['run'] == self.scope['origin_run'], 'EXECUTOR_PREPARE_REPLAY')
                state = 'prepared'
            elif kind == 'SESSION_BOUND':
                require(self.staged and state == 'prepared' and outcome == 'UNKNOWN'
                        and event['run']['run_id'] != self.scope['origin_run']['run_id'],
                        'EXECUTOR_SESSION_BIND_REPLAY')
                session_run = event['run']
                state = 'session_bound'
            elif kind == 'SESSION_INTENT':
                require(state == ('session_bound' if self.staged else 'bound') and outcome == 'UNKNOWN'
                        and event['run'] == session_run, 'EXECUTOR_SESSION_REPLAY')
                state = 'session_unknown'
            elif kind in ('SESSION_RESULT', 'SESSION_ERROR'):
                require(state == 'session_unknown' and event['run'] == session_run
                        and outcome in ('BEFORE', 'AFTER', 'UNKNOWN', 'DRIFT'), 'EXECUTOR_RESULT_REPLAY')
                if kind == 'SESSION_RESULT':
                    require(outcome == ('BEFORE' if self.scope['operation'] == 'rollback' else 'AFTER'),
                            'EXECUTOR_RESULT_MISMATCH')
                state = 'session_recorded'
            elif kind == 'RESTORE_INTENT':
                require(state in ('bound', 'prepared', 'session_bound', 'session_unknown', 'session_recorded', 'restoring')
                        and outcome in ('BEFORE', 'AFTER'), 'EXECUTOR_RESTORE_REPLAY')
                state = 'restoring'
            elif kind == 'RESTORED':
                require(state == 'restoring' and outcome in ('BEFORE', 'AFTER'), 'EXECUTOR_RESTORED_REPLAY')
                state = 'restored'
            else:
                raise ExecutionError()
        self.state = state
        self.session_run = session_run

    def _event(self, kind, outcome):
        self.operation_journal.append({'kind': kind, 'run': self.current_run, 'outcome': outcome})
        self._replay()
        return self._sync_checkpoint()

    def _sync_checkpoint(self):
        self._assert_window()
        head = self.checkpoint.sync(self.scope_digest, self.operation_journal, self.pause_journal)
        # Remote persistence can consume the lease: recheck before dispatch.
        self._assert_window()
        return head

    def _assert_window(self):
        require(not self.failed, 'EXECUTOR_ALREADY_FAILED')
        try:
            self.operation_journal.assert_live()
            self.pause_journal.assert_live()
            self.lifetime.assert_alive()
            self.run.assert_running()
            require(self._run_identity() == self.current_run and self.run.source == self.scope['source'],
                    'EXECUTOR_RUN_CHANGED')
            self.operator.assert_held(self.scope_digest)
            self.lifetime.assert_alive()
        except BaseException:
            self.failed = True
            raise

    def assert_scope(self, plan_digest):
        require(plan_digest == self.scope['workflow_plan_digest'], 'EXECUTOR_PAUSE_SCOPE')
        # Local pause bookkeeping/read observations do not admit an effect.
        # WorkflowAPI still brackets every PUT with assert_dispatch, whose
        # checkpoint has fresh authenticated pre/post authority observations.
        require(not self.failed, 'EXECUTOR_ALREADY_FAILED')
        try:
            self.operation_journal.assert_live()
            self.pause_journal.assert_live()
            self.lifetime.assert_alive()
            self.run.assert_current()
            require(self._run_identity() == self.current_run and self.run.source == self.scope['source'],
                    'EXECUTOR_RUN_CHANGED')
            self.operator.assert_local(self.scope_digest)
            self.lifetime.assert_alive()
        except BaseException:
            self.failed = True
            raise

    def assert_dispatch(self, plan_digest, action, workflow_id):
        # _sync_checkpoint below supplies the fresh pre/post authority checks.
        # Keep local scope/phase checks, without an extra identical remote round.
        require(plan_digest == self.scope['workflow_plan_digest'], 'EXECUTOR_PAUSE_SCOPE')
        require((self.phase == 'pausing' and action == 'disable')
                or (self.phase == 'restoring' and action == 'enable'), 'EXECUTOR_DISPATCH_PHASE')
        require(workflow_id in self.pause.states, 'EXECUTOR_DISPATCH_SCOPE')
        if action == 'enable':
            self.release.assert_reconciled(self.scope_digest, self.release_outcome)
        self._sync_checkpoint()
        if action == 'enable':
            self.release.assert_reconciled(self.scope_digest, self.release_outcome)

    def execute(self, connection_factory, *, route_root=None, seconds=30):
        """Exactly one session. Success does not automatically restore workflows."""
        require(not self.staged, 'EXECUTOR_USE_STAGED_ENTRYPOINT')
        self._assert_window()
        require(self.state == 'bound' and self.current_run == self.scope['origin_run'], 'EXECUTOR_SESSION_ALREADY_ATTEMPTED')
        expected = 'BEFORE' if self.scope['operation'] == 'rollback' else 'AFTER'
        try:
            self._sync_checkpoint()  # Publish pristine BOUND/PLAN before any intent.
            self.phase = 'pausing'
            self.pause.pause()
            self.phase = 'session'
            self.hold.assert_held(self.target, self.scope['operation'])
            self._event('SESSION_INTENT', 'UNKNOWN')
            # Persisted UNKNOWN exists before the first possible GRANT/REVOKE.
            self.hold.assert_held(self.target, self.scope['operation'])
            kwargs = {'external_guard': self.hold, 'seconds': seconds,
                      'rollback': self.scope['operation'] == 'rollback'}
            if route_root is not None:
                kwargs['route_root'] = route_root
            result = permission_session(self.target, self.manifest_path, self.scope['manifest_digest'],
                                        connection_factory, self.scope['route'], **kwargs)
            require(result == expected, 'EXECUTOR_UNEXPECTED_RESULT')
            self.hold.assert_held(self.target, self.scope['operation'])
            self._event('SESSION_RESULT', result)
            return result
        except BaseException:
            # Never infer rollback or release admission from a thrown exception.
            if self.state == 'session_unknown':
                try:
                    self._event('SESSION_ERROR', 'UNKNOWN')
                except BaseException:
                    pass  # Durable intent still requires external reconciliation.
            self.failed = True
            raise ExecutionError() from None
        finally:
            self.phase = 'idle'

    def prepare(self):
        """Pause only, in the origin run; return the durably accepted head.

        No SQL session or workflow restoration. A failed stage needs separate
        reconciliation; its in-memory object cannot extend or renew a lease.
        """
        require(self.staged and self.state == 'bound'
                and self.current_run == self.scope['origin_run'], 'EXECUTOR_PREPARE_REFUSED')
        try:
            self._sync_checkpoint()
            self.phase = 'pausing'
            self.pause.pause()
            self.hold.assert_held(self.target, self.scope['operation'])
            return self._event('PREPARED', 'UNKNOWN')
        except BaseException:
            self.failed = True
            raise ExecutionError() from None
        finally:
            self.phase = 'idle'

    def execute_prepared(self, connection_factory, *, route_root=None, seconds=30):
        """Consume one prepared operation in a different authenticated run.

        The accepted remote pair must equal the locked local pair before any
        append. SESSION_BOUND consumes the dispatch opportunity even if the
        process dies before SESSION_INTENT. No resume/rebind entrypoint exists.
        """
        require(self.staged and self.state == 'prepared'
                and self.current_run['run_id'] != self.scope['origin_run']['run_id'],
                'EXECUTOR_PREPARED_SESSION_REFUSED')
        expected = 'BEFORE' if self.scope['operation'] == 'rollback' else 'AFTER'
        try:
            self._assert_window()
            self.checkpoint.accept_resume(self.scope_digest, self.operation_journal, self.pause_journal)
            self.hold.assert_held(self.target, self.scope['operation'])
            self.phase = 'session'
            self._event('SESSION_BOUND', 'UNKNOWN')
            self.hold.assert_held(self.target, self.scope['operation'])
            self._event('SESSION_INTENT', 'UNKNOWN')
            self.hold.assert_held(self.target, self.scope['operation'])
            kwargs = dict(external_guard=self.hold, seconds=seconds,
                          rollback=self.scope['operation'] == 'rollback')
            if route_root is not None:
                kwargs['route_root'] = route_root
            result = permission_session(self.target, self.manifest_path, self.scope['manifest_digest'],
                                        connection_factory, self.scope['route'], **kwargs)
            require(result == expected, 'EXECUTOR_UNEXPECTED_RESULT')
            self.hold.assert_held(self.target, self.scope['operation'])
            self._event('SESSION_RESULT', result)
            return result
        except BaseException:
            if self.state == 'session_unknown':
                try:
                    self._event('SESSION_ERROR', 'UNKNOWN')
                except BaseException:
                    pass
            self.failed = True
            raise ExecutionError() from None
        finally:
            self.phase = 'idle'

    def restore(self, release, outcome):
        """Explicit separate recovery, including after reconstruction/new run.

release must independently re-observe the bound DB manifest and completion of
remote processes/backends. It may not trust this executor's recorded result.
An ambiguous GitHub PUT still requires the pause library's separate recovery.
"""
        self._assert_window()
        require(outcome in ('BEFORE', 'AFTER') and callable(getattr(release, 'assert_reconciled', None)),
                'EXECUTOR_RELEASE_REQUIRED')
        require(self.state != 'restored', 'EXECUTOR_ALREADY_RESTORED')
        self.release, self.release_outcome = release, outcome
        outer = self

        class ReleaseAdapter:
            def assert_reconciled(self, plan_digest):
                outer.assert_scope(plan_digest)
                release.assert_reconciled(outer.scope_digest, outcome)

        try:
            release.assert_reconciled(self.scope_digest, outcome)
            self._sync_checkpoint()
            self._event('RESTORE_INTENT', outcome)
            self.phase = 'restoring'
            self.pause.restore(ReleaseAdapter())
            self._assert_window()
            release.assert_reconciled(self.scope_digest, outcome)
            self._event('RESTORED', outcome)
        except BaseException:
            self.failed = True
            raise ExecutionError() from None
        finally:
            self.phase = 'idle'
