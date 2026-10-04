from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import re
from . import resident_preflight as r

BINDING = r.Binding("synthetic-project", "synthetic-branch", "synthetic-endpoint", "synthetic.neon.tech")


class SecretEntry:
    keyword = b"password"

    @property
    def val(self):
        raise AssertionError("Must filter secret keyword before reading value")


class PolicyInfo:
    def __init__(self, params):
        self.params = params

    def __iter__(self):
        yield SecretEntry()
        for key, value in self.params.items():
            yield SimpleNamespace(keyword=key.encode(), val=value.encode())


class Conn:
    def __init__(self):
        self.closed = False
        self.autocommit = True
        self.params = dict(sslmode="verify-full", sslrootcert="synthetic-ca", channel_binding="require", gssencmode="disable")
        self.info = SimpleNamespace(transaction_status=0, host=BINDING.host, port=5432,
                                    hostaddr="127.0.0.1", get_parameters=lambda: self.params.copy())
        self.pgconn = SimpleNamespace(ssl_in_use=True, info=PolicyInfo(self.params))
        self.identity = ("neondb", "neondb_owner", "neondb_owner")
        self.allowed = True
        self.server_source = "configuration file"
        self.endpoint_context = "superuser"
        self.calls = []
        self.writes = []
        self.events = []
        self.search_path = "hostile, pg_catalog"
        self.ignore_path = False
        self.overrides_called = []
        self.rollback_failure = False

    @contextmanager
    def transaction(self, *, force_rollback=False):
        self.events.append("begin")
        old_path = self.search_path
        try:
            yield
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("rollback" if force_rollback else "commit")
            if force_rollback and self.rollback_failure:
                raise RuntimeError("synthetic rollback failure")
        finally:
            self.search_path = old_path

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql == "SET LOCAL search_path = pg_catalog":
            if not self.ignore_path:
                self.search_path = "pg_catalog"
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])
        if sql.strip().startswith("SELECT"):
            unqualified = re.search(r"(?<![.\w])(current_setting|current_database|has_[a-z_]+_privilege|unnest)\s*\(", sql)
            if unqualified:
                self.overrides_called.append(sql)
                if self.search_path != "pg_catalog":
                    # Synthetic malicious schema: forged builtin-looking results.
                    rows = [self.identity] if "current_database" in sql else [(True,)]
                    return SimpleNamespace(fetchone=lambda: rows[0], fetchall=lambda: rows)
                raise AssertionError("Builtin was not schema-qualified")
        if sql == "SELECT pg_catalog.current_setting('search_path')":
            rows = [(self.search_path,)]
        elif "current_database(),session_user" in sql:
            rows = [self.identity]
        elif "current_database()" in sql:
            rows = [("tournament_rehearsal",)]
        elif "pg_catalog.pg_settings" in sql:
            rows = [(name, value, context, self.server_source, value, False) for name, value, context in (
                ("neon.project_id", BINDING.project_id, "postmaster"),
                ("neon.branch_id", BINDING.branch_id, "postmaster"),
                ("neon.endpoint_id", BINDING.endpoint_id, self.endpoint_context))]
        elif "has_schema_privilege" in sql or "has_function_privilege" in sql:
            rows = [(self.allowed,)]
        elif "'public.school'" in sql:
            rows = [(self.allowed,)]
        elif "has_table_privilege" in sql:
            rows = [(name, True, self.allowed) for name in r.TABLES]
        elif "has_column_privilege" in sql:
            rows = [(self.allowed,)]
        else:
            rows = []
            assert sql.strip().startswith(("SET TRANSACTION", "SET LOCAL")), "Unexpected SQL in read-only fixture"
        return SimpleNamespace(fetchone=lambda: rows[0], fetchall=lambda: rows)


def test_readonly_resident_inventory_never_reads_secret_or_writes():
    conn = Conn()
    result = r.inspect_resident(conn, BINDING)
    assert result["owned_revoke_privileges"] and result["plan_table_privileges"]
    assert result["mutations"] is False and result["write_admission"] is False
    assert not conn.writes
    assert conn.calls[0][0].endswith("READ ONLY")


