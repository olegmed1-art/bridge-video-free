import os
import json
import unittest
from unittest.mock import Mock, patch

from ops import recovery_registry_preflight as preflight


class WriterPreflightTests(unittest.TestCase):
    def environment(self, event):
        return dict(GITHUB_SHA='a'*40, GITHUB_WORKFLOW_SHA='a'*40,
                    GITHUB_WORKFLOW_REF=preflight.WORKFLOW_REF,
                    GITHUB_REPOSITORY=preflight.REPOSITORY, GITHUB_REF='refs/heads/main',
                    GITHUB_ACTOR=preflight.OWNER, GITHUB_TRIGGERING_ACTOR=preflight.OWNER,
                    GITHUB_EVENT_NAME=event)

    def test_current_owner_push_and_dispatch_are_supported_without_weakening_maintenance(self):
        api = Mock()
        api.get.return_value = dict(ref='refs/heads/main', object=dict(type='commit', sha='a'*40))
        for event in ('push', 'workflow_dispatch'):
            with patch.dict(os.environ, self.environment(event), clear=True):
                preflight.check_source('a'*40, api)
        self.assertEqual(api.get.call_count, 2)
        api.get.assert_called_with('/git/ref/heads/main')

    def test_wrong_context_and_stale_main_refuse(self):
        for key in self.environment('push'):
            env = self.environment('push'); env[key] = 'wrong'
            api = Mock()
            with self.subTest(key=key), patch.dict(os.environ, env, clear=True), self.assertRaises(Exception):
                preflight.check_source('a'*40, api)

            api.get.assert_not_called()
        for row in (dict(ref='wrong'), dict(ref='refs/heads/main', object=dict(type='commit', sha='b'*40))):
            api = Mock(); api.get.return_value = row
            with patch.dict(os.environ, self.environment('push'), clear=True), self.assertRaises(Exception):
                preflight.check_source('a'*40, api)

    def test_observation_requires_current_source_before_and_after(self):
        api = Mock()
        current = dict(ref='refs/heads/main', object=dict(type='commit', sha='a'*40))
        stale = dict(ref='refs/heads/main', object=dict(type='commit', sha='b'*40))
        env = {**self.environment('push'), 'EXPECTED_MAIN': 'a'*40, 'GH_TOKEN': 'private-token',
               'NATIVE_REGISTRY_DATABASE_URL': 'private-uri'}
        with patch.dict(os.environ, env, clear=True), patch.object(preflight, 'API', return_value=api), \
             patch.object(preflight.registry, 'observe', return_value={'production_mutations': False}) as observe, \
             patch('builtins.print') as output:
            api.get.side_effect = [current, stale]
            with self.assertRaises(Exception): preflight.main()
            output.assert_not_called()
            self.assertEqual(observe.call_count, 1)
        with patch.dict(os.environ, env, clear=True), patch.object(preflight, 'API', return_value=api), \
             patch.object(preflight.registry, 'observe', return_value={'production_mutations': False}), \
             patch('builtins.print') as output:
            api.get.side_effect = [current, current]
            preflight.main()
            self.assertEqual(json.loads(output.call_args.args[0])['audit'], 'RECOVERY_REGISTRY_PREFLIGHT_PASS')


if __name__ == '__main__': unittest.main()
