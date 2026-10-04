"""Synthetic metadata only; no credentials or live endpoint access."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bridge_school_api import book_runtime_identity as identity
from bridge_school_api.main import app


class Connection:
    def __init__(self):
        self.autocommit = False
        self.info = SimpleNamespace(host=identity.PRODUCTION_HOST, port=5432, transaction_status=0)
        self.pgconn = SimpleNamespace(ssl_in_use=True)
        self.session = (identity.EXPECTED_PRINCIPAL, identity.EXPECTED_DATABASE, "on",
                        datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.rows = [(name, value, "postmaster", "configuration file", value, False)
                     for name, value in [("neon.project_id", identity.PROJECT),
                                         ("neon.branch_id", "br-synthetic-observed"),
                                         ("neon.endpoint_id", identity.ENDPOINT)]]
        self.executions, self.rollbacks = [], 0
        self.fail = False

    def cursor(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql):
        self.executions.append(sql)
        if self.fail:
            raise RuntimeError("SYNTHETIC_PASSWORD_MUST_NEVER_LEAK")

    def fetchone(self):
        return self.session

    def fetchall(self):
        return self.rows

    def rollback(self):
        self.rollbacks += 1


def install(monkeypatch, conn):
    monkeypatch.setattr(identity, "probe", lambda connect: {"status": "configuration_pass_connection_not_tested"})

    @contextmanager
    def connect():
        yield conn
    monkeypatch.setattr(identity, "connect", connect)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "a" * 40)


def test_runtime_identity_auth_precedes_connection_and_response_is_not_cached(monkeypatch):
    conn = Connection()
    install(monkeypatch, conn)
    monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-validation-token")
    client = TestClient(app)
    path = "/v1/knowledge/validation/runtime-identity"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 403
    malformed = client.get(path, headers=[(b"Authorization", b"Bearer \xff")])
    assert malformed.status_code == 403
    assert malformed.json() == {"detail": "invalid bearer token"}
    assert malformed.headers["cache-control"] == "private, no-store, max-age=0"
    assert not conn.executions
    response = client.get(path, headers={"Authorization": "Bearer synthetic-validation-token"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"contract_version", "status", "observed_at", "deployment_revision", "connection", "neon"}
    assert body["status"] == "OBSERVED_NOT_ADMITTED"
    assert body["neon"]["branch_id"] == "br-synthetic-observed"
    assert set(body["neon"]) == {"project_id", "branch_id", "endpoint_id", "source", "reset_matches", "pending_restart"}
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert "vercel-cdn-cache-control" not in response.headers
    assert conn.rollbacks == 1
    assert conn.executions[0] == "SET TRANSACTION READ ONLY"
    assert sum(sql.startswith("SELECT") for sql in conn.executions) == 2
    assert all(sql.startswith(("SELECT", "SET ")) for sql in conn.executions)
    assert all("public." not in sql for sql in conn.executions)


@pytest.mark.parametrize("context", ["postmaster", "superuser"])
def test_endpoint_privileged_context_compatibility(monkeypatch, context):
    conn = Connection()
    install(monkeypatch, conn)
    conn.rows[2] = ("neon.endpoint_id", identity.ENDPOINT, context,
                    "configuration file", identity.ENDPOINT, False)
    assert identity.observe_runtime_identity()["status"] == "OBSERVED_NOT_ADMITTED"


@pytest.mark.parametrize("context", ["user", "backend", "superuser-backend", "sighup", "internal"])
def test_other_endpoint_contexts_are_not_accepted(monkeypatch, context):
    conn = Connection()
    install(monkeypatch, conn)
    conn.rows[2] = ("neon.endpoint_id", identity.ENDPOINT, context,
                    "configuration file", identity.ENDPOINT, False)
    with pytest.raises(identity.RuntimeIdentityUnavailable):
        identity.observe_runtime_identity()


def test_configuration_failure_prevents_connection(monkeypatch):
    monkeypatch.setattr(identity, "probe", lambda connect: {"status": "effective_parameters_rejected"})
    monkeypatch.setattr(identity, "connect", lambda: pytest.fail("must not connect after config failure"))
    with pytest.raises(identity.RuntimeIdentityUnavailable):
        identity.observe_runtime_identity()


@pytest.mark.parametrize("mutation", [
    "missing", "duplicate", "unknown_tag", "context", "source", "reset", "restart",
    "project", "endpoint", "branch_shape", "principal", "database", "writable",
    "host", "port", "tls", "autocommit", "busy",
])
def test_unverified_identity_fails_closed(monkeypatch, mutation):
    conn = Connection()
    install(monkeypatch, conn)
    conn.rows = [list(row) for row in conn.rows]
    if mutation == "missing": conn.rows.pop()
    elif mutation == "duplicate": conn.rows[2] = conn.rows[1]
    elif mutation == "unknown_tag": conn.rows[2][0] = "unrelated.private.setting"
    elif mutation in {"context", "source", "reset", "restart"}:
        index = {"context": 2, "source": 3, "reset": 4, "restart": 5}[mutation]
        conn.rows[1][index] = True if mutation == "restart" else "wrong"
    elif mutation in {"project", "endpoint", "branch_shape"}:
        index = {"project": 0, "endpoint": 2, "branch_shape": 1}[mutation]
        conn.rows[index][1] = conn.rows[index][4] = "wrong"
    elif mutation in {"principal", "database", "writable"}:
        conn.session = list(conn.session)
        conn.session[{"principal": 0, "database": 1, "writable": 2}[mutation]] = "wrong"
        conn.session = tuple(conn.session)
    elif mutation == "host": conn.info.host = "wrong.invalid"
    elif mutation == "port": conn.info.port = 5433
    elif mutation == "tls": conn.pgconn.ssl_in_use = False
    elif mutation == "autocommit": conn.autocommit = True
    elif mutation == "busy": conn.info.transaction_status = 2
    with pytest.raises(identity.RuntimeIdentityUnavailable, match="^runtime identity unavailable$"):
        identity.observe_runtime_identity()
    if conn.executions:
        assert conn.rollbacks == 1


def test_db_error_is_rolled_back_and_never_serialized(monkeypatch):
    conn = Connection()
    conn.fail = True
    install(monkeypatch, conn)
    monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-validation-token")
    response = TestClient(app).get("/v1/knowledge/validation/runtime-identity",
        headers={"Authorization": "Bearer synthetic-validation-token"})
    assert response.status_code == 503
    assert response.json() == {"detail": "BOOK_RUNTIME_IDENTITY_UNAVAILABLE"}
    assert "SYNTHETIC_PASSWORD" not in response.text
    assert conn.rollbacks == 1


def test_deployment_revision_is_allowlisted_and_never_admits_a_branch(monkeypatch):
    conn = Connection()
    install(monkeypatch, conn)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "SYNTHETIC_PRIVATE_VALUE")
    result = identity.observe_runtime_identity()
    assert result["deployment_revision"] is None
    assert result["status"] == "OBSERVED_NOT_ADMITTED"
