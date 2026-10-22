"""Only new launcher seams; SQL response ports are not a DB/auth emulator."""
import copy
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from universal_video import private_one_job_entrypoint as entry
from universal_video.subset_worker import SubsetWorkerAdapter
from universal_video.terminal_finish_contract import Assignment
from database.video_post_finish_receipt import SQL_ASSIGNMENT


class Connection:
    def __init__(self, rows, calls):
        self.rows, self.calls = rows, calls

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, sql, params):
                connection.calls.append((sql, params))
                if sql not in connection.rows:
                    raise AssertionError('UNEXPECTED_SQL_PORT')
                self.row = copy.deepcopy(connection.rows[sql])

            def fetchone(self):
                return self.row

        return Cursor()


class PostFinishEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.claim = dict(batch_id=str(uuid4()), job_id=str(uuid4()),
            lease_token=str(uuid4()), attempt_count=1, stable_job_key='a'*32,
            source_file_id='source-file-123456', algorithm_revision='3.1-free-r25.16',
            output_folder_id='output-folder-123456')
        self.args = dict(queue_dsn='synthetic-dsn', expected_database='synthetic-db',
            expected_login='synthetic-login', claim=self.claim,
            expected_batch_id=self.claim['batch_id'], expected_job_id=self.claim['job_id'],
            expected_lease_token=self.claim['lease_token'], expected_attempt_count=1,
            expected_source_file_id=self.claim['source_file_id'], worker_key='synthetic-worker',
            admission_id=str(uuid4()), source_fingerprint='b'*64,
            subset_adapter=SubsetWorkerAdapter(lambda *_a, **_k: str(uuid4())),
            private_failure_handler=lambda *_a, **_k: None)
        self.assignment = Assignment.from_mapping(dict(schema='diana20-owner-assignment/v1',
            unit='school-autopilot-production-light.service',
            autopilot=dict(task_id=str(uuid4()), worker_id='synthetic-owner',
                lease_epoch=1, intake_source_revision='synthetic-source'),
            video=dict(self.claim, worker_key=self.args['worker_key']),
            admission_id=self.args['admission_id'], source_fingerprint=self.args['source_fingerprint'],
            source_sha256='1'*64, context_sha256='2'*64, request_sha256='3'*64,
            generation_sha256='4'*64, manifest_sha256='5'*64,
            production_source_sha256='6'*64, unit_sha256='7'*64, binding_sha256='8'*64,
            policy_id='synthetic-policy', policy_epoch=1))
        self.base = dict(zip(entry._QUEUE_IDENTITY_COLUMNS,
            ('synthetic-login', 'synthetic-db', True, False, False, False,
             False, True, True, True, False, False, False, False, False, True)))
        self.extra = dict.fromkeys(entry._POST_FINISH_IDENTITY_COLUMNS, True)
        self.extra['has_terminal_dml'] = False
        self.extra['can_finish_subset'] = False
        self.calls = []
        self.rows = {entry._QUEUE_IDENTITY_SQL: self.base,
                     entry._POST_FINISH_IDENTITY_SQL: self.extra,
                     SQL_ASSIGNMENT: {'assignment': self.assignment.value}}

    def connect(self, _dsn):
        return Connection(self.rows, self.calls)

    def run_bound(self, process=lambda *_a, **_k: None, **changes):
        with patch.object(entry, '_connect_queue', side_effect=self.connect), \
             patch.object(entry, '_process_private_claim', side_effect=process), \
             patch.dict(os.environ, {}, clear=True):
            return entry.run_private_one_job(**(self.args | {'assignment': self.assignment} | changes))

    def test_same_assignment_reaches_existing_handoff_and_every_connection_guard(self):
        result = {'job_status': 'REVIEW_READY', 'batch_status': 'REVIEW', 'released_jobs': 0}

        def process(_dsn, claim, key, *, subset_adapter, staging_handoff, **options):
            self.assertIs(staging_handoff.assignment, self.assignment)
            self.assertIs(subset_adapter.observation_provider, self.args['subset_adapter'].observation_provider)
            with staging_handoff.open_connection():
                pass
            subset_adapter.connection_validator(self.connect('synthetic-dsn'))
            self.assertEqual(claim, self.claim)
            self.assertEqual(key, self.args['worker_key'])
            self.assertIs(options['private_failure_handler'], self.args['private_failure_handler'])
            return result

        self.assertIs(self.run_bound(process), result)
        self.assertEqual([q for q, _ in self.calls].count(entry._POST_FINISH_IDENTITY_SQL), 3)
        self.assertEqual([q for q, _ in self.calls].count(SQL_ASSIGNMENT), 1)
        self.assertIn('video_queue.terminal_assignment_v1', self.calls[1][1][0])
        self.assertIn('video_queue.terminal_read_grant_v1', self.calls[1][1][0])
        self.assertIn('public.analysis_candidate', self.calls[1][1][0])
        self.assertIn('TRUNCATE', entry._POST_FINISH_IDENTITY_SQL)

    def test_default_remains_v1_and_does_not_require_new_schema(self):
        self.base['can_finish_private'] = True
        self.rows = {entry._QUEUE_IDENTITY_SQL: self.base}

        def process(_dsn, _claim, _key, *, staging_handoff, **_options):
            self.assertIsNone(staging_handoff.assignment)
            return 'legacy-result'

        self.assertEqual(self.run_bound(process, assignment=None), 'legacy-result')
        self.assertEqual(len(self.calls), 1)

    def test_unbound_or_changed_assignment_refuses_before_connection_or_processor(self):
        wrong_claim = self.assignment.value
        wrong_claim['video']['job_id'] = str(uuid4())
        changes = [dict(assignment=object()), dict(assignment=Assignment.from_mapping(wrong_claim)),
                   dict(admission_id=str(uuid4())), dict(source_fingerprint='f'*64),
                   dict(worker_key='other-worker'), dict(private_failure_handler=None)]
        with patch.object(entry, '_connect_queue') as connect, \
             patch.object(entry, '_process_private_claim') as process, \
             patch.dict(os.environ, {}, clear=True):
            for change in changes:
                with self.subTest(change=list(change)):
                    with self.assertRaises(entry.PrivateOneJobConfigurationError):
                        entry.run_private_one_job(**(self.args | {'assignment': self.assignment} | change))
            connect.assert_not_called()
            process.assert_not_called()

    def test_v1_finish_or_existing_identity_violation_refuses_v2(self):
        for field, value in [('can_finish_private', True), ('is_app_member', True),
                             ('has_pipeline_dml', True), ('active_role_matches_login', False),
                             ('session_login', 'wrong-login'), ('can_stage_private', False)]:
            with self.subTest(field=field):
                saved = dict(self.base)
                self.base[field] = value
                with self.assertRaises(entry.PrivateOneJobConfigurationError):
                    self.run_bound(lambda *_a, **_k: self.fail('PROCESSOR_REACHED'))
                self.base.clear()
                self.base.update(saved)

    def test_missing_function_integer_boolean_or_table_dml_refuses(self):
        for field in entry._POST_FINISH_IDENTITY_COLUMNS:
            for value in ([True, 0, None] if field in ('has_terminal_dml', 'can_finish_subset') else [False, 1, None]):
                with self.subTest(field=field, value=value):
                    saved = self.extra[field]
                    self.extra[field] = value
                    with self.assertRaises(entry.PrivateOneJobConfigurationError):
                        self.run_bound(lambda *_a, **_k: self.fail('PROCESSOR_REACHED'))
                    self.extra[field] = saved

    def test_inexact_extra_row_shape_refuses(self):
        for row in [None, {}, dict(self.extra, unexpected=False), tuple(self.extra.values())[:-1]]:
            with self.subTest(row=row):
                self.rows[entry._POST_FINISH_IDENTITY_SQL] = row
                with self.assertRaises(entry.PrivateOneJobConfigurationError):
                    self.run_bound(lambda *_a, **_k: self.fail('PROCESSOR_REACHED'))

    def test_missing_enrollment_refuses_before_processor(self):
        self.rows[SQL_ASSIGNMENT] = {'assignment': None}
        with self.assertRaises(entry.PrivateOneJobConfigurationError):
            self.run_bound(lambda *_a, **_k: self.fail('PROCESSOR_REACHED'))

    def test_mode_is_exact_boolean_before_connect(self):
        with patch.object(entry, '_connect_queue') as connect:
            for value in [0, 1, None, 'true']:
                with self.subTest(value=value):
                    with self.assertRaises(entry.PrivateOneJobConfigurationError):
                        with entry._open_checked_queue('synthetic-dsn', expected_login='synthetic-login',
                                expected_database='synthetic-db', post_finish=value):
                            self.fail('CONTEXT_ENTERED')
            connect.assert_not_called()

    def test_exact_claim_can_use_v2_capabilities_without_assignment_issuance(self):
        full = dict(self.claim, sequence=1, source_folder_id='source-folder-123456',
            work_folder_id='work-folder-123456', processing_profile='synthetic-profile',
            source_name='synthetic.mp4', source_mime_type='video/mp4', source_size_bytes=10,
            source_checksum=None, is_canary=True)
        claim_sql = 'SELECT * FROM video_queue.subset_claim_job(%s,%s,%s,%s,%s)'
        self.rows[claim_sql] = full
        captured = []
        with patch.object(entry, '_connect_queue', side_effect=self.connect), \
             patch.dict(os.environ, {}, clear=True):
            result = entry.claim_exact_private_job(queue_dsn='synthetic-dsn',
                expected_database='synthetic-db', expected_login='synthetic-login',
                expected_batch_id=full['batch_id'], expected_job_id=full['job_id'],
                expected_source_file_id=full['source_file_id'], expected_attempt_count=1,
                worker_key=self.args['worker_key'], processing_profile=full['processing_profile'],
                algorithm_revision=full['algorithm_revision'], claim_publisher=captured.append,
                post_finish=True)
        self.assertEqual(result, full)
        self.assertEqual(captured, [full])
        self.assertEqual([q for q, _ in self.calls],
                         [entry._QUEUE_IDENTITY_SQL, entry._POST_FINISH_IDENTITY_SQL, claim_sql])


if __name__ == '__main__':
    unittest.main()
