"""Explicit, inactive owner adapter for the proposed SQL65 terminal transition.

The SQL server verifies its own immutable terminal receipt and exact task/epoch.
This adapter creates no connection, authority, grant, credential or service. It
reuses the original terminal verifier; a digest or client Mapping is not proof
of persistence. A lost COMMIT remains UNKNOWN and is never automatically retried.
DONE here means persisted processing, never owner ACK or network delivery.
"""
from datetime import datetime
import threading

from .native_video_binding_provider import NativeVideoSession, uuid
from .owner_assignment_enrollment import _one
from .receipt_encoding import canonical_bytes, strict_loads, sha256
from .terminal_finish_contract import verify_terminal_response


COMPLETE_SQL = 'SELECT autopilot.complete_native_video_task_v1(%s::uuid,%s,%s,%s) AS result'


class NativeCompletionRefused(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise NativeCompletionRefused(code) from None


class NativeVideoTerminalCompletion:
    """One attempt with an already-authenticated original owner session.

    A known successful duplicate returns detached cached result DATA. It makes
    no new assertion about live authority and performs no further effect. An
    uncertain attempt poisons both adapter and session. Recovery after UNKNOWN
    requires explicit read-only owner reconciliation, not another completion.
    """
    def __init__(self, *, session, task_id, lease_epoch, enabled=False):
        need(type(session) is NativeVideoSession and not session.worker,
             'NATIVE_COMPLETION_OWNER_SESSION_REQUIRED')
        need(uuid(task_id) and type(lease_epoch) is int and lease_epoch > 0
             and type(enabled) is bool, 'NATIVE_COMPLETION_TASK_BINDING')
        self.session, self.task_id, self.epoch = session, task_id, lease_epoch
        self.enabled = enabled
        self._binding = (session, task_id, lease_epoch, enabled)
        self._lock = threading.Lock()
        self._attempted = False
        self._input = None
        self._result = None

    def complete(self, response, *, assignment, selected, bundle):
        need(self.enabled, 'NATIVE_COMPLETION_DISABLED')
        need((self.session, self.task_id, self.epoch, self.enabled) == self._binding,
             'NATIVE_COMPLETION_BINDING_CHANGED')
        # The existing verifier checks selector, source, selected/artifact hashes,
        # former lease and closed queue result BEFORE the new SQL transition.
        raw, _ = verify_terminal_response(response, assignment=assignment,
            selected=selected, bundle=bundle)
        need(assignment.value['autopilot']['task_id'] == self.task_id
             and assignment.value['autopilot']['lease_epoch'] == self.epoch
             and type(assignment.value['autopilot']['lease_epoch']) is int,
             'NATIVE_COMPLETION_ASSIGNMENT')
        need(self._lock.acquire(blocking=False), 'NATIVE_COMPLETION_BUSY')
        acquired = False
        try:
            if self._attempted:
                need(self._result is not None, 'NATIVE_COMPLETION_UNKNOWN_NO_RETRY')
                need(self._input == raw, 'NATIVE_COMPLETION_RECEIPT_CONFLICT')
                return strict_loads(self._result, max_bytes=65536)
            self._attempted = True  # Sticky BEFORE any transaction or possible effect.
            self._input = raw
            session = self.session
            acquired = session.lock.acquire(blocking=False)
            need(acquired, 'NATIVE_COMPLETION_SESSION_BUSY')
            session.check()
            connection = session.connection
            with connection.transaction():
                with connection.cursor() as cursor:
                    session.identity(cursor)
                    row = _one(cursor, COMPLETE_SQL,
                        (self.task_id, self.epoch, raw.decode('utf8'), sha256(raw)), {'result'})
                    result = row['result']
                    expected = dict(schema='native-video-completion/v1', status='DONE',
                        task_id=self.task_id, lease_epoch=self.epoch,
                        job_id=assignment.value['video']['job_id'],
                        selector_sha256=assignment.selector_sha256,
                        assignment_sha256=assignment.sha256,
                        terminal_receipt_sha256=sha256(raw), processing_persisted=True,
                        delivery_state='NOT_ATTESTED')
                    need(type(result) is dict and set(result) == set(expected) | {'completed_at_utc'}
                         and all(result[k] == v for k, v in expected.items())
                         and type(result['lease_epoch']) is int
                         and result['processing_persisted'] is True,
                         'NATIVE_COMPLETION_RESULT_BINDING')
                    stamp = datetime.strptime(result['completed_at_utc'], '%Y-%m-%dT%H:%M:%S.%fZ')
                    need(stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ') == result['completed_at_utc'],
                         'NATIVE_COMPLETION_RESULT_STAMP')
                    result_raw = canonical_bytes(result, max_bytes=65536)
            session.check()  # Only publish after real outer COMMIT acknowledgement.
            self._result = result_raw
            return strict_loads(result_raw, max_bytes=65536)
        except BaseException:
            if self._result is None:
                self.session.failed = True
                raise NativeCompletionRefused('NATIVE_COMPLETION_UNKNOWN_NO_RETRY') from None
            raise
        finally:
            if acquired:
                self.session.lock.release()
            self._lock.release()
