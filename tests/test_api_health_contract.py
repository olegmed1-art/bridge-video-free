#!/usr/bin/env python3
from __future__ import annotations

from contextlib import contextmanager

from fastapi import HTTPException

import bridge_school_api.main as api


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.row = row

    def cursor(self):
        return FakeCursor(self.row)


@contextmanager
def good_connect():
    yield FakeConnection({"principal": api.EXPECTED_PRINCIPAL, "school_count": 1})


@contextmanager
def bad_boundary_connect():
    yield FakeConnection({"principal": "unexpected", "school_count": 1})


@contextmanager
def failed_connect():
    raise RuntimeError("password authentication failed for synthetic-user")
    yield  # pragma: no cover


def expect_generic_503(connect_impl) -> None:
    original = api.connect
    api.connect = connect_impl
    try:
        try:
            api.healthz()
        except HTTPException as exc:
            assert exc.status_code == 503
            assert exc.detail == "service unavailable"
            assert "password" not in str(exc.detail).lower()
            assert "authentication" not in str(exc.detail).lower()
        else:
            raise AssertionError("expected HTTP 503")
    finally:
        api.connect = original


def main() -> None:
    original = api.connect
    api.connect = good_connect
    try:
        response = api.healthz()
    finally:
        api.connect = original

    assert response.status_code == 200
    assert response.body == b'{"status":"ok"}'
    assert response.headers["cache-control"] == "public, max-age=0, must-revalidate"
    assert response.headers["vercel-cdn-cache-control"] == "public, max-age=15, stale-while-revalidate=15"

    expect_generic_503(failed_connect)
    expect_generic_503(bad_boundary_connect)

    db_error = api.DatabaseConfigurationError("synthetic configuration detail")
    db_response = api.database_service_unavailable_response("/v1/students", db_error)
    assert db_response.status_code == 503
    assert db_response.body == b'{"detail":"service unavailable"}'
    assert db_response.headers["cache-control"] == "private, no-store, max-age=0"
    assert b"synthetic" not in db_response.body

    endpoint_error = api.DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL targets an unexpected Neon endpoint")
    principal_error = api.DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL uses an unexpected database principal")
    assert api._database_failure_category(endpoint_error) == "configuration_endpoint"
    assert api._database_failure_category(principal_error) == "configuration_principal"

    print("API_HEALTH_CONTRACT: PASS")


if __name__ == "__main__":
    main()
