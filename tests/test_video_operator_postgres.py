"""One opt-in PG18 rehearsal: synthetic bytes, actual login/SQL/transactions.

Only the transport's production-Neon DSN validator is replaced by an exact
loopback validator. psycopg.connect, session/privilege verification, all writers,
commit, rollback and the internal reader are real. No private fixture is read.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

import fitz
import psycopg
from psycopg import IsolationLevel, sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from bridge_contracts.video_canon_replay import digest
from bridge_contracts.video_local_evidence import LocalEvidenceReader
from bridge_contracts.video_replay_input import sha256
from database import video_operator_staging as operator
from database.video_result_persistence import SCHOOL_STABLE_NAME, _stable_uuid
from database.video_staging_handoff import CompletedGeneration, GenerationPins, handoff_completed_video
from database.video_staging_read import read_internal_school_video_candidate
from tests.test_video_replay_input import full_fixture


OWNER = 'rehearsal_owner'
WORKER = 'bridge_school_worker_principal'
APP = 'bridge_school_app_principal'
PASSWORD = 'synthetic-operator-ci-only'
REVISION = 'synthetic-operator-sql-rehearsal-v1'
OUTPUT = Path('operator-rehearsal-output')
TABLES = ('object_registry', 'changeset', 'ingestion_run', 'ingestion_item',
    'learning_interaction', 'episode', 'decision', 'asset', 'media_asset',
    'asset_location', 'source', 'source_asset', 'source_identity', 'transcript',
    'transcript_segment', 'analysis_run', 'analysis_run_input', 'analysis_run_output',
    'evidence', 'evidence_link', 'artifact', 'artifact_version', 'analysis_candidate',
    'domain_event', 'outbox_message', 'canon_activation', 'output_publication')


def local_dsn(user):
    assert user in (OWNER, WORKER, APP)
    return f'postgresql://{user}:{PASSWORD}@127.0.0.1:55434/operator_rehearsal'


def validate_loopback(value):
    parts = conninfo_to_dict(value)
    assert parts == {'user': parts.get('user'), 'password': PASSWORD, 'host': '127.0.0.1',
                     'port': '55434', 'dbname': 'operator_rehearsal'}
    assert parts['user'] in (OWNER, WORKER, APP)
    return value


@contextmanager
def owner():
    value = validate_loopback(local_dsn(OWNER))
    with psycopg.connect(value, connect_timeout=5) as connection:
        assert connection.execute('SELECT current_database(),session_user,inet_server_port()').fetchone() == (
            'operator_rehearsal', OWNER, 5432)
        yield connection


def counts():
    with owner() as connection:
        return {table: connection.execute(sql.SQL('SELECT count(*) FROM public.{}').format(
            sql.Identifier(table))).fetchone()[0] for table in TABLES}


@pytest.fixture(scope='module', autouse=True)
def disposable_database():
    if os.getenv('BRIDGE_OPERATOR_REHEARSAL') != 'github-ephemeral-pg18-v1':
        pytest.skip('explicit ephemeral PG18 rehearsal not enabled; no DB was contacted')
    assert os.getenv('GITHUB_ACTIONS') == 'true'
    assert os.getenv('GITHUB_REPOSITORY') == 'olegmed1-art/bridge-video-free'
    assert os.getenv('GITHUB_REF') == 'refs/heads/codex/video-operator-sql-rehearsal-20261013'
    assert os.getenv('GITHUB_RUN_ATTEMPT') == '1'
    assert not any(os.getenv(k) for k in ('DATABASE_URL', 'BRIDGE_WORKER_DATABASE_URL',
        'BRIDGE_API_DATABASE_URL', 'BRIDGE_VIDEO_REAL_FIXTURE_DIR', 'GOOGLE_DRIVE_OAUTH_JSON'))
    OUTPUT.mkdir(exist_ok=True)
    with owner() as connection:
        assert int(connection.execute('SHOW server_version_num').fetchone()[0]) // 10000 == 18
        school = connection.execute("SELECT school_id FROM school WHERE stable_name=%s AND status='active'",
                                    (SCHOOL_STABLE_NAME,)).fetchone()[0]
        for user in (WORKER, APP):
            attributes = connection.execute('''SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
                                               FROM pg_roles WHERE rolname=%s''', (user,)).fetchone()
            assert attributes == (False, False, False, False, False)
            # Only this disposable container; capabilities come from unchanged migrations.
            connection.execute(sql.SQL('ALTER ROLE {} LOGIN PASSWORD {}').format(
                sql.Identifier(user), sql.Literal(PASSWORD)))
        algorithm = connection.execute("SELECT algorithm_id FROM algorithm WHERE school_id=%s AND stable_key='bridge-video-master-analysis'",
                                       (school,)).fetchone()
        if algorithm is None:
            algorithm = connection.execute("INSERT INTO algorithm(school_id,stable_key,name) VALUES (%s,'bridge-video-master-analysis','Synthetic operator SQL rehearsal') RETURNING algorithm_id",
                                           (school,)).fetchone()
        connection.execute('''INSERT INTO algorithm_version(algorithm_id,version_no,version_label)
            SELECT %s,COALESCE(max(version_no),0)+1,%s FROM algorithm_version WHERE algorithm_id=%s''',
            (algorithm[0], REVISION, algorithm[0]))
        migrations = connection.execute('SELECT migration_key,checksum FROM schema_migration ORDER BY migration_key').fetchall()
    with psycopg.connect(validate_loopback(local_dsn(WORKER)), connect_timeout=5) as connection:
        session = connection.execute('SELECT session_user,current_user,current_database(),inet_server_port()').fetchone()
        assert session == (WORKER, WORKER, 'operator_rehearsal', 5432)
        assert not any(connection.execute("SELECT has_table_privilege(current_user,'public.canon_activation',%s)",
                                          (privilege,)).fetchone()[0] for privilege in ('INSERT', 'UPDATE', 'DELETE'))
    (OUTPUT/'database-identity.json').write_text(json.dumps({
        'source_kind': 'SYNTHETIC_ONLY', 'sql_mocks': False, 'server_session': session,
        'migration_count': len(migrations), 'migration_history_sha256': sha256(json.dumps(migrations).encode()),
        'endpoint_validator_override': 'TEST_ONLY_EXACT_LOOPBACK', 'new_capability_grants': False,
        'worker_canon_dml_allowed': False}, indent=2)+'\n', encoding='utf-8')
    before = counts()['canon_activation']
    yield
    assert counts()['canon_activation'] == before
    with owner() as connection:
        for user in (WORKER, APP):
            connection.execute(sql.SQL('ALTER ROLE {} NOLOGIN PASSWORD NULL').format(sql.Identifier(user)))
        assert connection.execute('SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s) AND rolcanlogin',
                                  ([WORKER, APP],)).fetchone()[0] == 0
    (OUTPUT/'test-cleanup.json').write_text(json.dumps({
        'temporary_runtime_logins_disabled': True, 'temporary_passwords_cleared': True,
        'canon_count_unchanged': True, 'container_removal': 'workflow_cleanup_step_required'}, indent=2)+'\n')


@pytest.fixture(scope='module', autouse=True)
def exact_test_endpoint(disposable_database):
    # This is the sole replaced production function. It grants no SQL permission.
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(operator, 'normalize_dsn', validate_loopback)
        yield


def make_generation(root, name):
    master, _, selection = full_fixture()
    master.update(job_id='synthetic-operator-'+name, algorithmRevision=REVISION,
                  algorithmVersion='synthetic-only', createdAt='2026-01-01T00:00:00Z')
    master['source'].update(driveId='synthetic-source-'+name, sha256=sha256(('media-claim-'+name).encode()),
        name='synthetic.mp4', sizeBytes=123, durationSeconds=30, mimeType='video/mp4')
    frame = b'SYNTHETIC FRAME, NOT REAL VIDEO'
    master['screenshots'][0]['sha256'] = sha256(frame)
    master_bytes = json.dumps(master, ensure_ascii=False, indent=2).encode()
    with fitz.open() as document:
        document.new_page()
        document.embfile_add('master_analysis.json', master_bytes)
        report_bytes = document.tobytes()
    report_id = 'synthetic-report-'+name
    done = {'status': 'AI_DONE', 'job_id': master['job_id'], 'algorithmRevision': REVISION,
        'original': {'driveId': master['source']['driveId']},
        'masterPdf': {'driveId': report_id, 'name': 'synthetic.pdf', 'sizeBytes': len(report_bytes),
            'sha256': sha256(report_bytes), 'masterJsonEmbedded': True, 'masterJsonSha256': sha256(master_bytes)}}
    done_bytes = json.dumps(done).encode()
    selection.pop('speaker_map_raw_sha256')
    selection.update(source_raw_sha256=sha256(master_bytes), source_document_drive_id=report_id)
    root.mkdir(parents=True, exist_ok=True)
    (root/'master.json').write_bytes(master_bytes)
    (root/'frame.bin').write_bytes(frame)
    index = {'schema': 'video-local-evidence-index-v1',
        'parent': {'path': 'master.json', 'raw_sha256': sha256(master_bytes), 'canonical_sha256': digest(master)},
        'source': {'path': None, 'video_file_id': master['source']['driveId'], 'sha256': master['source']['sha256']},
        'frames': [{'path': 'frame.bin', 'evidence_id': 'f1', 'sha256': sha256(frame)}]}
    encoded = json.dumps(index).encode()
    (root/'index.json').write_bytes(encoded)
    pins = GenerationPins(master['job_id'], REVISION, master['source']['driveId'], master['source']['sha256'],
        report_id, sha256(report_bytes), sha256(master_bytes), sha256(done_bytes))
    generation = CompletedGeneration(pins, master_bytes, done_bytes, json.dumps(selection).encode(),
        LocalEvidenceReader(root/'index.json', sha256(encoded)))
    return generation, report_bytes


class ObservedTransport(operator.OperatorStagingTransport):
    """Read-only observations around real guards; no replacement of any SQL writer."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.observed = []
        self.sql_errors = []
        self.last_connection = None

    @contextmanager
    def open_writer(self, generation):
        with super().open_writer(generation) as connection:
            self.last_connection = connection
            try:
                yield connection
            except psycopg.Error as exc:
                # Observe the actual server failure; do not change or replace it.
                self.sql_errors.append({'sqlstate': exc.sqlstate,
                                        'constraint': exc.diag.constraint_name})
                raise

    def verify_writer(self, connection, generation, *, result=None):
        super().verify_writer(connection, generation, result=result)
        self.observed.append(connection.execute(
            'SELECT session_user,current_user,pg_backend_pid(),txid_current()').fetchone())


