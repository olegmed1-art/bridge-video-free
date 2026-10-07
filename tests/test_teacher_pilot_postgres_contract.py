"""Offline Postgres adapter boundary tests; no driver, DB or SQL execution.

The fake transaction records control flow only. These tests do not establish
PostgreSQL isolation, constraints, rollback durability or network-loss behavior.
"""
from contextlib import contextmanager
from copy import deepcopy
import unittest

from bridge_school_api.teacher_pilot_sessions import PilotError
from bridge_school_api.teacher_pilot_postgres import PostgresStore


SID = "39e70c09-e873-4247-bf1e-742b3a811aef"


class DomainError(ValueError):
    code = "ANSWER_HCP_INTEGER"


class QueryError(RuntimeError):
    pass


class FakeDatabase:
    def __init__(self):
        self.row = {"session_id": SID, "identity": {"version": "public-fixture"},
                    "turn": 0, "checkpoint": {"fixture": True}, "events": []}
        self.statements = []
        self.rowcount = 1
        self.rollback_count = 0
        self.commit_attempts = 0
        self.fail_query = None
        self.lose_commit_ack = False

    @contextmanager
    def connection(self):
        yield self

    @contextmanager
    def transaction(self):
        try:
            yield
        except Exception:
            self.rollback_count += 1
            raise
        else:
            self.commit_attempts += 1
            if self.lose_commit_ack:
                raise ConnectionError("fixture commit acknowledgement unavailable")

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, query, params=None):
        self.statements.append((query, deepcopy(params)))
        if self.fail_query and self.fail_query in query:
            raise QueryError("fixture query failed")

    def fetchone(self):
        return {key: deepcopy(value) for key, value in self.row.items() if key != "events"}

    def fetchall(self):
        return [{"event": deepcopy(event)} for event in self.row["events"]]


class PurePostgresStore(PostgresStore):
    @staticmethod
    def _jsonb(value):
        # No psycopg import or database binding in these offline tests.
        return deepcopy(value)


def transition(row):
    row["turn"] += 1
    row["checkpoint"] = {"fixture": "updated"}
    row["events"].append({"turn": row["turn"] - 1, "request": {"turn": row["turn"] - 1}})
    return row, {"accepted": True}


class PostgresBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        self.store = PurePostgresStore(self.db.connection)

    def test_domain_validation_before_commit_preserves_original_error(self):
        error = DomainError("ANSWER_HCP_INTEGER")
        def reject(row):
            raise error
        with self.assertRaises(DomainError) as caught:
            self.store.mutate(SID, reject)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.db.commit_attempts, 0)
        self.assertEqual(self.db.rollback_count, 1)
        self.assertFalse(any("UPDATE " in query or "INSERT " in query for query, _ in self.db.statements))

    def test_query_failure_before_commit_is_not_unknown_commit(self):
        self.db.fail_query = "SELECT session_id"
        with self.assertRaises(QueryError):
            self.store.mutate(SID, transition)
        self.assertEqual(self.db.commit_attempts, 0)
        self.assertEqual(self.db.rollback_count, 1)

    def test_journal_insert_failure_prevents_commit_and_response(self):
        self.db.fail_query = "INSERT INTO teacher_pilot.event"
        with self.assertRaises(QueryError):
            self.store.mutate(SID, transition)
        self.assertEqual(self.db.commit_attempts, 0)
        self.assertEqual(self.db.rollback_count, 1)
        self.assertTrue(any("UPDATE teacher_pilot.session" in query for query, _ in self.db.statements))

    def test_commit_ack_loss_still_reports_unknown_outcome(self):
        self.db.lose_commit_ack = True
        with self.assertRaises(PilotError) as caught:
            self.store.mutate(SID, transition)
        self.assertEqual(caught.exception.code, "COMMIT_OUTCOME_UNKNOWN")
        self.assertEqual(self.db.commit_attempts, 1)

    def test_read_locks_session_before_reading_journal(self):
        row = self.store.read(SID)
        self.assertEqual(row, self.db.row)
        self.assertIn("FOR UPDATE", self.db.statements[1][0])
        self.assertIn("expires_at > clock_timestamp()", self.db.statements[1][0])
        self.assertIn("FROM teacher_pilot.event", self.db.statements[2][0])
        self.assertEqual(self.db.statements[1][1], (SID,))
        self.assertEqual(self.db.statements[2][1], (SID,))

    def test_identifier_input_remains_a_bound_parameter(self):
        untrusted = "fixture'; DROP TABLE teacher_pilot.session; --"
        self.db.row["session_id"] = untrusted
        self.store.mutate(untrusted, transition)
        for query, params in self.db.statements:
            self.assertNotIn(untrusted, query)
            if params is not None:
                self.assertIn(untrusted, params)
        self.assertEqual(self.db.commit_attempts, 1)

    def test_journal_prefix_change_fails_before_write(self):
        self.db.row["turn"] = 1
        self.db.row["events"] = [{"turn": 0, "request": {"turn": 0}}]
        def rewrite(row):
            row["events"][0]["request"]["turn"] = 99
            return transition(row)
        with self.assertRaises(PilotError) as caught:
            self.store.mutate(SID, rewrite)
        self.assertEqual(caught.exception.code, "STORE_TRANSITION")
        self.assertEqual(self.db.commit_attempts, 0)
        self.assertFalse(any("UPDATE " in query or "INSERT " in query for query, _ in self.db.statements))

    def test_compare_and_swap_miss_has_no_journal_write(self):
        self.db.rowcount = 0
        with self.assertRaises(PilotError) as caught:
            self.store.mutate(SID, transition)
        self.assertEqual(caught.exception.code, "STALE_TURN")
        self.assertEqual(self.db.commit_attempts, 0)
        self.assertFalse(any("INSERT INTO teacher_pilot.event" in query for query, _ in self.db.statements))


if __name__ == "__main__":
    unittest.main()
