"""Fresh GitHub evidence admits bounded READ_ONLY re-audits, never merge/repair."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .paused_reconcile import REPOSITORY, github, timestamp


def pr_binding(pr, number):
    head = pr['head']['sha']
    if (type(pr['number']) is not int or pr['number'] != number
            or pr['base']['repo']['full_name'] != REPOSITORY
            or pr['head']['repo']['full_name'] != REPOSITORY
            or pr['state'] != 'open' or pr.get('merged_at') is not None
            or not isinstance(head, str) or not re.fullmatch('[0-9a-f]{40}', head)
            or pr['title'].startswith('[Autopilot dispatch]')
            or pr['head']['ref'].startswith('autopilot/dispatch/')):
        raise ValueError('NEXT_STEP_TARGET_INVALID')
    return head


def collect(row, api=github, now=None):
    UUID(str(row['work_item_id']))
    number = row['target_pr']
    if type(number) is not int or not 1 <= number <= 1000000:
        raise ValueError('NEXT_STEP_TARGET_INVALID')
    now = now or datetime.now(timezone.utc)
    observed = timestamp(row['updated_at'])
    if observed > now or row.get('hold_reason') == 'OWNER_HOLD':
        raise ValueError('NEXT_STEP_SNAPSHOT_INVALID')
    head = pr_binding(api(f'pulls/{number}'), number)
    latest = None
    all_good = True
    count = 0
    total = None
    for page in range(1, 11):
        response = api(f'commits/{head}/check-runs?per_page=100&page={page}')
        checks = response['check_runs']
        reported_total = response['total_count']
        if type(reported_total) is not int or reported_total < 0 or not isinstance(checks, list):
            raise ValueError('NEXT_STEP_CHECKS_INVALID')
        if total is not None and total != reported_total:
            raise ValueError('NEXT_STEP_CHECKS_CHANGED')
        total = reported_total
        count += len(checks)
        for check in checks:
            if check['head_sha'] != head:
                raise ValueError('NEXT_STEP_CHECK_HEAD_MISMATCH')
            if (check['status'] != 'completed'
                    or check['conclusion'] not in ('success', 'skipped', 'neutral')
                    or not check['completed_at']):
                all_good = False
                continue
            completed = timestamp(check['completed_at'])
            if completed > now:
                raise ValueError('NEXT_STEP_CHECK_TIME_INVALID')
            latest = max(latest, completed) if latest else completed
        if len(checks) < 100:
            if count != total:
                raise ValueError('NEXT_STEP_CHECKS_INCOMPLETE')
            break
    else:
        raise ValueError('NEXT_STEP_CHECK_PAGE_LIMIT')
    # Fence changes while collecting evidence; the planner reads the head again
    # before materializing. A green CI timestamp only admits an audit, not PASS.
    if pr_binding(api(f'pulls/{number}'), number) != head:
        raise ValueError('NEXT_STEP_HEAD_CHANGED')
    return head, latest if all_good and count else None


def reconcile(rows, apply, api=github, limit=3):
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError('NEXT_STEP_LIMIT_INVALID')
    changed = 0
    errors = 0
    for row in rows:
        try:
            head, latest = collect(row, api)
            action = apply(row, head, latest)
            if action not in ('NO_CHANGE', 'RETRY_BUDGET_EXHAUSTED', 'REAUDIT_READY'):
                raise ValueError('NEXT_STEP_ACTION_INVALID')
            print(f"next_step work_item={row['work_item_id']} action={action}")
            changed += action == 'REAUDIT_READY'
        except (KeyError, TypeError, ValueError, RuntimeError, psycopg.Error) as exc:
            errors += 1
            print(f'NEXT_STEP_SKIPPED error_type={type(exc).__name__}')
        if changed == limit:
            break
    if errors:
        raise RuntimeError('NEXT_STEP_PARTIAL_FAILURE')
    return changed


def main():
    if os.environ.get('REPOSITORY') != REPOSITORY:
        raise ValueError('NEXT_STEP_REPOSITORY_INVALID')
    with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True, row_factory=dict_row,
                         connect_timeout=10,
                         options='-c statement_timeout=10000 -c lock_timeout=3000') as conn:
        rows = [r['candidate'] for r in conn.execute(
            'SELECT autopilot.project_progress_candidates(50) AS candidate').fetchall()]

        def apply(row, head, completed):
            return conn.execute(
                'SELECT autopilot.reconcile_project_progress(%s,%s,%s,%s) AS action',
                (row['work_item_id'], row['updated_at'], head, completed),
            ).fetchone()['action']

        print(f'next_step_changed={reconcile(rows, apply)}')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'NEXT_STEP_FAILED error_type={type(exc).__name__}')
        raise SystemExit(1) from None
