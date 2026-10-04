"""Read-only checks on an already opened connection; never connect or read env."""
from dataclasses import dataclass

TABLES = ("public.source", "public.knowledge_item", "public.knowledge_version",
          "public.knowledge_version_source", "bidding.rule", "bidding.rule_test",
          "bidding.rule_test_run", "ai.decision_position", "public.canon_activation",
          "bidding.runtime_activation", "bidding.ingestion_run", "bidding.ingestion_event")
UPDATES = (("public.knowledge_version", "authority_class"), ("public.knowledge_version", "review_status"),
           ("bidding.rule", "lifecycle_status"), ("public.canon_activation", "status"),
           ("public.canon_activation", "valid_to"), ("bidding.runtime_activation", "status"),
           ("bidding.runtime_activation", "valid_to"), ("bidding.ingestion_run", "status"),
           ("bidding.ingestion_run", "finished_at"))


class Refused(RuntimeError):
    pass


@dataclass(frozen=True)
class Binding:
    project_id: str
    branch_id: str
    endpoint_id: str
    host: str


def idle(conn):
    if conn.closed or not conn.autocommit or conn.info.transaction_status != 0:
        raise Refused("idle_dedicated_connection_required")


def capabilities(conn):
    """Exact plan privileges, including both revoke tables, before any activation."""
    rows = conn.execute("""
        SELECT name, has_table_privilege(current_user,name,'SELECT'),
                     has_table_privilege(current_user,name,'INSERT')
        FROM unnest(%s::text[]) AS name
    """, (list(TABLES),)).fetchall()
    update = [conn.execute("SELECT has_column_privilege(current_user,%s,%s,'UPDATE')",
                          (table, column)).fetchone()[0] for table, column in UPDATES]
    revoke = all(conn.execute("SELECT has_column_privilege(current_user,%s,%s,'UPDATE')",
                              (table, column)).fetchone()[0]
                 for table in ("public.canon_activation", "bidding.runtime_activation")
                 for column in ("status", "valid_to"))
    return {"plan_table_privileges": len(rows) == len(TABLES) and all(s and i for _, s, i in rows),
            "plan_update_privileges": all(update), "owned_revoke_privileges": revoke}


def inspect_resident(conn, binding):
    """Production-capable READ ONLY inventory; caller supplies fresh trusted binding.

    This is not a write admission or connection launcher. No connection secrets,
    DSN, environment, role grants, arbitrary SQL or secret fingerprints are read.
    """
    idle(conn)
    if (not isinstance(binding, Binding) or not all((binding.project_id, binding.branch_id,
                                                    binding.endpoint_id, binding.host))
            or conn.info.host != binding.host or conn.info.port != 5432
            or not conn.pgconn.ssl_in_use):
        raise Refused("resident_transport_binding_required")
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='2s'")
            if conn.execute("SELECT current_database(),session_user,current_user").fetchone() != (
                    "neondb", "neondb_owner", "neondb_owner"):
                raise Refused("existing_owner_connection_required")
            expected = {"neon.project_id": (binding.project_id, "postmaster"),
                        "neon.branch_id": (binding.branch_id, "postmaster"),
                        "neon.endpoint_id": (binding.endpoint_id, "superuser")}
            rows = conn.execute("""
                SELECT name,setting,context,source,reset_val,pending_restart
                FROM pg_catalog.pg_settings WHERE name=ANY(%s)
            """, (list(expected),)).fetchall()
            if (len(rows) != 3 or any((setting, context) != expected.get(name)
                    or source != "configuration file" or reset != setting or pending
                    for name, setting, context, source, reset, pending in rows)):
                raise Refused("server_binding_unproven")
            checks = capabilities(conn)
        return {"status": "resident_inventory_only", "server_binding": True, **checks,
                "mutations": False, "write_admission": False}
    except Refused:
        raise
    except Exception:
        raise Refused("resident_inventory_unavailable") from None


def inspect_disposable(conn):
    """Only the existing loopback PG18 fixture; not a production fallback."""
    idle(conn)
    if conn.info.host not in ("127.0.0.1", "localhost") or conn.info.port != 55432:
        raise Refused("disposable_loopback_required")
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        if conn.execute("SELECT current_database()").fetchone() != ("tournament_rehearsal",):
            raise Refused("disposable_database_required")
        return capabilities(conn)
