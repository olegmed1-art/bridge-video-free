"""Synthetic LogRecord tests; no psycopg, sockets, subprocess or secrets."""
import ast
import contextlib
import io
import json
import logging
from pathlib import Path
import unittest

import observer as obs

CANARY = "SYNTHETIC_PASSWORD_DO_NOT_OUTPUT"
DESCR4 = "host: 'synthetic.invalid', port: '5432', hostaddr: '203.0.113.123'"
DESCR6 = "host: 'synthetic.invalid', port: '5432', hostaddr: '2001:db8::123'"


class Clock:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 2000000
        return self.n


def record(msg, *args):
    return logging.LogRecord("psycopg", logging.DEBUG, "/synthetic/psycopg/connection.py",
                             obs.EVENTS.get(msg, (1, 0))[0], msg, args, None, "connect")


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.o = obs.AttemptObserver(Clock())

    def emit(self, msg, *args):
        self.o.handle(record(msg, *args))

    def emit_failure(self, descr=DESCR4, text="Connection refused"):
        self.emit(obs.START, descr)
        self.emit(obs.FAILED, descr, text)

    def test_ipv6_failure_survives_ipv4_success(self):
        self.emit_failure(DESCR6, "Network is unreachable")
        self.emit(obs.START, DESCR4)
        self.emit(obs.SUCCEEDED, DESCR4)
        snap = self.o.snapshot()
        self.assertTrue(snap["paired_observations_complete"])
        self.assertEqual([r["family"] for r in snap["attempts"]], ["ipv6", "ipv4"])
        self.assertEqual(snap["attempts"][0]["hints"], ["tcp_network_unreachable"])
        self.assertEqual(snap["attempts"][1]["outcome"], "attempt_succeeded")

    def test_success_log_does_not_prove_public_call_returned(self):
        self.emit(obs.START, DESCR4)
        self.emit(obs.SUCCEEDED, DESCR4)
        # The native method still has post-loop setup to perform after this log.
        self.assertIsNone(self.o.snapshot()["public_call_returned"])

    def test_all_failed_separate_categories(self):
        for text in ("No route to host", "Connection reset by peer", "TLS handshake failed",
                     "password authentication failed", "channel binding required", "timeout expired"):
            self.emit_failure(text=text)
        snap = self.o.snapshot()
        self.assertEqual([a["hints"] for a in snap["attempts"]],
                         [["tcp_no_route"], ["tcp_reset"], ["tls"], ["auth"], ["channel_binding"], ["timeout"]])
        self.assertEqual(snap["observed_completions"], 6)

    def test_multiple_hints_not_priority_guess(self):
        self.assertEqual(obs.hints("SSL error: channel binding required authentication failed"),
                         ["tls", "channel_binding", "auth"])

    def test_timeout_parameter_word_is_not_timeout_evidence(self):
        self.assertEqual(obs.hints("connect_timeout=10, unknown error"), ["unknown"])

    def test_unknown_phrase_stays_unknown(self):
        self.assertEqual(obs.hints("synthetic unusual failure"), ["unknown"])

    def test_no_raw_text_or_target_survives(self):
        self.emit_failure(text=f"postgresql://user:{CANARY}@synthetic.invalid/secret Connection refused")
        output = json.dumps(self.o.snapshot())
        for forbidden in (CANARY, "synthetic.invalid", "203.0.113.123", "postgresql://", "secret"):
            self.assertNotIn(forbidden, output)

    def test_descriptor_is_not_retained(self):
        self.emit(obs.START, DESCR6)
        internal = repr(self.o.__dict__)
        self.assertNotIn("synthetic.invalid", internal)
        self.assertNotIn("2001:db8", internal)

    def test_no_console_output_even_on_malformed_record(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.o.emit(object())
            self.emit_failure(text=CANARY)
        self.assertEqual(out.getvalue() + err.getvalue(), "")

    def test_no_arbitrary_string_conversion(self):
        class Trap:
            def __str__(self):
                raise AssertionError("must not coerce")
        self.assertEqual(obs.hints(Trap()), ["unknown"])
        self.o.emit(record(obs.START, Trap()))
        self.assertEqual(self.o.snapshot()["contract_errors"], 1)

    def test_oversize_message_and_descriptor(self):
        self.assertEqual(obs.hints("authentication failed" + "x" * 32768), ["unknown"])
        self.assertEqual(obs.family("x" * 4097), "unknown")
        self.o.emit(record(obs.FAILED, DESCR4, "x" * 32769))
        self.assertEqual(self.o.snapshot()["observed_completions"], 0)

    def test_literal_parser_cannot_execute_code(self):
        text = "host: 'x', port: '5432', hostaddr: __import__('os').system('bad')"
        self.assertEqual(obs.family(text), "unknown")

    def test_invalid_or_scoped_address_is_unknown(self):
        for text in ("not a descriptor", DESCR4.replace("203.0.113.123", "example.invalid"),
                     DESCR6.replace("2001:db8::123", "fe80::1%eth0"),
                     "host: 'x', port: '5432', hostaddr: None"):
            with self.subTest(text=text):
                self.assertEqual(obs.family(text), "unknown")

    def test_interruption_preserves_completed_and_pending(self):
        self.emit_failure()
        self.emit(obs.START, DESCR6)
        snap = self.o.snapshot()
        self.assertFalse(snap["paired_observations_complete"])
        self.assertTrue(snap["pending_observation"])
        self.assertEqual(snap["attempts"][1]["outcome"], "incomplete")
        self.assertIsNone(snap["attempts"][1]["duration_ms"])

    def test_retention_limit_does_not_abort_client_or_limit_attempts(self):
        for _ in range(131):
            self.emit_failure()
        snap = self.o.snapshot()
        self.assertEqual(snap["observed_starts"], 131)
        self.assertEqual(snap["observed_completions"], 131)
        self.assertEqual(len(snap["attempts"]), 128)
        self.assertTrue(snap["retention_overflow"])
        self.assertFalse(snap["paired_observations_complete"])
        self.assertFalse(snap["hard_attempt_cap_enforced"])

    def test_unknown_lifecycle_and_line_drift_are_incomplete(self):
        self.emit(obs.FAILED, DESCR4, "Connection refused")
        changed = record(obs.START, DESCR4)
        changed.lineno = 999
        self.o.emit(changed)
        self.assertEqual(self.o.snapshot()["contract_errors"], 2)
        self.assertEqual(self.o.snapshot()["observed_starts"], 0)

    def test_duplicate_start_does_not_invent_completed_failure(self):
        self.emit(obs.START, DESCR4)
        self.emit(obs.START, DESCR6)
        self.emit(obs.SUCCEEDED, DESCR6)
        snap = self.o.snapshot()
        self.assertEqual(snap["attempts"][0]["outcome"], "incomplete")
        self.assertFalse(snap["paired_observations_complete"])

    def test_mismatched_family_is_flagged(self):
        self.emit(obs.START, DESCR4)
        self.emit(obs.SUCCEEDED, DESCR6)
        self.assertEqual(self.o.snapshot()["contract_errors"], 1)

    def test_other_records_are_ignored_without_formatting(self):
        other = record("something: %s", CANARY)
        other.getMessage = lambda: self.fail("getMessage should not run")
        self.o.emit(other)
        self.assertEqual(self.o.snapshot()["ignored_records"], 1)

    def test_no_automatic_logger_configuration(self):
        logger = logging.getLogger("psycopg")
        before = (logger.level, logger.propagate, list(logger.handlers), logger.disabled)
        obs.AttemptObserver()
        self.assertEqual(before, (logger.level, logger.propagate, list(logger.handlers), logger.disabled))

    def test_recording_error_never_raises_into_client(self):
        def badclock():
            raise RuntimeError(CANARY)
        self.o = obs.AttemptObserver(badclock)
        self.emit(obs.START, DESCR4)
        self.assertEqual(self.o.snapshot()["contract_errors"], 1)

    def test_duration_bounded_and_snapshot_independent(self):
        ticks = iter((1000000, 999999999999))
        self.o = obs.AttemptObserver(lambda: next(ticks))
        self.emit_failure()
        snap = self.o.snapshot()
        self.assertEqual(snap["attempts"][0]["duration_ms"], 25000)
        snap["attempts"][0]["hints"].append(CANARY)
        self.assertNotIn(CANARY, json.dumps(self.o.snapshot()))

    def test_no_zero_attempt_completion_claim(self):
        snap = self.o.snapshot()
        self.assertFalse(snap["paired_observations_complete"])
        self.assertIsNone(snap["internal_libpq_transport_attempt_count"])
        self.assertFalse(snap["individual_sqlstates_available"])

    def test_source_has_no_network_secret_or_execution_entrypoint(self):
        source = Path(obs.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(n.name for n in node.names)
            if isinstance(node, ast.ImportFrom):
                modules.add(node.module)
        self.assertEqual(modules, {"__future__", "ast", "ipaddress", "logging", "re", "time"})
        for forbidden in ("psycopg.connect(", "os.environ", "getaddrinfo(", "settrace(",
                          "subprocess", "basicConfig(", "setLevel(", "print("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
