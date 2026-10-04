"""Read-only behavior on a caller-provided dedicated application connection.

No connection launcher, environment/credential lookup, role change, or live CLI.
A disposable result never qualifies the live application credential.
"""
from uuid import UUID
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import tuple_row
from bridge_school_api import tournament_teacher as teacher
from .resident_preflight import Refused, catalog_path

PRINCIPAL = "bridge_school_app_principal"
ABSENT_ID = UUID(int=0)
READ_TABLES = ("public.school", "ai.decision_position", "public.source",
               "public.knowledge_version_source")
REVOKE_COLUMNS = (("bidding.runtime_activation", "status"),
                  ("bidding.runtime_activation", "valid_to"),
                  ("public.canon_activation", "status"),
                  ("public.canon_activation", "valid_to"))


def inspect_teacher_connection(conn, *, database="neondb"):
    if (database not in ("neondb", "tournament_rehearsal") or conn.closed
            or conn.info.transaction_status != 0):
        raise Refused("idle_teacher_connection_required")
    try:
        with conn.transaction(force_rollback=True):
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='2s'")
            with conn.cursor(row_factory=tuple_row) as cur:
                catalog_path(cur)
                identity = cur.execute("SELECT pg_catalog.current_database(),session_user,current_user").fetchone()
                if identity != (database, PRINCIPAL, PRINCIPAL):
                    raise Refused("application_login_identity_required")
                if cur.execute("SELECT pg_catalog.current_setting('transaction_read_only')").fetchone() != ("on",):
                    raise Refused("teacher_readonly_required")
                reads = all(cur.execute("SELECT pg_catalog.has_table_privilege(current_user,%s,'SELECT')",
                                        (table,)).fetchone() == (True,) for table in READ_TABLES)
                no_revoke = all(cur.execute("SELECT pg_catalog.has_column_privilege(current_user,%s,%s,'UPDATE')",
                                            (table, column)).fetchone() == (False,)
                                for table, column in REVOKE_COLUMNS)
                for table, key in (("public.school", "school_id"),):
                    exists = cur.execute("SELECT EXISTS(SELECT 1 FROM " + table +
                                         " WHERE " + key + "=%s)", (ABSENT_ID,)).fetchone()
                    if exists != (False,):
                        raise Refused("absent_synthetic_ids_required")
                # Execute the actual fixed routines, with an absent synthetic ID.
                # No rows, identifiers or private school contexts leave this module.
                # Internal activation gate is deliberately inaccessible to app.
                # The public school catalog calls it as SECURITY DEFINER.
                gate_denied = False
                try:
                    with conn.transaction(force_rollback=True):
                        cur.execute("SELECT bidding.rule_passes_activation_gates(%s)", (ABSENT_ID,))
                except InsufficientPrivilege:
                    gate_denied = True
                catalog_empty = cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM bidding.get_school_runtime_rule_catalog(%s,%s))",
                    (ABSENT_ID, teacher.SCOPE)).fetchone() == (False,)
                if not all((reads, no_revoke, gate_denied, catalog_empty)):
                    raise Refused("teacher_behavior_required")
        return {"status": "teacher_readonly_behavior", "application_login_identity": True,
                "read_privileges": True, "owned_revoke_denied": True,
                "internal_gate_execute_denied": True, "absent_school_catalog_empty": True,
                "transaction_read_only": True, "mutations": False, "write_admission": False}
    except Refused:
        raise
    except Exception:
        raise Refused("teacher_behavior_unavailable") from None