def transport(source, *, user=WORKER):
    generation, report_bytes = source
    return ObservedTransport(generation=generation, report_bytes=report_bytes, worker_dsn=local_dsn(user))


def assert_blocked(result):
    assert result['status'] == 'BLOCKED'
    assert result['canon_write_allowed'] is result['teacher_access_granted'] is False
    assert not {'candidate_ids', 'analysis_run_id', 'source_id', 'replay_receipt_id'} & result.keys()


@pytest.fixture(scope='module')
def committed(tmp_path_factory, exact_test_endpoint):
    selected = make_generation(tmp_path_factory.mktemp('real-sql'), 'committed')
    adapter = transport(selected)
    receipt = handoff_completed_video(transport=adapter)
    assert receipt['status'] == 'COMMITTED_STAGING', receipt
    assert receipt['replay_status'] == 'BLOCKED'
    assert receipt['canon_write_allowed'] is receipt['teacher_access_granted'] is False
    assert len(adapter.observed) == 2 and adapter.observed[0] == adapter.observed[1]
    assert adapter.observed[0][:2] == (WORKER, WORKER)
    assert adapter._connection is None and adapter.last_connection.closed
    (OUTPUT/'committed.json').write_text(json.dumps({'receipt': receipt,
        'same_backend_and_outer_transaction': adapter.observed}, indent=2)+'\n')
    return selected, receipt


