"""Explicit opt-in private result handoff for one approved queue lease.

Only the explicit one-shot resident path constructs this object. Ordinary
resident polling does not. It uses the existing queue-worker identity and
the 0062 private SQL gate, with no broad writer DSN.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from database.video_one_job_private_result import stage_completed_drive_result_private


class PrivateResultHandoffError(RuntimeError):
    error_code = 'UV_PRIVATE_RESULT_HANDOFF_FAILED'


class PrivateOneJobConfigurationError(PrivateResultHandoffError):
    """Stop the private route without a worker retry or alternate writer."""


_BOUND = ('batch_id', 'job_id', 'lease_token', 'stable_job_key',
          'source_file_id', 'algorithm_revision', 'attempt_count',
          'output_folder_id')


class PrivateResultStagingHandoff:
    def __init__(self, *, claim: Mapping[str, Any], worker_key: str,
                 admission_id: str, source_fingerprint: str,
                 open_connection: Callable, assignment=None):
        if not callable(open_connection) or not isinstance(worker_key, str) or not worker_key:
            raise PrivateResultHandoffError('UV_PRIVATE_RESULT_CONNECTION_REQUIRED')
        try:
            self.binding = {key: str(claim[key]) for key in _BOUND}
            self.admission_id = str(UUID(str(admission_id)))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise PrivateResultHandoffError('UV_PRIVATE_RESULT_CLAIM_INCOMPLETE') from exc
        if (any(not value or value == 'None' for value in self.binding.values())
                or type(claim['attempt_count']) is not int
                or not 1 <= claim['attempt_count'] <= 3
                or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}',
                                    self.binding['output_folder_id'])
                or not isinstance(source_fingerprint, str)
                or not re.fullmatch(r'[0-9a-f]{64}', source_fingerprint)):
            raise PrivateResultHandoffError('UV_PRIVATE_RESULT_CLAIM_INCOMPLETE')
        self.worker_key = worker_key
        self.source_fingerprint = source_fingerprint
        self.open_connection = open_connection
        self.assignment = assignment  # Owner-enrolled binding, never inferred.
        self._sealed_attempted = False
        self._sealed_snapshot = None
        self.terminal_recovery = None  # Explicit local join; no default owner capability.

    def stage_selected_terminal(self,claim,worker_key,token,selected_output):
        """Sealed path: exact selected ID; missing assignment is configuration HOLD."""
        from .terminal_finish_contract import Assignment
        from .receipt_encoding import canonical_bytes
        import copy
        if type(self.assignment) is not Assignment:
            raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_ASSIGNMENT_UNBOUND')
        if (worker_key != self.worker_key or type(token) is not str or not token
                or any(str(claim.get(k))!=v for k,v in self.binding.items())):
            raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_CLAIM_CHANGED')
        self.assignment.check_claim(claim,worker_key)
        if (not isinstance(selected_output,Mapping)
                or not isinstance(selected_output.get('saved_frame_observations'),Mapping)
                or not isinstance(selected_output.get('completed_report_baseline'),Mapping)):
            raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_FIELDS_REQUIRED')
        if self._sealed_attempted:
            raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_STAGE_REPLAY_NO_RETRY')
        if self.terminal_recovery is not None:
            self.terminal_recovery.before_stage(self)
        self._sealed_attempted=True  # Sticky before IO, including lost COMMIT reply.
        try:
            selected_raw=canonical_bytes(dict(selected_output));claim_raw=canonical_bytes(dict(claim))
        except Exception:
            raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_INPUT_INVALID') from None
        from .sealed_private_staging import stage_selected_drive_result_private
        admission=dict(admission_id=self.admission_id,job_id=self.binding['job_id'],
            lease_token=self.binding['lease_token'],worker_key=worker_key,
            attempt_count=int(self.binding['attempt_count']),source_fingerprint=self.source_fingerprint)
        try:
            staged=stage_selected_drive_result_private(self.open_connection,claim=claim,
                selected=selected_output,token=token,assignment=self.assignment,admission=admission)
            if canonical_bytes(dict(selected_output))!=selected_raw or canonical_bytes(dict(claim))!=claim_raw:
                raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_SELECTION_CHANGED')
            from .terminal_finish_contract import checked_bundle
            checked_bundle(staged)
            self._sealed_snapshot=(copy.deepcopy(dict(claim)),copy.deepcopy(dict(selected_output)),
                copy.deepcopy(staged),selected_raw,canonical_bytes(staged,max_bytes=4096),self.assignment.raw,claim_raw)
            if self.terminal_recovery is not None:
                self.terminal_recovery.prepared(self)
            return staged
        except Exception:
            # Includes uncertain staging COMMIT/Drive readback. Never fall back
            # to basename staging, worker retry, or the v1 finish route.
            raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_STAGE_UNKNOWN_NO_RETRY') from None

    def make_post_finish_reader(self,*,connection_validator,access_token,
                                metadata_reader=None,downloader=None):
        """Build after stage from retained PRE-finish facts, never finish reply."""
        from .receipt_encoding import canonical_bytes
        if self._sealed_snapshot is None:
            raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_STAGE_NOT_CONFIRMED')
        claim,selected,bundle,selected_raw,bundle_raw,assignment_raw,claim_raw=self._sealed_snapshot
        if (self.assignment is None or self.assignment.raw!=assignment_raw
                or canonical_bytes(selected)!=selected_raw or canonical_bytes(claim)!=claim_raw
                or canonical_bytes(bundle,max_bytes=4096)!=bundle_raw):
            raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_SNAPSHOT_CHANGED')
        from database.video_post_finish_receipt import PostFinishReceiptReader
        return PostFinishReceiptReader(assignment=self.assignment,claim=claim,selected=selected,bundle=bundle,
            open_connection=self.open_connection,connection_validator=connection_validator,access_token=access_token,
            metadata_reader=metadata_reader,downloader=downloader)

    def post_finish_reader(self,**ports):
        """Construct the lazy TerminalAckQueue source port before stage exists."""
        from database.video_post_finish_receipt import StagedPostFinishReceiptReader
        return StagedPostFinishReceiptReader(self,**ports)

    def retain_prepared_terminal(self, retention, *, metadata):
        """Save original PRE-finish facts in the existing owner contour.

        The returned watermark must be retained by the UID1001 owner outside
        this process before FINISH_ATTEMPTED. This method neither finishes nor
        asserts that the unimplemented owner adapter has stored that watermark.
        """
        from .private_admission_retention import TerminalHandoffRetention
        if type(retention) is not TerminalHandoffRetention or self._sealed_snapshot is None:
            raise PrivateOneJobConfigurationError('ONE_JOB_TERMINAL_RETENTION_UNBOUND')
        claim, selected, bundle, selected_raw, bundle_raw, assignment_raw, claim_raw = self._sealed_snapshot
        from .receipt_encoding import canonical_bytes
        if (self.assignment is None or self.assignment.raw != assignment_raw
                or canonical_bytes(claim) != claim_raw or canonical_bytes(selected) != selected_raw
                or canonical_bytes(bundle, max_bytes=4096) != bundle_raw):
            raise PrivateOneJobConfigurationError('ONE_JOB_SEALED_SNAPSHOT_CHANGED')
        return retention.prepare(assignment=self.assignment, claim=claim,
                                 selected=selected, bundle=bundle, metadata=metadata)

    def before_terminal_finish(self):
        """Durability barrier at the real v2 finish callsite, not queue return."""
        if self.terminal_recovery is not None:
            self.terminal_recovery.before_finish(self)

    def __call__(self, claim: Mapping[str, Any], worker_key: str, token: str) -> dict[str, Any]:
        if (worker_key != self.worker_key or not isinstance(token, str) or not token
                or any(str(claim.get(key)) != value for key, value in self.binding.items())):
            raise PrivateResultHandoffError('UV_PRIVATE_RESULT_LEASE_BINDING_MISMATCH')
        admission = dict(admission_id=self.admission_id, job_id=self.binding['job_id'],
                         lease_token=self.binding['lease_token'], worker_key=worker_key,
                         attempt_count=int(self.binding['attempt_count']),
                         source_fingerprint=self.source_fingerprint)
        staged = stage_completed_drive_result_private(
            self.open_connection, token=token, admission=admission,
            stable_job_key=self.binding['stable_job_key'],
            algorithm_revision=self.binding['algorithm_revision'],
            output_folder_id=self.binding['output_folder_id'])
        if staged.get('status') != 'PRIVATE_RESULT_STAGED':
            raise PrivateResultHandoffError('UV_PRIVATE_RESULT_NOT_COMMITTED')
        return staged


def recover_retained_terminal(retention, watermark_raw, *, open_connection,
                              connection_validator, access_token,
                              metadata_reader=None, downloader=None, expected_binding=None):
    """Only original reader.recover; no caller receives a runnable handoff.

    Fresh owner authorization/Assignment/target/LOGIN/READ_RESULT validation
    remains in the existing reader. No grant is issued by this loader.
    UNKNOWN with no terminal receipt stays unknown; no work or ACK is replayed.
    """
    from .private_admission_retention import TerminalHandoffRetention
    from database.video_post_finish_receipt import PostFinishReceiptReader
    if type(retention) is not TerminalHandoffRetention:
        raise PrivateOneJobConfigurationError('ONE_JOB_TERMINAL_RETENTION_UNBOUND')
    saved = retention.load(watermark_raw)
    if expected_binding is not None:
        from .receipt_encoding import canonical_bytes,sha256
        envelope=saved['envelope']
        actual=dict(claim_sha256=sha256(canonical_bytes(saved['claim'],max_bytes=16384)),
            assignment_sha256=saved['assignment'].sha256,selector=saved['assignment'].selector_sha256,
            intent_sha256=envelope['intent_hash'],**{k:envelope[k] for k in (
                'enrollment_sha256','admission_record_sha256','admission_anchor_sha256',
                'target_identity_sha256','principal')})
        if type(expected_binding) is not dict or expected_binding!=actual:
            raise PrivateOneJobConfigurationError('ONE_JOB_TERMINAL_HEAD_INPUT_CHANGED')
    reader = PostFinishReceiptReader(assignment=saved['assignment'], claim=saved['claim'],
        selected=saved['selected'], bundle=saved['bundle'], open_connection=open_connection,
        connection_validator=connection_validator, access_token=access_token,
        metadata_reader=metadata_reader, downloader=downloader)
    return reader.recover()


__all__ = ['PrivateResultStagingHandoff', 'PrivateResultHandoffError', 'recover_retained_terminal',
           'PrivateOneJobConfigurationError']
