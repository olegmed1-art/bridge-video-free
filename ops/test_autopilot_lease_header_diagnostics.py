"""Offline reason classification; never contact SSH or a database."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops import github_autopilot_db_route as target


CERTIFICATE = 'synthetic-certificate'
CERTIFICATE_SHA = hashlib.sha256(CERTIFICATE.encode()).hexdigest()
ROUTE = {'version': 1, 'backend': 'neon', 'database': 'autopilot', 'epoch': 4}


class UntrustedValue:
    def __str__(self):
        raise AssertionError('untrusted value was stringified')

    __repr__ = __str__


class LeaseHeaderDiagnostics(unittest.TestCase):
    def test_reader_reasons_are_distinct(self):
        cases = (
            (target.LeaseHeaderReason.EOF, True, b'', None, [0, 0]),
            (target.LeaseHeaderReason.PROCESS_EXITED, False, b'', 1, [0, 0]),
            (target.LeaseHeaderReason.TIMEOUT, False, b'', None, [0, 21]),
            (target.LeaseHeaderReason.TOO_LARGE, True, b'x', None, None),
        )
        for expected, readable, payload, returncode, clock in cases:
            with self.subTest(reason=expected.value):
                process = Mock()
                process.stdout.fileno.return_value = 7
                process.poll.return_value = returncode
                ready = ([process.stdout], [], []) if readable else ([], [], [])
                clock_args = {'side_effect': clock} if clock else {'return_value': 0}
                with patch.object(target.select, 'select', return_value=ready), \
                        patch.object(target.os, 'read', return_value=payload), \
                        patch.object(target.time, 'monotonic', **clock_args), \
                        self.assertRaises(target.LeaseHeaderError) as observed:
                    target.header(process)
                self.assertIs(observed.exception.reason, expected)
                self.assertEqual(observed.exception.args, ())

    def test_record_errors_are_classified_without_payload(self):
        cases = (
            (b'\xff', target.LeaseHeaderReason.DECODE_INVALID),
            (b'{synthetic-private-payload', target.LeaseHeaderReason.JSON_INVALID),
            (b'{"unexpected":"synthetic-private-payload"}', target.LeaseHeaderReason.RECORD_INVALID),
            (json.dumps({'route': ROUTE, 'ca_pem': 'different-certificate'}).encode(),
             target.LeaseHeaderReason.RECORD_INVALID),
        )
        for raw, expected in cases:
            with self.subTest(reason=expected.value), \
                    patch.object(target, 'header', return_value=raw), \
                    patch.object(target, 'CA_SHA256', CERTIFICATE_SHA), \
                    self.assertRaises(target.LeaseHeaderError) as observed:
                target.read_lease_record(Mock(), 4)
            self.assertIs(observed.exception.reason, expected)
            self.assertEqual(observed.exception.args, ())

    def test_closed_lease_prevents_consumer_start(self):
        process = Mock()
        process.poll.return_value = 1
        raw = json.dumps({'route': ROUTE, 'ca_pem': CERTIFICATE}).encode()
        with patch.object(target, '_failure_stage', target.RoutingStage.ARGUMENTS), \
                patch.object(target, 'header', return_value=raw), \
                patch.object(target, 'CA_SHA256', CERTIFICATE_SHA), \
                patch.object(target.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(target, 'stop') as stop:
            with self.assertRaises(target.LeaseHeaderError) as observed:
                target.execute_under_lease([], 'mailbox', {}, Path('unused-fixture-path'))
            self.assertIs(observed.exception.reason, target.LeaseHeaderReason.LEASE_CLOSED)
            self.assertIs(target._failure_stage, target.RoutingStage.LEASE_HEADER)
            self.assertEqual(spawn.call_count, 1)
            stop.assert_called_once_with(process)

    def test_busy_paused_and_valid_records_keep_protocol(self):
        records = (
            ({'busy': True}, None, 1),
            ({'route': {**ROUTE, 'backend': 'paused'}},
             {'route': {**ROUTE, 'backend': 'paused'}}, 1),
            ({'route': ROUTE, 'ca_pem': CERTIFICATE},
             {'route': ROUTE, 'ca_pem': CERTIFICATE}, None),
        )
        for record, expected, returncode in records:
            process = Mock()
            process.poll.return_value = returncode
            with patch.object(target, 'header', return_value=json.dumps(record).encode()), \
                    patch.object(target, 'CA_SHA256', CERTIFICATE_SHA):
                self.assertEqual(target.read_lease_record(process, 4), expected)

    def test_cli_reason_marker_is_fixed_and_only_for_typed_errors(self):
        cases = (
            (target.LeaseHeaderError(target.LeaseHeaderReason.EOF), 'HEADER_EOF'),
            (target.LeaseHeaderError(UntrustedValue()), 'HEADER_UNKNOWN'),
            (RuntimeError(UntrustedValue()), None),
        )
        for error, expected in cases:
            def fail_main(_selection):
                target._failure_stage = target.RoutingStage.LEASE_HEADER
                raise error

            output = io.StringIO()
            with patch.object(target, '_failure_stage', target.RoutingStage.ARGUMENTS), \
                    patch.object(target.sys, 'argv', ['route', 'mailbox']), \
                    patch.object(target, 'main', side_effect=fail_main), \
                    contextlib.redirect_stderr(output):
                self.assertEqual(target.cli(), 1)
            expected_output = 'AUTOPILOT_DATABASE_ROUTING_FAILED stage=LEASE_HEADER\n'
            if expected is not None:
                expected_output += 'AUTOPILOT_LEASE_HEADER_DIAGNOSTIC reason='+expected+'\n'
            self.assertEqual(output.getvalue(), expected_output)

    def test_non_header_failure_preserves_mailbox_diagnostics(self):
        def fail_main(_selection):
            target._failure_stage = target.RoutingStage.MAILBOX_QUERY
            raise RuntimeError(UntrustedValue())

        output = io.StringIO()
        with patch.object(target, '_failure_stage', target.RoutingStage.ARGUMENTS), \
                patch.object(target.sys, 'argv', ['route', 'mailbox']), \
                patch.object(target, 'main', side_effect=fail_main), \
                contextlib.redirect_stderr(output):
            self.assertEqual(target.cli(), 1)
        self.assertEqual(output.getvalue(),
            'AUTOPILOT_DATABASE_ROUTING_FAILED stage=MAILBOX_QUERY error_type=OTHER sqlstate=NONE\n')


if __name__ == '__main__':
    unittest.main()
