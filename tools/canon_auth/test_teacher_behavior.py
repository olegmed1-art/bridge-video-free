from contextlib import contextmanager
from psycopg.errors import InsufficientPrivilege
from types import SimpleNamespace
import pytest
from .teacher_behavior import inspect_teacher_connection, Refused, PRINCIPAL
from . import vercel_validator as v


class Connection:
    def __init__(self):
        self.closed = False
        self.info = SimpleNamespace(transaction_status=0)
        self.calls, self.end = [], None
        self.identity = ("neondb", PRINCIPAL, PRINCIPAL)
        self.readonly, self.reads, self.revoke, self.gate, self.catalog, self.exists = "on", True, False, "denied", False, False
        self.fail_rollback = False
        self.gate_privilege = False

    @contextmanager
    def transaction(self, *, force_rollback):
        assert force_rollback
        try:
            yield
        finally:
            self.end = "rollback"
            if self.fail_rollback:
                raise RuntimeError("private server error")

    @contextmanager
    def cursor(self, **kwargs):
        yield self

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        self.value = []
        if "current_database()" in sql: self.value = [self.identity]
        elif "current_setting('search_path')" in sql: self.value = [("pg_catalog",)]
        elif "transaction_read_only" in sql: self.value = [(self.readonly,)]
        elif "has_table_privilege" in sql: self.value = [(self.reads,)]
        elif "has_column_privilege" in sql: self.value = [(self.revoke,)]
        elif "has_function_privilege" in sql: self.value = [(self.gate_privilege,)]
        elif "rule_passes_activation_gates" in sql:
            if self.gate == "denied": raise InsufficientPrivilege("synthetic denied")
            self.value = [(self.gate,)]
        elif "get_school_runtime_rule_catalog" in sql: self.value = [(self.catalog,)]
        elif "SELECT EXISTS" in sql: self.value = [(self.exists,)]
        return self

    def fetchone(self): return self.value[0]
    def fetchall(self): return self.value


def test_fixed_routines_executed_only_readonly_and_report_after_rollback():
    conn = Connection()
    result = inspect_teacher_connection(conn)
    assert conn.end == "rollback"
    assert result["internal_gate_execute_denied"] and result["absent_school_catalog_empty"]
    assert result["mutations"] is False and result["write_admission"] is False
    sql = [s for s, _ in conn.calls]
    assert sql[0].endswith("READ ONLY")
    assert sql.index("SET LOCAL search_path = pg_catalog") < next(i for i, s in enumerate(sql) if "rule_passes" in s)
    assert not any(s.startswith(("UPDATE", "INSERT", "DELETE", "SET ROLE", "GRANT")) for s in sql)


@pytest.mark.parametrize("field,value", [
    ("identity", ("neondb", "neondb_owner", PRINCIPAL)),
    ("identity", ("neondb", "neondb_owner", "neondb_owner")),
    ("readonly", "off"), ("reads", False), ("revoke", True),
    ("gate", None), ("gate", True), ("gate_privilege", True), ("catalog", True), ("exists", True),
    ("fail_rollback", True)])
def test_wrong_role_gate_or_privilege_refuses_without_private_values(field, value):
    conn = Connection()
    setattr(conn, field, value)
    with pytest.raises(Refused) as exc:
        inspect_teacher_connection(conn)
    assert "private" not in str(exc.value)
    assert conn.end == "rollback"


@pytest.mark.parametrize("field,value", [("closed", True), ("busy", 2)])
def test_shared_or_closed_connection_never_queries(field, value):
    conn = Connection()
    if field == "busy": conn.info.transaction_status = value
    else: conn.closed = value
    with pytest.raises(Refused): inspect_teacher_connection(conn)
    assert conn.calls == []


def test_expired_live_validation_reads_no_token_and_creates_no_claim(tmp_path):
    class Env(dict):
        def get(self, key, default=None):
            if key == "BRIDGE_API_TOKEN":
                pytest.fail("Expired validation must not read credential")
            return super().get(key, default)
    env = Env(CANON_VALIDATION_INTENT=v.INTENT, CANON_READY_SHA=v.READY_SHA,
              CANON_READY_DEPLOYMENT=v.DEPLOYMENT, CANON_READY_STATE="READY",
              CANON_READY_ORIGIN=v.ORIGIN, VERCEL_PROJECT_ID=v.PROJECT, VERCEL_ENV="production")
    class Transport:
        def open(self, *args, **kwargs): pytest.fail("Expired validation must not send HTTP")
    claim = tmp_path / "never-created"
    with pytest.raises(v.Rejected, match="intent_rejected"):
        v.run(env, now=v.DEADLINE, opener=Transport(), claim=claim)
    assert not claim.exists()
