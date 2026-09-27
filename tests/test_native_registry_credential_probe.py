import json
import os
import unittest
from unittest.mock import patch

from ops import native_registry_credential_probe as probe


class CredentialProbeTests(unittest.TestCase):
    def env(self):
        return dict(GITHUB_REPOSITORY='olegmed1-art/bridge-video-free',
                    GITHUB_REF='refs/heads/main', GITHUB_EVENT_NAME='workflow_dispatch',
                    GITHUB_ACTOR='olegmed1-art', GITHUB_TRIGGERING_ACTOR='olegmed1-art',
                    EXPECTED_MAIN='a'*40, REGISTRY_CANDIDATE_DATABASE_URL='private-candidate')

    def test_candidate_is_observed_read_only_with_source_checks_and_no_cutover_claim(self):
        report = dict(audit='NATIVE_REGISTRY_RUNTIME_SCOPE_PASS', production_mutations=False,
                      connection_read_only=True, workflow_pause_exempted=False)
        with patch.dict(os.environ, self.env(), clear=True), \
             patch.object(probe, 'source_check') as source, \
             patch.object(probe.registry, 'observe', return_value=report) as observe, \
             patch('builtins.print') as output:
            probe.main()
            self.assertEqual(source.call_count, 2)
            self.assertEqual(observe.call_args.args[1], 'private-candidate')
            self.assertNotIn('REGISTRY_CANDIDATE_DATABASE_URL', os.environ)
            value = json.loads(output.call_args.args[0])
        self.assertEqual(value['audit'], 'NATIVE_REGISTRY_CREDENTIAL_CANDIDATE_PASS')
        self.assertFalse(value['writer_route_changed'])
        self.assertFalse(value['production_mutations'])
        self.assertNotIn('private-candidate', json.dumps(value))

    def test_untrusted_context_refuses_before_observation(self):
        for key in ('GITHUB_REPOSITORY', 'GITHUB_REF', 'GITHUB_EVENT_NAME',
                    'GITHUB_ACTOR', 'GITHUB_TRIGGERING_ACTOR'):
            env = self.env(); env[key] = 'untrusted'
            with self.subTest(key=key), patch.dict(os.environ, env, clear=True), \
                 patch.object(probe.registry, 'observe') as observe, self.assertRaises(Exception):
                probe.main()
            observe.assert_not_called()

    def test_failure_never_echoes_private_exception_and_never_reports_success(self):
        with patch.object(probe, 'main', side_effect=RuntimeError('password authentication failed private-uri')), \
             patch('builtins.print') as output, self.assertRaises(SystemExit) as failure:
            probe.entrypoint()
        self.assertEqual(failure.exception.code, 2)
        value = json.loads(output.call_args.args[0])
        self.assertEqual(value['reason'], 'authentication')
        self.assertNotIn('private-uri', json.dumps(value))


if __name__ == '__main__': unittest.main()
