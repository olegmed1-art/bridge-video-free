"""Transport spies only; real transaction assertions live in the PostgreSQL runner."""
from copy import deepcopy
from types import ModuleType, SimpleNamespace
import socket
import sys
from unittest.mock import Mock

import pytest

from universal_video import admission_binding, admission_client as client, batch_plan, video_queue
from test_universal_video_admission_binding import rows, uid


@pytest.fixture
def transport(monkeypatch, rows):
    state = SimpleNamespace(events=[], calls=[], executions=[], error=None, commit_error=None,
        receipt=dict(status="ADMITTED", binding=admission_binding.build_binding(**rows),
                     **admission_binding.DISABLED))

    class Cursor:
        def __enter__(self):
            state.events.append("cursor_enter")
            return self

        def __exit__(self, *_):
            state.events.append("cursor_exit")

        def execute(self, statement, parameters):
            state.events.append("execute")
            state.executions.append((statement, parameters))
            if state.error:
                raise state.error

        def fetchone(self):
            state.events.append("fetch")
            return (deepcopy(state.receipt),)

    class Connection:
        def __enter__(self):
            state.events.append("transaction_enter")
            return self

        def __exit__(self, error_type, *_):
            state.events.append("rollback" if error_type else "commit")
            if error_type is None and state.commit_error:
                raise state.commit_error
            state.events.append("connection_closed")

        def cursor(self):
            return Cursor()

    def connect(dsn, **kwargs):
        state.events.append("connect")
        state.calls.append((dsn, kwargs))
        return Connection()

    psycopg = ModuleType("psycopg")
    psycopg.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    return state


def arguments(rows):
    return {key: rows[key] for key in ("contract", "expected_contract_sha256", "approval_id")} | {
        "database_url": "postgresql://synthetic:synthetic@127.0.0.1/fixedsubset_test"}


def admit(rows):
    return client.admit_approved_subset(**arguments(rows), observation_id=uid(40))


