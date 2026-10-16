import io
import json
import os
import random
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, call, patch

from bridge_school_api import incident_db_probe as probe
from bridge_school_api.incident_error_detail import (
    MAX_MESSAGE_CHARS, MAX_REPORTED_ATTEMPTS, connection_error_detail, has_explicit_timeout,
)


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
        for phase in ("connect", "transaction_setup", "query", "rollback", "close"):
            with self.subTest(phase=phase):
                conn, cursor = self.fake_connection()
                error = TimeoutError("synthetic timeout " + DSN)
                if phase != "connect":
                    target = cursor.execute if phase in {"transaction_setup", "query"} else getattr(conn, phase)
                    target.side_effect = [None, error] if phase == "query" else error
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
        cursor.execute.side_effect = [None, RuntimeError("query timed out " + DSN)]
        conn.rollback.side_effect = RuntimeError("rollback timed out " + DSN)
        conn.close.side_effect = RuntimeError("close timed out " + DSN)
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

    def test_aggregate_connect_hints_do_not_turn_parameter_names_into_timeouts(self):
        message = aggregate([
            (probe.PRODUCTION_HOST, '192.0.2.10', STARTUP + ' ' + SECRET),
            (probe.PRODUCTION_HOST, '2001:db8::1', UNREACHABLE),
        ])
        with patch.object(probe.psycopg, 'connect', side_effect=probe.psycopg.OperationalError(message)):
            result = self.run_probe(connect=True)
        self.assertEqual(result['status'], 'connection_failed')
        self.assertEqual(result['phase'], 'connect')
        self.assertEqual(result['diagnostic_markers'], ['tcp'])
        self.assertFalse(result['connected'])
        self.assertEqual(result['error_detail']['reported_attempt_count'], 2)
        self.assertEqual([a['reasons'] for a in result['error_detail']['attempts']],
                         [['startup_parameter_rejected'], ['network_unreachable']])
        for forbidden in ('192.0.2.10', '2001:db8::1', probe.PRODUCTION_HOST):
            self.assertNotIn(forbidden, json.dumps(result))

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
        cursor.execute.side_effect = [None, RuntimeError("canceling statement due to statement timeout " + DSN)]
        conn.rollback.side_effect = RuntimeError("SSL error: " + DSN)
        conn.close.side_effect = RuntimeError("Connection refused " + DSN)
        with patch.object(probe.psycopg, "connect", return_value=conn):
            result = self.run_probe(connect=True)
        self.assertEqual((result["phase"], result["diagnostic_markers"]), ("query", ["timeout"]))
        self.assertEqual([(e["phase"], e["diagnostic_markers"]) for e in result["cleanup_errors"]],
                         [("rollback", ["tls"]), ("close", ["tcp"])])

    def test_cli_marker_fields_never_emit_raw_diagnostics(self):
        for phase in ("connect", "transaction_setup", "query", "rollback", "close"):
            conn, cursor = self.fake_connection()
            error = RuntimeError('could not translate host name "test.invalid": SSL error: ' + DSN + " 203.0.113.7")
            error.sqlstate = "28P01\n" + SECRET
            if phase != "connect":
                target = cursor.execute if phase in {"transaction_setup", "query"} else getattr(conn, phase)
                target.side_effect = [None, error] if phase == "query" else error
            with self.subTest(phase=phase), \
                 patch.dict(os.environ, {"VERCEL_ENV": "production", "BRIDGE_APP_DATABASE_URL": DSN}, clear=True), \
                 patch("sys.argv", ["incident_db_probe", "--connect"]), \
                 patch.object(probe.psycopg, "connect", return_value=conn,
                              side_effect=error if phase == "connect" else None), \
                 redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                self.assertEqual(probe.main(), 1)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["probe"], "incident-1911-v5")
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
        cursor.execute.side_effect = [None, RuntimeError("timed out")]
        ticks = [0, 1, 2, 5, 6, 7, 11]  # start, setup, query, failure, rollback, close, end
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
        for phase in ("connect", "transaction_setup", "query", "rollback", "close"):
            conn, cursor = self.fake_connection()
            error = RuntimeError("timeout " + DSN)
            if phase != "connect":
                target = cursor.execute if phase in {"transaction_setup", "query"} else getattr(conn, phase)
                target.side_effect = [None, error] if phase == "query" else error
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
            )
            self.assertIs(conn.read_only, True)
            self.assertEqual(conn.cursor.return_value.__enter__.return_value.execute.call_args_list, [
                call("SET LOCAL statement_timeout = 5000"),
                call("SELECT current_user, current_database(), current_setting('transaction_read_only')"),
            ])
            conn.rollback.assert_called_once()
            conn.close.assert_called_once()
            conn.commit.assert_not_called()

    def test_wrong_session_fails(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("neondb_owner", probe.EXPECTED_DATABASE, "off")
        with patch.object(probe.psycopg, "connect", return_value=conn):
            self.assertEqual(self.run_probe(connect=True)["status"], "session_contract_failed")



HOST = 'synthetic-pooler.invalid'
BANNER = 'Multiple connection attempts failed. All failures were:'
STARTUP = 'connection failed: ERROR: unsupported startup parameter in options: statement_timeout'
UNREACHABLE = 'connection is bad: Network is unreachable'


def aggregate(attempts):
    # The documented psycopg 3.3.4 aggregation: final error, then all failures.
    return '\n'.join([attempts[-1][2], BANNER] + [
        f'- host: {host!r}, port: {"5432"!r}, hostaddr: {address!r}: {body}'
        for host, address, body in attempts])


class DetailTests(unittest.TestCase):
    def parse(self, message, **kwargs):
        result = connection_error_detail(message, expected_host=HOST, **kwargs)
        rendered = json.dumps(result)
        for secret in (HOST, '192.0.2.10', '2001:db8::1', 'CANARY_secret_928', 'postgresql://'):
            self.assertNotIn(secret, rendered)
        return result

    def test_startup_ipv4_unreachable_ipv6_separated_and_prefix_not_counted(self):
        result = self.parse(aggregate([(HOST, '192.0.2.10', STARTUP),
                                       (HOST, '2001:db8::1', UNREACHABLE)]))
        self.assertEqual(result['aggregate_format'], 'multiple')
        self.assertEqual(result['reported_attempt_count'], 2)
        first, last = result['attempts']
        self.assertEqual(first['family'], 'ipv4')
        self.assertTrue(first['host_matches_expected'])
        self.assertEqual(first['reasons'], ['startup_parameter_rejected'])
        self.assertEqual(first['startup_parameters'], ['statement_timeout'])
        self.assertEqual(first['timeout_evidence'], 'identifier_only')
        self.assertEqual(last['family'], 'ipv6')
        self.assertEqual(last['reasons'], ['network_unreachable'])
        self.assertEqual(last['timeout_evidence'], 'none')

    def test_order_and_duplicate_addresses_are_preserved(self):
        cases = [(HOST, '2001:db8::1', UNREACHABLE), (HOST, '192.0.2.10', STARTUP),
                 (HOST, '192.0.2.10', 'connection refused')]
        result = self.parse(aggregate(cases))
        self.assertEqual(result['reported_attempt_count'], 3)
        self.assertEqual([x['family'] for x in result['attempts']], ['ipv6', 'ipv4', 'ipv4'])
        self.assertEqual([x['ordinal'] for x in result['attempts']], [1, 2, 3])

    def test_single_message_does_not_invent_address_attempts(self):
        result = self.parse('connection to server at "' + HOST + '" (192.0.2.10), port 5432 failed: timed out')
        self.assertEqual(result['aggregate_format'], 'single')
        self.assertIsNone(result['reported_attempt_count'])
        self.assertEqual(result['attempts'], [])
        self.assertEqual(result['message_hints']['reasons'], ['explicit_timeout'])

    def test_identifiers_and_token_are_not_expiration(self):
        for message, expected in [('statement_timeout', 'identifier_only'),
                                  ('connect_timeout=10', 'identifier_only'),
                                  ('timeout', 'token_only'), ('timeout setting invalid', 'token_only'),
                                  ('connection timeout setting invalid', 'token_only'),
                                  ('statement_timeout expired', 'identifier_only')]:
            with self.subTest(message=message):
                result = self.parse(message)
                self.assertFalse(has_explicit_timeout(message))
                self.assertEqual(result['message_hints']['timeout_evidence'], expected)
                self.assertEqual(result['message_hints']['reasons'], ['unknown'])

    def test_explicit_timeout_and_exception_type_are_distinguished(self):
        for message in ('timeout expired', 'Connection timed out', 'canceling statement due to statement timeout'):
            self.assertEqual(self.parse(message)['message_hints']['timeout_evidence'], 'explicit_phrase')
        result = self.parse('CANARY_secret_928', timeout_exception=True)
        self.assertEqual(result['message_hints']['timeout_evidence'], 'exception_type')
        result = self.parse(aggregate([(HOST, '192.0.2.10', STARTUP),
                                      (HOST, '2001:db8::1', 'CANARY_secret_928')]), timeout_exception=True)
        self.assertEqual(result['message_hints']['timeout_evidence'], 'exception_type')
        self.assertEqual(result['attempts'][0]['timeout_evidence'], 'identifier_only')
        self.assertEqual(result['attempts'][1]['timeout_evidence'], 'none')

    def test_startup_parameters_are_allowlisted(self):
        for parameter, expected in [('statement_timeout', 'statement_timeout'),
                                    ('default_transaction_read_only', 'default_transaction_read_only'),
                                    ('CANARY_secret_928', 'other'), ('', 'unknown')]:
            result = self.parse('unsupported startup parameter in options: ' + parameter)
            self.assertEqual(result['message_hints']['startup_parameters'], [expected])
        self.assertEqual(self.parse('default_transaction_read_only=on')['message_hints']['startup_parameters'], [])

    def test_aggregate_with_multiline_errors_and_crlf(self):
        body = STARTUP + '\nDETAIL: CANARY_secret_928\n'
        text = aggregate([(HOST, '192.0.2.10', body), (HOST, '2001:db8::1', UNREACHABLE)])
        self.assertEqual(self.parse(text.replace('\n', '\r\n'))['reported_attempt_count'], 2)

    def test_unknown_family_host_and_escaped_repr(self):
        text = aggregate([('other.invalid', None, 'unknown'), (HOST, 'not-an-ip', 'unknown'),
                          ('quote\'\\CANARY_secret_928', 'fe80::1%CANARY_secret_928', 'unknown')])
        result = self.parse(text)
        self.assertEqual(result['reported_attempt_count'], 3)
        self.assertEqual([x['family'] for x in result['attempts']], ['unknown'] * 3)
        self.assertEqual([x['host_matches_expected'] for x in result['attempts']], [False, True, False])

    def test_broken_or_ambiguous_framing_fails_closed(self):
        valid = aggregate([(HOST, '192.0.2.10', STARTUP), (HOST, '2001:db8::1', UNREACHABLE)])
        cases = [valid.replace(BANNER, BANNER + '\n' + BANNER),
                 valid.replace('All failures were:', 'Different version:'),
                 valid.replace("port: '5432'", "port: 'CANARY_secret_928'", 1),
                 valid.replace('- host:', '- host', 1),
                 valid.replace('hostaddr:', 'different_field:', 1),
                 'prefix mismatch\n' + valid,
                 aggregate([(HOST, '192.0.2.10', STARTUP)]),
                 valid.replace(BANNER + '\n', BANNER + '\nforeign preamble\n')]
        for message in cases:
            with self.subTest(message=message):
                result = self.parse(message)
                self.assertEqual(result['aggregate_format'], 'unrecognized')
                self.assertIsNone(result['reported_attempt_count'])
                self.assertEqual(result['attempts'], [])

    def test_controls_nonstring_size_and_attempt_limits(self):
        for message in (None, 'x' * (MAX_MESSAGE_CHARS + 1), '\x00timed out', '\u2028timed out', '\rbroken'):
            result = self.parse(message)
            self.assertEqual(result['aggregate_format'], 'unrecognized')
            self.assertEqual(result['attempts'], [])
        for count in (MAX_REPORTED_ATTEMPTS, MAX_REPORTED_ATTEMPTS + 1):
            result = self.parse(aggregate([(HOST, '192.0.2.10', 'connection refused')] * count))
            self.assertEqual(result['reported_attempt_count'], count if count == MAX_REPORTED_ATTEMPTS else None)
        self.assertEqual(self.parse('x' * MAX_MESSAGE_CHARS)['aggregate_format'], 'single')

    def test_fixed_reason_categories_and_redaction(self):
        text = ('postgresql://CANARY_secret_928@' + HOST + '/private\nconnection refused\n'
                'network is unreachable\nno route to host\nconnection reset by peer\n'
                'certificate verify failed\nchannel binding required\nauthentication failed\n'
                'could not translate host name\nunsupported startup parameter: CANARY_secret_928')
        result = self.parse(text)
        self.assertEqual(result['message_hints']['reasons'], [
            'network_unreachable', 'no_route', 'refused', 'reset', 'dns', 'tls',
            'channel_binding', 'auth', 'startup_parameter_rejected'])

    def test_deterministic_malformed_text_never_copies_input(self):
        rng = random.Random(1911)
        for _ in range(100):
            text = 'CANARY_secret_928' + ''.join(rng.choice("ab'\\:\n\r-()123") for _ in range(100))
            self.parse(text)



SET = 'SET LOCAL statement_timeout = 5000'
SELECT = "SELECT current_user, current_database(), current_setting('transaction_read_only')"


class OrderedConnection:
    def __init__(self, driver, events, row, faults=()):
        self.driver, self.events, self.row, self.faults = driver, events, row, faults
        self._read_only = None

    def event(self, name):
        self.events.append(name)
        if name in self.faults:
            raise self.driver.OperationalError(SECRET)

    @property
    def read_only(self):
        return self._read_only

    @read_only.setter
    def read_only(self, value):
        if value is not True:
            raise AssertionError('read_only must be true')
        self.event('read_only')
        self._read_only = value

    def cursor(self):
        self.event('cursor')
        connection = self

        class Cursor:
            def __enter__(self):
                connection.event('cursor_enter')
                return self

            def __exit__(self, *args):
                connection.event('cursor_exit')
                return False

            def execute(self, sql):
                if connection.read_only is not True:
                    raise AssertionError('statement before read_only')
                if sql not in (SET, SELECT):
                    raise AssertionError('unexpected SQL')
                connection.event('set_local' if sql == SET else 'select')

            def fetchone(self):
                connection.event('fetch')
                return connection.row

        return Cursor()

    def rollback(self):
        self.event('rollback')

    def close(self):
        self.event('close')

    def commit(self):
        raise AssertionError('commit forbidden')



class TransactionTests(unittest.TestCase):
    setUp = IncidentProbeTests.setUp
    run_probe = IncidentProbeTests.run_probe
    driver = probe.psycopg

    def run_case(self, faults=(), row=None):
        events = []
        if row is None:
            row = (probe.EXPECTED_PRINCIPAL, probe.EXPECTED_DATABASE, "on")
        conn = OrderedConnection(self.driver, events, row, faults)

        def connect(*args, **kwargs):
            conn.event('connect')
            return conn

        with patch.object(self.driver, 'connect', side_effect=connect) as connect_call:
            result = self.run_probe(connect=True)
        connect_call.assert_called_once_with(DSN, connect_timeout=10,
                                             application_name='incident-1911-readonly-probe')
        return result, events

    def test_success_order_and_no_startup_options(self):
        result, events = self.run_case()
        self.assertEqual(events, ['connect', 'read_only', 'cursor', 'cursor_enter',
                                  'set_local', 'select', 'fetch', 'cursor_exit', 'rollback', 'close'])
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['probe'], 'incident-1911-v5')

    def test_connect_failure_has_parser_and_no_transaction_or_cleanup(self):
        # Startup rejection is only synthetic input here, never a live diagnosis.
        events = []
        error = self.driver.OperationalError('unsupported startup parameter in options: statement_timeout')
        def connect(*args, **kwargs):
            events.append('connect')
            raise error
        with patch.object(self.driver, 'connect', side_effect=connect) as call:
            result = self.run_probe(connect=True)
        call.assert_called_once()
        self.assertEqual(events, ['connect'])
        self.assertFalse(result['connected'])
        self.assertEqual(result['phase'], 'connect')
        self.assertEqual(result['status'], 'connection_failed')
        self.assertEqual(result['error_detail']['message_hints']['startup_parameters'], ['statement_timeout'])

    def test_setup_failure_never_reaches_select_and_always_cleans_up(self):
        for fault in ('read_only', 'cursor', 'cursor_enter', 'set_local'):
            with self.subTest(fault=fault):
                result, events = self.run_case((fault,))
                self.assertEqual(result['phase'], 'transaction_setup')
                self.assertEqual(result['status'], 'transaction_setup_failed')
                self.assertTrue(result['connected'])
                self.assertNotIn('select', events)
                self.assertNotIn('fetch', events)
                self.assertEqual(events[-2:], ['rollback', 'close'])
                self.assertEqual(events.count('rollback'), 1)
                self.assertEqual(events.count('close'), 1)
                self.assertNotIn('error_detail', result)

    def test_select_and_fetch_failure_preserve_query_phase(self):
        for fault in ('select', 'fetch', 'cursor_exit'):
            with self.subTest(fault=fault):
                result, events = self.run_case((fault,))
                self.assertEqual(result['phase'], 'query')
                self.assertEqual(result['status'], 'query_failed')
                self.assertEqual(events[-2:], ['rollback', 'close'])

    def test_setup_failure_is_primary_when_both_cleanup_operations_fail(self):
        result, events = self.run_case(('set_local', 'rollback', 'close'))
        self.assertEqual(result['phase'], 'transaction_setup')
        self.assertEqual([x['phase'] for x in result['cleanup_errors']], ['rollback', 'close'])
        self.assertNotIn('select', events)
        self.assertEqual(events.count('rollback'), 1)
        self.assertEqual(events.count('close'), 1)

    def test_cleanup_failure_after_success_never_commits_or_reports_pass(self):
        for faults, phase, secondary in [(('rollback',), 'rollback', []),
                                         (('close',), 'close', []),
                                         (('rollback', 'close'), 'rollback', ['close'])]:
            with self.subTest(faults=faults):
                result, events = self.run_case(faults)
                self.assertEqual(result['phase'], phase)
                self.assertEqual(result['status'], phase + '_failed')
                self.assertEqual([x['phase'] for x in result['cleanup_errors']], secondary)
                self.assertEqual(events[-2:], ['rollback', 'close'])

    def test_read_only_off_is_a_contract_failure_with_cleanup(self):
        result, events = self.run_case(row=(probe.EXPECTED_PRINCIPAL, probe.EXPECTED_DATABASE, 'off'))
        self.assertEqual(result['status'], 'session_contract_failed')
        self.assertEqual(events[-2:], ['rollback', 'close'])


if __name__ == "__main__":
    unittest.main()
