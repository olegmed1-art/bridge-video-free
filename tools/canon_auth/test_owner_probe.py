import io
import json
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from . import owner_probe as p
from .test_resident import BINDING, Conn


def environment():
    return dict(EXPECTED_PROBE_SHA="a" * 40, GITHUB_SHA="a" * 40,
                EXPECTED_MAIN=p.MAIN, GITHUB_REPOSITORY=p.REPOSITORY,
                GITHUB_REF="refs/heads/" + p.BRANCH, GITHUB_EVENT_NAME="workflow_dispatch",
                GITHUB_ACTOR="olegmed1-art", GITHUB_TRIGGERING_ACTOR="olegmed1-art",
                GITHUB_WORKFLOW_REF=p.REPOSITORY + "/" + p.WORKFLOW + "@refs/heads/" + p.BRANCH,
                OWNER_PROBE_SCOPE="canon-readonly")


def git(*args):
    return "a" * 40 if args[0] == "rev-parse" else p.MAIN


def test_exact_reviewed_context_and_checkout():
    assert p.context(environment(), git) == "a" * 40


@pytest.mark.parametrize("key", list(environment()))
def test_wrong_context_refused_before_connection(key):
    env = environment()
    env[key] = "unreviewed"
    with pytest.raises(p.resident.Refused):
        p.context(env, git)


def test_checkout_or_main_ancestry_mismatch_refused():
    with pytest.raises(p.resident.Refused, match="probe_checkout_refused"):
        p.context(environment(), lambda *args: "b" * 40)


class Response:
    def __init__(self, raw=None, url=p.URL):
        self.status, self.url = 200, url
        self.raw = raw or json.dumps({"ref": "refs/heads/main",
                    "object": {"type": "commit", "sha": p.MAIN}}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, count):
        assert count == 16385
        return self.raw[:count]


def test_source_checks_are_single_bounded_get_without_redirect():
    calls = []
    def open_(request, timeout):
        calls.append((request.get_method(), request.full_url, timeout))
        return Response()
    p.source_check("synthetic-token", SimpleNamespace(open=open_))
    assert calls == [("GET", p.URL, 5)]
    assert p.NoRedirect().redirect_request(None, None, None, None, None, None) is None


@pytest.mark.parametrize("response", [Response(url="https://other.invalid"),
    Response(raw=b"x" * 17000),
    Response(raw=b'{"ref":"refs/heads/main","ref":"refs/heads/main"}'),
    Response(raw=json.dumps({"ref": "refs/heads/main", "object": {
        "type": "commit", "sha": "b" * 40}}).encode())])
def test_untrusted_or_stale_source_refused(response):
    with pytest.raises((p.resident.Refused, ValueError)):
        p.source_check("synthetic-token", SimpleNamespace(open=lambda *a, **kw: response))


class Resident(Conn):
    read_only = False

    def execute(self, sql, params=()):
        assert self.read_only
        assert sql.strip().startswith(("SELECT", "SET TRANSACTION", "SET LOCAL"))
        if sql == "SELECT current_setting('transaction_read_only')":
            self.calls.append((sql, params))
            return SimpleNamespace(fetchone=lambda: ("on",))
        return super().execute(sql, params)


@contextmanager
def fixture(conn):
    closed = []
    @contextmanager
    def connect(**kwargs):
        assert kwargs["autocommit"] is True
        assert kwargs["application_name"] == "canon-owner-readonly-probe"
        try:
            yield conn
        finally:
            closed.append(True)
    # The existing helper imports Unix pwd; tests inject the credential adapter
    # on Windows. Real helper contracts remain in the Ubuntu workflow contract.
    adapter = SimpleNamespace(parameters=lambda raw: {"password": "synthetic-private"},
                              EXPECTED_TARGET={"neon": vars(BINDING)})
    with patch.dict("sys.modules", {"ops.native_maintenance_owner_attest": adapter}):
        yield connect, closed


def test_real_inventory_sql_is_readonly_and_outputs_no_connection_values():
    conn = Resident()
    with fixture(conn) as (connect, closed):
        report = p.observe(connect, "synthetic-private")
    assert closed == [True]
    assert report["status"] == "PASS"
    assert report["write_admission"] is False and report["production_mutations"] is False
    assert report["transaction_read_only"] is True
    assert not conn.writes
    assert not any(value in json.dumps(report) for value in ("synthetic-private", BINDING.host, BINDING.project_id))


def test_missing_privileges_are_reported_without_self_elevation():
    conn = Resident()
    conn.allowed = False
    with fixture(conn) as (connect, closed):
        report = p.observe(connect, "synthetic-private")
    assert report["status"] == "MISSING_PRIVILEGES"
    assert report["owned_revoke_privileges"] is False
    assert closed and not conn.writes


def test_server_readonly_off_refused_before_catalog_reads():
    conn = Resident()
    def off(sql, params=()):
        conn.calls.append((sql, params))
        return SimpleNamespace(fetchone=lambda: ("off",))
    conn.execute = off
    with fixture(conn) as (connect, closed), pytest.raises(p.resident.Refused, match="server_read_only_required"):
        p.observe(connect, "synthetic-private")
    assert closed and not any("pg_settings" in sql for sql, _ in conn.calls)


def test_main_context_failure_never_reads_owner_credential_or_connects():
    class Environment(dict):
        def get(self, key, default=None):
            if key == "NATIVE_OWNER_DATABASE_URL":
                pytest.fail("Credential read before context admission")
            return super().get(key, default)
    with patch.object(p.os, "environ", Environment()), patch("psycopg.connect") as connect:
        with pytest.raises(p.resident.Refused):
            p.main()
        connect.assert_not_called()


def test_failed_probe_never_serializes_exception_or_credential():
    output = io.StringIO()
    with patch.object(p, "main", side_effect=RuntimeError("synthetic-private")), redirect_stdout(output):
        with pytest.raises(SystemExit) as exc:
            p.entrypoint()
    assert exc.value.code == 2
    assert "synthetic-private" not in output.getvalue()
    assert json.loads(output.getvalue())["write_admission"] is False
