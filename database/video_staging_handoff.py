"""Local composition seam for one completed generation; no installed caller.

The transport is a trusted SERVER dependency, never JSON, an environment flag,
or an artifact receipt. It must pin one generation (including the verified PDF
to embedded-master relation), enforce staging-write authorization, and own an
idle authorized writer connection. This module does not implement admission,
credentials, discovery, outbox publication, teacher authorization or canon writes.
Universal Video's SHADOW_REVIEW_ONLY receipt is NOT admission to this seam.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import re
from typing import Protocol

from psycopg.pq import TransactionStatus

from bridge_contracts.video_local_evidence import LocalEvidenceReader
from bridge_contracts.video_replay_input import build_replay_input, read_json, sha256
from database.source_identity_persistence import ensure_drive_source_identity_on_connection
from database.video_candidate_persistence import persist_quality_candidates_on_connection, validate_replay
from database.video_result_persistence import persist_video_result_on_connection
from diana_longitudinal_quality_v4_2 import build_quality_layer


@dataclass(frozen=True)
class GenerationPins:
    """Out-of-band transport bindings, not media/role/identity authorization.

    source_media_sha256 binds the existing source claim. Unless original-media
    evidence is independently available, the replay must retain MEDIA_UNVERIFIED.
    """
    job_id: str
    algorithm_revision: str
    source_drive_id: str
    source_media_sha256: str
    master_pdf_drive_id: str
    master_pdf_sha256: str
    master_raw_sha256: str
    ai_done_sha256: str


@dataclass(frozen=True)
class CompletedGeneration:
    pins: GenerationPins
    master_bytes: bytes
    ai_done_bytes: bytes
    selection_bytes: bytes
    evidence_reader: LocalEvidenceReader
    constraints_bytes: bytes | None = None
    speaker_map_bytes: bytes | None = None


class HandoffTransport(Protocol):
    def load_completed(self) -> CompletedGeneration:
        """Read one pinned generation without latest-result rediscovery.

        Independently verify the report bytes and exact embedded master before
        returning. Pins copied solely from generated artifacts are insufficient.
        """
        ...

    def open_writer(self, generation: CompletedGeneration) -> AbstractContextManager:
        """Own/close an idle writer using the existing operator worker credential.

        Queue receipts and artifact hashes never authorize a writer. The narrow
        operator transport needs no new per-generation grant or member identity.
        """
        ...

    def verify_writer(self, connection, generation: CompletedGeneration, *, result=None) -> None:
        """Raise unless the actual session and fixed-school binding are valid.

        Called inside the outer transaction, before and after the writers.
        These observations are not an instantaneous revocation/commit fence.
        """
        ...


class _Blocked(Exception):
    pass


def _need(condition, code):
    if not condition:
        raise _Blocked(code)


def _prepare(generation):
    _need(isinstance(generation, CompletedGeneration), 'COMPLETED_GENERATION_REQUIRED')
    pins = generation.pins
    _need(isinstance(pins, GenerationPins), 'GENERATION_PINS_REQUIRED')
    _need(all(isinstance(v, str) and v.strip() for v in (
        pins.job_id, pins.algorithm_revision, pins.source_drive_id, pins.master_pdf_drive_id)),
        'GENERATION_IDENTITY_REQUIRED')
    _need(all(isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in (
        pins.source_media_sha256, pins.master_pdf_sha256, pins.master_raw_sha256, pins.ai_done_sha256)),
        'GENERATION_HASH_REQUIRED')
    _need(type(generation.master_bytes) is bytes and type(generation.ai_done_bytes) is bytes
          and type(generation.selection_bytes) is bytes, 'IMMUTABLE_INPUT_BYTES_REQUIRED')
    _need(sha256(generation.master_bytes) == pins.master_raw_sha256
          and sha256(generation.ai_done_bytes) == pins.ai_done_sha256, 'GENERATION_BYTES_MISMATCH')
    master, done = read_json(generation.master_bytes), read_json(generation.ai_done_bytes)
    _need(isinstance(master, dict) and isinstance(done, dict), 'GENERATION_OBJECTS_REQUIRED')
    _need(done.get('status') == 'AI_DONE', 'PROCESSING_INCOMPLETE')
    _need(master.get('job_id') == done.get('job_id') == pins.job_id
          and master.get('algorithmRevision') == done.get('algorithmRevision') == pins.algorithm_revision,
          'GENERATION_IDENTITY_MISMATCH')
    source, report = master.get('source') or {}, done.get('masterPdf') or {}
    _need(source.get('driveId') == (done.get('original') or {}).get('driveId') == pins.source_drive_id,
          'SOURCE_ID_MISMATCH')
    _need(source.get('sha256') == pins.source_media_sha256, 'SOURCE_HASH_MISMATCH')
    _need(report.get('driveId') == pins.master_pdf_drive_id
          and report.get('sha256') == pins.master_pdf_sha256
          and report.get('masterJsonEmbedded') is True
          and report.get('masterJsonSha256') == pins.master_raw_sha256,
          'MASTER_REPORT_BINDING_MISMATCH')
    selection = read_json(generation.selection_bytes)
    _need(isinstance(selection, dict) and selection.get('input_kind') == 'full_master'
          and selection.get('source_document_drive_id') == pins.master_pdf_drive_id,
          'FULL_MASTER_SELECTION_REQUIRED')
    _need(isinstance(generation.evidence_reader, LocalEvidenceReader), 'LOCAL_EVIDENCE_READER_REQUIRED')
    generation.evidence_reader.bind_master(generation.master_bytes)
    packet = build_replay_input(generation.master_bytes, selection,
                                constraints_bytes=generation.constraints_bytes,
                                speaker_map_bytes=generation.speaker_map_bytes)
    # The existing producer uses these very master bytes. Never accept a second
    # caller-provided quality mapping, persisted IDs, or re-hashed candidate JSON.
    # No DDS executor or correction-store resolver is installed by this seam.
    quality = build_quality_layer(master, source_draft_input=packet,
                                  replay_evidence_reader=generation.evidence_reader)
    validate_replay(quality.get('video_canon_auto_pipeline'))
    return master, done, {'job_id': pins.job_id, 'quality_v2': quality}


def handoff_completed_video(*, transport: HandoffTransport | None = None) -> dict:
    """Compose existing writers atomically; return IDs only after commit + close.

    Default is BLOCKED before any IO. A successful receipt is technical staging
    only; consumers still use the existing independently authorized readers.
    Any failure after opening the writer has an unconfirmed outcome: reconcile
    the exact generation before retrying, never infer rollback from an exception.
    """
    phase = 'INPUT'
    outcome = 'NOT_STARTED'
    try:
        _need(transport is not None, 'ADMISSION_TRANSPORT_UNAVAILABLE')
        generation = transport.load_completed()
        master, done, payload = _prepare(generation)
        phase, outcome = 'WRITE', 'UNKNOWN'
        transaction_exited = False
        with transport.open_writer(generation) as connection:
            _need(connection.info.transaction_status == TransactionStatus.IDLE, 'IDLE_WRITER_REQUIRED')
            with connection.transaction():
                transport.verify_writer(connection, generation)
                result = persist_video_result_on_connection(connection, master, done)
                _need(result.get('persisted') is True and result.get('rolled_back') is False,
                      'RESULT_NOT_PERSISTED')
                identity = ensure_drive_source_identity_on_connection(
                    connection, source_drive_id=generation.pins.source_drive_id,
                    display_name=master['source'].get('name'), job_id=generation.pins.job_id)
                _need(identity.get('source_id') == result.get('source_id'), 'SOURCE_IDENTITY_MISMATCH')
                staging = persist_quality_candidates_on_connection(connection, payload, persisted_result=result)
                _need(staging.get('status') == 'PERSISTED', 'STAGING_NOT_PERSISTED')
                _need(staging.get('authoritative_tables_modified') is False
                      and staging.get('replay_receipt_id')
                      and isinstance(staging.get('candidate_ids'), list)
                      and len(staging['candidate_ids']) == len(payload['quality_v2']['candidate_staging_records'])
                      and all(staging.get(k) == result.get(k) and result.get(k) for k in (
                          'source_id', 'source_asset_id', 'analysis_run_id')), 'STAGING_RESULT_BINDING_MISMATCH')
                transport.verify_writer(connection, generation, result=result)
            transaction_exited = True
        _need(transaction_exited, 'HANDOFF_WRITE_UNCONFIRMED')
        _need(connection.closed is True, 'WRITER_NOT_CLOSED')
        # Returning inside either context would falsely attest commit/close.
        replay = payload['quality_v2']['video_canon_auto_pipeline']
        return {'schema': 'video-staging-handoff-v1', 'status': 'COMMITTED_STAGING',
                'commit_outcome': 'COMMITTED', 'job_id': generation.pins.job_id,
                'analysis_run_id': result['analysis_run_id'], 'source_id': result['source_id'],
                'candidate_ids': staging['candidate_ids'], 'replay_receipt_id': staging['replay_receipt_id'],
                'replay_sha256': replay['replay_sha256'], 'replay_status': replay['status'],
                'canon_write_allowed': False, 'teacher_access_granted': False}
    except _Blocked as exc:
        code = str(exc)  # Only static codes raised here; never expose backend exceptions.
    except Exception:
        code = 'HANDOFF_INPUT_UNAVAILABLE' if phase == 'INPUT' else 'HANDOFF_WRITE_UNCONFIRMED'
    return {'schema': 'video-staging-handoff-v1', 'status': 'BLOCKED', 'gaps': [code],
            'commit_outcome': outcome, 'canon_write_allowed': False, 'teacher_access_granted': False}