def test_admission_owns_one_transaction_calls_sql_once_and_returns_after_commit(rows, transport, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("external or independent queue entrypoint called"))
    for name in ("enqueue_manifest", "enqueue_drive_request", "database_url_from_env", "claim_job"):
        monkeypatch.setattr(video_queue, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = admit(rows)
    assert result == transport.receipt
    assert transport.events == ["connect", "transaction_enter", "cursor_enter", "execute", "fetch",
                                "cursor_exit", "commit", "connection_closed"]
    assert len(transport.calls) == len(transport.executions) == 1
    assert transport.calls[0][1] == dict(autocommit=False, connect_timeout=8,
        options="-c statement_timeout=15000 -c lock_timeout=5000",
        application_name="universal-video-subset-admission")
    assert transport.executions[0] == (
        "SELECT video_queue.admit_approved_subset(%s::uuid,%s::text,%s::text,%s::text,%s::uuid)",
        (rows["approval_id"], rows["contract"]["enqueue_manifest"]["request_key"],
         rows["expected_contract_sha256"], rows["contract"]["queue_manifest_sha256"], uid(40)))
    forbidden.assert_not_called()


def test_commit_acknowledgement_loss_returns_no_receipt_and_never_retries(rows, transport):
    transport.commit_error = OSError("password=DO_NOT_EXPOSE lost acknowledgement")
    with pytest.raises(client.AdmissionOutcomeUnknown) as failure:
        admit(rows)
    assert str(failure.value) == "TRANSACTION_OUTCOME_UNKNOWN_RECOVER_ORIGINAL_REQUEST"
    assert len(transport.calls) == len(transport.executions) == 1
    assert transport.events[-1] == "commit"


@pytest.mark.parametrize("field", ["binding_sha256", "approval_id", "request_key", "contract_sha256",
                                   "queue_manifest_sha256", "plan_sha256", "subset_sha256", "batch_id"])
def test_corrupted_server_binding_rolls_back_before_commit(rows, transport, field):
    transport.receipt["binding"][field] = "corrupted"
    with pytest.raises(client.AdmissionClientError):
        admit(rows)
    assert "rollback" in transport.events and "commit" not in transport.events


@pytest.mark.parametrize("field,value", [
    ("file_id", "otherSource00001"), ("sequence", True), ("source_identity", "google_drive:other"),
    ("source_fingerprint", "f" * 64), ("job_id", "not-a-uuid"),
])
def test_rehashed_job_mapping_must_still_match_approved_selection(rows, transport, field, value):
    bound = transport.receipt["binding"]
    bound["jobs"][0][field] = value
    bound["binding_sha256"] = batch_plan._digest({k: v for k, v in bound.items() if k != "binding_sha256"})
    with pytest.raises(client.AdmissionClientError):
        admit(rows)
    assert transport.events[-2:] == ["rollback", "connection_closed"]


@pytest.mark.parametrize("mutation", ["duplicate_uuid", "missing_job", "extra_job", "reverse_jobs",
                                     "extra_key", "enabled", "wrong_status", "scalar"])
def test_malformed_receipt_does_not_commit(rows, transport, mutation):
    bound = transport.receipt["binding"]
    if mutation == "duplicate_uuid":
        bound["jobs"][1]["job_id"] = bound["jobs"][0]["job_id"]
    elif mutation == "missing_job":
        bound["jobs"].pop()
    elif mutation == "extra_job":
        bound["jobs"].append(deepcopy(bound["jobs"][0]))
    elif mutation == "reverse_jobs":
        bound["jobs"].reverse()
    elif mutation == "extra_key":
        transport.receipt["mutable_progress"] = "RUNNING"
    elif mutation == "enabled":
        transport.receipt["execution_allowed"] = True
    elif mutation == "wrong_status":
        transport.receipt["status"] = "RUNNING"
    else:
        transport.receipt = "not-json-object"
    with pytest.raises(client.AdmissionClientError):
        admit(rows)
    assert "commit" not in transport.events


@pytest.mark.parametrize("field,value", [("database_url", ""), ("database_url", None),
                                        ("approval_id", None), ("expected_contract_sha256", "f" * 64)])
def test_invalid_inputs_fail_before_any_connection(rows, transport, field, value):
    args = arguments(rows) | {field: value}
    with pytest.raises(client.AdmissionClientError):
        client.admit_approved_subset(**args, observation_id=uid(40))
    assert transport.calls == []


def test_invalid_observation_fails_before_connect(rows, transport):
    with pytest.raises(client.AdmissionClientError, match="INVALID_OBSERVATION_ID"):
        client.admit_approved_subset(**arguments(rows), observation_id=None)
    assert transport.calls == []


@pytest.mark.parametrize("status", ["RECOVERED_IMMUTABLE_BINDING", "NOT_FOUND"])
def test_recovery_uses_original_anchors_without_observation_or_reenqueue(rows, transport, monkeypatch, status):
    transport.receipt["status"] = status
    if status == "NOT_FOUND":
        transport.receipt.pop("binding")
    forbidden = Mock(side_effect=AssertionError("source collection or enqueue during recovery"))
    monkeypatch.setattr(video_queue, "discover_drive_manifest", forbidden)
    monkeypatch.setattr(video_queue, "enqueue_manifest", forbidden)
    result = client.recover_subset_admission(**arguments(rows))
    assert result == transport.receipt
    assert transport.executions == [(
        "SELECT video_queue.recover_subset_admission(%s::text,%s::text,%s::text)",
        (rows["contract"]["enqueue_manifest"]["request_key"], rows["expected_contract_sha256"],
         rows["contract"]["queue_manifest_sha256"]))]
    assert len(transport.calls) == 1
    forbidden.assert_not_called()


@pytest.mark.parametrize("message,safe", [("VIDEO_ADMISSION_SOURCE_CHANGED", True),
                                         ("SUBSET_CANONICAL_INTEGER_DOMAIN", True),
                                         ("password=DO_NOT_EXPOSE", False),
                                         ("VIDEO_ADMISSION_SOURCE_CHANGED\nprivate detail", False)])
def test_only_fixed_server_codes_survive_error_sanitization(rows, transport, message, safe):
    error = RuntimeError(message)
    error.sqlstate = "P0001"
    error.diag = SimpleNamespace(message_primary=message)
    transport.error = error
    with pytest.raises(client.AdmissionClientError) as failure:
        admit(rows)
    assert str(failure.value) == (message if safe else "TRANSACTION_OUTCOME_UNKNOWN_RECOVER_ORIGINAL_REQUEST")
    assert transport.events[-2:] == ["rollback", "connection_closed"]
