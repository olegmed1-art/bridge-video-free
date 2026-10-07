"""Real PostgresStore tests against one verified disposable Actions service only.

Synthetic runtime; no private canon, production DSN, DB helper, solver or job.
COMMIT acknowledgement injection is labelled separately from actual wire loss.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Barrier, Thread
import unittest

import psycopg
from psycopg.rows import dict_row
from bridge_school_api.teacher_pilot_postgres import PostgresStore
from bridge_school_api.teacher_pilot_sessions import PilotError, SessionService
from test_teacher_pilot_public import PublicRuntime

ROOT = Path(__file__).resolve().parents[1]
DB = "teacher_pilot_disposable"
OWNER = "teacher_fixture_owner"
APP = "teacher_fixture_app"
HOST = "127.0.0.1"
PORT = 55432


def verify_service():
    if os.environ.get("TEACHER_PILOT_DISPOSABLE_PG") != "1":
        raise RuntimeError("DISPOSABLE_PG_EXPLICIT_FLAG_REQUIRED")
    for key in ("BRIDGE_APP_DATABASE_URL", "DATABASE_URL", "ADMIN_DATABASE_URL",
                "NEON_DATABASE_URL", "PGSERVICE", "PGSERVICEFILE"):
        if os.environ.get(key):
            raise RuntimeError("EXTERNAL_DATABASE_CONFIGURATION_REJECTED")
    if any(key.startswith("PG") and value for key, value in os.environ.items()):
        raise RuntimeError("LIBPQ_ENVIRONMENT_REJECTED")
    cid = os.environ.get("TEACHER_PILOT_PG_CONTAINER_ID", "")
    if not re.fullmatch("[0-9a-f]{12,64}", cid):
        raise RuntimeError("DISPOSABLE_ACTIONS_SERVICE_ID_REQUIRED")
    process = subprocess.run(["docker", "--host", "unix:///var/run/docker.sock",
                              "inspect", cid], check=True, capture_output=True, text=True,
                             timeout=5)
    metadata = json.loads(process.stdout)[0]
    env = metadata["Config"]["Env"]
    ports = metadata["HostConfig"]["PortBindings"].get("5432/tcp", [])
    if (metadata["Config"]["Image"] != "postgres:18"
            or "POSTGRES_DB=" + DB not in env
            or "POSTGRES_HOST_AUTH_METHOD=trust" not in env
            or metadata["HostConfig"].get("Privileged") is not False
            or ports != [{"HostIp": HOST, "HostPort": str(PORT)}]):
        raise RuntimeError("DISPOSABLE_SERVICE_METADATA_REJECTED")


@contextmanager
def connection(*, role=None):
    # No DSN/environment fallback. This driver cannot select a production host.
    conn = psycopg.connect(host=HOST, hostaddr=HOST, port=PORT, dbname=DB,
                           user="postgres", password="", passfile="/dev/null",
                           sslmode="disable", gssencmode="disable", connect_timeout=2,
                           options="-c statement_timeout=3000", autocommit=True,
                           row_factory=dict_row)
    try:
        if role is not None:
            if role not in {OWNER, APP}:
                raise RuntimeError("FIXTURE_ROLE_REJECTED")
            conn.execute("SET ROLE " + role)
        conn.autocommit = False
        yield conn
    finally:
        conn.close()


class FixtureRuntime(PublicRuntime):
    def checkpoint(self):
        identity = SessionService.identity(self.view())
        return {"identity": identity, "state": {"_turn": self.turn}}


class FixtureFactory:
    def create(self):
        return FixtureRuntime()

    def restore(self, checkpoint):
        return FixtureRuntime(checkpoint["state"]["_turn"])


def service(connection_factory=None):
    return SessionService(FixtureFactory(),
                          PostgresStore(connection_factory or (lambda: connection(role=APP))))


class DisposablePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        verify_service()
        with connection() as conn:
            observed = conn.execute(
                "SELECT current_database() AS db, current_user AS principal, "
                "current_setting('server_version_num')::int AS version").fetchone()
            if observed["db"] != DB or observed["principal"] != "postgres" or observed["version"] < 180000:
                raise RuntimeError("DISPOSABLE_DATABASE_IDENTITY_REJECTED")
            if conn.execute("SELECT to_regnamespace('teacher_pilot') AS existing").fetchone()["existing"]:
                raise RuntimeError("DISPOSABLE_DATABASE_NOT_FRESH")
            conn.execute("CREATE ROLE teacher_fixture_owner NOLOGIN NOSUPERUSER "
                         "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
            conn.execute("CREATE ROLE teacher_fixture_app NOLOGIN NOSUPERUSER "
                         "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
            conn.execute("GRANT CREATE ON DATABASE teacher_pilot_disposable TO teacher_fixture_owner")
            conn.commit()
        with connection(role=OWNER) as conn:
            # Schema proposal is from the independently checked overlay; no migration runner.
            conn.autocommit = True
            conn.execute((ROOT / "proposal/teacher_pilot_schema.sql").read_text(encoding="utf-8"))
        with connection() as conn:
            conn.execute("GRANT USAGE ON SCHEMA teacher_pilot TO teacher_fixture_app")
            conn.execute("GRANT SELECT, INSERT ON teacher_pilot.session TO teacher_fixture_app")
            conn.execute("GRANT UPDATE (turn, checkpoint) ON teacher_pilot.session TO teacher_fixture_app")
            conn.execute("GRANT SELECT, INSERT ON teacher_pilot.event TO teacher_fixture_app")
            conn.commit()

    def start(self):
        started = service().start()
        return started["session_id"], started

    def inspect(self, sid):
        with connection() as conn:
            row = conn.execute("SELECT turn, checkpoint FROM teacher_pilot.session WHERE session_id=%s",
                               (sid,)).fetchone()
            count = conn.execute("SELECT count(*) AS count FROM teacher_pilot.event WHERE session_id=%s",
                                 (sid,)).fetchone()["count"]
            return row, count

    def test_cold_connection_restores_checkpoint_and_journal(self):
        sid, started = self.start()
        result = service().event(sid, {"turn": 0})
        self.assertEqual(result["session"]["turn"], 1)
        self.assertEqual(service().read(sid), result)
        row, count = self.inspect(sid)
        self.assertEqual(row["turn"], 1)
        self.assertEqual(count, 1)

    def test_two_connections_accept_exactly_one_same_turn(self):
        sid, _ = self.start()
        barrier = Barrier(3)
        results = []
        def submit():
            barrier.wait()
            try:
                service().event(sid, {"turn": 0})
                results.append("ACCEPTED")
            except PilotError as exc:
                results.append(exc.code)
        workers = [Thread(target=submit, daemon=True) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertCountEqual(results, ["ACCEPTED", "STALE_TURN"])
        row, count = self.inspect(sid)
        self.assertEqual((row["turn"], count), (1, 1))

    def test_journal_insert_failure_rolls_back_checkpoint_update(self):
        sid, _ = self.start()
        with connection(role=OWNER) as conn:
            conn.execute("""CREATE FUNCTION teacher_pilot.fixture_insert_failure()
                            RETURNS trigger LANGUAGE plpgsql AS $$
                            BEGIN RAISE EXCEPTION 'FIXTURE_INSERT_FAILURE'; END; $$""")
            conn.execute("""CREATE TRIGGER fixture_insert_failure BEFORE INSERT ON teacher_pilot.event
                            FOR EACH ROW EXECUTE FUNCTION teacher_pilot.fixture_insert_failure()""")
            conn.commit()
        try:
            with self.assertRaises(psycopg.errors.RaiseException):
                service().event(sid, {"turn": 0})
            row, count = self.inspect(sid)
            self.assertEqual((row["turn"], count), (0, 0))
            self.assertEqual(row["checkpoint"]["runtime"]["state"]["_turn"], 0)
        finally:
            with connection(role=OWNER) as conn:
                conn.execute("DROP TRIGGER fixture_insert_failure ON teacher_pilot.event")
                conn.execute("DROP FUNCTION teacher_pilot.fixture_insert_failure()")
                conn.commit()

    def test_checkpoint_session_identity_and_turn_constraints(self):
        sid, _ = self.start()
        commands = [
            ("UPDATE teacher_pilot.session SET turn=1 WHERE session_id=%s", (sid,)),
            ("UPDATE teacher_pilot.session SET checkpoint=jsonb_set(checkpoint, '{session_id}', "
             "'\"00000000-0000-4000-8000-000000000000\"'::jsonb) WHERE session_id=%s", (sid,)),
            ("UPDATE teacher_pilot.session SET identity='{}'::jsonb WHERE session_id=%s", (sid,)),
        ]
        for query, params in commands:
            with self.assertRaises(psycopg.errors.CheckViolation):
                with connection() as conn:
                    conn.execute(query, params)
                    conn.commit()
        row, count = self.inspect(sid)
        self.assertEqual((row["turn"], count), (0, 0))

    def test_app_has_no_journal_rewrite_or_schema_ownership(self):
        sid, _ = self.start()
        service().event(sid, {"turn": 0})
        commands = [
            "UPDATE teacher_pilot.event SET event='{}'::jsonb",
            "DELETE FROM teacher_pilot.event",
            "CREATE TABLE teacher_pilot.fixture_forbidden(value integer)",
            "UPDATE teacher_pilot.session SET expires_at=clock_timestamp()+interval '1 hour'",
        ]
        for query in commands:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with connection(role=APP) as conn:
                    conn.execute(query)
                    conn.commit()
        row, count = self.inspect(sid)
        self.assertEqual((row["turn"], count), (1, 1))

    def test_trigger_blocks_owner_event_update_and_delete(self):
        sid, _ = self.start()
        service().event(sid, {"turn": 0})
        for query in ("UPDATE teacher_pilot.event SET event=event WHERE session_id=%s",
                      "DELETE FROM teacher_pilot.event WHERE session_id=%s"):
            with self.assertRaises(psycopg.errors.RaiseException):
                with connection(role=OWNER) as conn:
                    conn.execute(query, (sid,))
                    conn.commit()
        self.assertEqual(self.inspect(sid)[1], 1)

    def test_expired_session_refuses_read_and_event(self):
        sid, _ = self.start()
        with connection() as conn:
            conn.execute("UPDATE teacher_pilot.session SET created_at=clock_timestamp()-interval '1 hour', "
                         "expires_at=clock_timestamp()-interval '1 minute' WHERE session_id=%s", (sid,))
            conn.commit()
        for operation in (lambda: service().read(sid), lambda: service().event(sid, {"turn": 0})):
            with self.assertRaisesRegex(PilotError, "SESSION_NOT_FOUND"):
                operation()
        self.assertEqual(self.inspect(sid)[0]["turn"], 0)

    def test_expiry_between_lock_and_update_causes_atomic_refusal(self):
        sid, _ = self.start()
        active = {}
        @contextmanager
        def locked_fixture_connection():
            # Owner is used only to place this fixture deadline after the row lock.
            # Other mutation/grant tests run under the restricted APP role.
            with connection(role=OWNER) as conn:
                active["connection"] = conn
                try:
                    yield conn
                finally:
                    active.pop("connection", None)
        store = PostgresStore(locked_fixture_connection)
        def delayed(row):
            # transform runs after SELECT FOR UPDATE. Set and wait for the actual
            # DB deadline inside that same transaction; no pre-lock timing race.
            conn = active["connection"]
            conn.execute("UPDATE teacher_pilot.session SET expires_at=clock_timestamp()"
                         "+interval '200 milliseconds' WHERE session_id=%s", (sid,))
            conn.execute("SELECT pg_sleep(0.25)")
            runtime = FixtureFactory().restore(row["checkpoint"]["runtime"])
            before = runtime.view()
            runtime.handle({"turn": 0})
            from bridge_school_api.teacher_pilot_sessions import digest
            after = runtime.view()
            row["turn"] = 1
            row["checkpoint"] = SessionService._checkpoint(sid, runtime, after)
            row["events"].append({"turn": 0, "request": {"turn": 0},
                                  "before_sha256": digest(before), "after_sha256": digest(after)})
            return row, {"accepted": True}
        with self.assertRaisesRegex(PilotError, "STALE_TURN"):
            store.mutate(sid, delayed)
        row, count = self.inspect(sid)
        self.assertEqual((row["turn"], count), (0, 0))
        self.assertEqual(service().read(sid)["session"]["turn"], 0)

    def test_ack_injection_after_real_commit_recovers_without_duplicate(self):
        sid, _ = self.start()
        @contextmanager
        def lost_ack():
            with connection(role=APP) as conn:
                class Proxy:
                    def cursor(self):
                        return conn.cursor()
                    @contextmanager
                    def transaction(self):
                        with conn.transaction():
                            yield
                        # The actual disposable DB commit completed. Hide only acknowledgement.
                        raise ConnectionError("FIXTURE_ACK_INJECTION_AFTER_REAL_COMMIT")
                yield Proxy()
        with self.assertRaisesRegex(PilotError, "COMMIT_OUTCOME_UNKNOWN"):
            service(lost_ack).event(sid, {"turn": 0})
        self.assertEqual(service().read(sid)["session"]["turn"], 1)
        with self.assertRaisesRegex(PilotError, "STALE_TURN"):
            service().event(sid, {"turn": 0})
        row, count = self.inspect(sid)
        self.assertEqual((row["turn"], count), (1, 1))


if __name__ == "__main__":
    unittest.main()
