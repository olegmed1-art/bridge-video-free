"""Read-only owner evidence for one pilot under an explicit no-write window.

This is an observation, not a database lock or self-approval. The controller
must independently accept its bytes and hold the agreed window through terminal
and trigger readback. Privileged concurrent edits are outside that assumption.
"""
import re

from database import native_cli_permission_engine as engine
from ops.native_permission_hold_guard import EXPECTED_TARGET
from . import codex_cli_bridge as bridge


def require(ok):
    if not ok:
        raise RuntimeError('PILOT_OWNER_PREFLIGHT_INVALID')


def validate(value, dispatch, issued_at, expires_at):
    require(type(value) is dict and set(value) == {'target', 'dispatch_sha256', 'task_id',
        'role_id', 'work_item_id', 'goal_json_sha256', 'successor_task_key', 'can_repair',
        'publication', 'competing_native_receipts', 'agreement_sha256', 'issued_at', 'expires_at'})
    require(value['target'] == EXPECTED_TARGET and value['dispatch_sha256'] ==
            bridge.digest(bridge.canonical(dispatch)))
    require(all(type(value[k]) is str and bridge.UUID.fullmatch(value[k])
                for k in ('task_id', 'work_item_id'))
            and value['task_id'] == dispatch['assignment'].get('task_id')
            and value['role_id'] == dispatch['assignment'].get('role')
            and type(value['role_id']) is str and bool(value['role_id']))
    require(all(type(value[k]) is str and re.fullmatch('[0-9a-f]{64}', value[k])
                for k in ('goal_json_sha256', 'agreement_sha256')))
    require(value['successor_task_key'] is None and value['can_repair'] is False
            and type(value['competing_native_receipts']) is int and value['competing_native_receipts'] == 0
            and type(value['issued_at']) is int and type(value['expires_at']) is int
            and value['issued_at'] == issued_at and value['expires_at'] == expires_at)
    publication = value['publication']
    require(type(publication) is dict and set(publication) == {'status', 'version', 'comment_id'}
            and publication['status'] == 'PUBLISHED' and type(publication['version']) is int
            and publication['version'] == 3 and type(publication['comment_id']) is int
            and publication['comment_id'] > 0)


def observe(conn, dispatch, *, issued_at, expires_at, agreement_sha256):
    """Caller retains result privately for independent acceptance; never grants."""
    target = engine.Target(**{**EXPECTED_TARGET,
                              'neon': engine.NeonBinding(**EXPECTED_TARGET['neon'])})
    require(conn.read_only and conn.autocommit)
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='5s'")
        engine.identity(conn, target)
        row = conn.execute('''SELECT to_jsonb(o),to_jsonb(t),to_jsonb(r),to_jsonb(w),to_jsonb(a)
            FROM autopilot.role_dispatch_outbox o
            JOIN autopilot.task t USING(task_id)
            JOIN autopilot.role_registry r ON r.role_id=o.role
            JOIN autopilot.project_work_task m USING(task_id)
            JOIN autopilot.project_work_item w USING(work_item_id)
            CROSS JOIN LATERAL autopilot.get_dispatch_assignment(o.dispatch_id) a
            WHERE o.dispatch_id=%s::uuid''', (dispatch['dispatch_id'],)).fetchone()
        require(row is not None and len(row) == 5)
        outbox, task, role, work, assignment = row
        require(assignment == dispatch['assignment'] and role['enabled'] is True
                and task['status'] == 'WAITING_EXTERNAL' and work['state'] == 'ACTIVE'
                and work['last_task_id'] == task['task_id']
                and all(outbox[k] == dispatch[k] for k in ('dispatch_id', 'expected_head_sha',
                    'mode', 'target_pr', 'task_fingerprint')))
        count = conn.execute("SELECT count(*) FROM autopilot.native_cli_receipt "
                             "WHERE state<>'TERMINAL'").fetchone()[0]
        goal = task['goal_json']
        require(type(goal) is dict)
        value = dict(target=EXPECTED_TARGET, dispatch_sha256=bridge.digest(bridge.canonical(dispatch)),
            task_id=task['task_id'], role_id=role['role_id'], work_item_id=work['work_item_id'],
            goal_json_sha256=bridge.digest(bridge.canonical(goal)),
            successor_task_key=goal.get('successor_task_key'), can_repair=role['can_repair'],
            publication=dict(status=outbox['status'], version=outbox['delivery_contract_version'],
                             comment_id=outbox['github_dispatch_comment_id']),
            competing_native_receipts=count, agreement_sha256=agreement_sha256,
            issued_at=issued_at, expires_at=expires_at)
        validate(value, dispatch, issued_at, expires_at)
        return value