def test_committed_records_read_on_separate_authenticated_session(committed):
    _, receipt = committed
    with psycopg.connect(validate_loopback(local_dsn(APP)), autocommit=True, connect_timeout=5) as connection:
        assert connection.execute('SELECT session_user,current_user').fetchone() == (APP, APP)
        connection.read_only = True
        connection.isolation_level = IsolationLevel.REPEATABLE_READ
        row = connection.execute("SELECT analysis_candidate_id FROM analysis_candidate WHERE analysis_run_id=%s AND candidate_type='video_artifact_source_observation'",
                                 (receipt['analysis_run_id'],)).fetchone()
        assert row
        read = read_internal_school_video_candidate(connection, str(row[0]))
    assert read['status'] == 'READABLE_STAGING', read
    assert read['speaker_identity_status'] == 'UNKNOWN' and read['media_review_status'] == 'UNVERIFIED'
    assert read['replay_status'] == 'BLOCKED' and read['replay_gap_codes']
    assert read['accepted_canon'] == [] and not read['canon_write_allowed'] and not read['student_delivery_allowed']
    (OUTPUT/'internal-read.json').write_text(json.dumps(read, indent=2)+'\n')


def test_duplicate_reprocess_uses_same_ids_and_no_extra_rows(committed):
    selected, receipt = committed
    before = counts()
    again = handoff_completed_video(transport=transport(selected))
    assert again == receipt
    assert counts() == before


def test_native_candidate_constraint_failure_rolls_back_complete_graph(tmp_path):
    selected = make_generation(tmp_path, 'rollback')
    with owner() as connection:
        connection.execute("ALTER TABLE analysis_candidate ADD CONSTRAINT rehearsal_reject_candidate CHECK (candidate_type <> 'video_artifact_source_observation') NOT VALID")
    before = counts()
    try:
        adapter = transport(selected)
        result = handoff_completed_video(transport=adapter)
        assert_blocked(result)
        assert result['commit_outcome'] == 'UNKNOWN'
        assert len(adapter.observed) == 1  # real first guard, then SQL constraint error
        assert adapter.sql_errors == [{'sqlstate': '23514', 'constraint': 'rehearsal_reject_candidate'}]
        assert adapter.last_connection.closed
        assert counts() == before  # source, result, identity, events/outbox all rolled back
        (OUTPUT/'rollback.json').write_text(json.dumps({'receipt': result,
            'actual_sql_error': adapter.sql_errors, 'graph_counts_equal': True,
            'connection_closed': True}, indent=2)+'\n')
    finally:
        with owner() as connection:
            connection.execute('ALTER TABLE analysis_candidate DROP CONSTRAINT rehearsal_reject_candidate')


