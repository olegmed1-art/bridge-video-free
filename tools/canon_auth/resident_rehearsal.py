"""Failure/revoke experiment restricted to the existing disposable loopback DB."""
from contextlib import contextmanager
from .resident_preflight import Refused, inspect_disposable
from .pilot_sql import plan


@contextmanager
def revoke_on_failure(conn, school_id, code_sha):
    compiled = plan(school_id, code_sha)
    checks = inspect_disposable(conn)
    if not all(checks.values()):
        raise Refused("revoke_capability_required_before_pilot")
    try:
        yield
    except BaseException:
        # The surrounding experiment must not leave an open caller transaction.
        # A lost process/connection cannot be repaired by a finally block; do not
        # claim guaranteed revoke when this dedicated connection is unavailable.
        try:
            if conn.closed or conn.info.transaction_status != 0:
                raise Refused("revoke_connection_unavailable")
            with conn.transaction():
                for sql in compiled["emergency"]:
                    conn.execute(sql)
        except BaseException:
            raise Refused("emergency_revoke_unproven") from None
        raise Refused("rehearsal_failure_owned_bindings_revoked") from None
