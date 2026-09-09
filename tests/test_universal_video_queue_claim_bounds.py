from __future__ import annotations

import sys
import types

from universal_video import video_queue


class _Cursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, object | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement: str, params=None) -> None:
        self.executions.append((statement, params))

    def fetchone(self):
        return {
            "job_id": "job-id",
            "batch_id": "batch-id",
            "lease_token": "lease-token",
            "status": "RUNNING",
        }


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor


def test_resident_claim_connection_and_statement_are_bounded(monkeypatch):
    cursor = _Cursor()
    calls: list[tuple[str, dict[str, object]]] = []

    def connect(database_url: str, **kwargs):
        calls.append((database_url, kwargs))
        return _Connection(cursor)

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)

    result = video_queue.claim_job(
        "postgresql://queue.invalid/neondb",
        "worker-1",
        900,
        processing_profile="transcript_only",
        algorithm_revision="r1",
    )

    assert calls == [
        (
            "postgresql://queue.invalid/neondb",
            {
                "row_factory": rows.dict_row,
                "connect_timeout": 8,
                "options": "-c statement_timeout=10000 -c lock_timeout=2000",
                "application_name": "universal-video-worker-claim",
            },
        )
    ]
    assert cursor.executions == [
        (
            "SELECT * FROM video_queue.claim_job(%s,%s,%s,%s)",
            ("worker-1", 900, "transcript_only", "r1"),
        ),
    ]
    assert result == {
        "job_id": "job-id",
        "batch_id": "batch-id",
        "lease_token": "lease-token",
        "status": "RUNNING",
    }
