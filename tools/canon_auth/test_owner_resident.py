from contextlib import contextmanager
from types import SimpleNamespace

import pytest
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

    @contextmanager
    def transaction(self):
        self.events.append("begin")
        try:
            yield
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "current_database(),session_user" in sql:
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
