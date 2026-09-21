from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from oracle_autopilot import next_step_reconcile as progress


NOW = datetime.now(timezone.utc)


def row(**changes):
    return dict(work_item_id=str(uuid4()), target_pr=123,
                updated_at=NOW-timedelta(hours=1), hold_reason=None) | changes


def pr(**changes):
    return dict(number=123, title='Bounded target', state='open', merged_at=None,
                base={'repo': {'full_name': progress.REPOSITORY}},
                head={'sha': 'b'*40, 'ref': 'fix/audit',
                      'repo': {'full_name': progress.REPOSITORY}}) | changes


def check(**changes):
    return dict(head_sha='b'*40, status='completed', conclusion='success',
                completed_at=(NOW-timedelta(minutes=1)).isoformat()) | changes


def api_with(checks=None, prs=None):
    checks = [check()] if checks is None else checks
    prs = iter(prs or [pr(), pr()])

    def api(path):
        if path.startswith('pulls/'):
            return next(prs)
        return dict(total_count=len(checks), check_runs=checks)
    return api


def test_exact_head_green_checks_collect_then_reread():
    head, completed = progress.collect(row(), api_with(), NOW)
    assert head == 'b'*40 and completed == NOW-timedelta(minutes=1)


@pytest.mark.parametrize('state', ['queued', 'in_progress'])
def test_pending_ci_cannot_be_fresh_success_evidence(state):
    assert progress.collect(row(), api_with([check(status=state)]), NOW)[1] is None


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', 'timed_out', 'action_required', 'stale', 'startup_failure'])
def test_unsuccessful_ci_not_fresh_success(conclusion):
    assert progress.collect(row(), api_with([check(conclusion=conclusion)]), NOW)[1] is None


def test_empty_ci_is_not_success():
    assert progress.collect(row(), api_with([]), NOW)[1] is None


def test_changed_head_during_read_fails_closed():
    second = pr()
    second['head']['sha'] = 'c'*40
    with pytest.raises(ValueError, match='HEAD_CHANGED'):
        progress.collect(row(), api_with(prs=[pr(), second]), NOW)


def test_closed_during_read_fails_closed():
    with pytest.raises(ValueError, match='TARGET_INVALID'):
        progress.collect(row(), api_with(prs=[pr(), pr(state='closed')]), NOW)


@pytest.mark.parametrize('bad', [pr(title='[Autopilot dispatch] x'),
    pr(base={'repo': {'full_name': 'attacker/repo'}}),
    pr(head={'sha': 'b'*40, 'ref': 'fix/x', 'repo': {'full_name': 'fork/repo'}})])
def test_dispatch_or_foreign_repository_rejected(bad):
    with pytest.raises(ValueError):
        progress.collect(row(), api_with(prs=[bad]), NOW)


def test_owner_hold_rejected_before_network():
    with pytest.raises(ValueError):
        progress.collect(row(hold_reason='OWNER_HOLD'), lambda p: pytest.fail('network'), NOW)


def test_wrong_check_head_rejected():
    with pytest.raises(ValueError):
        progress.collect(row(), api_with([check(head_sha='c'*40)]), NOW)


def test_future_check_evidence_rejected():
    with pytest.raises(ValueError):
        progress.collect(row(), api_with([check(completed_at=(NOW+timedelta(minutes=1)).isoformat())]), NOW)


def test_incomplete_pagination_rejected():
    def api(path):
        return pr() if path.startswith('pulls/') else dict(total_count=2, check_runs=[check()])
    with pytest.raises(ValueError, match='INCOMPLETE'):
        progress.collect(row(), api, NOW)


def test_bounded_batch_and_no_change_does_not_starve(monkeypatch):
    monkeypatch.setattr(progress, 'collect', lambda *args: ('b'*40, None))
    actions = iter(['NO_CHANGE', 'RETRY_BUDGET_EXHAUSTED', 'REAUDIT_READY', 'REAUDIT_READY', 'REAUDIT_READY'])
    assert progress.reconcile([row() for _ in range(8)], lambda *a: next(actions)) == 3


def test_failed_read_no_mutation():
    def api(path):
        raise RuntimeError('unavailable')
    with pytest.raises(RuntimeError, match='PARTIAL_FAILURE'):
        progress.reconcile([row()], lambda *a: pytest.fail('mutation'), api)
