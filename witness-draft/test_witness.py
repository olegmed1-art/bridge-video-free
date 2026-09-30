import unittest
import json
from unittest.mock import patch
import witness


class Contract(unittest.TestCase):
    def test_unknown_never_ready(self):
        self.assertEqual(witness.decision({}), 'BLOCKED_CAPABILITY')

    def test_equal_inode_not_sufficient(self):
        self.assertEqual(witness.decision({'inode_match': 'YES'}), 'BLOCKED_CAPABILITY')

    def test_mismatch_without_host_target_proof_cannot_recommend_recreation(self):
        self.assertEqual(witness.decision({'inode_match': 'NO', 'observation_stable': True}), 'BLOCKED_CAPABILITY')

    def test_unstable_mismatch_is_unknown(self):
        self.assertEqual(witness.decision({'inode_match': 'NO'}), 'BLOCKED_CAPABILITY')

    def test_configuration_precedence_and_redaction(self):
        env = {'BRIDGE_VIDEO_QUEUE_DATABASE_URL': 'SECRET_CANARY',
               'BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE': witness.RESIDENT}
        self.assertEqual(witness.configuration_mode(env), 'DIRECT_OVERRIDE')
        self.assertNotIn('SECRET_CANARY', witness.configuration_mode(env))
        env['BRIDGE_VIDEO_QUEUE_DATABASE_URL'] = '  '
        self.assertEqual(witness.configuration_mode(env), 'EXPECTED_FILE')

    def test_fallbacks(self):
        for env, expected in [({}, 'UNCONFIGURED'),
            ({'BRIDGE_APP_DATABASE_URL': 'x', 'BRIDGE_WORKER_DATABASE_URL': 'y'}, 'APP_FALLBACK'),
            ({'BRIDGE_WORKER_DATABASE_URL': 'y'}, 'WORKER_FALLBACK'),
            ({'BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE': '/other'}, 'OTHER_FILE'),
            ({'BRIDGE_VIDEO_QUEUE_DATABASE_URL': None}, 'UNKNOWN')]:
            with self.subTest(expected=expected):
                self.assertEqual(witness.configuration_mode(env), expected)

    def test_valid_probe(self):
        result = dict(production=True, schema=True, function=True, claimable=0, leased=0)
        self.assertEqual(witness.sanitize_probe(json.dumps(result)), result)

    def test_bad_probe_rejected(self):
        good = dict(production=True, schema=True, function=True, claimable=0, leased=0)
        for delta in [dict(claimable=True), dict(leased=-1), dict(leased='0'),
                      dict(production='true'), dict(secret='SECRET_CANARY'),
                      dict(schema=False)]:
            with self.subTest(delta=delta):
                with self.assertRaises(ValueError):
                    witness.sanitize_probe(json.dumps(good | delta))

    def test_duplicate_and_missing_fields(self):
        for raw in ['{}', '[]', '{"production":true,"production":false}',
                    '{"production":true,"schema":true,"function":true}', 'x' * 1025]:
            with self.assertRaises(ValueError):
                witness.sanitize_probe(raw)

    def test_error_secret_not_returned(self):
        with patch('witness.run', side_effect=RuntimeError('SECRET_CANARY')):
            result = witness.collect()
        self.assertNotIn('SECRET_CANARY', str(result))
        self.assertEqual(result['PRECANARY_READY'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
