"""All dependencies mocked; no live connection, environment secret or network."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from ops import neon_backup_preflight_once as once


class PreflightOnceTests(unittest.TestCase):
    @staticmethod
    def passed_preflight(pg, *, gates):
        gates.update({k: 'PASS' for k in gates})

    def test_workflow_is_dispatch_only_single_bounded_job_with_only_maintenance_secret(self):
        text = Path('.github/workflows/native-registry-credential-probe.yml').read_text()
        self.assertIn('timeout-minutes: 2', text)
        self.assertIn('github.run_attempt == 1', text)
        self.assertIn('inputs.expected_review_sha == github.sha', text)
        self.assertIn('ref: ${{ github.sha }}', text)
        self.assertIn('100s python -m ops.neon_backup_preflight_once', text)
        self.assertEqual(text.count('secrets.'), 1)
        self.assertIn('secrets.LIGHT_MAINTENANCE_DATABASE_URL', text)
        for forbidden in ('pull_request:', 'push:', 'schedule:', 'upload-artifact',
                          'pg_dump', 'pg_restore', 'NEON_BACKUP_PASSPHRASE',
                          'secrets.NEON_DATABASE_URL', 'ops.native_registry_credential_probe'):
            self.assertNotIn(forbidden, text)

    def env(self):
        return dict(GITHUB_REPOSITORY=once.REPOSITORY, GITHUB_REF='refs/heads/' + once.BRANCH,
                    GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_ACTOR='olegmed1-art',
                    GITHUB_TRIGGERING_ACTOR='olegmed1-art', GITHUB_RUN_ATTEMPT='1',
                    GITHUB_SHA='a'*40, EXPECTED_REVIEW='a'*40, EXPECTED_MAIN=once.MAIN,
                    GITHUB_RUN_ID='123', DATABASE_URL='synthetic-not-a-secret')

    def test_context_requires_exact_actor_event_ref_sha_first_attempt_and_checkout(self):
        with patch.dict(os.environ, self.env(), clear=True), \
             patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'a'*40+'\n')):
            self.assertEqual(once.context(), 'a'*40)
        for key in ('GITHUB_REPOSITORY', 'GITHUB_REF', 'GITHUB_EVENT_NAME', 'GITHUB_ACTOR',
                    'GITHUB_TRIGGERING_ACTOR', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA',
                    'EXPECTED_REVIEW', 'EXPECTED_MAIN', 'GITHUB_RUN_ID'):
            env = self.env(); env[key] = 'wrong'
            with self.subTest(key=key), patch.dict(os.environ, env, clear=True), \
                 patch.object(once.backup, 'parameters') as parameters, \
                 patch.object(once, 'check_main') as check:
                row = once.observe()
            self.assertEqual(row['status'], 'FAIL')
            parameters.assert_not_called(); check.assert_not_called()
        with patch.dict(os.environ, self.env(), clear=True), \
             patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'b'*40)), \
             self.assertRaises(ValueError):
            once.context()

    def test_success_calls_preflight_exactly_once_never_backup_mode_and_reports_safe_gates(self):
        with patch.dict(os.environ, self.env(), clear=True), patch.object(once, 'context', return_value='a'*40), \
             patch.object(once, 'check_main') as main_check, \
             patch.object(once.backup, 'parameters', return_value={'safe': 'synthetic'}) as parameters, \
             patch.object(once.backup, 'preflight', side_effect=self.passed_preflight) as preflight, \
             patch.object(once.backup, 'main') as modes:
            row = once.observe()
            self.assertNotIn('DATABASE_URL', os.environ)
        preflight.assert_called_once_with({'safe': 'synthetic'}, gates=row['gates'])
        parameters.assert_called_once_with('synthetic-not-a-secret')
        modes.assert_not_called(); self.assertEqual(main_check.call_count, 2)
        self.assertEqual(row['status'], 'PASS')
        self.assertEqual(set(row['gates'].values()), {'PASS'})
        self.assertEqual(row['expected_identity']['branch'], 'br-aged-mud-b1i64914')
        self.assertNotIn('synthetic', json.dumps(row))
        for key in ('dump', 'upload', 'restore', 'production_writes', 'retry'):
            self.assertFalse(row[key])

    def test_failure_is_sanitized_not_retried_and_gates_are_not_proven(self):
        for stage in ('main_before', 'credential_policy', 'auth_identity_acl_rls_readonly', 'main_after'):
            with self.subTest(stage=stage), patch.dict(os.environ, self.env(), clear=True), \
                 patch.object(once, 'context', return_value='a'*40), \
                 patch.object(once, 'check_main', side_effect=
                     [RuntimeError('private')] if stage=='main_before' else
                     [None, RuntimeError('private')] if stage=='main_after' else [None,None]), \
                 patch.object(once.backup, 'parameters', side_effect=
                     ValueError('private') if stage=='credential_policy' else None), \
                 patch.object(once.backup, 'preflight', side_effect=
                     RuntimeError('private DSN') if stage=='auth_identity_acl_rls_readonly' else self.passed_preflight) as preflight:
                row = once.observe()
            self.assertEqual(row['status'], 'FAIL')
            self.assertEqual(row['phase'], stage)
            self.assertLessEqual(preflight.call_count, 1)
            self.assertNotIn('private', json.dumps(row))
            self.assertEqual(set(row['gates'].values()), {'PASS'} if stage=='main_after' else {'NOT_PROVEN'})


if __name__ == '__main__':
    unittest.main()
