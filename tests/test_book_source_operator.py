"""Synthetic operator admission tests: connection and writer are always mocked."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from database import book_source_operator as operator
from test_book_world import reviewed_fixture, reviewed_identity_fixture

SCHOOL = "11111111-1111-1111-1111-111111111111"
SOURCE = "22222222-2222-2222-2222-222222222222"
DSN = ("postgresql://bridge_school_worker_principal:SYNTHETIC_SECRET@"
       "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech/"
       "neondb?sslmode=require&channel_binding=require")


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.capability_query = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.connection.statements.append(sql)
        self.capability_query = "has_table_privilege" in sql

    def fetchone(self):
        if self.capability_query:
            self.connection.capability_observations += 1
            return self.connection.capabilities(self.connection.capability_observations)
        self.connection.observations += 1
        return self.connection.identity(self.connection.observations)


class Connection:
    def __init__(self):
        self.info = SimpleNamespace(
            host=operator.EXPECTED_HOST, port=5432, dbname="neondb",
            user=operator.EXPECTED_PRINCIPAL, transaction_status=0)
        self.statements = []
        self.observations = 0
        self.capability_observations = 0
        self.capabilities = lambda count: (True,) * 9
        self.identity = lambda count: (
            "neondb", operator.EXPECTED_PRINCIPAL, operator.EXPECTED_PRINCIPAL,
            True, False, False, False)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return Cursor(self)


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    bundle, receipt = reviewed_fixture()
    identity = reviewed_identity_fixture(
        bundle, receipt, SCHOOL, SOURCE, bundle["source"]["locator"])
    argv = ["--school-id", SCHOOL, "--source-id", SOURCE,
            "--target-project-id", "misty-poetry-18012774",
            "--target-branch-id", "br-aged-mud-b1i64914",
            "--target-endpoint-id", "ep-noisy-pine-b1pe30sf",
            "--principal", "bridge_school_worker_principal"]
    for label, value in (("bundle", bundle), ("receipt", receipt),
                         ("identity-review", identity)):
        data = json.dumps(value, ensure_ascii=False).encode()
        path = tmp_path / (label + ".json")
        path.write_bytes(data)
        argv += ["--" + label, str(path), "--" + label + "-sha256",
                 hashlib.sha256(data).hexdigest()]
    connection = Connection()
    connect = Mock(return_value=connection)
    # Isolate the synthetic environment without reading actual environment values.
    monkeypatch.setattr(operator.os, "environ", {"BRIDGE_WORKER_DATABASE_URL": DSN})
    monkeypatch.setattr(operator.psycopg, "connect", connect)
    results = []
    def reconcile(conn, value, review, **kwargs):
        assert conn is connection
        results.append(kwargs)
        kwargs["before_commit"]()
        return dict(asset_id=identity["asset_id"], source_id=SOURCE,
                    new_source_rows=int(kwargs["register_missing_source"]),
                    new_asset_rows=1, new_link_rows=1,
                    dry_run=kwargs["dry_run"], committed=not kwargs["dry_run"],
                    source_updated=False, publication_performed=False)
    writer = Mock(side_effect=reconcile)
    monkeypatch.setattr(operator, "reconcile_book_source_asset", writer)
    return SimpleNamespace(argv=argv, connection=connection, connect=connect,
                           writer=writer, results=results, bundle=bundle,
                           receipt=receipt, identity=identity, tmp_path=tmp_path)


def execute(invocation, extra=()):
    return operator.main(invocation.argv + list(extra))


def test_default_dry_run_revalidates_actual_principal_before_commit(invocation, capsys):
    assert execute(invocation) == 0
    assert invocation.results[0]["dry_run"] is True
    assert invocation.results[0]["register_missing_source"] is False
    assert invocation.connection.observations == 2
    output = json.loads(capsys.readouterr().out)
    assert output["committed"] is False
    assert output["publication_performed"] is False
    assert output["max_new_rows"] == {"source": 0, "asset": 1, "source_asset": 1}
    assert output["input_sha256"]["identity_review"]
    assert output["publication_hash"]
    assert "SYNTHETIC_SECRET" not in json.dumps(output)


def test_explicit_commit_is_limited_to_existing_source(invocation, capsys):
    assert execute(invocation, ["--commit"]) == 0
    assert invocation.results[0]["dry_run"] is False
    assert invocation.results[0]["register_missing_source"] is False
    output = json.loads(capsys.readouterr().out)
    assert output["committed"] is True
    assert output["new_source_rows"] == output["max_new_rows"]["source"] == 0


@pytest.mark.parametrize("change", [
    lambda d: d.replace("bridge_school_worker_principal", "autopilot_light_worker_login"),
    lambda d: d.replace("ep-noisy-pine-b1pe30sf-pooler", "ep-other-branch-pooler"),
    lambda d: d.replace("/neondb?", "/other?"),
    lambda d: d.replace("sslmode=require", "sslmode=prefer"),
    lambda d: d.replace("channel_binding=require", "channel_binding=prefer"),
    lambda d: d + "&host=evil.example",
    lambda d: d + "&options=-c%20role%3Dneondb_owner",
    lambda d: d + "&sslmode=require",
    lambda d: d + "#fragment",
    lambda d: d.replace("SYNTHETIC_SECRET", ""),
    lambda d: d.replace(".tech/", ".tech:6543/"),
    lambda d: " " + d,
])
def test_dsn_policy_never_retargets_or_falls_back(invocation, monkeypatch, change, capsys):
    monkeypatch.setenv("BRIDGE_WORKER_DATABASE_URL", change(DSN))
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()
    invocation.writer.assert_not_called()
    assert "SYNTHETIC_SECRET" not in capsys.readouterr().err


@pytest.mark.parametrize("field,value", [
    ("--target-project-id", "other"), ("--target-branch-id", "other"),
    ("--target-endpoint-id", "other"), ("--principal", "autopilot_light_worker_login"),
    ("--school-id", "not-a-uuid"), ("--source-id", SCHOOL),
])
def test_scope_mismatch_fails_before_connection(invocation, field, value):
    invocation.argv[invocation.argv.index(field) + 1] = value
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()


def test_no_database_url_fallback(invocation, monkeypatch):
    monkeypatch.delenv("BRIDGE_WORKER_DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", DSN)
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()


@pytest.mark.parametrize("bad", [
    ("wrong_db", operator.EXPECTED_PRINCIPAL, operator.EXPECTED_PRINCIPAL, True, False, False, False),
    ("neondb", "autopilot_light_worker_login", operator.EXPECTED_PRINCIPAL, True, False, False, False),
    ("neondb", operator.EXPECTED_PRINCIPAL, "neondb_owner", True, False, False, False),
    ("neondb", operator.EXPECTED_PRINCIPAL, operator.EXPECTED_PRINCIPAL, False, False, False, False),
    ("neondb", operator.EXPECTED_PRINCIPAL, operator.EXPECTED_PRINCIPAL, True, True, False, False),
    ("neondb", operator.EXPECTED_PRINCIPAL, operator.EXPECTED_PRINCIPAL, True, False, True, False),
    ("neondb", operator.EXPECTED_PRINCIPAL, operator.EXPECTED_PRINCIPAL, True, False, False, True),
])
def test_actual_identity_and_privileges_fail_closed(invocation, bad):
    invocation.connection.identity = lambda count: bad
    assert execute(invocation) == 1
    invocation.writer.assert_not_called()


def test_role_change_during_operation_fails_precommit(invocation):
    safe = invocation.connection.identity(1)
    invocation.connection.identity = lambda count: safe if count == 1 else (
        "neondb", operator.EXPECTED_PRINCIPAL, "neondb_owner", True, False, False, False)
    assert execute(invocation, ["--commit"]) == 1
    assert invocation.connection.observations == 2


@pytest.mark.parametrize("value", ['null', '[]', '"bad"', '{"x":1,"x":2}', '{"x":NaN}', '{'])
def test_malformed_json_rejected_before_connection(invocation, value):
    path = Path(invocation.argv[invocation.argv.index("--receipt") + 1])
    data = value.encode()
    path.write_bytes(data)
    invocation.argv[invocation.argv.index("--receipt-sha256") + 1] = hashlib.sha256(data).hexdigest()
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()


def test_changed_receipt_bytes_reject_before_connection(invocation):
    path = Path(invocation.argv[invocation.argv.index("--receipt") + 1])
    path.write_bytes(path.read_bytes() + b" ")
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()


def test_deadline_is_real_and_cannot_reuse_existing_timer():
    import signal
    import time
    with pytest.raises(operator.OperatorError, match="DEADLINE"):
        with operator.Deadline(0.01):
            time.sleep(0.1)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    signal.setitimer(signal.ITIMER_REAL, 10)
    try:
        with pytest.raises(operator.OperatorError, match="DEADLINE_UNAVAILABLE"):
            with operator.Deadline(1):
                pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def test_database_error_is_bounded_and_redacted_without_retry(invocation, capsys):
    invocation.connect.side_effect = operator.psycopg.OperationalError(
        "password=SYNTHETIC_SECRET " + DSN)
    assert execute(invocation) == 1
    assert invocation.connect.call_count == 1
    output = capsys.readouterr()
    assert "SYNTHETIC_SECRET" not in output.err
    assert "postgresql://" not in output.err
    assert "OperationalError" not in output.err


def test_connect_and_query_timeouts_are_fixed_and_autocommit(invocation):
    assert execute(invocation) == 0
    kwargs = invocation.connect.call_args.kwargs
    assert kwargs["autocommit"] is True
    assert kwargs["connect_timeout"] <= 10
    assert "statement_timeout=15000" in kwargs["options"]
    assert "lock_timeout=5000" in kwargs["options"]


def test_invalid_timeout_has_no_connection(invocation):
    assert execute(invocation, ["--timeout-seconds", "61"]) == 1
    invocation.connect.assert_not_called()


def test_unknown_dsn_argument_is_not_echoed(invocation, capsys):
    assert execute(invocation, ["--dsn", DSN]) == 1
    invocation.connect.assert_not_called()
    assert "SYNTHETIC_SECRET" not in capsys.readouterr().err


def test_named_pipe_input_does_not_block_or_connect(invocation):
    import os
    path = invocation.tmp_path / "blocked-pipe"
    os.mkfifo(path)
    invocation.argv[invocation.argv.index("--bundle") + 1] = str(path)
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()


def test_symlink_input_is_rejected(invocation):
    path = invocation.tmp_path / "link.json"
    path.symlink_to(invocation.argv[invocation.argv.index("--bundle") + 1])
    invocation.argv[invocation.argv.index("--bundle") + 1] = str(path)
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()


def test_oversized_file_is_rejected(invocation):
    path = Path(invocation.argv[invocation.argv.index("--bundle") + 1])
    path.write_bytes(b" " * (operator.MAX_INPUT_BYTES + 1))
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()


def test_lost_commit_acknowledgement_is_unknown_and_never_retried(invocation, capsys):
    invocation.writer.side_effect = operator.psycopg.OperationalError(
        "SYNTHETIC_SECRET acknowledgement lost")
    assert execute(invocation, ["--commit"]) == 1
    assert invocation.writer.call_count == 1
    value = json.loads(capsys.readouterr().err)
    assert value["commit_outcome"] == "not_confirmed"
    assert value["automatic_retry"] is False


def test_connection_metadata_mismatch_fails_before_writer(invocation):
    invocation.connection.info.host = "wrong-host"
    assert execute(invocation) == 1
    invocation.writer.assert_not_called()


def test_preexisting_transaction_fails_before_writer(invocation):
    invocation.connection.info.transaction_status = 2
    assert execute(invocation) == 1
    invocation.writer.assert_not_called()


def test_deadline_closes_connection_before_unwinding():
    deadline = operator.Deadline(1)
    connection = Mock()
    deadline.connection = connection
    with pytest.raises(operator.OperatorError, match="DEADLINE_EXCEEDED"):
        deadline._expired(None, None)
    connection.close.assert_called_once_with()


@pytest.mark.parametrize("error,expected", [
    ("BOOK_SOURCE_ASSET_CONFLICT", "BOOK_SOURCE_ASSET_CONFLICT"),
    ("BOOK_SOURCE_SECRET_SYNTHETIC_SECRET", "BOOK_SOURCE_OPERATOR_INPUT_INVALID"),
])
def test_only_allowlisted_contract_codes_are_emitted(invocation, capsys, error, expected):
    invocation.writer.side_effect = ValueError(error)
    assert execute(invocation) == 1
    output = capsys.readouterr().err
    assert json.loads(output)["error"] == expected
    assert "SYNTHETIC_SECRET" not in output


@pytest.mark.parametrize("name", [
    "PGHOSTADDR", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGSERVICE",
    "PGSERVICEFILE", "PGSYSCONFDIR", "PGOPTIONS", "PGTARGETSESSIONATTRS",
    "PGLOADBALANCEHOSTS", "PGPASSFILE", "PGSSLMODE", "PGAPPNAME", "PGFUTUREOPTION",
])
@pytest.mark.parametrize("value", ["", "SYNTHETIC_SECRET_OVERRIDE"])
def test_inherited_libpq_environment_is_rejected_by_presence(
        invocation, monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()
    invocation.writer.assert_not_called()
    output = capsys.readouterr()
    assert json.loads(output.err)["error"] == "BOOK_SOURCE_OPERATOR_LIBPQ_ENVIRONMENT_FORBIDDEN"
    assert "SYNTHETIC_SECRET_OVERRIDE" not in output.err
    assert name not in output.err


def test_libpq_environment_guard_does_not_read_values(invocation, monkeypatch, capsys):
    class PresenceOnlyEnvironment(dict):
        def get(self, key, default=None):
            if key.startswith("PG") or key == "BRIDGE_WORKER_DATABASE_URL":
                raise AssertionError("connection environment value accessed before presence guard")
            return super().get(key, default)
        def __getitem__(self, key):
            if key.startswith("PG") or key == "BRIDGE_WORKER_DATABASE_URL":
                raise AssertionError("connection environment value accessed before presence guard")
            return super().__getitem__(key)
    monkeypatch.setattr(operator.os, "environ", PresenceOnlyEnvironment({
        "BRIDGE_WORKER_DATABASE_URL": DSN, "PGHOSTADDR": object(),
    }))
    assert execute(invocation) == 1
    invocation.connect.assert_not_called()
    assert json.loads(capsys.readouterr().err)["error"] == "BOOK_SOURCE_OPERATOR_LIBPQ_ENVIRONMENT_FORBIDDEN"


def test_connect_explicitly_binds_port_and_metadata_rejects_other_port(invocation):
    assert execute(invocation) == 0
    assert invocation.connect.call_args.kwargs["port"] == 5432
    invocation.writer.reset_mock()
    invocation.connection.info.port = 6543
    assert execute(invocation) == 1
    invocation.writer.assert_not_called()


@pytest.mark.parametrize("missing", range(9))
def test_missing_common_capability_fails_before_identity_writes(invocation, capsys, missing):
    capabilities = [True] * 9
    capabilities[missing] = False
    invocation.connection.capabilities = lambda count: tuple(capabilities)
    assert execute(invocation, ["--commit"]) == 1
    invocation.writer.assert_not_called()
    assert all(sql.lstrip().startswith("SELECT") for sql in invocation.connection.statements)
    assert json.loads(capsys.readouterr().err)["error"] == "BOOK_SOURCE_OPERATOR_CAPABILITY_MISSING"


@pytest.mark.parametrize("capabilities", [
    None, [True] * 9, (True,) * 8, (True,) * 10, (True,) * 8 + (1,),
])
def test_invalid_capability_observation_fails_before_writes(
        invocation, capsys, capabilities):
    invocation.connection.capabilities = lambda count: capabilities
    assert execute(invocation, ["--commit"]) == 1
    invocation.writer.assert_not_called()
    assert json.loads(capsys.readouterr().err)["error"] == "BOOK_SOURCE_OPERATOR_CAPABILITY_MISSING"


def test_existing_source_mode_does_not_require_school_update_or_source_insert(invocation):
    invocation.connection.capabilities = lambda count: (True,) * 9
    assert execute(invocation) == 0
    assert invocation.connection.capability_observations == 2
    assert invocation.results[0]["register_missing_source"] is False


def test_capability_revoked_before_commit_aborts(invocation, capsys):
    invocation.connection.capabilities = lambda count: ((True,) * 9 if count == 1
                                                       else (False,) + (True,) * 8)
    assert execute(invocation, ["--commit"]) == 1
    assert invocation.connection.capability_observations == 2
    assert json.loads(capsys.readouterr().err)["error"] == "BOOK_SOURCE_OPERATOR_CAPABILITY_MISSING"


def test_native_source_identity_select_is_preflighted(invocation):
    assert execute(invocation) == 0
    checks = [sql for sql in invocation.connection.statements if "has_table_privilege" in sql]
    assert len(checks) == 2
    assert all("pg_catalog.has_table_privilege(current_user,'public.source_identity','SELECT')" in sql
               for sql in checks)


@pytest.mark.parametrize("extra", [[], ["--commit"]])
def test_missing_source_mode_unavailable_before_inputs_or_connection(
        invocation, monkeypatch, capsys, extra):
    inputs = Mock(wraps=operator._inputs)
    monkeypatch.setattr(operator, "_inputs", inputs)
    assert execute(invocation, ["--register-missing-source", *extra]) == 1
    inputs.assert_not_called()
    invocation.connect.assert_not_called()
    invocation.writer.assert_not_called()
    output = json.loads(capsys.readouterr().err)
    assert output["error"] == "BOOK_SOURCE_OPERATOR_MISSING_SOURCE_UNAVAILABLE"
    assert output["commit_outcome"] == ("not_started" if extra else "not_requested")
    assert output["automatic_retry"] is False


def test_capability_query_is_limited_to_existing_source_privileges(invocation):
    assert execute(invocation, ["--commit"]) == 0
    checks = [sql for sql in invocation.connection.statements if "has_table_privilege" in sql]
    assert len(checks) == 2
    assert all("public.school" not in sql for sql in checks)
    assert all("'public.source','INSERT'" not in sql for sql in checks)
