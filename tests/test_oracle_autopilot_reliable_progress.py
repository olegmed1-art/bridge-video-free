"""Regression tests for bounded progress and deployment evidence."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from oracle_autopilot import worker, paused_reconcile as paused, rollout_verify
from oracle_autopilot.parallel_work_intake import manifest_sha256


def receipt(**values):
    return dict(manifest_sha256=manifest_sha256(), item_count=4,
                registered_count=0, replayed=True) | values


@pytest.mark.parametrize('values', [dict(manifest_sha256='a'*64), dict(item_count=3),
    dict(registered_count=True), dict(registered_count=5), dict(replayed='true')])
def test_invalid_receipt_rejected_by_worker_and_rollout(monkeypatch, values):
    monkeypatch.setattr(worker, '_PARALLEL_WORK_MANIFEST_REGISTERED', False)
    monkeypatch.setattr(worker, '_rpc_one', lambda *a: receipt(**values))
    with pytest.raises(worker.AutopilotContractError):
        worker.reconcile_parallel_work_intake(None)
    with pytest.raises(ValueError):
        rollout_verify.validate_receipt(receipt(**values))


def test_all_reused_receipt_is_valid():
    rollout_verify.validate_receipt(receipt())


def test_cycle_visits_all_nonempty_lanes(monkeypatch):
    calls = []
    for name in ('drain_ready', 'drain_role_dispatch_outbox', 'drain_project_work'):
        def drain(config, name=name):
            calls.append(name)
            return 6
        monkeypatch.setattr(worker, name, drain)
    assert worker.drain_cycle(None) == 18
    assert len(calls) == 3


def test_permanently_nonempty_queues_are_bounded(monkeypatch):
    monkeypatch.setattr(worker, 'process_one', lambda c: True)
    monkeypatch.setattr(worker, 'process_role_dispatch_outbox', lambda c: True)
    monkeypatch.setattr(worker, 'process_project_work', lambda c: True)
    monkeypatch.setattr(worker, '_rpc_one', lambda *a: {'reconciled': 0})
    monkeypatch.setattr(worker, 'reconcile_parallel_work_intake', lambda c: 0)
    assert worker.drain_cycle(None) == 3 * worker.QUEUE_BATCH_SIZE


def test_manifest_error_does_not_starve_existing_work(monkeypatch):
    monkeypatch.setattr(worker, '_PARALLEL_WORK_MANIFEST_RETRY_AT', 0)
    calls = []
    def broken(c):
        calls.append(1)
        raise psycopg.DatabaseError('sensitive text must not be logged')
    monkeypatch.setattr(worker, 'reconcile_parallel_work_intake', broken)
    monkeypatch.setattr(worker, 'process_project_work', lambda c: True)
    assert worker.drain_project_work(None) == worker.QUEUE_BATCH_SIZE
    assert worker.drain_project_work(None) == worker.QUEUE_BATCH_SIZE
    assert len(calls) == 1


def candidate(**values):
    return dict(work_item_id=uuid4(), target_pr=123, result_code='BOUNDED_DEFECT',
        blocker_action='REPOSITORY_REPAIR', last_observed_head_sha='a'*40,
        progress_token=None, owner_gated=False, provider_state='CLOSED',
        provider_last_success_at=None, updated_at=datetime.now(timezone.utc)-timedelta(hours=1)) | values


def api(path):
    if path.startswith('pulls/'):
        return {'number': 123, 'state': 'open', 'merged_at': None,
            'base': {'repo': {'full_name': paused.REPOSITORY}}, 'head': {'sha': 'b'*40}}
    return {'check_runs': []}


def test_nullable_fields_preserve_owner_gate():
    row = candidate(progress_token=None, provider_state=None, provider_last_success_at=None)
    assert paused.collect_evidence(row, 'c'*40, api)
    assert row['owner_gated'] is False


@pytest.mark.parametrize('values', [dict(owner_gated='f'), dict(target_pr=True),
    dict(progress_token='CLOSED'), dict(updated_at=datetime(2020, 1, 1))])
def test_malformed_candidates_fail_closed(values):
    with pytest.raises((ValueError, TypeError)):
        paused.collect_evidence(candidate(**values), 'c'*40, api)


def test_no_change_does_not_starve_later_candidate_and_mutations_bounded():
    results = iter(['NO_CHANGE', 'REMEDIATE', 'OWNER_HOLD', 'CLOSE_SUPERSEDED'])
    calls = []
    def apply(*args):
        calls.append(args)
        return next(results)
    assert paused.reconcile_batch([candidate() for _ in range(8)], 'c'*40, apply, api) == 3
    assert len(calls) == 4


def test_owner_hold_with_existing_token_is_not_rearmed():
    assert paused.collect_evidence(candidate(owner_gated=True, progress_token='d'*64), 'c'*40, api) is None


def test_no_new_head_or_checks_is_not_fresh():
    assert paused.collect_evidence(candidate(last_observed_head_sha='b'*40), 'c'*40, api) is None


def test_failed_github_read_never_applies():
    def failed(path):
        raise RuntimeError('read failed')
    def forbidden(*a):
        pytest.fail('must not mutate')
    with pytest.raises(RuntimeError):
        paused.reconcile_batch([candidate()], 'c'*40, forbidden, failed)


def test_invalid_first_candidate_does_not_starve_later_candidate():
    calls = []
    def apply(*args):
        calls.append(args)
        return 'REMEDIATE'
    with pytest.raises(RuntimeError, match='PARTIAL_FAILURE'):
        paused.reconcile_batch([candidate(owner_gated='f'), candidate()], 'c'*40, apply, api)
    assert len(calls) == 1


def test_null_result_code_is_preserved():
    assert paused.collect_evidence(candidate(result_code=None, blocker_action='HOLD_UNKNOWN'), 'c'*40, api)


def test_only_reviewed_native_cli_adapter_launches_a_child():
    root = Path(__file__).resolve().parents[1] / 'oracle_autopilot'
    for path in root.glob('*.py'):
        tree = ast.parse(path.read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == 'subprocess']
        if path.name != 'codex_cli_bridge.py':
            assert not calls
            continue
        assert len(calls) == 1 and calls[0].func.attr == 'run'
        call = calls[0]
        assert isinstance(call.args[0], ast.List)
        assert ast.unparse(call.args[0].elts[0]) == 'str(CLI)'
        keywords = {k.arg: k.value for k in call.keywords}
        assert 'shell' not in keywords
        assert ast.unparse(keywords['env']) == 'child_environment()'
        assert 'timeout' in keywords
