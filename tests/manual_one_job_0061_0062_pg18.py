"""Disposable localhost PG18 rehearsal of actual 0060/0061/0062 SQL.

Run explicitly with --pg-bin pointing to an existing PostgreSQL 18 bin folder.
Creates and destroys only its own temporary cluster below this checkout.
No production DSN, Drive media, IBM process, or provider callback is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
_SYNTHETIC_PASSWORD: ContextVar[str | None] = ContextVar(
    'one_job_synthetic_password', default=None)
sys.path.insert(0, str(ROOT))
from universal_video.private_one_job_approver import approve_once
from universal_video.private_one_job_entrypoint import claim_exact_private_job
from universal_video.private_one_job_operator import ClaimReceiptJournal, write_claim_receipt
from universal_video import private_one_job_entrypoint as claim_entry
from universal_video import private_one_job_operator as operator
from universal_video import private_one_job_approver as approver


def _run(args: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(args, cwd=ROOT, env=env, text=True,
                            encoding='utf-8', errors='replace',
                            capture_output=True, check=False, timeout=180)
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed: {result.stderr[-3000:]}')
    return result.stdout


def _sha(connection, value) -> str:
    return next(iter(connection.execute('SELECT video_queue.subset_sha(%s::jsonb)',
                                        (Jsonb(value),)).fetchone().values()))


def _canonical(connection, value) -> str:
    return next(iter(connection.execute('SELECT video_queue.subset_json(%s::jsonb)',
                                        (Jsonb(value),)).fetchone().values()))


def _connect(port: int, user: str = 'postgres'):
    password = _SYNTHETIC_PASSWORD.get()
    return psycopg.connect(host='127.0.0.1', port=port, dbname='postgres',
                           user=user, connect_timeout=5, row_factory=dict_row,
                           **({'password': password} if password is not None else {}))


def _test_dsn(port: int, user: str) -> str:
    password = _SYNTHETIC_PASSWORD.get()
    credentials = user if password is None else f'{user}:{quote(password, safe="")}'
    return f'postgresql://{credentials}@127.0.0.1:{port}/postgres'


def _roles(port: int, password: str | None = None) -> None:
    with _connect(port) as db:
        for login, capability in (
                ('onejob_approver', 'video_subset_approver'),
                ('onejob_collector', 'video_subset_collector'),
                ('onejob_admitter', 'video_subset_admitter'),
                ('onejob_worker', 'video_subset_worker_v2'),
                ('onejob_dual', 'video_subset_approver')):
            if password is None:
                db.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(login)))
            else:
                db.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(
                    sql.Identifier(login), sql.Literal(password)))
            db.execute(f'GRANT {capability} TO {login}')
        db.execute('GRANT video_subset_worker_v2 TO onejob_dual')
        for signature in (
                'approve_one_job_staging_attempt_v1(uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text)',
                'revoke_one_job_staging_v1(uuid)'):
            db.execute(f'GRANT EXECUTE ON FUNCTION video_queue.{signature} TO video_subset_approver')
        for signature in (
                'stage_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,text,text,text)',
                'read_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid)',
                'finish_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)'):
            db.execute(f'GRANT EXECUTE ON FUNCTION video_queue.{signature} TO video_subset_worker_v2')


def _admit(port: int, suffix: str = '0001', *, approval_seconds: int = 1200) -> dict:
    file_id = 'synthetic_video_' + suffix
    source_folder = 'source_folder_001'
    output_folder = 'output_folder_001'
    revision = dict(source_identity='google_drive:' + file_id,
                    file_id=file_id, parent=source_folder, version='1',
                    modifiedTime='2026-10-15T00:00:00Z', mime_type='video/mp4',
                    size_bytes=1048576, checksum=None, checksums={})
    observed = dict(id=file_id, parents=[source_folder], name='Synthetic.mp4',
                    version='1', modifiedTime='2026-10-15T00:00:00Z',
                    mimeType='video/mp4', size='1048576', trashed=False)
    with _connect(port) as db:
        fingerprint = _sha(db, revision)
        source_file = dict(revision, name='Synthetic.mp4', trashed=False,
                           source_fingerprint=fingerprint, duration_ms=None,
                           estimated_cost_usd=None)
        queue_file = dict(sequence=1, file_id=file_id, name='Synthetic.mp4',
                          mime_type='video/mp4', size_bytes=1048576,
                          checksum=None)
        manifest = dict(source_folder_id=source_folder,
                        output_folder_id=output_folder,
                        work_folder_id='work_folder_0001',
                        processing_profile='bridge_3_1_free',
                        algorithm_revision='3.1-free-r25.16',
                        canary_source_file_id=file_id,
                        inventory_sha256=_sha(db, [queue_file]),
                        expected_count=1, total_size_bytes=1048576,
                        files=[queue_file], result_mode='SHADOW_REVIEW_ONLY',
                        canonical_promotion_allowed=False,
                        database_persistence_allowed=False)
        revision_key = dict(plan_sha256='a' * 64,
                            subset_sha256=_sha(db, [source_file]),
                            subset_sequence=1,
                            processing_profile='bridge_3_1_free',
                            algorithm_revision='3.1-free-r25.16',
                            canary_source_file_id=file_id)
        request_key = 'subset-v1:' + _sha(db, revision_key)
        manifest['request_key'] = request_key
        contract = dict(schema='universal-video-subset-contract-v1',
                        **revision_key, files=[source_file],
                        enqueue_manifest=manifest,
                        queue_manifest_sha256=_sha(db, manifest),
                        execution_allowed=False, release_allowed=False,
                        database_persistence_allowed=False,
                        canonical_promotion_allowed=False)
        contract['contract_sha256'] = _sha(db, contract)
        contract_bytes = _canonical(db, contract)
    approval_id, observation_id = str(uuid4()), str(uuid4())
    with _connect(port, 'onejob_approver') as db:
        db.execute('SELECT video_queue.approve_subset(%s,%s,%s,%s)',
                   (approval_id, contract_bytes, Jsonb(dict(
                       approved_by='synthetic-reviewer',
                       approval_reference='local-test',
                       plan_sha256='a' * 64)),
                    datetime.now(timezone.utc) + timedelta(seconds=approval_seconds)))
        db.execute('SELECT video_queue.set_subset_runtime(%s,true)',
                   (approval_id,))
    with _connect(port, 'onejob_collector') as db:
        db.execute("SELECT video_queue.observe_subset(%s,%s,'ADMISSION',%s,%s,%s)",
                   (observation_id, approval_id, Jsonb([observed]),
                    datetime.now(timezone.utc), str(uuid4())))
    with _connect(port, 'onejob_admitter') as db:
        result = db.execute('SELECT video_queue.admit_approved_subset(%s,%s,%s,%s,%s)',
                            (approval_id, request_key, contract['contract_sha256'],
                             contract['queue_manifest_sha256'], observation_id)).fetchone()['admit_approved_subset']
        assert result['status'] == 'ADMITTED', result
        batch_id = result['binding']['batch_id']
        job_id = result['binding']['jobs'][0]['job_id']
    return dict(approval_id=approval_id, batch_id=batch_id, job_id=job_id,
                source_file_id=file_id, source_fingerprint=fingerprint,
                observed=[observed], source_folder=source_folder,
                output_folder=output_folder)


def _read(path: Path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def _claim(port: int, fixture: dict, journal: ClaimReceiptJournal, *,
           connection_factory=None):
    factory = connection_factory or (lambda: _connect(port, 'onejob_worker'))
    with patch.object(claim_entry, '_connect_queue', side_effect=lambda _dsn: factory()):
        claim = claim_exact_private_job(
            queue_dsn='disposable-pg18-only', expected_database='postgres',
            expected_login='onejob_worker', expected_batch_id=fixture['batch_id'],
            expected_job_id=fixture['job_id'],
            expected_source_file_id=fixture['source_file_id'],
            expected_attempt_count=1, worker_key='one-job-worker',
            processing_profile='bridge_3_1_free',
            algorithm_revision='3.1-free-r25.16',
            claim_publisher=lambda selected: journal.publish_precommit(
                selected, 'one-job-worker', journal.binding['admission_id'],
                fixture['source_fingerprint']))
    journal.acknowledge_commit(claim)
    return claim


def _observe_attempt(port: int, fixture: dict, claim: dict) -> str:
    observation_id = str(uuid4())
    with _connect(port, 'onejob_collector') as db:
        db.execute("SELECT video_queue.observe_subset(%s,%s,'ATTEMPT',%s,%s,NULL,%s,%s,%s,%s)",
                   (observation_id, fixture['approval_id'],
                    Jsonb(fixture['observed']), datetime.now(timezone.utc),
                    claim['job_id'], claim['lease_token'], claim['attempt_count'],
                    'one-job-worker'))
    return observation_id


def _terminal_sha(db, value) -> str:
    # ASCII-only fixture; SQL's canonical_json_text independently checks it.
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _private_payload(db, fixture: dict, claim: dict, stage: dict):
    pdf_id, done_id = 'synthetic_pdf_0001', 'synthetic_done_0001'
    pdf_sha = 'd' * 64
    master = dict(job_id=claim['stable_job_key'],
                  algorithmRevision='3.1-free-r25.16',
                  source=dict(driveId=fixture['source_file_id'],
                              name='Synthetic.mp4', mimeType='video/mp4',
                              sizeBytes=1048576,
                              parentFolderId=fixture['source_folder'],
                              sha256='e' * 64))
    master_raw = json.dumps(master, sort_keys=True, separators=(',', ':'))
    done = dict(job_id=claim['stable_job_key'], status='AI_DONE',
                algorithmRevision='3.1-free-r25.16',
                masterPdf=dict(driveId=pdf_id, sha256=pdf_sha,
                               masterJsonSha256=hashlib.sha256(
                                   master_raw.encode()).hexdigest()))
    done_raw = json.dumps(done, sort_keys=True, separators=(',', ':'))
    done_sha = hashlib.sha256(done_raw.encode()).hexdigest()
    source_identity = dict(file_id=fixture['source_file_id'],
                           name='Synthetic.mp4', mime_type='video/mp4',
                           size_bytes=1048576,
                           parent_folder_id=fixture['source_folder'],
                           checksum=None)
    artifacts = [dict(kind='master_pdf', drive_id=pdf_id,
                      locator='gdrive:file:' + pdf_id,
                      mime_type='application/pdf',
                      parent_id=fixture['output_folder'], sha256=pdf_sha,
                      size_bytes=100, modified_time='2026-10-15T00:00:01Z',
                      version='2'),
                 dict(kind='ai_done', drive_id=done_id,
                      locator='gdrive:file:' + done_id,
                      mime_type='application/json',
                      parent_id=fixture['output_folder'], sha256=done_sha,
                      size_bytes=len(done_raw),
                      modified_time='2026-10-15T00:00:02Z', version='3')]
    manifest = dict(schema_version='universal-video-artifact-manifest/v1',
                    job_id=claim['stable_job_key'],
                    source_file_id=fixture['source_file_id'],
                    algorithm_revision='3.1-free-r25.16',
                    result_mode='SHADOW_REVIEW_ONLY',
                    canonical_promotion_allowed=False,
                    database_persistence_allowed=False,
                    publication_state='NOT_PUBLISHED',
                    source_identity=source_identity, artifacts=artifacts)
    manifest_sha = _terminal_sha(db, manifest)
    terminal = dict(schema_version='universal-video-terminal-receipt/v1',
                    status='PASS', job_id=claim['stable_job_key'],
                    source_file_id=fixture['source_file_id'],
                    source_identity_verified=True, drive_readback_verified=True,
                    result_readback_verified=True, checksum_verified=True,
                    artifact_count=2, artifact_manifest_sha256=manifest_sha,
                    canonical_promotion_allowed=False,
                    database_persistence_allowed=False,
                    publication_state='NOT_PUBLISHED')
    terminal_sha = _terminal_sha(db, terminal)
    terminal['evidence_sha256'] = terminal_sha
    output = dict(result_mode='PRIVATE_RESULT_STAGING_ONLY',
                  canonical_promotion_allowed=False,
                  database_persistence_allowed=True,
                  publication_state='NOT_PUBLISHED',
                  source_file_id=fixture['source_file_id'],
                  stable_job_key=claim['stable_job_key'],
                  algorithm_revision='3.1-free-r25.16',
                  private_staging=stage,
                  master_pdf_drive_id=pdf_id, master_pdf_sha256=pdf_sha,
                  ai_done_drive_id=done_id, ai_done_sha256=done_sha,
                  artifact_manifest=manifest,
                  artifact_manifest_sha256=manifest_sha,
                  terminal_receipt=terminal,
                  terminal_evidence_sha256=terminal_sha,
                  artifact_locators=dict(master_pdf=pdf_id, ai_done=done_id))
    return master_raw, done_raw, output


class _LostAckConnection:
    def __init__(self, port: int):
        self.db = _connect(port, 'onejob_worker')

    def __enter__(self):
        self.db.__enter__()
        return self

    def __exit__(self, kind, value, trace):
        result = self.db.__exit__(kind, value, trace)
        if kind is None:
            raise OSError('SYNTHETIC_ACK_LOST_AFTER_REAL_COMMIT')
        return result

    def cursor(self):
        return self.db.cursor()


def _rehearse(port: int, tmp: Path, fixture: dict) -> dict[str, str]:
    admission_id = str(uuid4())
    journal = ClaimReceiptJournal(tmp / 'claim.json', dict(
        admission_id=admission_id,
        expected_job_id=fixture['job_id'],
        expected_batch_id=fixture['batch_id']))
    with patch.object(operator, '_read_local_json', side_effect=_read), \
         patch.object(approver, '_read_local_json', side_effect=_read), \
         patch.dict(os.environ, {}, clear=True):
        journal.reserve()
        claim = _claim(port, fixture, journal)
        assert _read(tmp / 'claim.json')['claim_state'] == 'COMMIT_ACKNOWLEDGED'
        scope = (admission_id, claim['job_id'], claim['lease_token'],
                 'one-job-worker', claim['attempt_count'], fixture['source_fingerprint'])
        before_id = _observe_attempt(port, fixture, claim)
        with _connect(port, 'onejob_worker') as db:
            checked = db.execute('SELECT video_queue.authorize_subset_attempt(%s,%s,%s,%s)',
                                 (claim['job_id'], claim['lease_token'],
                                  'one-job-worker', before_id)).fetchone()['authorize_subset_attempt']
            assert checked['status'] == 'SOURCE_AND_LEASE_VERIFIED'
        # A nine-minute request must be rejected when only 90 seconds remain,
        # without leaving admission rows. Then give this same lease five
        # minutes: the approval must be capped, not rejected or extended.
        with _connect(port, 'onejob_worker') as db:
            db.execute('SELECT video_queue.heartbeat_subset_job(%s,%s,%s,%s)',
                       (claim['job_id'], claim['lease_token'], 'one-job-worker', 90))
        with _connect(port, 'onejob_approver') as db:
            try:
                with db.transaction():
                    db.execute('SELECT video_queue.approve_one_job_staging_attempt_v1('
                               '%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                               (*scope[:1], str(uuid4()), *scope[1:],
                                datetime.now(timezone.utc)+timedelta(minutes=9),
                                'insufficient-budget-control'))
            except psycopg.Error as exc:
                assert 'ONE_JOB_APPROVAL_TIME_BUDGET_LOW' in str(exc), exc
            else:
                raise AssertionError('insufficient approval budget accepted')
        with _connect(port) as db:
            assert db.execute('SELECT count(*) AS n FROM '
                              'video_queue.one_job_staging_admission_v1 '
                              'WHERE admission_id=%s', (admission_id,)).fetchone()['n'] == 0
        with _connect(port, 'onejob_worker') as db:
            db.execute('SELECT video_queue.heartbeat_subset_job(%s,%s,%s,%s)',
                       (claim['job_id'], claim['lease_token'], 'one-job-worker', 300))
        ready_path = tmp / 'approval.json.ready'
        write_claim_receipt(ready_path, claim, 'one-job-worker',
                            admission_id, fixture['source_fingerprint'])
        binding = dict(expected_database='postgres', expected_host='127.0.0.1',
                       expected_port=port,
                       expected_approver_login='onejob_approver',
                       expected_worker_login='onejob_worker',
                       expected_batch_id=fixture['batch_id'],
                       expected_job_id=fixture['job_id'],
                       expected_source_file_id=fixture['source_file_id'],
                       expected_attempt_count=1, admission_id=admission_id,
                       source_fingerprint=fixture['source_fingerprint'],
                       worker_key='one-job-worker',
                       claim_receipt_path=str(tmp / 'claim.json'),
                       approval_signal_path=str(tmp / 'approval.json'),
                       approver_dsn_file=str(tmp / 'unused-dsn-file'),
                       approval_reference='local-real-sql-rehearsal')
        binding_path = tmp / 'approver-binding.json'
        binding_path.write_text(json.dumps(binding), encoding='utf-8')
        os.chmod(binding_path, 0o600)
        with patch.object(approver, '_read_approver_dsn',
                          return_value=_test_dsn(port, 'onejob_dual')):
            binding['expected_approver_login'] = 'onejob_dual'
            binding_path.write_text(json.dumps(binding), encoding='utf-8')
            try:
                approve_once(str(binding_path))
            except Exception as exc:
                assert 'ONE_JOB_APPROVER_IDENTITY_OR_ACL_INVALID' in str(exc), exc
            else:
                raise AssertionError('dual role approved')
        binding['expected_approver_login'] = 'onejob_approver'
        binding_path.write_text(json.dumps(binding), encoding='utf-8')
        with _connect(port, 'onejob_approver') as db:
            for token, expiry, marker in (
                    (str(uuid4()), datetime.now(timezone.utc) + timedelta(seconds=10),
                     'ONE_JOB_APPROVAL_LEASE_LOST'),
                    (claim['lease_token'], datetime.now(timezone.utc) - timedelta(seconds=1),
                     'ONE_JOB_APPROVAL_ARGUMENT_INVALID')):
                try:
                    with db.transaction():
                        db.execute('SELECT video_queue.approve_one_job_staging_attempt_v1('
                                   '%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                   (admission_id, str(uuid4()), claim['job_id'], token,
                                    'one-job-worker', 1, fixture['source_fingerprint'],
                                    expiry, 'negative-control'))
                except psycopg.Error as exc:
                    assert marker in str(exc), exc
                else:
                    raise AssertionError(marker + ' not rejected')
        with patch.object(approver, '_read_approver_dsn',
                          return_value=_test_dsn(port, 'onejob_approver')):
            approved = approve_once(str(binding_path))
            assert approved['status'] == 'APPROVED_ONCE'
            try:
                approve_once(str(binding_path))
            except Exception as exc:
                assert 'ONE_JOB_APPROVER_SIGNAL_EXISTS' in str(exc), exc
            else:
                raise AssertionError('approval signal replayed')
        with _connect(port) as db:
            deadlines = db.execute(
                'SELECT a.expires_at AS admission_expiry, '
                'l.expires_at AS approval_expiry, j.lease_expires_at, '
                's.expires_at AS subset_expiry '
                'FROM video_queue.one_job_staging_admission_v1 a '
                'JOIN video_queue.one_job_staging_lease_approval_v1 l '
                'ON l.admission_id=a.admission_id '
                'JOIN video_queue.job j ON j.job_id=a.job_id '
                'JOIN video_queue.subset_approval s ON s.approval_id=a.subset_approval_id '
                'WHERE a.admission_id=%s', (admission_id,)).fetchone()
            assert deadlines['admission_expiry'] == deadlines['approval_expiry']
            assert deadlines['approval_expiry'] <= deadlines['lease_expires_at']
            assert deadlines['approval_expiry'] <= deadlines['subset_expiry']
            assert deadlines['approval_expiry'] > datetime.now(timezone.utc) + timedelta(minutes=3)
        with _connect(port, 'onejob_approver') as db:
            try:
                with db.transaction():
                    db.execute('SELECT video_queue.approve_one_job_staging_attempt_v1('
                               '%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                               (admission_id, str(uuid4()), claim['job_id'],
                                claim['lease_token'], 'one-job-worker', 1,
                                fixture['source_fingerprint'],
                                datetime.now(timezone.utc)+timedelta(minutes=9),
                                'same-attempt-replay-control'))
            except psycopg.Error as exc:
                assert 'ONE_JOB_APPROVAL_REPLAY' in str(exc), exc
            else:
                raise AssertionError('same-attempt approval replay accepted')
        with _connect(port, 'onejob_approver') as db:
            try:
                db.execute('SELECT video_queue.stage_one_job_private_result_v1('
                           '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (*scope, str(uuid4()), '{}',
                            'synthetic_done_0001', '{}'))
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError('approver executed worker stage')
        with _connect(port, 'onejob_worker') as db:
            master_raw, done_raw, _ = _private_payload(db, fixture, claim, {})
            receipt_id = str(uuid4())
            stage = db.execute('SELECT video_queue.stage_one_job_private_result_v1('
                               '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS staged',
                               (*scope, receipt_id, master_raw,
                                'synthetic_done_0001', done_raw)).fetchone()['staged']
            assert stage['status'] == 'PRIVATE_RESULT_STAGED'
        with _connect(port, 'onejob_worker') as db:
            readback = db.execute('SELECT video_queue.read_one_job_private_result_v1('
                                  '%s,%s,%s,%s,%s,%s,%s) AS receipt',
                                  (*scope, receipt_id)).fetchone()['receipt']
            assert readback['status'] == 'COMMITTED_PRIVATE_RESULT'
            assert readback['bundle_sha256'] == stage['bundle_sha256']
        after_id = _observe_attempt(port, fixture, claim)
        with _connect(port, 'onejob_worker') as db:
            _, _, output = _private_payload(db, fixture, claim, stage)
            finished = db.execute('SELECT * FROM video_queue.finish_one_job_private_result_v1('
                                  '%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                  (*scope, receipt_id, after_id, Jsonb(output))).fetchone()
            assert finished['job_status'] == 'REVIEW_READY'
            assert finished['released_jobs'] == 0
        with _connect(port) as db:
            row = db.execute('SELECT status,output FROM video_queue.job WHERE job_id=%s',
                             (claim['job_id'],)).fetchone()
            assert row['status'] == 'REVIEW_READY'
            assert row['output']['private_staging']['receipt_id'] == receipt_id
            assert db.execute('SELECT count(*) AS n FROM video_queue.one_job_private_result_v1'
                              ).fetchone()['n'] == 1
            assert db.execute('SELECT count(*) AS n FROM video_queue.one_job_staging_lease_approval_v1'
                              ).fetchone()['n'] == 1
        with _connect(port, 'onejob_approver') as db:
            db.execute('SELECT video_queue.revoke_one_job_staging_v1(%s)',
                       (admission_id,))
        return dict(claim='PASS_REAL_0060', evidence='PASS_REAL_0060',
                    approval='PASS_REAL_0061_SEPARATE_ROLE',
                    private_result='PASS_REAL_0062_STAGE_READ_FINISH',
                    readback='PASS_POST_FINISH_CATALOG',
                    wrong_role_expired_replay='PASS_REJECTED')


def _ack_loss_case(port: int, tmp: Path, fixture: dict) -> dict[str, str]:
    journal_path = tmp / 'ack-lost-claim.json'
    journal = ClaimReceiptJournal(journal_path, dict(
        admission_id=str(uuid4()),
        expected_job_id=fixture['job_id'],
        expected_batch_id=fixture['batch_id']))
    with patch.object(operator, '_read_local_json', side_effect=_read), \
         patch.dict(os.environ, {}, clear=True):
        journal.reserve()
        try:
            _claim(port, fixture, journal,
                   connection_factory=lambda: _LostAckConnection(port))
        except OSError as exc:
            assert str(exc) == 'SYNTHETIC_ACK_LOST_AFTER_REAL_COMMIT'
        else:
            raise AssertionError('lost ACK was not simulated')
        uncertain = _read(journal_path)
        assert uncertain['claim_state'] == 'PRECOMMIT_UNCONFIRMED'
        with _connect(port) as db:
            actual = db.execute('SELECT status,attempt_count,lease_token,lease_owner '
                                'FROM video_queue.job WHERE job_id=%s',
                                (fixture['job_id'],)).fetchone()
        assert actual['status'] == 'LEASED'
        assert actual['attempt_count'] == 1
        assert str(actual['lease_token']) == uncertain['lease_token']
        assert actual['lease_owner'] == 'one-job-worker'
        try:
            journal.reserve()
        except Exception as exc:
            assert 'ONE_JOB_CLAIM_RECEIPT_WRITE_FAILED' in str(exc), exc
        else:
            raise AssertionError('uncertain claim journal overwritten')
        assert _read(journal_path) == uncertain
    return dict(ack_loss='PASS_REAL_0060_COMMIT_TOKEN_RECONCILED',
                uncertain_replay='PASS_BLOCKED')


def _subset_deadline_case(port: int, tmp: Path, fixture: dict) -> dict[str, str]:
    admission_id = str(uuid4())
    journal = ClaimReceiptJournal(tmp / 'subset-deadline-claim.json', dict(
        admission_id=admission_id, expected_job_id=fixture['job_id'],
        expected_batch_id=fixture['batch_id']))
    with patch.object(operator, '_read_local_json', side_effect=_read):
        journal.reserve()
        claim = _claim(port, fixture, journal)
    with _connect(port, 'onejob_worker') as db:
        db.execute('SELECT video_queue.heartbeat_subset_job(%s,%s,%s,%s)',
                   (claim['job_id'], claim['lease_token'], 'one-job-worker', 420))

    def request_approval() -> None:
        with _connect(port, 'onejob_approver') as db:
            db.execute('SELECT video_queue.approve_one_job_staging_attempt_v1('
                       '%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       (admission_id, str(uuid4()), claim['job_id'],
                        claim['lease_token'], 'one-job-worker', 1,
                        fixture['source_fingerprint'],
                        datetime.now(timezone.utc)+timedelta(minutes=9),
                        'subset-deadline-control'))

    request_approval()
    with _connect(port) as db:
        row = db.execute('SELECT l.expires_at AS approval_expiry, '
                         's.expires_at AS subset_expiry, j.lease_expires_at '
                         'FROM video_queue.one_job_staging_lease_approval_v1 l '
                         'JOIN video_queue.one_job_staging_admission_v1 a '
                         'ON a.admission_id=l.admission_id '
                         'JOIN video_queue.subset_approval s '
                         'ON s.approval_id=a.subset_approval_id '
                         'JOIN video_queue.job j ON j.job_id=a.job_id '
                         'WHERE a.admission_id=%s', (admission_id,)).fetchone()
        assert row['approval_expiry'] <= row['subset_expiry']
        assert row['approval_expiry'] <= row['lease_expires_at']
        assert row['approval_expiry'] > datetime.now(timezone.utc)+timedelta(minutes=3)
    return dict(subset_deadline='PASS_CAPPED_WITHOUT_EXTENSION')


def _short_subset_budget_case(port: int, tmp: Path, fixture: dict) -> dict[str, str]:
    admission_id = str(uuid4())
    journal = ClaimReceiptJournal(tmp / 'short-subset-claim.json', dict(
        admission_id=admission_id, expected_job_id=fixture['job_id'],
        expected_batch_id=fixture['batch_id']))
    with patch.object(operator, '_read_local_json', side_effect=_read):
        journal.reserve()
        claim = _claim(port, fixture, journal)
    with _connect(port, 'onejob_approver') as db:
        try:
            with db.transaction():
                db.execute('SELECT video_queue.approve_one_job_staging_attempt_v1('
                           '%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (admission_id, str(uuid4()), claim['job_id'],
                            claim['lease_token'], 'one-job-worker', 1,
                            fixture['source_fingerprint'],
                            datetime.now(timezone.utc)+timedelta(minutes=9),
                            'short-subset-budget-control'))
        except psycopg.Error as exc:
            assert 'ONE_JOB_APPROVAL_TIME_BUDGET_LOW' in str(exc), exc
        else:
            raise AssertionError('short subset deadline accepted')
    with _connect(port) as db:
        assert db.execute('SELECT count(*) AS n FROM '
                          'video_queue.one_job_staging_admission_v1 '
                          'WHERE admission_id=%s', (admission_id,)).fetchone()['n'] == 0
    return dict(short_subset_budget='PASS_REJECTED')


def _approved_sql_inventory() -> list[Path]:
    """Require the reviewed baseline and exactly one approved 0060 source."""
    inventory_path = ROOT / 'database' / 'fixtures' / 'one_job_pg18_approved_inventory.json'
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    if inventory.get('schema') != 'one-job-pg18-approved-inventory-v1':
        raise RuntimeError('ONE_JOB_INVENTORY_SCHEMA_INVALID')
    baseline = inventory.get('baseline_migrations')
    if not isinstance(baseline, list) or not baseline:
        raise RuntimeError('ONE_JOB_INVENTORY_BASELINE_INVALID')
    migration_dir = ROOT / 'database' / 'migrations'
    actual_baseline = sorted(
        path.name for path in migration_dir.glob('[0-9][0-9][0-9][0-9]_*.sql')
        if int(path.name[:4]) <= 59)
    expected_baseline = [item.get('name') for item in baseline]
    if actual_baseline != expected_baseline or len(set(expected_baseline)) != len(baseline):
        raise RuntimeError('ONE_JOB_INVENTORY_BASELINE_MISMATCH')
    selected = []
    for item in baseline:
        path = migration_dir / item['name']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item.get('sha256'):
            raise RuntimeError(f'ONE_JOB_INVENTORY_HASH_MISMATCH:{item["name"]}')
        selected.append(path)
    actual_components = sorted(
        child.relative_to(migration_dir).as_posix()
        for path in selected for child in path.with_suffix('').rglob('*')
        if child.is_file())
    components = inventory.get('baseline_components')
    if not isinstance(components, list) or actual_components != [
            item.get('path') for item in components]:
        raise RuntimeError('ONE_JOB_INVENTORY_COMPONENTS_MISMATCH')
    for item in components:
        path = migration_dir / item['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item.get('sha256'):
            raise RuntimeError(f'ONE_JOB_INVENTORY_HASH_MISMATCH:{item["path"]}')
    candidate = inventory.get('candidate_0060')
    if not isinstance(candidate, dict):
        raise RuntimeError('ONE_JOB_INVENTORY_0060_INVALID')
    name = candidate.get('name')
    if name != '0060_fixed_subset_admission.sql':
        raise RuntimeError('ONE_JOB_INVENTORY_0060_INVALID')
    possible = [ROOT / directory / name for directory in
                ('database/migrations', 'database/candidates')]
    extra_0060 = [path for directory in ('database/migrations', 'database/candidates')
                  for path in (ROOT / directory).glob('0060_*.sql')
                  if path.name != name]
    matches = [path for path in possible if path.is_file()]
    if len(matches) != 1 or extra_0060:
        raise RuntimeError('ONE_JOB_INVENTORY_0060_LOCATION_INVALID')
    if hashlib.sha256(matches[0].read_bytes()).hexdigest() != candidate.get('sha256'):
        raise RuntimeError('ONE_JOB_INVENTORY_0060_HASH_MISMATCH')
    selected.append(matches[0])
    return selected


def _exercise(port: int, tmp: Path, prefix: list[str], env: dict[str, str],
              database_label: str, role_password: str | None = None) -> None:
    files = _approved_sql_inventory()
    for path in files:
        _run(prefix + ['-f', str(path)], env=env)
        print(f'installed {path.stem}', flush=True)
    proposals = ('0061_one_job_staging_admission_v1.sql',
                 '0062_one_job_private_result_gate_v1.sql')
    for name in proposals:
        _run(prefix + ['-f', str(ROOT / 'database' / 'proposals' / name)], env=env)
        print(f'installed {name}', flush=True)
    _roles(port, role_password)
    admitted = _admit(port, approval_seconds=420)
    print(json.dumps({'status': 'SYNTHETIC_BATCH_ADMITTED',
                      'job_id': admitted['job_id']}, sort_keys=True), flush=True)
    result = _rehearse(port, tmp, admitted)
    print(json.dumps(result, sort_keys=True), flush=True)
    second = _admit(port, '0002')
    result.update(_ack_loss_case(port, tmp, second))
    short = _admit(port, '0003', approval_seconds=90)
    result.update(_short_subset_budget_case(port, tmp, short))
    third = _admit(port, '0004', approval_seconds=300)
    result.update(_subset_deadline_case(port, tmp, third))
    print(json.dumps(result, sort_keys=True), flush=True)
    checksums = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (ROOT / 'database' / 'fixtures' /
                              'one_job_pg18_approved_inventory.json',
                              ROOT / 'tests' / 'manual_one_job_0061_0062_pg18.py',
                              *(ROOT / 'database' / 'proposals' / name
                                for name in proposals))}
    checkout_sha = _run(['git', 'rev-parse', 'HEAD']).strip()
    print(json.dumps({'status': 'SCHEMA_INSTALLED', 'migrations': len(files),
                      'proposals': ['0061', '0062'], 'database': database_label,
                      'checkout_sha': checkout_sha, 'input_sha256': checksums},
                     sort_keys=True), flush=True)


def _ci_service(port: int) -> None:
    # This route is only for the isolated service in one-job-pg18-rehearsal.yml.
    if (os.getenv('GITHUB_ACTIONS') != 'true' or
            os.getenv('GITHUB_EVENT_NAME') != 'pull_request' or
            os.getenv('RUNNER_OS') != 'Linux' or
            os.getenv('GITHUB_REPOSITORY') != 'olegmed1-art/bridge-video-free' or
            port != 5432):
        raise RuntimeError('CI_SERVICE_CONTEXT_INVALID')
    actual_sha = _run(['git', 'rev-parse', 'HEAD']).strip()
    if actual_sha != os.getenv('ONE_JOB_EXPECTED_HEAD_SHA'):
        raise RuntimeError('CI_SERVICE_CHECKOUT_SHA_INVALID')
    parsed = urlsplit(os.environ.get('ONE_JOB_CI_ADMIN_DATABASE_URL', ''))
    if (parsed.scheme != 'postgresql' or parsed.hostname != 'localhost' or
            parsed.port != port or parsed.username != 'postgres' or
            parsed.path != '/postgres' or parsed.password != 'postgres'):
        raise RuntimeError('CI_SERVICE_DSN_INVALID')
    psql = shutil.which('psql')
    if not psql:
        raise RuntimeError('CI_SERVICE_PSQL_MISSING')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('PG') and not key.endswith('DATABASE_URL')}
    env['PGCLIENTENCODING'] = 'UTF8'
    env['PGPASSWORD'] = parsed.password
    token = _SYNTHETIC_PASSWORD.set(parsed.password)
    try:
        # This dedicated service has no other job using its cluster-global roles.
        with _connect(port) as connection:
            version, actual_port, database, login, existing, existing_role = connection.execute(
                "SELECT current_setting('server_version_num')::integer,"
                "inet_server_port(),current_database(),current_user,"
                "to_regnamespace('video_queue'),"
                "EXISTS(SELECT 1 FROM pg_roles WHERE rolname='video_subset_owner')"
            ).fetchone().values()
            if not (180000 <= version < 190000 and actual_port == port and
                    database == 'postgres' and login == 'postgres' and
                    existing is None and not existing_role):
                raise RuntimeError('CI_SERVICE_DATABASE_INVALID')
        prefix = [psql, '-X', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1',
                  '-p', str(port), '-U', 'postgres', '-d', 'postgres']
        with tempfile.TemporaryDirectory(prefix='one-job-ci-', dir=ROOT) as tmp:
            _exercise(port, Path(tmp), prefix, env,
                      'github_ci_postgres18_service', parsed.password)
    finally:
        _SYNTHETIC_PASSWORD.reset(token)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--pg-bin', type=Path)
    parser.add_argument('--port', type=int, default=55442)
    parser.add_argument('--ci-service', action='store_true')
    args = parser.parse_args()
    if args.ci_service:
        if args.pg_bin is not None or args.port != 5432:
            raise RuntimeError('CI_SERVICE_ARGUMENTS_INVALID')
        _ci_service(args.port)
        return
    if args.pg_bin is None:
        parser.error('--pg-bin is required for a disposable local cluster')
    pg_bin = args.pg_bin.resolve(strict=True)
    for exe in ('initdb.exe', 'pg_ctl.exe', 'psql.exe'):
        if not (pg_bin / exe).is_file():
            raise RuntimeError(f'missing local PG18 binary: {exe}')
    if not 1024 <= args.port <= 65535:
        raise RuntimeError('invalid disposable port')
    # Avoid ambient libpq connection overrides in every subprocess.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('PG') and not key.endswith('DATABASE_URL')}
    env['PGCLIENTENCODING'] = 'UTF8'
    with tempfile.TemporaryDirectory(prefix='one-job-pg18-', dir=ROOT) as tmp:
        data = Path(tmp) / 'data'
        log = Path(tmp) / 'postgres.log'
        assert data.resolve().is_relative_to(ROOT.resolve())
        _run([str(pg_bin / 'initdb.exe'), '-D', str(data), '-U', 'postgres',
              '-A', 'trust', '--no-instructions', '--no-sync'], env=env)
        started = False
        try:
            # Do not capture pg_ctl pipes: the spawned postmaster inherits
            # them and keeps subprocess.run waiting for EOF indefinitely.
            start = subprocess.run(
                [str(pg_bin / 'pg_ctl.exe'), '-D', str(data), '-l', str(log),
                 '-o', f'-h 127.0.0.1 -p {args.port}', 'start'],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=90, check=False)
            if start.returncode:
                raise RuntimeError(f'disposable pg_ctl start failed: {log.read_text(errors="replace")[-1500:]}')
            started = True
            prefix = [str(pg_bin / 'psql.exe'), '-X', '-v', 'ON_ERROR_STOP=1',
                      '-h', '127.0.0.1', '-p', str(args.port), '-U', 'postgres',
                      '-d', 'postgres']
            dsn = f'postgresql://postgres@127.0.0.1:{args.port}/postgres'
            with psycopg.connect(dsn) as connection:
                version, port, directory = connection.execute(
                    'SELECT current_setting(\'server_version_num\')::integer,'
                    'inet_server_port(),current_setting(\'data_directory\')').fetchone()
                assert 180000 <= version < 190000
                assert port == args.port
                assert Path(directory).resolve() == data.resolve()
            _exercise(args.port, Path(tmp), prefix, env, 'disposable_local_pg18')
        finally:
            if started:
                _run([str(pg_bin / 'pg_ctl.exe'), '-D', str(data), '-m', 'immediate',
                      'stop'], env=env)


if __name__ == '__main__':
    main()
