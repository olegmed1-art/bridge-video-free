"""One bounded native-delivery reconciliation step; no scheduler or new slots.

The queue port must provide an already reserved item from the shared admission
controller. Its ACK/finish methods must be atomic and idempotent. This module
does not implement that SQL port and must not be wired to legacy GitHub ACKs.
The provider is the durable codex_cli_bridge, on the reservation's original host.
"""
from __future__ import annotations

from typing import Protocol

from . import codex_cli_bridge as bridge


class QueuePort(Protocol):
    def begin_submission(self, request: dict) -> bool:
        """Persist one-shot creation intent; never regrant after lost journal."""

    def snapshot(self, dispatch_id: str) -> dict:
        """Return state, immutable request, provider_task_id, and terminal.

        States: RESERVED, SUBMITTED, TERMINAL. Only the native reservation
        owner may see this row. Do not derive native state from a GitHub PR.
        """

    def acknowledge(self, request: dict, task_id: str, prompt_sha256: str) -> None:
        """CAS native reservation to SUBMITTED or confirm an identical replay."""

    def finish(self, request: dict, task_id: str, receipt: dict) -> None:
        """Atomically retain exact receipt and complete/release the shared slot.

        SUCCEEDED requires locked current task/work authority. BLOCKED may
        close the original provider-terminal attempt after work was paused.
        Conflicting replays must reject, including a different receipt hash.
        """


class ProviderPort(Protocol):
    def lookup(self, request: dict) -> dict | None:
        """Read the original host's retained creation receipt without creating."""

    def submit(self, request: dict) -> dict:
        """Persist intent before creation; repeat calls return the same receipt."""

    def collect(self, dispatch_id: str) -> dict:
        """Return immutable terminal evidence for the retained native task ID."""


class AuthorityPort(Protocol):
    def inspect(self, request: dict) -> dict:
        """Fresh primary read: current (bool), head_sha (40 hex), open (bool)."""


def _authority(authority: AuthorityPort, request: dict) -> dict:
    current = authority.inspect(request)
    if (type(current.get('current')) is not bool or type(current.get('open')) is not bool
            or not isinstance(current.get('head_sha'), str)
            or not bridge.SHA.fullmatch(current['head_sha'])):
        raise ValueError('CURRENT_AUTHORITY_UNAVAILABLE')
    return current


def _bound(current: dict, request: dict) -> bool:
    return (current['current'] and current['open']
            and current['head_sha'] == request['expected_head_sha'])


def _terminal(request: dict, result: dict, current: dict) -> dict:
    if result['state'] in ('PROVIDER_TERMINAL_FAILURE', 'RESULT_REJECTED'):
        code = result['result_code']
        summary = ('Native result failed validation.' if result['state'] == 'RESULT_REJECTED'
                   else 'Native provider reported a terminal failure.')
        status = 'BLOCKED'
    else:
        report = result['report']
        # The bridge is the trusted parser, but the queue binding is checked
        # again here before any terminal mutation.
        if any(report.get(key) != request[key] for key in (
                'dispatch_id', 'expected_head_sha', 'target_pr', 'task_fingerprint')):
            raise ValueError('NATIVE_REPORT_BINDING_INVALID')
        if result['report_sha256'] != bridge.digest(bridge.canonical(report)):
            raise ValueError('NATIVE_REPORT_DIGEST_INVALID')
        status, code, summary = report['status'], report['result_code'], report['summary']
        if status == 'SUCCEEDED' and not _bound(current, request):
            status, code, summary = ('BLOCKED', 'TARGET_AUTHORITY_CHANGED',
                                     'Target head or task authority changed before acceptance.')
        elif status == 'SUCCEEDED' and request['mode'] == 'REPAIR':
            # A generated patch has no authority to attest its own publication.
            status, code, summary = ('BLOCKED', 'TARGET_PR_NOT_UPDATED',
                                     'Native patch retrieved; atomic publication is not connected.')
    if status not in ('SUCCEEDED', 'BLOCKED'):
        raise ValueError('NATIVE_TERMINAL_STATUS_INVALID')
    return {'status': status, 'result_code': code, 'summary': summary,
            'target_head_sha': current['head_sha'],
            'provider_evidence_sha256': bridge.digest(bridge.canonical(result))}


