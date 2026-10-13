"""Uninstalled operator transport using existing worker grants; no new IAM.

Only trusted server Python code supplies the immutable retained bytes, out-of-
band generation pins and existing credential. This is not an HTTP/member API,
queue consumer, artifact parser, environment toggle or production entry point.
It authenticates technical staging, never the semantic authority of a source.
"""
from contextlib import contextmanager

import psycopg
from psycopg.pq import TransactionStatus

from bridge_contracts.video_replay_input import sha256
from database.runtime_worker_preflight import EXPECTED_CAPABILITY, EXPECTED_PRINCIPAL, normalize_dsn
from database.video_result_persistence import _stable_uuid
from database.video_staging_handoff import CompletedGeneration
from database.video_staging_lineage import fixed_school_id


def _require(condition, code):
    if not condition:
        raise ValueError(code)


class OperatorStagingTransport:
    """One explicitly selected generation, one exclusively owned connection.

    A valid DSN is configuration, not proof. verify_writer queries the actual
    authenticated session inside the same transaction as all existing writers.
    No constructor/import/load operation connects, reads env, or discovers jobs.
    """

    def __init__(self, *, generation: CompletedGeneration, report_bytes: bytes, worker_dsn: str):
        self._generation = generation
        self._report_bytes = report_bytes
        self._worker_dsn = worker_dsn
        self._connection = None
        self._loaded = False

    def load_completed(self):
        import fitz

        generation = self._generation
        _require(isinstance(generation, CompletedGeneration), 'COMPLETED_GENERATION_REQUIRED')
        _require(type(self._report_bytes) is bytes, 'IMMUTABLE_REPORT_BYTES_REQUIRED')
        _require(sha256(self._report_bytes) == generation.pins.master_pdf_sha256, 'REPORT_HASH_MISMATCH')
        # Parse only the already-supported PDF attachment; no generic parser.
        with fitz.open(stream=self._report_bytes, filetype='pdf') as document:
            names = document.embfile_names()
            _require(names.count('master_analysis.json') == 1, 'UNIQUE_EMBEDDED_MASTER_REQUIRED')
            embedded = document.embfile_get('master_analysis.json')
        _require(embedded == generation.master_bytes
                 and sha256(embedded) == generation.pins.master_raw_sha256, 'EMBEDDED_MASTER_MISMATCH')
        self._loaded = True
        return generation

    @contextmanager
    def open_writer(self, generation):
        _require(self._loaded and generation is self._generation, 'PINNED_GENERATION_REQUIRED')
        _require(self._connection is None, 'EXCLUSIVE_WRITER_REQUIRED')
        try:
            dsn = normalize_dsn(self._worker_dsn)
        except SystemExit:
            raise ValueError('WORKER_DSN_REJECTED') from None
        _require(bool(dsn), 'WORKER_DSN_REQUIRED')
        # autocommit keeps the connection idle until the handoff's outer tx.
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10,
                             application_name='bridge-video-operator-staging') as connection:
            self._connection = connection
            try:
                yield connection
            finally:
                self._connection = None

    def verify_writer(self, connection, generation, *, result=None):
        _require(connection is self._connection and generation is self._generation,
                 'OWNED_WRITER_REQUIRED')
        _require(connection.info.transaction_status == TransactionStatus.INTRANS,
                 'OUTER_TRANSACTION_REQUIRED')
        with connection.cursor() as cursor:
            cursor.execute('''SELECT session_user, current_user,
                pg_has_role(current_user, %s, 'usage'),
                has_table_privilege(current_user, 'public.school', 'SELECT'),
                has_table_privilege(current_user, 'public.operational_health_policy', 'UPDATE'),
                has_table_privilege(current_user, 'public.person', 'DELETE'),
                has_schema_privilege(current_user, 'public', 'CREATE')''', (EXPECTED_CAPABILITY,))
            _require(cursor.fetchone() == (EXPECTED_PRINCIPAL, EXPECTED_PRINCIPAL,
                                            True, True, False, False, False), 'WORKER_SESSION_REJECTED')
            school = fixed_school_id(cursor)
            pins = generation.pins
            source_id = _stable_uuid('source', 'google-drive', pins.source_drive_id)
            media_id = _stable_uuid('asset', 'sha256', pins.source_media_sha256)
            report_id = _stable_uuid('asset', 'sha256', pins.master_pdf_sha256)
            # Source/assets have existing UPDATE grants. Lock only these rows;
            # never school/policy rows, which would demand a new privilege.
            cursor.execute('SELECT source_id, school_id FROM public.source WHERE source_id=%s FOR SHARE',
                           (source_id,))
            sources = cursor.fetchall()
            cursor.execute('SELECT asset_id, school_id FROM public.asset WHERE asset_id IN (%s,%s) FOR SHARE',
                           (media_id, report_id))
            assets = cursor.fetchall()
            _require(all(str(row[1]) == str(school) for row in sources + assets), 'FOREIGN_SCHOOL_COLLISION')
            if result is not None:
                _require(str(result.get('school_id')) == str(school)
                         and result.get('source_id') == str(source_id)
                         and result.get('source_asset_id') == str(media_id), 'WRITER_SCOPE_MISMATCH')
                _require({str(row[0]) for row in sources} == {str(source_id)}
                         and {str(row[0]) for row in assets} == {str(media_id), str(report_id)},
                         'WRITER_ENTITIES_MISSING')
        # Existing candidate writer independently verifies run/artifact/source
        # lineage. PostgreSQL enforces privileges on each write. A failed SQL
        # statement/postcheck aborts the outer tx; neither check fences a future
        # administrator REVOKE or retroactively cancels an already executed SQL.