@pytest.mark.parametrize('user', [OWNER, APP])
def test_actual_wrong_session_principal_is_denied(tmp_path, user):
    before = counts()
    result = handoff_completed_video(transport=transport(make_generation(tmp_path, 'wrong-'+user), user=user))
    assert_blocked(result)
    assert counts() == before


def test_set_role_does_not_replace_authenticated_session_identity(tmp_path):
    class RoleSwitchedTransport(ObservedTransport):
        @contextmanager
        def open_writer(self, generation):
            with super().open_writer(generation) as connection:
                connection.execute('SET ROLE bridge_school_worker_principal')
                assert connection.execute('SELECT session_user,current_user').fetchone() == (OWNER, WORKER)
                yield connection

    generation, report = make_generation(tmp_path, 'role-switched')
    adapter = RoleSwitchedTransport(generation=generation, report_bytes=report, worker_dsn=local_dsn(OWNER))
    before = counts()
    assert_blocked(handoff_completed_video(transport=adapter))
    assert counts() == before and adapter.last_connection.closed


def test_actual_revoked_worker_membership_is_denied(tmp_path):
    selected = make_generation(tmp_path, 'revoked')
    before = counts()
    with owner() as connection:
        connection.execute('REVOKE bridge_school_worker FROM bridge_school_worker_principal')
    try:
        assert_blocked(handoff_completed_video(transport=transport(selected)))
        assert counts() == before
    finally:
        with owner() as connection:
            # Restore only the existing migration grant inside the disposable service.
            connection.execute('GRANT bridge_school_worker TO bridge_school_worker_principal')


@pytest.mark.parametrize('entity', ['source', 'media', 'report'])
def test_actual_foreign_school_collision_is_denied(tmp_path, entity):
    selected = make_generation(tmp_path, 'foreign-'+entity)
    pins = selected[0].pins
    other = uuid4()
    with owner() as connection:
        connection.execute('INSERT INTO school(school_id,stable_name) VALUES (%s,%s)', (other, 'Synthetic other '+entity))
        if entity == 'source':
            connection.execute("INSERT INTO source(source_id,school_id,source_type) VALUES (%s,%s,'lesson_video')",
                               (_stable_uuid('source', 'google-drive', pins.source_drive_id), other))
        else:
            checksum = pins.source_media_sha256 if entity == 'media' else pins.master_pdf_sha256
            connection.execute("INSERT INTO asset(asset_id,school_id,asset_type,checksum_value) VALUES (%s,%s,'video',%s)",
                               (_stable_uuid('asset', 'sha256', checksum), other, checksum))
    before = counts()
    assert_blocked(handoff_completed_video(transport=transport(selected)))
    assert counts() == before


@pytest.mark.parametrize('damage', ['source_id', 'pdf_bytes', 'embedded_master', 'incomplete'])
def test_source_pdf_master_and_completion_mismatch_no_writes(tmp_path, damage):
    generation, report = make_generation(tmp_path, 'bad-'+damage)
    if damage == 'source_id':
        generation = replace(generation, pins=replace(generation.pins, source_drive_id='wrong-source'))
    elif damage == 'pdf_bytes':
        report += b'tampered'
    elif damage == 'embedded_master':
        with fitz.open() as document:
            document.new_page()
            document.embfile_add('master_analysis.json', b'{}')
            report = document.tobytes()
        generation = replace(generation, pins=replace(generation.pins, master_pdf_sha256=sha256(report)))
    else:
        done = json.loads(generation.ai_done_bytes)
        done['status'] = 'RUNNING'
        encoded = json.dumps(done).encode()
        generation = replace(generation, ai_done_bytes=encoded, pins=replace(generation.pins, ai_done_sha256=sha256(encoded)))
    before = counts()
    result = handoff_completed_video(transport=transport((generation, report)))
    assert_blocked(result)
    assert result['commit_outcome'] == 'NOT_STARTED'
    assert counts() == before


def test_worker_cannot_insert_canon_activation():
    before = counts()['canon_activation']
    with psycopg.connect(validate_loopback(local_dsn(WORKER)), autocommit=True, connect_timeout=5) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute('INSERT INTO canon_activation DEFAULT VALUES')
    assert counts()['canon_activation'] == before
