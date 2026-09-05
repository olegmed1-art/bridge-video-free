from __future__ import annotations

import uuid

import pytest

from database import video_canon_promotion_consumer as consumer


class _Cursor:
    def __init__(self, scripted):
        self.scripted = scripted
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params):
        self.calls.append((" ".join(query.split()), params))

    def fetchone(self):
        result = self.scripted.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _Connection:
    def __init__(self, scripted):
        self.cursor_value = _Cursor(scripted)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


def test_idle_does_not_attempt_consume(monkeypatch):
    connections = [_Connection([None])]
    monkeypatch.setattr(consumer, "_connect", lambda _dsn: connections.pop(0))
    assert consumer.consume_one("postgresql://user@example/db", lease_seconds=60) == {"status": "IDLE"}
    assert not connections


def test_success_returns_retained_receipt(monkeypatch):
    job, token, receipt = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    claim = (job, uuid.uuid4(), uuid.uuid4(), "a" * 64, "b" * 64, "c" * 64, token, 7, None)
    connections = [_Connection([claim]), _Connection([("renewed",)]), _Connection([(receipt,)])]
    monkeypatch.setattr(consumer, "_connect", lambda _dsn: connections.pop(0))
    result = consumer.consume_one("postgresql://user@example/db")
    assert result == {
        "status": "POST_WRITE_INTEGRITY_PASS",
        "job_id": str(job),
        "delivery_receipt_id": str(receipt),
        "fencing_token": 7,
    }


def test_failure_is_bounded_and_recorded_without_raw_error(monkeypatch):
    job, token = uuid.uuid4(), uuid.uuid4()
    claim = (job, uuid.uuid4(), uuid.uuid4(), "a" * 64, "b" * 64, "c" * 64, token, 2, None)
    connections = [
        _Connection([claim]),
        _Connection([("renewed",)]),
        _Connection([RuntimeError("database connection reset with secret detail")]),
        _Connection([RuntimeError("reconciliation failed")]),
        _Connection([("QUEUED",)]),
    ]
    monkeypatch.setattr(consumer, "_connect", lambda _dsn: connections.pop(0))
    result = consumer.consume_one("postgresql://user@example/db")
    assert result == {
        "status": "QUEUED",
        "job_id": str(job),
        "error_code": "RETRYABLE_DATABASE_ERROR",
    }
    assert "secret detail" not in str(result)


def test_expired_effective_period_is_a_terminal_bounded_error():
    assert consumer._safe_error_code(
        RuntimeError("VIDEO_CANON_EFFECTIVE_PERIOD_EXPIRED")
    ) == "EFFECTIVE_PERIOD_EXPIRED"


def test_conflicting_retained_bundle_is_terminal():
    assert consumer._safe_error_code(
        RuntimeError("VIDEO_CANON_IDEMPOTENCY_MISMATCH")
    ) == "STATE_STALE"


def test_ambiguous_consume_commit_is_reconciled_from_retained_receipt(monkeypatch):
    job, token, receipt = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    claim = (job, uuid.uuid4(), uuid.uuid4(), "a" * 64, "b" * 64, "c" * 64, token, 3, None)
    connections = [
        _Connection([claim]),
        _Connection([("renewed",)]),
        _Connection([RuntimeError("connection lost after possible commit")]),
        _Connection([(receipt,)]),
    ]
    monkeypatch.setattr(consumer, "_connect", lambda _dsn: connections.pop(0))
    assert consumer.consume_one("postgresql://user@example/db") == {
        "status": "POST_WRITE_INTEGRITY_PASS",
        "job_id": str(job),
        "delivery_receipt_id": str(receipt),
        "fencing_token": 3,
    }


def test_lost_heartbeat_fails_closed_before_consume(monkeypatch):
    job, token = uuid.uuid4(), uuid.uuid4()
    claim = (job, uuid.uuid4(), uuid.uuid4(), "a" * 64, "b" * 64, "c" * 64, token, 4, None)
    connections = [_Connection([claim]), _Connection([RuntimeError("lease expired")])]
    monkeypatch.setattr(consumer, "_connect", lambda _dsn: connections.pop(0))
    assert consumer.consume_one("postgresql://user@example/db") == {
        "status": "LEASE_LOST",
        "job_id": str(job),
    }
    assert not connections


@pytest.mark.parametrize("seconds", [0, 29, 901])
def test_invalid_lease_duration_is_rejected_before_connect(monkeypatch, seconds):
    monkeypatch.setattr(consumer, "_connect", lambda _dsn: pytest.fail("must not connect"))
    with pytest.raises(ValueError):
        consumer.consume_one("postgresql://user@example/db", lease_seconds=seconds)
