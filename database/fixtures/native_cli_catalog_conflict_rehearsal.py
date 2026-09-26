"""Disposable PG18 evidence for the catalog interval after final ACL snapshot.

This deliberately demonstrates why table fences and later drift detection are
not sufficient maintenance authority. No production entry point is provided.
"""
import psycopg

from database import native_cli_permission_engine as engine
from database.native_cli_write_fence import DatabaseWriteFence
from database.fixtures.native_cli_commit_rehearsal import connection, LOGIN, PARENT


def run(target, path, manifest_digest, guard_factory):
    before = engine.load_manifest(path, manifest_digest)['before']

    def owned(conn):
        conn.execute('SET ROLE bridge_ci_owner')
        engine.identity(conn, target)
        return conn

    def inspect(expected):
        with connection() as conn:
            engine.check(engine.inspect(owned(conn), target, path, manifest_digest) == expected,
                         'CATALOG_RACE_STATE_MISMATCH')

    class FinalSnapshotProbe(engine.MaintenanceGuard):
        def __init__(self, probe):
            self.probe = probe
            self.calls = 0

        def assert_held(self, checked_target, operation):
            engine.check(checked_target == target and operation == 'apply', 'CI_PROBE_TARGET')
            self.calls += 1
            if self.calls == 3:
                self.probe()

    # Execute a real conflicting catalog update on every just-granted function.
    # Every outer transaction is intentionally rolled back after the probe.
    for signature in engine.FUNCTIONS:
        with connection() as reader:
            definition = reader.execute('SELECT pg_get_functiondef(%s::regprocedure)',
                                        (signature,)).fetchone()[0]
        engine.check('AS $function$' in definition, 'UNEXPECTED_FIXTURE_FUNCTION_FORMAT')
        replacement = definition.replace('AS $function$',
                                         'AS $function$\n-- disposable catalog conflict\n', 1)
        commands = (
            f'GRANT EXECUTE ON FUNCTION {signature} TO {PARENT}',
            f"ALTER FUNCTION {signature} SET statement_timeout TO '3s'",
            f'ALTER FUNCTION {signature} OWNER TO postgres',
            replacement,
        )
        for command in commands:
            def probe(command=command):
                with connection() as other:
                    # A superuser contender removes privilege-denial ambiguity;
                    # the grant engine itself remains the non-superuser owner.
                    other.execute("SET lock_timeout='100ms'")
                    try:
                        with other.transaction(force_rollback=True):
                            other.execute(command)
                    except psycopg.errors.LockNotAvailable:
                        pass
                    else:
                        raise AssertionError('TARGET_CATALOG_WRITE_NOT_BLOCKED')
                raise engine.Refused('CI_TARGET_CONFLICT_OBSERVED')
            with connection() as holder:
                with DatabaseWriteFence(owned(holder), target, before) as fence:
                    with connection() as conn:
                        try:
                            engine.change(owned(conn), target, path, manifest_digest,
                                          FinalSnapshotProbe(probe), database_fence=fence)
                        except engine.Refused as exc:
                            engine.check(str(exc) == 'CI_TARGET_CONFLICT_OBSERVED',
                                         'UNEXPECTED_TARGET_CONFLICT_RESULT')
                        else:
                            raise AssertionError('PROBE_DID_NOT_RUN')
            inspect('BEFORE')
    print('NATIVE_GRANTED_FUNCTION_CATALOG_CONFLICTS_PASS')

    # These are relevant objects not updated by the six GRANTs. Commit their
    # changes AFTER the engine's final snapshot, while all table locks remain.
    # A stub guard allows the engine to return AFTER; fresh inspection must
    # report DRIFT. This is limitation evidence, not safe production admission.
    cases = (
        ('helper-acl',
         f'GRANT EXECUTE ON FUNCTION {engine.HELPER} TO {PARENT}',
         f'REVOKE EXECUTE ON FUNCTION {engine.HELPER} FROM {PARENT} RESTRICT'),
        ('recipient-membership',
         f'GRANT autopilot_callback TO {LOGIN}',
         f'REVOKE autopilot_callback FROM {LOGIN} RESTRICT'),
    )
    for label, mutation, restore in cases:
        changed = False
        def probe():
            nonlocal changed
            with connection() as other:
                other.execute(mutation)
                changed = True
        try:
            with connection() as holder:
                with DatabaseWriteFence(owned(holder), target, before) as fence:
                    with connection() as conn:
                        result = engine.change(owned(conn), target, path, manifest_digest,
                                               FinalSnapshotProbe(probe), database_fence=fence)
                    engine.check(changed and result == 'AFTER', 'LATE_CATALOG_PROBE_MISSING')
                    with connection() as observer:
                        engine.check(fence.inspect(owned(observer), path, manifest_digest) == 'DRIFT',
                                     'LATE_CATALOG_DRIFT_NOT_DETECTED')
        finally:
            if changed:
                with connection() as other:
                    other.execute(restore)
            with connection() as conn:
                owned(conn)
                state = engine.inspect(conn, target, path, manifest_digest)
                if state == 'AFTER':
                    engine.change(conn, target, path, manifest_digest, guard_factory(), rollback=True)
                else:
                    engine.check(state == 'BEFORE', 'CATALOG_PROBE_CLEANUP_DRIFT')
        inspect('BEFORE')
        print('NATIVE_LATE_CATALOG_WINDOW_CONFIRMED', label)
