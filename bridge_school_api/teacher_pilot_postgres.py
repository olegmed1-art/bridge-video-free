"""Postgres store. Construction and import perform no I/O; DDL and grants are separate."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from .teacher_pilot_sessions import PilotError, json_copy, require


class PostgresStore:
    def __init__(self, connection_factory):
        self.connection = connection_factory

    @staticmethod
    def _jsonb(value):
        from psycopg.types.json import Jsonb
        return Jsonb(json_copy(value))

    @staticmethod
    def _row(value):
        require(value is not None, "SESSION_NOT_FOUND")
        return {"session_id": str(value["session_id"]), "identity": value["identity"],
                "turn": value["turn"], "checkpoint": value["checkpoint"], "events": value["events"]}

    @contextmanager
    def _transaction(self, *, mutating=False):
        body_completed = False
        try:
            with self.connection() as conn, conn.transaction(), conn.cursor() as cur:
                yield cur
                body_completed = True
        except PilotError:
            raise
        except Exception:
            # Validation/query errors inside the body precede transaction commit.
            # Preserve those errors; the transaction context rolls back the body.
            # An interrupted COMMIT acknowledgement can hide a committed write.
            # Caller must read the same session/turn, never assume rollback or retry a new session.
            if mutating and body_completed:
                raise PilotError("COMMIT_OUTCOME_UNKNOWN") from None
            raise

    def create(self, row):
        with self._transaction(mutating=True) as cur:
            cur.execute("SET LOCAL statement_timeout = '3000ms'")
            cur.execute(
                """INSERT INTO teacher_pilot.session
                   (session_id, identity, turn, checkpoint)
                   VALUES (%s, %s, %s, %s)""",
                (row["session_id"], self._jsonb(row["identity"]), row["turn"],
                 self._jsonb(row["checkpoint"])))

    def _select(self, cur, session_id, *, lock=False):
        # Events are read only as an append-only audit journal; they are never replayed.
        cur.execute(
            """SELECT session_id, identity, turn, checkpoint
               FROM teacher_pilot.session
               WHERE session_id=%s AND expires_at > clock_timestamp()"""
            + (" FOR UPDATE" if lock else ""), (session_id,))
        value = cur.fetchone()
        require(value is not None, "SESSION_NOT_FOUND")
        # A second READ COMMITTED statement runs after acquiring the session lock.
        # Its journal snapshot cannot predate the update of a writer we waited for.
        cur.execute("SELECT event FROM teacher_pilot.event WHERE session_id=%s ORDER BY turn",
                    (session_id,))
        value = dict(value)
        value["events"] = [item["event"] for item in cur.fetchall()]
        return self._row(value)

    def read(self, session_id):
        with self._transaction() as cur:
            cur.execute("SET LOCAL statement_timeout = '3000ms'")
            return deepcopy(self._select(cur, session_id, lock=True))

    def mutate(self, session_id, transform):
        with self._transaction(mutating=True) as cur:
            cur.execute("SET LOCAL statement_timeout = '3000ms'")
            before = self._select(cur, session_id, lock=True)
            updated, response = transform(deepcopy(before))
            require(updated["identity"] == before["identity"]
                    and updated["session_id"] == session_id
                    and updated["turn"] == before["turn"] + 1
                    and updated["events"][:-1] == before["events"], "STORE_TRANSITION")
            cur.execute(
                """UPDATE teacher_pilot.session SET turn=%s, checkpoint=%s
                   WHERE session_id=%s AND turn=%s AND expires_at > clock_timestamp()""",
                (updated["turn"], self._jsonb(updated["checkpoint"]), session_id, before["turn"]))
            require(cur.rowcount == 1, "STALE_TURN")
            cur.execute(
                """INSERT INTO teacher_pilot.event (session_id, turn, event)
                   VALUES (%s, %s, %s)""",
                (session_id, before["turn"], self._jsonb(updated["events"][-1])))
            return response
