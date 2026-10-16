"""Synthetic diagnostics only: no credentials, network or real subprocesses."""
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from database import runtime_health_preflight as health
from ops import github_autopilot_db_route as route

PRIVATE = "SYNTHETIC_PRIVATE_VALUE"
BAD_TEXT = "postgresql://synthetic:" + PRIVATE + "@example.invalid/db\npassword=" + PRIVATE
CAPACITY = "autopilot_mailbox_capacity"
BACKLOG = "autopilot_planner_backlog"


def mailbox_dsn():
    return ("postgresql://autopilot_callback_login:synthetic-" + PRIVATE + "@"
            + route.SOURCE + "/neondb?sslmode=require&channel_binding=require")


class UnprintableError(RuntimeError):
    def __str__(self):
        raise AssertionError("exception text must not be read")


class DiagnosticContract(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {}, clear=True))
        stack.enter_context(patch.object(route, "_failure_stage", route.RoutingStage.ARGUMENTS))
        self.connect = stack.enter_context(patch.object(
            health.psycopg, "connect", side_effect=AssertionError("unexpected database call")))
        self.popen = stack.enter_context(patch.object(
            route.subprocess, "Popen", side_effect=AssertionError("unexpected subprocess")))
        self.run = stack.enter_context(patch.object(
            route.subprocess, "run", side_effect=AssertionError("unexpected subprocess")))
        self.stop = stack.enter_context(patch.object(route, "stop"))

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["route", *args]), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = route.cli()
        return code, stdout.getvalue(), stderr.getvalue()

    def health_check(self, rows, *, required=True, installed=True):
        cur = MagicMock()
        cur.fetchone.return_value = ("view" if installed else None,)
        cur.fetchall.return_value = rows
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                health.check_autopilot_health(cur, required=required)
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_stage(self, result, stage, detail=""):
        self.assertEqual(result, (1, "", "AUTOPILOT_DATABASE_ROUTING_FAILED stage=" + stage + detail + "\n"))

    def test_argument_and_context_failures_emit_only_fixed_stage(self):
        self.assert_stage(self.cli(BAD_TEXT, BAD_TEXT), "ARGUMENTS")
        self.assert_stage(self.cli(BAD_TEXT), "CONTEXT")
        self.connect.assert_not_called()
        self.popen.assert_not_called()

    def test_missing_ssh_key_emits_fixed_stage(self):
        with patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "olegmed1-art/bridge-video-free",
            "GITHUB_REF": "refs/heads/main",
        }), patch.object(route.signal, "signal"):
            self.assert_stage(self.cli("health"), "SSH_KEY")
        self.connect.assert_not_called()
        self.popen.assert_not_called()

    def test_enum_is_closed_and_invalid_stage_never_stringified(self):
        self.assertEqual({stage.value for stage in route.RoutingStage}, {
            "ARGUMENTS", "CONTEXT", "SSH_KEY", "SSH_HOST_KEY", "LEASE_START",
            "LEASE_HEADER", "TARGET_SETUP", "CONSUMER", "ROUTE_WAIT",
            "MAILBOX_DRIVER", "MAILBOX_DSN", "MAILBOX_CONNECT", "MAILBOX_QUERY", "MAILBOX_RESULT", "UNKNOWN",
        })
        for stage in [*route.RoutingStage, BAD_TEXT, UnprintableError(BAD_TEXT)]:
            with self.subTest(stage_type=type(stage).__name__):
                out = io.StringIO()
                with patch.object(route, "_failure_stage", stage), contextlib.redirect_stderr(out):
                    route.report_routing_failure()
                expected = stage.value if type(stage) is route.RoutingStage else "UNKNOWN"
                self.assertEqual(out.getvalue(), "AUTOPILOT_DATABASE_ROUTING_FAILED stage=" + expected + "\n")

    def test_exceptions_are_not_rendered_and_return_codes_are_preserved(self):
        for error in (RuntimeError(BAD_TEXT), UnprintableError(BAD_TEXT)):
            with patch.object(route, "main", side_effect=error):
                self.assert_stage(self.cli("health"), "ARGUMENTS")
        for code in (0, 1, 7):
            with patch.object(route, "main", return_value=code):
                self.assertEqual(self.cli("health"), (code, "", ""))
        with patch.object(route, "main", side_effect=SystemExit(143)), self.assertRaises(SystemExit) as raised:
            self.cli("health")
        self.assertEqual(raised.exception.code, 143)

    def test_lease_header_failure_keeps_cleanup_and_redacts_exception(self):
        lease = MagicMock()
        self.popen.side_effect = None
        self.popen.return_value = lease
        with patch.object(route, "header", side_effect=UnprintableError(BAD_TEXT)), patch.object(
            route, "main", side_effect=lambda _: route.execute_under_lease([], "health", {}, MagicMock())
        ):
            self.assert_stage(self.cli("health"), "LEASE_HEADER")
        self.stop.assert_called_once_with(lease)

    def test_missing_target_credential_keeps_cleanup_and_fixed_stage(self):
        lease = MagicMock()
        lease.poll.return_value = None
        self.popen.side_effect = None
        self.popen.return_value = lease
        record = {"route": {"backend": "postgresql"}, "ca_pem": "synthetic certificate"}
        with patch.object(route, "header", return_value=b"synthetic"), patch.object(
            route, "parse_record", return_value=record
        ), patch.object(route, "main", side_effect=lambda _: route.execute_under_lease([], "health", {}, MagicMock())):
            self.assert_stage(self.cli("health"), "TARGET_SETUP")
        self.stop.assert_called_once_with(lease)

    def mailbox_cursor(self):
        self.connect.side_effect = None
        conn = self.connect.return_value.__enter__.return_value
        return conn.cursor.return_value.__enter__.return_value

    def test_mailbox_connection_and_query_errors_never_echo_credentials(self):
        with patch.dict(os.environ, {"DATABASE_URL": mailbox_dsn()}):
            self.connect.side_effect = UnprintableError(BAD_TEXT)
            self.assert_stage(self.cli("--mailbox-read"), "MAILBOX_CONNECT", " error_type=OTHER sqlstate=NONE")
            cur = self.mailbox_cursor()
            cur.execute.side_effect = UnprintableError(BAD_TEXT)
            self.assert_stage(self.cli("--mailbox-read"), "MAILBOX_QUERY", " error_type=OTHER sqlstate=NONE")

    def test_mailbox_invalid_private_dsn_fails_before_database_call(self):
        with patch.dict(os.environ, {"DATABASE_URL": BAD_TEXT}):
            self.assert_stage(self.cli("--mailbox-read"), "MAILBOX_DSN", " error_type=ValueError sqlstate=NONE")
        self.connect.assert_not_called()
        self.popen.assert_not_called()

    def test_mailbox_result_failure_and_success_protocol_are_preserved(self):
        with patch.dict(os.environ, {"DATABASE_URL": mailbox_dsn()}):
            cur = self.mailbox_cursor()
            for rows in ([], [(1, 2, 3, BAD_TEXT)], [(1, 2, 3, "bad|value")]):
                cur.fetchall.return_value = rows
                self.assert_stage(self.cli("--mailbox-read"), "MAILBOX_RESULT", " error_type=OTHER sqlstate=NONE")
            cur.fetchall.return_value = [(881, 81, 100, "PREPARE_ROTATION")]
            self.assertEqual(self.cli("--mailbox-read"), (0, "881|81|100|PREPARE_ROTATION\n", ""))
            self.assertIn("mailbox_rotation_readiness()", cur.execute.call_args.args[0])

    def test_only_whitelisted_numbers_are_logged_even_with_extra_private_fields(self):
        rows = [
            (CAPACITY, "critical", {"used_dispatches": 101, "max_dispatches": 100, "remaining": -1,
                                    "dsn": BAD_TEXT, "mailbox_pr": 999, "readiness": BAD_TEXT}),
            (BACKLOG, "critical", {"paused_count": 3, "open_actionable_count": 2,
                                   "planner_decision": BAD_TEXT, "job": {"contents": BAD_TEXT}}),
            ("autopilot_mailbox_e2e", "ok", {"private": BAD_TEXT}),
        ]
        code, stdout, stderr = self.health_check(rows)
        self.assertEqual(code, 1)
        self.assertEqual(stdout,
            "RUNTIME_AUTOPILOT_HEALTH: critical=2 warning=0 signals=3\n"
            "RUNTIME_AUTOPILOT_COUNTERS: signal=autopilot_mailbox_capacity used_dispatches=101 max_dispatches=100 remaining=-1\n"
            "RUNTIME_AUTOPILOT_COUNTERS: signal=autopilot_planner_backlog paused_count=3 open_actionable_count=2\n")
        self.assertEqual(stderr, "RUNTIME_DB_HEALTH: FAIL: critical Autopilot operational health signal detected: " + CAPACITY + "," + BACKLOG + "\n")

    def test_malformed_details_never_coerce_or_replace_critical_with_pass(self):
        good = {"used_dispatches": 81, "max_dispatches": 100, "remaining": 19}
        invalid = [None, BAD_TEXT, [], {}, {"used_dispatches": 81},
                   {**good, "remaining": 18}, {**good, "max_dispatches": 101},
                   {"used_dispatches": -1, "max_dispatches": 100, "remaining": 101}]
        for field in good:
            for value in (True, False, "81", BAD_TEXT, 81.0, None, [], {}, 2**31, UnprintableError()):
                invalid.append({**good, field: value})
        for details in invalid:
            with self.subTest(details_type=type(details).__name__):
                code, stdout, stderr = self.health_check([(CAPACITY, "critical", details)])
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "RUNTIME_AUTOPILOT_HEALTH: critical=1 warning=0 signals=1\n"
                    "RUNTIME_AUTOPILOT_COUNTERS_UNAVAILABLE: signal=autopilot_mailbox_capacity\n")
                self.assertNotIn(PRIVATE, stdout + stderr)

    def test_backlog_malformed_details_are_not_logged(self):
        for details in (None, {}, {"paused_count": -1, "open_actionable_count": 1},
                        {"paused_count": 0, "open_actionable_count": True},
                        {"paused_count": BAD_TEXT, "open_actionable_count": 1},
                        {"paused_count": 0, "open_actionable_count": 2**31}):
            code, stdout, stderr = self.health_check([(BACKLOG, "critical", details)])
            self.assertEqual(code, 1)
            self.assertIn("RUNTIME_AUTOPILOT_COUNTERS_UNAVAILABLE: signal=" + BACKLOG, stdout)
            self.assertNotIn(PRIVATE, stdout + stderr)

    def test_numeric_details_do_not_change_existing_severity_decision(self):
        for severity in ("ok", "warning", "critical"):
            for details in ({"used_dispatches": 0, "max_dispatches": 100, "remaining": 100},
                            {"used_dispatches": 101, "max_dispatches": 100, "remaining": -1}, None):
                code, stdout, stderr = self.health_check([(CAPACITY, severity, details)])
                self.assertEqual(code, 1 if severity == "critical" else 0)
                self.assertIn("critical=" + str(int(severity == "critical")), stdout)
                self.assertIn("warning=" + str(int(severity == "warning")), stdout)

    def test_unknown_signal_never_echoes_its_key_or_details(self):
        code, stdout, stderr = self.health_check([(BAD_TEXT, "critical", {"job": BAD_TEXT})])
        self.assertEqual(code, 1)
        self.assertIn("unknown_signal", stderr)
        self.assertNotIn(PRIVATE, stdout + stderr)

    def test_missing_or_empty_required_view_still_fails(self):
        for installed in (False, True):
            code, stdout, stderr = self.health_check([], installed=installed)
            self.assertEqual(code, 1)
        code, stdout, stderr = self.health_check([], installed=False, required=False)
        self.assertEqual((code, stdout, stderr), (0, "RUNTIME_AUTOPILOT_HEALTH: SKIP view_not_installed\n", ""))

    def test_database_error_outputs_only_fixed_message(self):
        class PrivateDatabaseError(health.psycopg.Error):
            def __str__(self):
                raise AssertionError("database error text must not be read")

        self.connect.side_effect = PrivateDatabaseError(BAD_TEXT)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(health, "normalize_health_dsn", return_value=BAD_TEXT), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            health.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "RUNTIME_DB_HEALTH: FAIL: database connection/query failed\n")

    def test_query_projects_only_allowed_details(self):
        # Guard the data minimization boundary: no raw details/job payload column.
        import re
        self.assertEqual(set(re.findall(r"details->'([^']+)'", health.AUTOPILOT_COUNTER_QUERY)), {
            "used_dispatches", "max_dispatches", "remaining", "paused_count", "open_actionable_count",
        })
        self.assertNotRegex(health.AUTOPILOT_COUNTER_QUERY, r"\bdetails\s*[,\n]")
        for field in ("planner_decision", "mailbox_pr", "readiness", "job"):
            self.assertNotIn(field, health.AUTOPILOT_COUNTER_QUERY)


    def test_sensitive_nonnumeric_port_fails_without_traceback_or_database_call(self):
        raw = ("postgresql://" + health.EXPECTED_PRINCIPAL + ":synthetic-password@"
               + sorted(health.EXPECTED_HOSTS)[0] + ":" + PRIVATE
               + "/neondb?sslmode=require&channel_binding=require")
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"BRIDGE_HEALTH_DATABASE_URL": raw}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            health.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "RUNTIME_DB_HEALTH: FAIL: HEALTH_DSN_INVALID_URI\n")
        self.assertNotIn(PRIVATE, out.getvalue() + err.getvalue())
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())
        self.connect.assert_not_called()

    def test_host_key_failure_emits_fixed_stage_before_any_lease(self):
        self.run.side_effect = [None, UnprintableError(BAD_TEXT)]
        with patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "olegmed1-art/bridge-video-free",
            "GITHUB_REF": "refs/heads/main", "SSH_PRIVATE_KEY": "synthetic private key",
        }), patch.object(route.signal, "signal"), patch.object(route, "Path"), patch.object(
            route.tempfile, "TemporaryDirectory"
        ) as temporary:
            temporary.return_value.__enter__.return_value = "/synthetic-only"
            self.assert_stage(self.cli("health"), "SSH_HOST_KEY")
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(self.run.call_args_list[0].args[0][0], "ssh-keygen")
        self.assertEqual(self.run.call_args_list[1].args[0][0], "bash")
        self.popen.assert_not_called()
        self.connect.assert_not_called()

    def test_lease_start_failure_emits_fixed_stage_without_cleanup_of_missing_process(self):
        self.popen.side_effect = UnprintableError(BAD_TEXT)
        with patch.object(route, "main", side_effect=lambda _: route.execute_under_lease([], "health", {}, MagicMock())):
            self.assert_stage(self.cli("health"), "LEASE_START")
        self.popen.assert_called_once()
        self.stop.assert_not_called()

    def test_consumer_failures_keep_fixed_stage_and_cleanup(self):
        for failure in ("spawn", "lease_lost", "timeout", "completion_loss"):
            with self.subTest(failure=failure):
                self.popen.reset_mock()
                self.stop.reset_mock()
                lease, child = MagicMock(), MagicMock()
                lease.poll.side_effect = [None, 0] if failure in ("lease_lost", "completion_loss") else None
                lease.poll.return_value = None
                child.poll.return_value = 0 if failure == "completion_loss" else None
                self.popen.side_effect = [lease, UnprintableError(BAD_TEXT) if failure == "spawn" else child]
                record = {"route": {"backend": "neon"}, "ca_pem": "synthetic certificate"}
                with patch.object(route, "header", return_value=b"synthetic"), patch.object(
                    route, "parse_record", return_value=record
                ), patch.object(route.time, "monotonic", side_effect=[0, 0, 0, 241] if failure == "timeout" else None, return_value=0), patch.object(
                    route.time, "sleep", side_effect=AssertionError("unexpected wait")
                ), patch.object(route, "main", side_effect=lambda _: route.execute_under_lease([], "health", {}, MagicMock())):
                    self.assert_stage(self.cli("health"), "CONSUMER")
                self.assertEqual(self.popen.call_count, 2)
                if failure == "spawn":
                    self.stop.assert_called_once_with(lease)
                else:
                    self.assertEqual(self.stop.call_args_list, [unittest.mock.call(child, group=True), unittest.mock.call(lease)])

    def test_busy_route_deadline_emits_route_wait_without_starting_consumer(self):
        lease = MagicMock()
        self.popen.side_effect = None
        self.popen.return_value = lease
        with patch.object(route.time, "monotonic", side_effect=[0, 0, 601]), patch.object(route.time, "sleep") as sleep, patch.object(
            route, "header", return_value=b"synthetic"
        ), patch.object(route, "parse_record", return_value=None), patch.object(
            route, "main", side_effect=lambda _: route.execute_under_lease([], "health", {}, MagicMock())
        ):
            self.assert_stage(self.cli("health"), "ROUTE_WAIT")
        self.popen.assert_called_once()
        sleep.assert_called_once_with(2)
        self.assertEqual(self.stop.call_args_list, [unittest.mock.call(lease), unittest.mock.call(lease)])

    def test_mailbox_numeric_boundaries(self):
        limit = 2**31-1
        for used, maximum in ((0, 1), (1, 1), (2, 1), (0, 100), (100, 100), (limit, 1), (limit, 100)):
            remaining = maximum-used
            details = {"used_dispatches": used, "max_dispatches": maximum, "remaining": remaining}
            with self.subTest(used=used, maximum=maximum):
                code, stdout, stderr = self.health_check([(CAPACITY, "warning", details)])
                self.assertEqual((code, stderr), (0, ""))
                self.assertIn("used_dispatches=" + str(used) + " max_dispatches=" + str(maximum) + " remaining=" + str(remaining) + "\n", stdout)
                self.assertNotIn("COUNTERS_UNAVAILABLE", stdout)
        for details in (
            {"used_dispatches": 0, "max_dispatches": 0, "remaining": 0},
            {"used_dispatches": 0, "max_dispatches": 101, "remaining": 101},
            {"used_dispatches": -1, "max_dispatches": 1, "remaining": 2},
            {"used_dispatches": limit+1, "max_dispatches": 1, "remaining": -limit},
            {"used_dispatches": limit, "max_dispatches": 1, "remaining": -2**31},
        ):
            self.assertEqual(health.autopilot_counter_line(CAPACITY, details),
                             "RUNTIME_AUTOPILOT_COUNTERS_UNAVAILABLE: signal=" + CAPACITY)

    def test_backlog_numeric_boundaries(self):
        limit = 2**31-1
        for paused, actionable in ((0, 0), (limit, 0), (0, limit), (limit, limit)):
            details = {"paused_count": paused, "open_actionable_count": actionable}
            self.assertEqual(health.autopilot_counter_line(BACKLOG, details),
                "RUNTIME_AUTOPILOT_COUNTERS: signal=" + BACKLOG
                + " paused_count=" + str(paused) + " open_actionable_count=" + str(actionable))
        for field in ("paused_count", "open_actionable_count"):
            for value in (-1, limit+1):
                details = {"paused_count": 0, "open_actionable_count": 0, field: value}
                self.assertEqual(health.autopilot_counter_line(BACKLOG, details),
                                 "RUNTIME_AUTOPILOT_COUNTERS_UNAVAILABLE: signal=" + BACKLOG)


if __name__ == "__main__":
    unittest.main()
