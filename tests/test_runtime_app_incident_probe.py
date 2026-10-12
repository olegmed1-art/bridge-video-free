import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

from bridge_school_api import incident_db_probe as probe


SECRET = "synthetic-password-must-not-appear"
DSN = (f"postgresql://{probe.EXPECTED_PRINCIPAL}:{SECRET}@{probe.PRODUCTION_HOST}/"
       f"{probe.EXPECTED_DATABASE}?sslmode=require&channel_binding=require")


class IncidentProbeTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(probe.psycopg, "connect", side_effect=AssertionError("real connections forbidden"))
        self.connection_guard = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.connection_guard.assert_not_called)

    def run_probe(self, dsn=DSN, connect=False, extra=None):
        env = {"VERCEL_ENV": "production", "BRIDGE_APP_DATABASE_URL": dsn, **(extra or {})}
        with patch.dict(os.environ, env, clear=True):
            result = probe.probe(connect)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn("postgresql://", json.dumps(result))
        return result

    def test_missing_production_environment_never_connects(self):
        with patch.dict(os.environ, {"BRIDGE_APP_DATABASE_URL": DSN}, clear=True), \
             patch.object(probe.psycopg, "connect") as connect:
            self.assertEqual(probe.probe(True)["status"], "production_environment_required")
            connect.assert_not_called()

    def test_cli_propagates_preflight_failure(self):
        statuses = {
            "production_environment_required": 1,
            "source_endpoint_requires_review": 1,
            "configuration_failed": 1,
            "authentication_failed": 1,
            "connection_timeout": 1,
            "session_contract_failed": 1,
            "pass": 0,
        }
        for status, expected_code in statuses.items():
            with self.subTest(status=status), \
                 patch("sys.argv", ["incident_db_probe", "--connect"]), \
                 patch.object(probe, "probe", return_value={"status": status}) as check, \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(probe.main(), expected_code)
                check.assert_called_once_with(True)
                self.assertEqual(json.loads(output.getvalue()), {"status": status})

    def test_default_does_not_connect(self):
        with patch.object(probe.psycopg, "connect") as connect:
            self.assertEqual(self.run_probe()["status"], "configuration_pass_connection_not_tested")
            connect.assert_not_called()

    def test_source_endpoint_observations(self):
        cases = (
            (probe.PRODUCTION_HOST, True, False, "configuration_pass_connection_not_tested"),
            (probe.PRODUCTION_DIRECT_HOST, True, True, "configuration_pass_connection_not_tested"),
            ("unrelated-synthetic-endpoint.neon.tech", False, True, "source_endpoint_requires_review"),
        )
        for host, expected, rewritten, status in cases:
            with self.subTest(host=host), patch.object(probe.psycopg, "connect") as connect:
                result = self.run_probe(DSN.replace(probe.PRODUCTION_HOST, host))
                self.assertEqual(result["status"], status)
                self.assertEqual(result["source"], {
                    "raw_host_is_expected": expected,
                    "raw_host_is_pooler": host == probe.PRODUCTION_HOST,
                    "raw_host_is_direct": host == probe.PRODUCTION_DIRECT_HOST,
                    "endpoint_was_rewritten": rewritten,
                })
                self.assertNotIn(host, json.dumps(result))
                connect.assert_not_called()

    def test_unrelated_source_cannot_connect_after_normalization(self):
        with patch.object(probe.psycopg, "connect") as connect:
            result = self.run_probe(DSN.replace(probe.PRODUCTION_HOST, "other-synthetic.neon.tech"), True)
            self.assertEqual(result["status"], "source_endpoint_requires_review")
            self.assertTrue(result["checks"]["endpoint_matches"])
            connect.assert_not_called()

    def test_quoted_uri_retains_source_observations(self):
        result = self.run_probe("  '" + DSN + "'  ")
        self.assertEqual(result["status"], "configuration_pass_connection_not_tested")
        self.assertFalse(result["source"]["endpoint_was_rewritten"])

    def test_actual_health_failure_logs_config_without_extra_connection(self):
        from fastapi import HTTPException
        from bridge_school_api import main as api

        env = {"VERCEL_ENV": "production", "BRIDGE_APP_DATABASE_URL": DSN}
        with patch.dict(os.environ, env, clear=True), \
             patch.object(api, "connect", side_effect=RuntimeError("password authentication failed " + DSN)), \
             patch.object(probe.psycopg, "connect") as connect, \
             patch.object(api.logger, "error") as log:
            with self.assertRaises(HTTPException) as raised:
                api.healthz()
            self.assertEqual(raised.exception.status_code, 503)
            self.assertEqual(raised.exception.detail, "service unavailable")
            connect.assert_not_called()
            rendered = log.call_args.args[0] % log.call_args.args[1:]
            self.assertIn("configuration_pass_connection_not_tested", rendered)
            self.assertNotIn(SECRET, rendered)
            self.assertNotIn("postgresql://", rendered)
            self.assertNotIn(probe.PRODUCTION_HOST, rendered)

    @staticmethod
    def fake_connection():
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (probe.EXPECTED_PRINCIPAL, probe.EXPECTED_DATABASE, "on")
        return conn, cursor

    def test_each_failure_phase_preserves_connection_established_fact(self):
        for phase in ("connect", "query", "rollback", "close"):
            with self.subTest(phase=phase):
                conn, cursor = self.fake_connection()
                error = TimeoutError("synthetic timeout " + DSN)
                if phase != "connect":
                    target = cursor.execute if phase == "query" else getattr(conn, phase)
                    target.side_effect = error
                with patch.object(probe.psycopg, "connect", return_value=conn,
                                  side_effect=error if phase == "connect" else None) as connect:
                    result = self.run_probe(connect=True)
                self.assertEqual(result["phase"], phase)
                self.assertEqual(result["connected"], phase != "connect")
                self.assertEqual(result["status"], ("connection" if phase == "connect" else phase) + "_timeout")
                self.assertEqual(result["exception_class"], "TimeoutError")
                self.assertIsNone(result["sqlstate"])
                self.assertEqual(result["cleanup_errors"], [])
                self.assertIs(type(result["elapsed_ms"]), int)
                self.assertIs(type(result["phase_elapsed_ms"]), int)
                connect.assert_called_once()
                if phase != "connect":
                    conn.rollback.assert_called_once()
                    conn.close.assert_called_once()
                conn.commit.assert_not_called()
                conn.__enter__.assert_not_called()

    def test_query_failure_is_not_replaced_by_cleanup_errors(self):
        conn, cursor = self.fake_connection()
        cursor.execute.side_effect = RuntimeError("query timeout " + DSN)
        conn.rollback.side_effect = RuntimeError("rollback timeout " + DSN)
        conn.close.side_effect = RuntimeError("close timeout " + DSN)
        with patch.object(probe.psycopg, "connect", return_value=conn):
            result = self.run_probe(connect=True)
        self.assertEqual((result["phase"], result["status"]), ("query", "query_timeout"))
        self.assertTrue(result["connected"])
        self.assertEqual([e["phase"] for e in result["cleanup_errors"]], ["rollback", "close"])
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()
        conn.commit.assert_not_called()

    def test_rollback_failure_is_not_replaced_by_close_error(self):
        conn, _ = self.fake_connection()
        conn.rollback.side_effect = RuntimeError("rollback failed " + DSN)
        conn.close.side_effect = RuntimeError("close failed " + DSN)
        with patch.object(probe.psycopg, "connect", return_value=conn):
            result = self.run_probe(connect=True)
        self.assertEqual(result["phase"], "rollback")
        self.assertEqual(result["status"], "rollback_failed")
        self.assertEqual([e["phase"] for e in result["cleanup_errors"]], ["close"])

    def test_wrong_session_is_not_replaced_by_cleanup_errors(self):
        for failed_cleanup in ((), ("rollback",), ("close",), ("rollback", "close")):
            with self.subTest(failed_cleanup=failed_cleanup):
                conn, cursor = self.fake_connection()
                cursor.fetchone.return_value = ("wrong-principal", "wrong-database", "off")
                for phase in failed_cleanup:
                    getattr(conn, phase).side_effect = RuntimeError("timeout " + DSN)
                with patch.object(probe.psycopg, "connect", return_value=conn):
                    result = self.run_probe(connect=True)
                self.assertEqual(result["status"], "session_contract_failed")
                self.assertEqual(result["phase"], "query")
                self.assertTrue(result["connected"])
                self.assertIsNone(result["exception_class"])
                self.assertIsNone(result["sqlstate"])
                self.assertEqual([e["phase"] for e in result["cleanup_errors"]], list(failed_cleanup))
                conn.rollback.assert_called_once()
                conn.close.assert_called_once()
                conn.commit.assert_not_called()

    def test_safe_exception_family_and_explicit_sqlstate_allowlist(self):
        for code in ("ABCDE", DSN, "28P01\n" + SECRET, b"28P01", None, 28000):
            with self.subTest(code=repr(code)):
                error_type = type(SECRET, (RuntimeError,), {"sqlstate": code})
                with patch.object(probe.psycopg, "connect", side_effect=error_type(DSN)):
                    result = self.run_probe(connect=True)
                self.assertEqual(result["exception_class"], "RuntimeError")
                self.assertIsNone(result["sqlstate"])
        with patch.object(probe.psycopg, "connect", side_effect=probe.psycopg.errors.InvalidPassword(DSN)):
            result = self.run_probe(connect=True)
        self.assertEqual(result["sqlstate"], "28P01")
        self.assertEqual(result["exception_class"], "OperationalError")
        self.assertEqual(result["status"], "authentication_failed")

    def test_unrenderable_error_and_throwing_sqlstate_do_not_escape(self):
        class UnexpectedError(Exception):
            @property
            def sqlstate(self):
                raise RuntimeError(DSN)

            def __str__(self):
                raise RuntimeError(DSN)

        with patch.object(probe.psycopg, "connect", side_effect=UnexpectedError()):
            result = self.run_probe(connect=True)
        self.assertEqual(result["status"], "connection_failed")
        self.assertEqual(result["exception_class"], "Exception")
        self.assertIsNone(result["sqlstate"])
        self.assertFalse(result["sqlstate_present"])
        self.assertTrue(result["sqlstate_filtered"])
        self.assertEqual(result["diagnostic_markers"], ["unknown"])

    def test_diagnostic_markers_are_fixed_narrow_hints(self):
        fixtures = (
            ('could not translate host name "test.invalid" to address', ["dns"]),
            ("connection to server at 203.0.113.7 failed: Connection refused", ["tcp"]),
            ("SSL error: certificate verify failed", ["tls"]),
            ("channel binding required, but server did not offer it", ["channel_binding"]),
            ('password authentication failed for user "synthetic-user"', ["auth"]),
            ("FATAL: the database system is starting up", ["server"]),
            ("connection to server at 2001:db8::7 failed: timeout expired", ["timeout"]),
            ("dns tcp tls channelbinding auth server timeout", ["unknown"]),
            ("unrecognized or localized message", ["unknown"]),
        )
        for message, expected in fixtures:
            with self.subTest(expected=expected):
                result = probe._failure(RuntimeError(message + " " + DSN), "connect", probe.time.monotonic_ns())
                self.assertEqual(result["diagnostic_markers"], expected)
                self.assertFalse(result["sqlstate_present"])
                self.assertFalse(result["sqlstate_filtered"])
                for forbidden in (SECRET, DSN, "test.invalid", "203.0.113.7", "2001:db8::7", "synthetic-user"):
                    self.assertNotIn(forbidden, json.dumps(result))

    def test_multiple_attempt_messages_keep_all_distinct_markers(self):
        attempts = [
            'could not translate host name "test.invalid" to address',
            'connection to server at 203.0.113.7 failed: Connection refused',
            'SSL error: certificate verify failed',
            'channel binding is required, but was not used',
            'password authentication failed',
            'FATAL: the database system is starting up',
            'connection to server at 2001:db8::7 failed: Operation timed out',
            'Connection refused',
            'unrecognized additional attempt',
        ]
        for lines in (attempts, list(reversed(attempts))):
            result = probe._failure(RuntimeError("\n".join(lines)), "connect", probe.time.monotonic_ns())
            self.assertEqual(result["diagnostic_markers"],
                             ["dns", "tcp", "tls", "channel_binding", "auth", "server", "timeout"])
            self.assertNotIn("203.0.113.7", json.dumps(result))
            self.assertNotIn("2001:db8::7", json.dumps(result))

    def test_markers_from_driver_type_and_sqlstate_do_not_overclaim(self):
        fixtures = (
            (TimeoutError(SECRET), ["timeout"]),
            (probe.psycopg.errors.ConnectionTimeout(SECRET), ["timeout"]),
            (probe.psycopg.errors.InvalidPassword(SECRET), ["auth"]),
            (probe.psycopg.errors.CannotConnectNow(SECRET), ["server"]),
            (probe.psycopg.errors.ConnectionFailure(SECRET), ["unknown"]),
            (probe.psycopg.errors.QueryCanceled(SECRET), ["unknown"]),
        )
        for error, expected in fixtures:
            with self.subTest(expected=expected):
                result = probe._failure(error, "connect", probe.time.monotonic_ns())
                self.assertEqual(result["diagnostic_markers"], expected)
                self.assertNotIn(SECRET, json.dumps(result))

    def test_sqlstate_presence_and_filtering_are_distinct(self):
        class StringSubclass(str):
            pass

        fixtures = (
            (None, None, False, False),
            ("28P01", "28P01", True, False),
            ("ZZ999", None, True, True),
            ("", None, True, True),
            (b"28P01", None, True, True),
            (StringSubclass("28P01"), None, True, True),
            (False, None, True, True),
            ([SECRET], None, True, True),
            ("28P01\n" + DSN, None, True, True),
        )
        for code, safe, present, filtered in fixtures:
            with self.subTest(present=present, filtered=filtered):
                error = RuntimeError(SECRET)
                error.sqlstate = code
                result = probe._failure(error, "connect", probe.time.monotonic_ns())
                self.assertEqual(result["sqlstate"], safe)
                self.assertIs(result["sqlstate_present"], present)
                self.assertIs(result["sqlstate_filtered"], filtered)
                self.assertNotIn(SECRET, json.dumps(result))

    def test_markers_preserve_primary_and_cleanup_error_evidence(self):
        conn, cursor = self.fake_connection()
        cursor.execute.side_effect = RuntimeError("statement timeout " + DSN)
        conn.rollback.side_effect = RuntimeError("SSL error: " + DSN)
        conn.close.side_effect = RuntimeError("Connection refused " + DSN)
        with patch.object(probe.psycopg, "connect", return_value=conn):
            result = self.run_probe(connect=True)
        self.assertEqual((result["phase"], result["diagnostic_markers"]), ("query", ["timeout"]))
        self.assertEqual([(e["phase"], e["diagnostic_markers"]) for e in result["cleanup_errors"]],
                         [("rollback", ["tls"]), ("close", ["tcp"])])

    def test_cli_marker_fields_never_emit_raw_diagnostics(self):
        for phase in ("connect", "query", "rollback", "close"):
            conn, cursor = self.fake_connection()
            error = RuntimeError('could not translate host name "test.invalid": SSL error: ' + DSN + " 203.0.113.7")
            error.sqlstate = "28P01\n" + SECRET
            if phase != "connect":
                (cursor.execute if phase == "query" else getattr(conn, phase)).side_effect = error
            with self.subTest(phase=phase), \
                 patch.dict(os.environ, {"VERCEL_ENV": "production", "BRIDGE_APP_DATABASE_URL": DSN}, clear=True), \
                 patch("sys.argv", ["incident_db_probe", "--connect"]), \
                 patch.object(probe.psycopg, "connect", return_value=conn,
                              side_effect=error if phase == "connect" else None), \
                 redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                self.assertEqual(probe.main(), 1)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["probe"], "incident-1911-v4")
            self.assertEqual(payload["phase"], phase)
            self.assertEqual(payload["diagnostic_markers"], ["dns", "tls"])
            self.assertTrue(payload["sqlstate_present"])
            self.assertTrue(payload["sqlstate_filtered"])
            self.assertIsNone(payload["sqlstate"])
            for forbidden in (SECRET, DSN, "test.invalid", "203.0.113.7", probe.PRODUCTION_HOST, "postgresql://"):
                self.assertNotIn(forbidden, out.getvalue() + err.getvalue())
            self.assertEqual(err.getvalue(), "")

    def test_elapsed_distinguishes_failure_phase_from_cleanup(self):
        conn, cursor = self.fake_connection()
        cursor.execute.side_effect = RuntimeError("timeout")
        ticks = [0, 2, 5, 6, 7, 11]  # start, query, failure, rollback, close, end
        with patch.object(probe.psycopg, "connect", return_value=conn), \
             patch.object(probe.time, "monotonic_ns", side_effect=[n * 1_000_000 for n in ticks]):
            result = self.run_probe(connect=True)
        self.assertEqual(result["phase_elapsed_ms"], 3)
        self.assertEqual(result["elapsed_ms"], 11)

    def test_elapsed_is_nonnegative_and_bounded(self):
        for clock, expected in ((-1, 0), (10**30, 600_000)):
            with self.subTest(clock=clock), patch.object(probe.time, "monotonic_ns", return_value=clock):
                self.assertEqual(probe._elapsed_ms(0), expected)

    def test_cli_phase_errors_fail_without_stdout_or_stderr_leaks(self):
        for phase in ("connect", "query", "rollback", "close"):
            conn, cursor = self.fake_connection()
            error = RuntimeError("timeout " + DSN)
            if phase != "connect":
                (cursor.execute if phase == "query" else getattr(conn, phase)).side_effect = error
            with self.subTest(phase=phase), \
                 patch.dict(os.environ, {"VERCEL_ENV": "production", "BRIDGE_APP_DATABASE_URL": DSN}, clear=True), \
                 patch("sys.argv", ["incident_db_probe", "--connect"]), \
                 patch.object(probe.psycopg, "connect", return_value=conn,
                              side_effect=error if phase == "connect" else None), \
                 redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                self.assertEqual(probe.main(), 1)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["phase"], phase)
            for forbidden in (SECRET, DSN, probe.PRODUCTION_HOST, "postgresql://"):
                self.assertNotIn(forbidden, out.getvalue() + err.getvalue())
            self.assertEqual(err.getvalue(), "")

    def test_query_overrides_cannot_redirect_probe(self):
        for parameter in ("host=elsewhere.neon.tech", "hostaddr=127.0.0.1", "user=neondb_owner", "service=other"):
            with self.subTest(parameter=parameter), patch.object(probe.psycopg, "connect") as connect:
                self.assertEqual(self.run_probe(DSN + "&" + parameter, True)["status"], "configuration_failed")
                connect.assert_not_called()

    def test_effective_parameters_remain_a_second_line_of_defense(self):
        for parameter in ("host=elsewhere.neon.tech", "hostaddr=127.0.0.1", "user=neondb_owner", "service=other"):
            with self.subTest(parameter=parameter), \
                 patch.object(probe, "database_dsn", return_value=DSN + "&" + parameter), \
                 patch.object(probe.psycopg, "connect") as connect:
                self.assertEqual(self.run_probe(connect=True)["status"], "effective_parameters_rejected")
                connect.assert_not_called()

    def test_libpq_environment_requires_review(self):
        with patch.object(probe.psycopg, "connect") as connect:
            self.assertEqual(self.run_probe(connect=True, extra={"PGHOSTADDR": "127.0.0.1"})["status"], "libpq_environment_requires_review")
            connect.assert_not_called()

    def test_connection_failure_does_not_leak(self):
        with patch.object(probe.psycopg, "connect", side_effect=RuntimeError("password authentication failed " + DSN)):
            self.assertEqual(self.run_probe(connect=True)["status"], "authentication_failed")

    def test_success_is_readonly_and_rolled_back(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (probe.EXPECTED_PRINCIPAL, probe.EXPECTED_DATABASE, "on")
        with patch.object(probe.psycopg, "connect", return_value=conn) as connect:
            self.assertEqual(self.run_probe(connect=True)["status"], "pass")
            connect.assert_called_once_with(
                DSN, connect_timeout=10, application_name="incident-1911-readonly-probe",
                options="-c default_transaction_read_only=on -c statement_timeout=5000",
            )
            conn.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
                "SELECT current_user, current_database(), current_setting('transaction_read_only')"
            )
            conn.rollback.assert_called_once()
            conn.close.assert_called_once()
            conn.commit.assert_not_called()

    def test_wrong_session_fails(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("neondb_owner", probe.EXPECTED_DATABASE, "off")
        with patch.object(probe.psycopg, "connect", return_value=conn):
            self.assertEqual(self.run_probe(connect=True)["status"], "session_contract_failed")


if __name__ == "__main__":
    unittest.main()
