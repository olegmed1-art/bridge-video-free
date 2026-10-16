"""Compose route exclusion, DB fencing and fresh outcome inspection.

No production CLI or privileged-writer agreement is supplied. The caller must
provide real external coordination; its default remains the refusing guard.
"""
from contextlib import ExitStack

from database import native_cli_permission_engine as engine
from database.native_cli_write_fence import DatabaseWriteFence
from ops.native_permission_route_fence import RouteMaintenanceFence
from ops.oracle_light_route_lease import ROOT


class MaintenanceOutcomeError(RuntimeError):
    def __init__(self, outcome, *, cleanup_failed=False):
        self.outcome = outcome
        self.cleanup_failed = cleanup_failed
        super().__init__('MAINTENANCE_FAILED_' + outcome +
                         ('_CLEANUP_FAILED' if cleanup_failed else ''))


class HeldResources(engine.MaintenanceGuard):
    def __init__(self, target, operation, route, fence, external):
        self.target = target
        self.operation = operation
        self.route = route
        self.fence = fence
        self.external = external

    def assert_held(self, target, operation):
        engine.check(target == self.target and operation == self.operation,
                     'MAINTENANCE_SCOPE_MISMATCH')
        self.external.assert_held(target, operation)
        self.route.assert_held()
        self.fence.assert_held(target)


def execute(target, path, manifest_digest, connection_factory, expected_route, *,
            external_guard=None, route_root=ROOT, seconds=30, rollback=False):
    """One apply/revoke; never retry or auto-rollback an uncertain outcome.

connection_factory returns exclusively owned context-managed, autocommit owner
connections. The mutation connection stays open through inspection so the fresh
observer can be proved distinct. The external guard must cover the whole window,
including source/run/HOLD checks and relevant privileged writer coordination.
"""
    operation = 'rollback' if rollback else 'apply'
    external = external_guard if external_guard is not None else engine.MaintenanceGuard()
    external.assert_held(target, operation)
    record = engine.load_manifest(path, manifest_digest)
    engine.check(record['target'] == engine.asdict(target), 'MANIFEST_TARGET_MISMATCH')
    expected = 'BEFORE' if rollback else 'AFTER'
    outcome = 'UNKNOWN'
    phase = 'acquiring'
    try:
        with ExitStack() as stack:
            route = stack.enter_context(RouteMaintenanceFence(
                expected_route, root=route_root, seconds=seconds))
            holder = stack.enter_context(connection_factory())
            fence = stack.enter_context(DatabaseWriteFence(holder, target, record['before']))
            guard = HeldResources(target, operation, route, fence, external)
            mutation = stack.enter_context(connection_factory())
            engine.check(mutation.info.backend_pid != holder.info.backend_pid,
                         'SEPARATE_MUTATION_BACKEND_REQUIRED')

            def inspect_fresh():
                guard.assert_held(target, operation)
                with connection_factory() as observer:
                    engine.check(observer.info.backend_pid not in
                                 (holder.info.backend_pid, mutation.info.backend_pid),
                                 'FRESH_OBSERVER_BACKEND_REQUIRED')
                    outcome = fence.inspect(observer, path, manifest_digest)
                guard.assert_held(target, operation)
                return outcome

            try:
                engine.change(mutation, target, path, manifest_digest, guard,
                              rollback=rollback, database_fence=fence)
                outcome = inspect_fresh()
                engine.check(outcome == expected, 'MAINTENANCE_RESULT_DRIFT')
                return outcome
            except Exception as exc:
                # Classification is diagnostic, never authorization to retry/revoke.
                # A lost resource or failed observer means UNKNOWN, not rollback.
                try:
                    outcome = inspect_fresh()
                except Exception:
                    outcome = 'UNKNOWN'
                raise MaintenanceOutcomeError(outcome) from exc
            finally:
                phase = 'releasing'
    except MaintenanceOutcomeError:
        raise
    except Exception as exc:
        if phase != 'releasing':
            raise
        raise MaintenanceOutcomeError(outcome, cleanup_failed=True) from exc
