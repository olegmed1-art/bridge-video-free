"""Explicit opt-in private result handoff for one approved queue lease.

The resident never constructs this object. It uses the existing queue-worker
identity and the 0062 private SQL gate, with no broad writer DSN.
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
                 open_connection: Callable):
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


__all__ = ['PrivateResultStagingHandoff', 'PrivateResultHandoffError',
           'PrivateOneJobConfigurationError']
