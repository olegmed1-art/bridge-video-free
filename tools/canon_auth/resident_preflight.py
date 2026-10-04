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
    """Necessary plan privileges, including both revoke tables; not write admission."""
    schemas = all(conn.execute("SELECT has_schema_privilege(current_user,%s,'USAGE')",
                               (name,)).fetchone()[0] for name in ("public", "bidding", "ai"))
    school = conn.execute("SELECT has_table_privilege(current_user,'public.school','SELECT')").fetchone()[0]
    gate = conn.execute("SELECT has_function_privilege(current_user,'bidding.rule_passes_activation_gates(uuid)','EXECUTE')").fetchone()[0]
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
    return {"plan_table_privileges": len(rows) == len(TABLES) and {r[0] for r in rows} == set(TABLES)
                and all(s and i for _, s, i in rows),
            "plan_update_privileges": all(update), "owned_revoke_privileges": revoke,
            "schema_usage": schemas, "school_select": school, "explicit_gate_execute": gate}


def inspect_resident(conn, binding):
    """Production-capable READ ONLY inventory; caller supplies fresh trusted binding.

    This is not a write admission or connection launcher. No connection secrets,
    DSN, environment, role grants, arbitrary SQL or secret fingerprints are read.
    """
    idle(conn)
    try:
        if (not isinstance(binding, Binding) or not all((binding.project_id, binding.branch_id,
                                                        binding.endpoint_id, binding.host))
                or conn.info.host != binding.host or not conn.pgconn.ssl_in_use
                or conn.info.port != 5432):
            raise Refused("resident_transport_binding_required")
        # get_parameters omits compiled defaults; read effective policy fields only.
        # Filter keywords BEFORE touching values: raw libpq info includes secrets.
        allowed = {b"sslmode", b"sslrootcert", b"channel_binding", b"gssencmode", b"options", b"service"}
        params = {row.keyword.decode("ascii"): row.val.decode("utf-8")
                  for row in conn.pgconn.info if row.keyword in allowed and row.val is not None}
        if (params.get("sslmode") != "verify-full" or not params.get("sslrootcert")
                or params.get("channel_binding") != "require" or params.get("gssencmode") != "disable"
                or params.get("options") or params.get("service")):
            # Psycopg resolves hostaddr itself. TLS authenticates the expected
            # hostname; immutable server tags below authenticate the target.
            raise Refused("preverified_owner_transport_required")
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='2s'")
            if conn.execute("SELECT current_database(),session_user,current_user").fetchone() != (
                    "neondb", "neondb_owner", "neondb_owner"):
                raise Refused("existing_owner_connection_required")
            expected = {"neon.project_id": (binding.project_id, {"postmaster"}),
                        "neon.branch_id": (binding.branch_id, {"postmaster"}),
                        "neon.endpoint_id": (binding.endpoint_id, {"postmaster", "superuser"})}
            rows = conn.execute("""
                SELECT name,setting,context,source,reset_val,pending_restart
                FROM pg_catalog.pg_settings WHERE name=ANY(%s)
            """, (list(expected),)).fetchall()
            if (len(rows) != 3 or {r[0] for r in rows} != set(expected)
                    or any(setting != expected[name][0] or context not in expected[name][1]
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
