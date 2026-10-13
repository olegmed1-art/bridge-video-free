"""Offline runner gate tests. No PostgreSQL test is executed by this module."""
import importlib.util
import json
from pathlib import Path
import socket
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
URL = "postgresql://postgres:fixedsubset-local-only@127.0.0.1:5432/fixedsubset_test"


@pytest.fixture
def runner(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("offline mode attempted external I/O"))
    driver = ModuleType("psycopg")
    driver.connect = forbidden
    monkeypatch.setitem(sys.modules, "psycopg", driver)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("DATABASE_URL", "postgresql://do-not-use.invalid/private")
    monkeypatch.setenv("PGHOST", "do-not-use.invalid")
    path = ROOT / "database/tests/fixed_subset_admission/run.py"
    spec = importlib.util.spec_from_file_location("fixed_subset_runner_offline_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    forbidden.assert_not_called()


def test_collection_maps_all_29_real_implementations_and_stays_not_run(runner, capsys):
    assert runner.main(["--collect-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["case_count"] == 29
    assert result["status"] == "NOT_RUN" and result["sql_executed"] is False
    assert {case["id"] for case in result["cases"]} == set(runner.CASES)
    assert all(case["status"] == "NOT_RUN" and case["sql_executed"] is False for case in result["cases"])
    assert all(callable(runner.CASES[case["id"]]) for case in result["cases"])


def test_list_mode_does_not_resolve_poisoned_database_environment(runner, capsys):
    assert runner.main(["--list-cases"]) == 0
    assert tuple(capsys.readouterr().out.splitlines()) == runner.CASE_IDS


@pytest.mark.parametrize("arguments", [[], ["--execute"], ["--execute", "--dsn", URL],
    ["--collect-only", "--dsn", URL], ["--list-cases", "--evidence-dir", "not-created"]])
def test_explicit_execution_gate_rejects_incomplete_or_mixed_modes(runner, arguments):
    with pytest.raises(SystemExit) as failure:
        runner.main(arguments)
    assert failure.value.code == 2


@pytest.mark.parametrize("value", [
    "", "service=production", "postgresql://postgres:fixedsubset-local-only@localhost/fixedsubset_test",
    URL.replace("127.0.0.1", "203.0.113.1"), URL.replace("fixedsubset_test", "neondb"),
    URL.replace("fixedsubset-local-only", "other-password"), URL.replace("postgres:", "other-user:"),
    URL.replace(":5432", ":6432"), URL + "?hostaddr=203.0.113.1", URL + "?service=production", URL + "#fragment",
])
def test_runner_rejects_any_non_disposable_or_overridden_dsn_before_driver_import(runner, value):
    with pytest.raises((AssertionError, ValueError)):
        runner.explicit_dsn(value)


def test_only_documented_synthetic_endpoint_is_resolved(runner):
    config = runner.explicit_dsn(URL)
    assert config["host"] == config["hostaddr"] == "127.0.0.1"
    assert config["dbname"] == "fixedsubset_test" and config["port"] == 5432
    assert config["user"] == "postgres" and config["password"] == "fixedsubset-local-only"
    assert config["sslmode"] == config["gssencmode"] == "disable"


def test_migration_expansion_is_local_and_bounds_includes(runner):
    expanded = runner.migration_text(ROOT / "database/migrations/0058_universal_video_terminal_v2_gate.sql")
    assert "video_job_terminal_revision_check" in expanded
    assert "\\ir " not in expanded and "\\set ON_ERROR_STOP" not in expanded
    with pytest.raises(AssertionError, match="escapes"):
        runner.migration_text(ROOT / "README.md")
