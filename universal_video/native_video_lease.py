"""A non-renewable native execution budget, never execution authority.

The original owner injects this object after exact committed enrollment. Its
snapshot/hash/clock values are data: SQL fencing, owner/native checks and the
terminal read grant still independently authorize every effect. No duration is
guessed from a video name, frame count or configuration default. The five-minute
SQL cap and immutable post-BIND expiry are deliberately preserved.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
import time
import threading
from uuid import UUID

from .receipt_encoding import canonical_bytes


NATIVE_WORKER_KEY = 'db-login:autopilot_light_worker_login'


class NativeLeaseRefused(RuntimeError):
    pass


def _need(ok, code):
    if not ok:
        raise NativeLeaseRefused(code)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _expiry(value, *, canonical=False):
    _need(type(value) is str and 20 <= len(value) <= 40, 'NATIVE_LEASE_EXPIRY')
    try:
        parsed = (datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
                  if canonical else datetime.fromisoformat(value.replace('Z', '+00:00')))
        _need(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0,
              'NATIVE_LEASE_EXPIRY')
        if canonical:
            _need(parsed.strftime('%Y-%m-%dT%H:%M:%S.%fZ') == value, 'NATIVE_LEASE_EXPIRY')
        result = parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        raise NativeLeaseRefused('NATIVE_LEASE_EXPIRY') from None
    _need(_finite(result), 'NATIVE_LEASE_EXPIRY')
    return result


@dataclass(frozen=True)
class NativeExecutionBudget:
    """Detached exact binding plus both wall and monotonic deadline limits.

    This is a cooperative timeout; SIGALRM in the original owning worker bounds
    its main-thread processing. It is not a hard cgroup/process resource limit.
    A lost/expired budget is never renewed or converted into fresh authority.
    """
    claim_raw: bytes
    task_id: str
    lease_epoch: int
    assignment_sha256: str
    work_seconds: float
    cleanup_seconds: float
    _expires_wall: float
    _deadline: float
    _wall: object
    _clock: object
    _last_clock: float
    _expired: bool = False
    _sample_lock: object = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    @classmethod
    def from_snapshot(cls, *, snapshot, claim, required_seconds, cleanup_seconds=5,
                      wall=time.time, clock=time.monotonic):
        _need(_finite(required_seconds) and 0 < required_seconds <= 300,
              'NATIVE_EXECUTION_DECLARED_BOUND_REQUIRED')
        _need(_finite(cleanup_seconds) and 1 <= cleanup_seconds <= 30,
              'NATIVE_EXECUTION_CLEANUP_BOUND')
        _need(type(snapshot) is dict and isinstance(claim, Mapping)
              and callable(wall) and callable(clock), 'NATIVE_EXECUTION_SNAPSHOT_REQUIRED')
        selected = dict(claim)
        raw = canonical_bytes(selected, max_bytes=16384)
        _need(snapshot.get('schema') == 'native-video-db-snapshot/v1'
              and _uuid(snapshot.get('task_id'))
              and type(snapshot.get('lease_epoch')) is int and snapshot['lease_epoch'] == 1
              and snapshot.get('worker_id') == NATIVE_WORKER_KEY
              and all(type(snapshot.get(k)) is str and re.fullmatch('[0-9a-f]{64}', snapshot[k])
                      for k in ('binding_sha256', 'assignment_sha256')),
              'NATIVE_EXECUTION_COMMITTED_ENROLLMENT_BINDING')
        _need(type(snapshot.get('claim')) is dict
              and canonical_bytes(snapshot['claim'], max_bytes=16384) == raw
              and all(_uuid(selected.get(k)) for k in ('job_id', 'batch_id', 'lease_token'))
              and type(selected.get('attempt_count')) is int and selected['attempt_count'] == 1,
              'NATIVE_EXECUTION_CLAIM_BINDING')
        expiry = min(_expiry(snapshot.get('expires_at_utc'), canonical=True),
                     _expiry(selected.get('lease_expires_at')))
        now, mono = wall(), clock()
        _need(_finite(now) and _finite(mono), 'NATIVE_EXECUTION_CLOCK')
        available = expiry - now - cleanup_seconds
        _need(available >= required_seconds, 'NATIVE_EXECUTION_BUDGET_INSUFFICIENT')
        return cls(raw, snapshot['task_id'], snapshot['lease_epoch'],
                   snapshot['assignment_sha256'], float(required_seconds),
                   float(cleanup_seconds), expiry, mono + available, wall, clock, mono)

    def remaining(self):
        # The main worker and its monitor share this object; serialize sampling
        # so a delayed sample cannot appear as a monotonic clock rollback.
        with self._sample_lock:
            now, mono = self._wall(), self._clock()
            if (self._expired or not _finite(now) or not _finite(mono)
                    or mono < self._last_clock):
                object.__setattr__(self, '_expired', True)
                raise NativeLeaseRefused('NATIVE_EXECUTION_EXPIRED_OR_CLOCK_ROLLBACK')
            object.__setattr__(self, '_last_clock', mono)
            remaining = min(self._deadline - mono, self._expires_wall - now - self.cleanup_seconds)
            if remaining <= 0:
                object.__setattr__(self, '_expired', True)
                raise NativeLeaseRefused('NATIVE_EXECUTION_EXPIRED')
            return remaining

    def check(self, claim, worker_key):
        _need(worker_key == NATIVE_WORKER_KEY and isinstance(claim, Mapping)
              and canonical_bytes(dict(claim), max_bytes=16384) == self.claim_raw,
              'NATIVE_EXECUTION_CLAIM_BINDING')
        self.remaining()

    def require_work_window(self, claim, worker_key):
        self.check(claim, worker_key)
        _need(self.remaining() >= self.work_seconds, 'NATIVE_EXECUTION_BUDGET_INSUFFICIENT')


def require_native_budget(claim, worker_key, budget):
    """Exact native lane only; callers cannot accidentally enable a legacy lane."""
    if worker_key == NATIVE_WORKER_KEY:
        _need(type(budget) is NativeExecutionBudget, 'NATIVE_EXECUTION_BUDGET_REQUIRED')
        budget.require_work_window(claim, worker_key)
    else:
        _need(budget is None, 'NATIVE_EXECUTION_WRONG_LANE')
    return budget