def advance(dispatch_id: str, queue: QueuePort, authority: AuthorityPort,
            provider: ProviderPort = bridge) -> dict:
    """Advance one existing reservation; propagate I/O failures for next tick.

    No loops, no sleep, no new work allocation, no alternate host or sender.
    UNKNOWN creation outcomes are quarantined. ACK retries use the exact saved
    provider ID. Queue terminal state is always read back before DONE is emitted.
    """
    row = queue.snapshot(dispatch_id)
    request = bridge.validate_request(row['request'])
    if request['dispatch_id'] != dispatch_id:
        raise ValueError('NATIVE_RESERVATION_BINDING_INVALID')
    if row['state'] == 'TERMINAL':
        return {'state': 'DONE', 'dispatch_id': dispatch_id,
                'provider_task_id': row['provider_task_id'], 'terminal': row['terminal']}
    if row['state'] not in ('RESERVED', 'SUBMITTED'):
        raise ValueError('NATIVE_RESERVATION_STATE_INVALID')
    if row['state'] == 'RESERVED':
        creation = provider.lookup(request)
        if creation is None:
            current = _authority(authority, request)
            if not _bound(current, request):
                return {'state': 'RESERVATION_HELD', 'reason': 'TARGET_AUTHORITY_CHANGED'}
            if not queue.begin_submission(request):
                return {'state': 'SUBMISSION_UNKNOWN', 'dispatch_id': dispatch_id}
            creation = provider.submit(request)
        if creation['state'] != 'SUBMITTED':
            return {'state': 'SUBMISSION_UNKNOWN', 'dispatch_id': dispatch_id}
        task_id = creation['provider_task_id']
        if not bridge.TASK_URL.fullmatch('https://chatgpt.com/codex/tasks/'+task_id):
            raise ValueError('NATIVE_TASK_ID_INVALID')
        queue.acknowledge(request, task_id, creation['prompt_sha256'])
        # ACK can be lost after commit; next tick repeats the same durable
        # creation receipt, not a second cloud task.
        row = queue.snapshot(dispatch_id)
        if row['request'] != request or row['provider_task_id'] != task_id:
            raise ValueError('NATIVE_ACK_READBACK_CONFLICT')
        if row['state'] not in ('SUBMITTED', 'TERMINAL'):
            raise ValueError('NATIVE_ACK_NOT_RETAINED')
    task_id = row['provider_task_id']
    if row['state'] == 'TERMINAL':
        return {'state': 'DONE', 'dispatch_id': dispatch_id,
                'provider_task_id': task_id, 'terminal': row['terminal']}
    result = provider.collect(dispatch_id)
    if result.get('provider_task_id') != task_id:
        raise ValueError('NATIVE_PROVIDER_TASK_CONFLICT')
    if result['state'] not in ('RESULT_RETRIEVED', 'PROVIDER_TERMINAL_FAILURE', 'RESULT_REJECTED'):
        return {'state': result['state'], 'dispatch_id': dispatch_id, 'provider_task_id': task_id}
    current = _authority(authority, request)
    terminal = _terminal(request, result, current)
    queue.finish(request, task_id, terminal)
    final = queue.snapshot(dispatch_id)
    if (final['state'] != 'TERMINAL' or final['request'] != request
            or final['provider_task_id'] != task_id or final['terminal'] != terminal):
        raise ValueError('NATIVE_TERMINAL_NOT_RETAINED')
    return {'state': 'DONE', 'dispatch_id': dispatch_id,
            'provider_task_id': task_id, 'terminal': terminal}
