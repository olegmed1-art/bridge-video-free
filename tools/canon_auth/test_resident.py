from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest
from . import resident_preflight as r
from .resident_rehearsal import revoke_on_failure
from .disposable_preflight import inspect_disposable

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
        self.fail_write = False
        self.server_source = "configuration file"
        self.endpoint_context = "superuser"
        self.calls = []
        self.writes = []
        self.events = []

    @contextmanager
    def transaction(self, *, force_rollback=False):
        self.events.append("begin")
        try:
            yield
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("rollback" if force_rollback else "commit")

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "current_setting('search_path')" in sql:
            rows = [("pg_catalog",)]
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
            if sql.startswith(("UPDATE", "DO $audit$")):
                if self.fail_write:
                    raise RuntimeError("private failure content must never escape")
                self.writes.append(sql)
        return SimpleNamespace(fetchone=lambda: rows[0], fetchall=lambda: rows)


def local(conn):
    conn.info.host, conn.info.port = "127.0.0.1", 55432
    return conn


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


def test_capability_inventory_does_not_turn_missing_grant_into_permission():
    conn = Conn()
    conn.allowed = False
    result = r.inspect_resident(conn, BINDING)
    assert not result["owned_revoke_privileges"]
    assert result["write_admission"] is False
    with pytest.raises(r.Refused, match="revoke_capability_required"):
        with revoke_on_failure(local(conn), UUID(int=101), "0" * 40):
            pytest.fail("Pilot body must not run without revoke")
    assert not conn.writes


def test_rehearsal_cannot_use_production_connection():
    conn = Conn()
    with pytest.raises(r.Refused, match="disposable_loopback_required"):
        with revoke_on_failure(conn, UUID(int=101), "0" * 40):
            pytest.fail("Must not reach production body")
    assert not conn.calls


def test_disposable_hostaddr_override_refused():
    conn = local(Conn())
    conn.info.hostaddr = "203.0.113.10"
    with pytest.raises(r.Refused, match="disposable_loopback_required"):
        inspect_disposable(conn)
    assert not conn.calls


def test_current_postmaster_endpoint_context_supported():
    conn = Conn()
    conn.endpoint_context = "postmaster"
    assert r.inspect_resident(conn, BINDING)["server_binding"] is True


def test_failed_gate_commits_only_compiled_owned_revoke():
    conn = local(Conn())
    with pytest.raises(r.Refused, match="rehearsal_failure_owned_bindings_revoked"):
        with revoke_on_failure(conn, UUID(int=101), "0" * 40):
            raise RuntimeError("private failure content must never escape")
    assert len(conn.writes) == 3
    assert conn.events[-1] == "commit"
    assert all(sql.startswith(("UPDATE bidding.runtime_activation", "UPDATE public.canon_activation", "DO $audit$"))
               for sql in conn.writes)


def test_lost_connection_does_not_claim_guaranteed_revoke():
    conn = local(Conn())
    with pytest.raises(r.Refused, match="emergency_revoke_unproven") as exc:
        with revoke_on_failure(conn, UUID(int=101), "0" * 40):
            conn.closed = True
            raise RuntimeError("private failure content must never escape")
    assert "private failure" not in str(exc.value)
    assert not conn.writes


def test_failed_revoke_reports_unproven_without_private_error():
    conn = local(Conn())
    conn.fail_write = True
    with pytest.raises(r.Refused, match="emergency_revoke_unproven") as exc:
        with revoke_on_failure(conn, UUID(int=101), "0" * 40):
            raise RuntimeError("private failure content must never escape")
    assert "private failure" not in str(exc.value)
    assert conn.events[-1] == "rollback"


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