@pytest.mark.parametrize("change", ["role", "host", "port", "tls", "sslmode", "service", "options", "server_override", "busy", "closed"])
def test_unbound_or_shared_connection_refused(change):
    conn = Conn()
    if change == "role": conn.identity = ("neondb", "bridge_school_app_principal", "bridge_school_app_principal")
    if change == "host": conn.info.host = "other.invalid"
    if change == "port": conn.info.port = 9999
    if change == "tls": conn.pgconn.ssl_in_use = False
    if change == "sslmode": conn.params["sslmode"] = "require"
    if change == "service": conn.params["service"] = "unverified"
    if change == "options": conn.params["options"] = "-c role=other"
    if change == "server_override": conn.server_source = "session"
    if change == "busy": conn.info.transaction_status = 2
    if change == "closed": conn.closed = True
    with pytest.raises(r.Refused): r.inspect_resident(conn, BINDING)
    assert not conn.writes


def test_current_postmaster_endpoint_context_supported():
    conn = Conn()
    conn.endpoint_context = "postmaster"
    assert r.inspect_resident(conn, BINDING)["server_binding"] is True


def test_effective_compiled_default_and_psycopg_resolved_address_supported():
    conn = Conn()
    # Actual get_parameters omits compiled-default disable. It must not be used.
    def forbidden_parameter_map():
        raise AssertionError("Read only allowlisted effective libpq fields")
    conn.info.get_parameters = forbidden_parameter_map
    conn.info.hostaddr = "203.0.113.10"
    conn.params["hostaddr"] = "203.0.113.10"
    assert r.inspect_resident(conn, BINDING)["server_binding"] is True
    assert not conn.writes

def test_hostile_inherited_path_is_replaced_and_success_rolls_back():
    conn = Conn()
    assert conn.search_path == "hostile, pg_catalog"
    assert r.inspect_resident(conn, BINDING)["server_binding"] is True
    assert conn.search_path == "hostile, pg_catalog"
    assert conn.events == ["begin", "rollback"]
    assert not conn.overrides_called
    first_select = next(i for i, (sql, _) in enumerate(conn.calls) if sql.strip().startswith("SELECT"))
    assert conn.calls[first_select - 1][0] == "SET LOCAL search_path = pg_catalog"
    assert all("::text[]" not in sql and "gates(uuid)" not in sql for sql, _ in conn.calls)


def test_ignored_path_pin_refuses_before_identity_catalog_or_privileges():
    conn = Conn()
    conn.ignore_path = True
    with pytest.raises(r.Refused, match="catalog_search_path_required"):
        r.inspect_resident(conn, BINDING)
    assert conn.events == ["begin", "rollback"]
    assert not conn.overrides_called
    assert not any("current_database" in sql or "pg_settings" in sql or "has_" in sql for sql, _ in conn.calls)


def test_failed_binding_rolls_back_with_no_commit():
    conn = Conn()
    conn.server_source = "session"
    with pytest.raises(r.Refused, match="server_binding_unproven"):
        r.inspect_resident(conn, BINDING)
    assert conn.events == ["begin", "rollback"]
    assert conn.search_path == "hostile, pg_catalog"


def test_rollback_failure_cannot_return_inventory():
    conn = Conn()
    conn.rollback_failure = True
    with pytest.raises(r.Refused, match="resident_inventory_unavailable"):
        r.inspect_resident(conn, BINDING)
    assert "commit" not in conn.events

def test_synthetic_hostile_override_can_forge_legacy_privilege_answer():
    conn = Conn()
    conn.allowed = False
    assert conn.execute("SELECT has_schema_privilege(current_user,'public','USAGE')").fetchone() == (True,)
    assert conn.overrides_called
    conn.overrides_called.clear()
    result = r.inspect_resident(conn, BINDING)
    assert result["schema_usage"] is False
    assert result["write_admission"] is False
    assert not conn.overrides_called
    assert conn.events == ["begin", "rollback"]
