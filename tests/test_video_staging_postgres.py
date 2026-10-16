"""Opt-in real PostgreSQL contracts; synthetic source data by default.

No SQL mocks, no production DSN fallback. Provision unchanged migrations first.
The client connects only to the explicit disposable loopback database. Private
source artifacts are optional local inputs and are never required or uploaded by CI.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import IsolationLevel
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
import pytest

from bridge_contracts.video_canon_replay import digest
from bridge_contracts.video_local_evidence import LocalEvidenceReader
from bridge_contracts.video_replay_input import build_replay_input
from database.source_identity_persistence import ensure_drive_source_identity_on_connection
from database.video_candidate_persistence import persist_quality_candidates_on_connection, persist_quality_candidates
from database.video_staging_handoff import persist_quality_handoff
from database.video_result_persistence import persist_video_result_on_connection, SCHOOL_STABLE_NAME
from database.video_staging_read import read_internal_school_video_candidate
from database.video_staging_receipt_read import read_committed_receipt_on_connection
from diana_longitudinal_quality_v4_2 import build_quality_layer


@pytest.fixture(scope='module')
def dsn():
    value = os.getenv('BRIDGE_VIDEO_REHEARSAL_DSN')
    if not value:
        pytest.skip('explicit isolated PostgreSQL not supplied; mocks are not an e2e substitute')
    parts = conninfo_to_dict(value)
    assert parts == {'host': '127.0.0.1', 'port': '55433', 'dbname': 'video_rehearsal', 'user': 'rehearsal_owner'}
    with psycopg.connect(value) as conn:
        database, port, principal = conn.execute('SELECT current_database(),inet_server_port(),session_user').fetchone()
        # Docker CI maps loopback client port 55433 to its isolated port 5432.
        assert (database, principal) == ('video_rehearsal', 'rehearsal_owner')
        assert port in (5432, 55433)
    return value


@contextmanager
def worker(dsn):
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute('SET LOCAL ROLE bridge_school_worker_principal')
        yield conn


@contextmanager
def reader(dsn):
    with psycopg.connect(dsn) as conn:
        conn.execute('SET ROLE bridge_school_app_principal')
        conn.commit()
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        yield conn


def synthetic_inputs():
    """One invented segment/frame; no media, transcript or role authority claim."""
    from tests.test_video_replay_input import full_fixture
    master, _, selection = full_fixture()
    frame = b'SYNTHETIC FRAME BYTES - NOT A REAL IMAGE'
    master.update(job_id='synthetic-postgres-staging-job', algorithmRevision='synthetic-staging-v1',
                  createdAt='2026-01-01T00:00:00Z')
    master['source'].update(name='synthetic-staging-video.bin', sizeBytes=123,
                            durationSeconds=30, mimeType='video/mp4')
    master['screenshots'][0]['sha256'] = hashlib.sha256(frame).hexdigest()
    raw = (json.dumps(master, ensure_ascii=False, indent=2)+'\n').encode()
    selection.pop('speaker_map_raw_sha256')
    selection['source_raw_sha256'] = hashlib.sha256(raw).hexdigest()
    done = {'masterPdf': {'driveId': 'synthetic-report', 'name': 'synthetic-report.pdf',
                         'sha256': hashlib.sha256(b'SYNTHETIC REPORT').hexdigest(),
                         'masterJsonEmbedded': True, 'masterJsonSha256': hashlib.sha256(raw).hexdigest(),
                         'sizeBytes': 16}}
    return raw, done, selection, [('frame_synthetic.bin', 'f1', frame)]


@pytest.fixture(scope='module')
def real(dsn, tmp_path_factory):
    # "real" refers to the SQL connection; source kind is always explicit below.
    root = os.getenv('BRIDGE_VIDEO_REAL_FIXTURE_DIR')
    if root:
        root = Path(root)
        manifest = json.loads((root/'MANIFEST.json').read_bytes())
        for entry in manifest['files']:
            b = (root/entry['path']).read_bytes()
            assert len(b) == entry['bytes'] and hashlib.sha256(b).hexdigest() == entry['sha256']
        raw = (root/'inputs/master_analysis.raw.json').read_bytes()
        done = json.loads((root/'inputs/AI_DONE.json').read_bytes())
        selection = json.loads((root/'inputs/selection.json').read_bytes())
        frame_inputs = [(p.name, p.stem, p.read_bytes()) for p in sorted((root/'inputs').glob('frame_*.jpg'))]
        source_kind = 'PRIVATE_SOURCE_ARTIFACT'
    else:
        raw, done, selection, frame_inputs = synthetic_inputs()
        source_kind = 'SYNTHETIC_SOURCE_CONTRACT_ONLY'
    master = json.loads(raw)
    local = tmp_path_factory.mktemp('staging-source')
    (local/'master.json').write_bytes(raw)
    frames = []
    for name, evidence_id, content in frame_inputs:
        (local/name).write_bytes(content)
        frames.append({'path': name, 'evidence_id': evidence_id, 'sha256': hashlib.sha256(content).hexdigest()})
    index = {'schema': 'video-local-evidence-index-v1',
             'parent': {'path': 'master.json', 'raw_sha256': hashlib.sha256(raw).hexdigest(), 'canonical_sha256': digest(master)},
             'source': {'path': None, 'video_file_id': master['source']['driveId'], 'sha256': master['source']['sha256']},
             'frames': frames}
    encoded = json.dumps(index).encode()
    (local/'index.json').write_bytes(encoded)
    evidence = LocalEvidenceReader(local/'index.json', hashlib.sha256(encoded).hexdigest())
    packet = build_replay_input(raw, selection)
    quality = build_quality_layer(master, source_draft_input=packet, replay_evidence_reader=evidence)
    payload = {'job_id': master['job_id'], 'quality_v2': quality}
    # Local-only algorithm registry setup; no assertion about production registration.
    with psycopg.connect(dsn) as conn:
        school = conn.execute('SELECT school_id FROM school WHERE stable_name=%s', (SCHOOL_STABLE_NAME,)).fetchone()[0]
        algorithm = conn.execute("SELECT algorithm_id FROM algorithm WHERE school_id=%s AND stable_key='bridge-video-master-analysis'", (school,)).fetchone()
        if not algorithm:
            algorithm = conn.execute("INSERT INTO algorithm(school_id,stable_key,name) VALUES (%s,'bridge-video-master-analysis','Local video rehearsal') RETURNING algorithm_id", (school,)).fetchone()
        version = conn.execute('SELECT algorithm_version_id FROM algorithm_version WHERE algorithm_id=%s AND version_label=%s', (algorithm[0], master['algorithmRevision'])).fetchone()
        if not version:
            conn.execute('INSERT INTO algorithm_version(algorithm_id,version_no,version_label) SELECT %s,COALESCE(max(version_no),0)+1,%s FROM algorithm_version WHERE algorithm_id=%s', (algorithm[0], master['algorithmRevision'], algorithm[0]))
        canon_before = conn.execute('SELECT count(*) FROM canon_activation').fetchone()[0]
    with worker(dsn) as conn:
        persisted = persist_video_result_on_connection(conn, master, done)
        ensure_drive_source_identity_on_connection(conn, source_drive_id=master['source']['driveId'], display_name=master['source']['name'], job_id=master['job_id'])
        written = persist_quality_candidates_on_connection(conn, payload, persisted_result=persisted)
    # The write connection has committed and closed before the independent read.
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT analysis_candidate_id FROM analysis_candidate WHERE analysis_run_id=%s AND input_fingerprint=%s AND candidate_type='video_artifact_source_observation'", (persisted['analysis_run_id'], written['input_fingerprint'])).fetchone()
        assert row
        candidate_id = str(row[0])
        assert conn.execute('SELECT count(*) FROM canon_activation').fetchone()[0] == canon_before
    with reader(dsn) as conn:
        read = read_internal_school_video_candidate(conn, candidate_id)
    assert read['status'] == 'READABLE_STAGING', read
    output = os.getenv('BRIDGE_VIDEO_REHEARSAL_OUTPUT')
    if output:
        Path(output).write_text(json.dumps({'scope': 'ISOLATED_POSTGRESQL', 'source_kind': source_kind, 'persisted_result': persisted,
                                          'write_result': written, 'read_result': read}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'master': master, 'done': done, 'payload': payload, 'persisted': persisted,
            'written': written, 'candidate_id': candidate_id, 'read': read, 'school': school, 'source_kind': source_kind}


def test_real_committed_candidate_read_on_new_connection(real, dsn):
    result = real['read']
    assert result['stage'] == 'SOURCE_OBSERVATION'
    assert result['source_locator'] == 'gdrive:file:' + real['master']['source']['driveId']
    assert result['replay_status'] == 'BLOCKED' and result['replay_gap_codes']
    assert result['speaker_identity_status'] == 'UNKNOWN' and result['media_review_status'] == 'UNVERIFIED'
    assert result['accepted_canon'] == [] and not result['canon_write_allowed'] and not result['student_delivery_allowed']
    with reader(dsn) as conn:
        stored = conn.execute('SELECT payload FROM analysis_candidate WHERE analysis_candidate_id=%s', (real['written']['replay_receipt_id'],)).fetchone()[0]
        assert stored['replay'] == real['payload']['quality_v2']['video_canon_auto_pipeline']
        methods = conn.execute('SELECT DISTINCT method_version FROM analysis_candidate WHERE analysis_run_id=%s', (real['persisted']['analysis_run_id'],)).fetchall()
        assert ('video-artifact-source-observation-v1',) in methods
        expected_methods = {r['method_version'] for r in real['payload']['quality_v2']['candidate_staging_records']
                            if r.get('method_version')}
        assert expected_methods <= {row[0] for row in methods}


def test_real_retry_is_idempotent(real, dsn):
    with worker(dsn) as conn:
        persisted_again = persist_video_result_on_connection(conn, real['master'], real['done'])
        assert persisted_again == real['persisted']
        repeated = persist_quality_candidates_on_connection(conn, real['payload'], persisted_result=real['persisted'])
    assert repeated['inserted'] == 0
    assert repeated['candidate_ids'] == real['written']['candidate_ids']
    assert repeated['replay_receipt_id'] == real['written']['replay_receipt_id']


def test_combined_queue_receipt_readback_on_separate_readonly_connection(real, dsn):
    with reader(dsn) as conn:
        witness = read_committed_receipt_on_connection(
            conn, receipt_id=real['written']['replay_receipt_id'],
            analysis_run_id=real['persisted']['analysis_run_id'],
            source_id=real['persisted']['source_id'])
    assert witness == {
        'status': 'COMMITTED_RECEIPT',
        'receipt_id': real['written']['replay_receipt_id'],
        'analysis_run_id': real['persisted']['analysis_run_id'],
        'source_id': real['persisted']['source_id'],
    }


def test_completed_result_handoff_reuses_sql_receipt_and_rejects_wrong_lineage(real, dsn):
    # The production handoff passes this exact producer result in memory. The
    # rehearsal adds only the blocked outbox decision required by the wrapper.
    actual = dict(real['persisted'], outbox={'publication_state': 'BLOCKED_PENDING'})

    def authorized_writer(_dsn, payload, *, persisted_result):
        assert _dsn == dsn
        with worker(dsn) as conn:
            return persist_quality_candidates_on_connection(
                conn, payload, persisted_result=persisted_result)

    repeated = persist_quality_handoff(dsn, real['payload'], actual,
                                       writer=authorized_writer)
    assert repeated['inserted'] == 0
    assert repeated['replay_receipt_id'] == real['written']['replay_receipt_id']
    assert repeated['candidate_ids'] == real['written']['candidate_ids']
    wrong = dict(actual, analysis_run_id=str(uuid4()))
    with pytest.raises(ValueError, match='BINDING_MISMATCH'):
        persist_quality_handoff(dsn, real['payload'], wrong,
                                writer=authorized_writer)


@pytest.mark.parametrize('field', ['source_id','source_asset_id','analysis_run_id','artifact_version_id'])
def test_wrong_exact_id_never_uses_latest(real, dsn, field):
    wrong = dict(real['persisted'], **{field: str(uuid4())})
    with worker(dsn) as conn:
        with pytest.raises(ValueError, match='BINDING_MISMATCH'):
            persist_quality_candidates_on_connection(conn, real['payload'], persisted_result=wrong)


def test_missing_binding_does_not_connect_or_trust_artifact(real):
    payload = dict(real['payload'], persisted_result=real['persisted'])
    assert persist_quality_candidates('invalid-never-connect', payload)['status'] == 'BLOCKED'


@pytest.mark.parametrize('kind', ['payload','method','promotion','duplicate','missing_receipt'])
def test_incomplete_or_altered_request_fails_atomically(real, dsn, kind):
    payload = deepcopy(real['payload'])
    records = payload['quality_v2']['candidate_staging_records']
    if kind == 'payload': records[-2]['payload']['transcript']['quote'] += ' tampered'
    if kind == 'method': records[-2]['method_version'] = 'forged-version'
    if kind == 'promotion': records[0]['promotion_status'] = 'promoted'
    if kind == 'duplicate': records.append(deepcopy(records[-1]))
    if kind == 'missing_receipt': payload['quality_v2'].pop('video_canon_auto_pipeline')
    with worker(dsn) as conn:
        before = conn.execute('SELECT count(*) FROM analysis_candidate').fetchone()[0]
        with pytest.raises((ValueError, _BlockedForTest)):
            persist_quality_candidates_on_connection(conn, payload, persisted_result=real['persisted'])
        assert conn.execute('SELECT count(*) FROM analysis_candidate').fetchone()[0] == before


from bridge_contracts.video_teacher_read import _Blocked as _BlockedForTest


def test_duplicate_replay_member_with_valid_outer_hash_is_rejected(real, dsn):
    payload = deepcopy(real['payload'])
    replay = payload['quality_v2']['video_canon_auto_pipeline']
    replay['candidates'].append(deepcopy(replay['candidates'][0]))
    replay['replay_sha256'] = digest({k:v for k,v in replay.items() if k!='replay_sha256'})
    with worker(dsn) as conn, pytest.raises(ValueError, match='REPLAY_MEMBER_NOT_UNIQUE'):
        persist_quality_candidates_on_connection(conn,payload,persisted_result=real['persisted'])


def test_output_link_conflict_rejects_retry(real, dsn):
    with psycopg.connect(dsn) as conn, conn.transaction(force_rollback=True):
        conn.execute("UPDATE analysis_run_output SET status='superseded' WHERE output_entity_id=%s", (real['written']['replay_receipt_id'],))
        with pytest.raises(ValueError, match='REPLAY_OUTPUT_IDEMPOTENCY_CONFLICT'):
            persist_quality_candidates_on_connection(conn,real['payload'],persisted_result=real['persisted'])


@pytest.mark.parametrize('status', ['VERIFIED', {'private_person': 'do not disclose'}, None])
def test_arbitrary_replay_status_rejected(real, dsn, status):
    payload=deepcopy(real['payload'])
    replay=payload['quality_v2']['video_canon_auto_pipeline']
    replay['status']=status
    replay['replay_sha256']=digest({k:v for k,v in replay.items() if k!='replay_sha256'})
    with worker(dsn) as conn, pytest.raises(ValueError,match='UNSAFE_STAGING_REPLAY'):
        persist_quality_candidates_on_connection(conn,payload,persisted_result=real['persisted'])


def test_jsonb_numeric_roundtrip_cannot_silently_break_sealed_hash(real, dsn):
    payload=deepcopy(real['payload'])
    quality=payload['quality_v2']
    quality['incremental_processing']['input_fingerprint']='negative-zero-local-unit-probe'
    quality['candidate_staging_records']=deepcopy(quality['video_canon_auto_pipeline']['candidates']) + [{
        'candidate_type':'local_negative_zero_probe','stable_key':'negative-zero','method_version':'test-only',
        'payload':{'start':-0.0},'promotion_status':'STAGING_ONLY'}]
    with worker(dsn) as conn:
        before=conn.execute('SELECT count(*) FROM analysis_candidate').fetchone()[0]
        with pytest.raises(ValueError,match='IDEMPOTENCY_CONFLICT'):
            persist_quality_candidates_on_connection(conn,payload,persisted_result=real['persisted'])
        assert conn.execute('SELECT count(*) FROM analysis_candidate').fetchone()[0]==before


def test_newer_decoy_run_cannot_rebind_existing_candidate(real, dsn):
    with psycopg.connect(dsn) as conn, conn.transaction(force_rollback=True):
        decoy=str(uuid4())
        conn.execute('''INSERT INTO analysis_run(analysis_run_id,school_id,algorithm_key,algorithm_version,
            algorithm_version_id,parameters_snapshot,completed_at,run_status)
            SELECT %s,school_id,algorithm_key,algorithm_version,algorithm_version_id,parameters_snapshot,
                   now()+interval '1 day','success' FROM analysis_run WHERE analysis_run_id=%s''',
            (decoy,real['persisted']['analysis_run_id']))
        result=persist_quality_candidates_on_connection(conn,real['payload'],persisted_result=real['persisted'])
        assert result['analysis_run_id']==real['persisted']['analysis_run_id'] and result['inserted']==0


def test_conflicting_existing_record_not_reported_as_idempotent(real, dsn):
    with psycopg.connect(dsn) as conn, conn.transaction(force_rollback=True):
        conn.execute("UPDATE analysis_candidate SET quality_status='TAMPERED' WHERE analysis_candidate_id=%s", (real['candidate_id'],))
        with pytest.raises(ValueError, match='IDEMPOTENCY_CONFLICT'):
            persist_quality_candidates_on_connection(conn, real['payload'], persisted_result=real['persisted'])


@pytest.mark.parametrize('change', ['metadata_source','missing_embodies','native_key','source_school','asset_school','run_school'])
def test_lineage_mutation_blocks_writer(real, dsn, change):
    p = real['persisted']
    with psycopg.connect(dsn) as conn, conn.transaction(force_rollback=True):
        if change == 'metadata_source':
            conn.execute("UPDATE analysis_run_input SET metadata=jsonb_set(metadata,'{source_id}',%s) WHERE analysis_run_id=%s", (Jsonb(str(uuid4())),p['analysis_run_id']))
        elif change == 'missing_embodies':
            conn.execute("DELETE FROM source_asset WHERE source_id=%s AND asset_id=%s AND relation_type='embodies'", (p['source_id'],p['source_asset_id']))
        elif change == 'native_key':
            conn.execute('UPDATE source_identity SET source_native_key=%s WHERE source_id=%s', (real['master']['source']['driveId'],p['source_id']))
        else:
            other = conn.execute("INSERT INTO school(stable_name) VALUES (%s) RETURNING school_id", ('local-adversarial-'+str(uuid4()),)).fetchone()[0]
            table, key = {'source_school':('source','source_id'),'asset_school':('asset','asset_id'),'run_school':('analysis_run','analysis_run_id')}[change]
            if change == 'run_school':
                # The actual schema rejects this earlier than the reader/writer.
                with pytest.raises(psycopg.errors.RaiseException, match='algorithm identity'):
                    conn.execute(f'UPDATE {table} SET school_id=%s WHERE {key}=%s', (other,p[key]))
                return
            conn.execute(f'UPDATE {table} SET school_id=%s WHERE {key}=%s', (other,p['source_asset_id' if change=='asset_school' else key]))
        with pytest.raises(ValueError):
            persist_quality_candidates_on_connection(conn, real['payload'], persisted_result=p)


@pytest.mark.parametrize('target', ['candidate_payload','receipt_payload','receipt_missing','output_link'])
def test_reader_rejects_durable_tampering(real, dsn, target):
    # Commit tampering in the disposable DB, read through a separate connection,
    # then restore exact original values even on assertion failure.
    key = real['candidate_id'] if target=='candidate_payload' else real['written']['replay_receipt_id']
    with psycopg.connect(dsn) as conn:
        original = conn.execute('SELECT payload,status FROM analysis_candidate WHERE analysis_candidate_id=%s',(key,)).fetchone()
        if target.endswith('payload'):
            conn.execute("UPDATE analysis_candidate SET payload=payload || '{\"tampered\":true}'::jsonb WHERE analysis_candidate_id=%s",(key,))
        elif target=='receipt_missing':
            conn.execute("UPDATE analysis_candidate SET status='superseded' WHERE analysis_candidate_id=%s",(key,))
        else:
            conn.execute("UPDATE analysis_run_output SET status='superseded' WHERE output_entity_id=%s",(key,))
    try:
        with reader(dsn) as conn:
            result = read_internal_school_video_candidate(conn,real['candidate_id'])
        assert result['status']=='BLOCKED', result
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute('UPDATE analysis_candidate SET payload=%s,status=%s WHERE analysis_candidate_id=%s',(Jsonb(original[0]),original[1],key))
            if target=='output_link': conn.execute("UPDATE analysis_run_output SET status='staging' WHERE output_entity_id=%s",(key,))


def test_read_requires_actual_repeatable_read_readonly(real, dsn):
    with psycopg.connect(dsn) as conn:
        assert read_internal_school_video_candidate(conn,real['candidate_id'])['gaps']==['READ_ONLY_CONNECTION_REQUIRED']
    with psycopg.connect(dsn) as conn:
        conn.read_only=True
        assert read_internal_school_video_candidate(conn,real['candidate_id'])['gaps']==['REPEATABLE_READ_REQUIRED']


def test_existing_http_service_auth_and_real_sql_read(real, dsn, monkeypatch):
    from fastapi.testclient import TestClient
    from bridge_school_api.main import app
    import bridge_school_api.knowledge as knowledge
    # Override only the transport to the real isolated DB; no SQL/read mocks.
    monkeypatch.setattr(knowledge,'connect',lambda: reader(dsn))
    monkeypatch.setenv('BRIDGE_API_TOKEN','local-rehearsal-only-not-a-production-key')
    with TestClient(app) as client:
        url='/v1/knowledge/internal/video-staging/'+real['candidate_id']
        assert client.get(url).status_code in (401,403)
        response=client.get(url,headers={'Authorization':'Bearer local-rehearsal-only-not-a-production-key'})
        assert response.status_code==200
        assert response.json()['source_id']==real['persisted']['source_id']
        assert response.json()['status']=='READABLE_STAGING'


# Exercise the actual runtime smoke fixture against the unchanged migration
# registry. Do not auto-register its revision: that would mask a fixture mismatch.
_RUNTIME_SMOKE_TABLES = (
    'changeset', 'ingestion_run', 'learning_interaction', 'episode', 'asset',
    'media_asset', 'asset_location', 'source', 'source_asset', 'ingestion_item',
    'transcript', 'transcript_segment', 'analysis_run', 'analysis_run_input',
    'artifact', 'artifact_version', 'analysis_run_output', 'domain_event',
    'outbox_message', 'storage_verification',
)


def _runtime_smoke_counts(conn):
    return {
        table: conn.execute(
            psycopg.sql.SQL('SELECT count(*) FROM public.{}').format(
                psycopg.sql.Identifier(table)
            )
        ).fetchone()[0]
        for table in _RUNTIME_SMOKE_TABLES
    }


def test_runtime_smoke_registered_fixture_and_repeated_rollback(dsn):
    from database.runtime_worker_persistence_smoke import smoke_inputs
    master, done = smoke_inputs()
    with worker(dsn) as conn:
        expected = conn.execute(
            """SELECT av.algorithm_version_id
                 FROM public.algorithm a
                 JOIN public.algorithm_version av USING (algorithm_id)
                 JOIN public.school s USING (school_id)
                WHERE s.stable_name=%s AND s.status='active'
                  AND a.stable_key='bridge-video-master-analysis'
                  AND av.version_label='3.1-free-r25.13-checkpoint'""",
            (SCHOOL_STABLE_NAME,),
        ).fetchall()
        assert len(expected) == 1, 'unchanged migration 0053 must register the fixture revision'
        before = _runtime_smoke_counts(conn)
        results = [
            persist_video_result_on_connection(conn, master, done, rollback=True)
            for _ in range(2)
        ]
        for result in results:
            assert result['algorithm_version_id'] == str(expected[0][0])
            assert result['rolled_back'] is True and result['persisted'] is False
            assert result['segments'] == 2
            assert result['publication_authorization_status'] == 'blocked'
            assert result['quality_confirmation_status'] == 'pending'
        assert results[0] == results[1]
        assert _runtime_smoke_counts(conn) == before


def test_runtime_smoke_unregistered_revision_remains_blocked(dsn):
    from database.runtime_worker_persistence_smoke import smoke_inputs
    master, done = smoke_inputs()
    master['algorithmRevision'] = 'runtime-persistence-smoke'
    with worker(dsn) as conn:
        before = _runtime_smoke_counts(conn)
        with pytest.raises(RuntimeError, match='registered algorithm_version_id required'):
            persist_video_result_on_connection(conn, master, done, rollback=True)
        assert _runtime_smoke_counts(conn) == before


def test_runtime_smoke_keeps_registered_candidate_gates(dsn):
    from database.runtime_worker_persistence_smoke import smoke_inputs
    master, done = smoke_inputs()
    with worker(dsn) as conn:
        before = _runtime_smoke_counts(conn)
        # Inspect the rows inside an enclosing rollback transaction only.
        with conn.transaction(force_rollback=True):
            result = persist_video_result_on_connection(conn, master, done)
            state = conn.execute(
                """SELECT run_status, technical_record_status,
                          quality_confirmation_status, publication_authorization_status
                     FROM public.analysis_run WHERE analysis_run_id=%s""",
                (result['analysis_run_id'],),
            ).fetchone()
            assert state == ('running', 'recorded', 'pending', 'blocked')
            artifact = conn.execute(
                """SELECT a.status, av.status FROM public.artifact a
                     JOIN public.artifact_version av USING (artifact_id)
                    WHERE av.generated_by_analysis_run_id=%s""",
                (result['analysis_run_id'],),
            ).fetchone()
            assert artifact == ('staging', 'candidate')
            # The runtime worker cannot invoke the confirmation helper at all.
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match='bridge_video_has_independent_pass'):
                with conn.transaction():
                    conn.execute(
                        "UPDATE public.analysis_run SET quality_confirmation_status='confirmed' WHERE analysis_run_id=%s",
                        (result['analysis_run_id'],),
                    )
            # On this disposable DB only, its owner can reach the semantic
            # guard. It must still reject confirmation without META evidence.
            with pytest.raises(psycopg.errors.RaiseException, match='independent META PASS'):
                with conn.transaction():
                    conn.execute('RESET ROLE')
                    conn.execute(
                        "UPDATE public.analysis_run SET quality_confirmation_status='confirmed' WHERE analysis_run_id=%s",
                        (result['analysis_run_id'],),
                    )
        assert _runtime_smoke_counts(conn) == before
