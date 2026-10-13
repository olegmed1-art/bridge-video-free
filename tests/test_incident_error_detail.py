"""Synthetic fixtures only: no DNS, real psycopg, or database required."""
import json
import random
import unittest

from bridge_school_api.incident_error_detail import (
    MAX_MESSAGE_CHARS, MAX_REPORTED_ATTEMPTS, connection_error_detail, has_explicit_timeout,
)

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
