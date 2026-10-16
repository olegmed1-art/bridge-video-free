"""Fail-closed checks before the synthetic PG18 CI rehearsal can connect."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import manual_one_job_0061_0062_pg18 as rehearsal


class CiServiceGuardTest(unittest.TestCase):
    def test_local_context_is_rejected_before_any_command_or_connection(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(rehearsal, '_run') as run, \
                patch.object(rehearsal, '_connect') as connect:
            with self.assertRaisesRegex(RuntimeError, 'CI_SERVICE_CONTEXT_INVALID'):
                rehearsal._ci_service(5432)
            run.assert_not_called()
            connect.assert_not_called()

    def test_wrong_checkout_is_rejected_before_connection(self):
        env = dict(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='pull_request',
                   RUNNER_OS='Linux',
                   GITHUB_REPOSITORY='olegmed1-art/bridge-video-free',
                   ONE_JOB_EXPECTED_HEAD_SHA='expected')
        with patch.dict(os.environ, env, clear=True), \
                patch.object(rehearsal, '_run', return_value='other') as run, \
                patch.object(rehearsal, '_connect') as connect:
            with self.assertRaisesRegex(RuntimeError, 'CI_SERVICE_CHECKOUT_SHA_INVALID'):
                rehearsal._ci_service(5432)
            run.assert_called_once()
            connect.assert_not_called()

    def test_foreign_dsn_is_rejected_before_connection(self):
        env = dict(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='pull_request',
                   RUNNER_OS='Linux',
                   GITHUB_REPOSITORY='olegmed1-art/bridge-video-free',
                   ONE_JOB_EXPECTED_HEAD_SHA='expected',
                   ONE_JOB_CI_ADMIN_DATABASE_URL='postgresql://postgres:postgres@db.example:5432/postgres')
        with patch.dict(os.environ, env, clear=True), \
                patch.object(rehearsal, '_run', return_value='expected'), \
                patch.object(rehearsal, '_connect') as connect:
            with self.assertRaisesRegex(RuntimeError, 'CI_SERVICE_DSN_INVALID'):
                rehearsal._ci_service(5432)
            connect.assert_not_called()

    def test_synthetic_password_survives_cleared_environment_and_ack_loss(self):
        token = rehearsal._SYNTHETIC_PASSWORD.set('postgres')
        try:
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(rehearsal.psycopg, 'connect') as connect:
                rehearsal._connect(5432, 'onejob_worker')
                self.assertEqual(connect.call_args.kwargs['password'], 'postgres')
                self.assertIn('onejob_approver:postgres@',
                              rehearsal._test_dsn(5432, 'onejob_approver'))
                with self.assertRaisesRegex(OSError,
                                            'SYNTHETIC_ACK_LOST_AFTER_REAL_COMMIT'):
                    with rehearsal._LostAckConnection(5432):
                        pass
                self.assertEqual(connect.call_args.kwargs['password'], 'postgres')
                self.assertNotIn('PGPASSWORD', os.environ)
        finally:
            rehearsal._SYNTHETIC_PASSWORD.reset(token)

    def test_ci_route_passes_only_synthetic_password_and_resets_context(self):
        env = dict(GITHUB_ACTIONS='true', GITHUB_EVENT_NAME='pull_request',
                   RUNNER_OS='Linux',
                   GITHUB_REPOSITORY='olegmed1-art/bridge-video-free',
                   ONE_JOB_EXPECTED_HEAD_SHA='expected',
                   ONE_JOB_CI_ADMIN_DATABASE_URL=
                   'postgresql://postgres:postgres@localhost:5432/postgres')
        with patch.dict(os.environ, env, clear=True), \
                patch.object(rehearsal, '_run', return_value='expected'), \
                patch.object(rehearsal.shutil, 'which', return_value='psql'), \
                patch.object(rehearsal, '_connect') as connect, \
                patch.object(rehearsal, '_exercise') as exercise:
            connect.return_value.__enter__.return_value.execute.return_value \
                .fetchone.return_value.values.return_value = (
                    180000, 5432, 'postgres', 'postgres', None, False)
            rehearsal._ci_service(5432)
            self.assertEqual(exercise.call_args.args[-1], 'postgres')
            self.assertNotIn('PGPASSWORD', os.environ)
        self.assertIsNone(rehearsal._SYNTHETIC_PASSWORD.get())


class ApprovedInventoryTest(unittest.TestCase):
    def test_reviewed_inventory_matches_current_checkout(self):
        files = rehearsal._approved_sql_inventory()
        self.assertEqual(files[-1].name, '0060_fixed_subset_admission.sql')
        self.assertEqual(files[0].name, '0001_global_registry.sql')

    def test_unchanged_0060_can_move_to_candidates(self):
        original = rehearsal.ROOT
        with tempfile.TemporaryDirectory(prefix='one-job-inventory-', dir=original) as tmp:
            root = Path(tmp)
            migrations = root / 'database' / 'migrations'
            candidates = root / 'database' / 'candidates'
            fixtures = root / 'database' / 'fixtures'
            for directory in (migrations, candidates, fixtures):
                directory.mkdir(parents=True)
            for path in rehearsal._approved_sql_inventory():
                destination = candidates if path.name.startswith('0060_') else migrations
                shutil.copyfile(path, destination / path.name)
                if path.with_suffix('').is_dir():
                    shutil.copytree(path.with_suffix(''),
                                    destination / path.stem)
            shutil.copyfile(original / 'database' / 'fixtures' /
                            'one_job_pg18_approved_inventory.json',
                            fixtures / 'one_job_pg18_approved_inventory.json')
            with patch.object(rehearsal, 'ROOT', root):
                files = rehearsal._approved_sql_inventory()
                self.assertEqual(files[-1].parent, candidates)
                (migrations / files[0].name).write_text('-- altered\n', encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'ONE_JOB_INVENTORY_HASH_MISMATCH'):
                    rehearsal._approved_sql_inventory()
                shutil.copyfile(original / 'database' / 'migrations' / files[0].name,
                                migrations / files[0].name)
                component = (migrations / '0058_universal_video_terminal_v2_gate' /
                             '001_apply_terminal_v2.sql')
                component.write_text('-- altered component\n', encoding='utf-8')
                with self.assertRaisesRegex(RuntimeError, 'ONE_JOB_INVENTORY_HASH_MISMATCH'):
                    rehearsal._approved_sql_inventory()


if __name__ == '__main__':
    unittest.main()
