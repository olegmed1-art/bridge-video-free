"""Offline checks of SQL probe inputs/coverage. These are not PostgreSQL results.

Only selected pure function ASTs are loaded, never the runner module, driver,
networking modules, environment, or a database connection.
"""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'database/tests/fixed_subset_admission/run.py'
TREE = ast.parse(RUNNER.read_text(encoding='utf-8'))
FUNCTIONS = {node.name: node for node in TREE.body if isinstance(node, ast.FunctionDef)}
PURE_NAMES = {'canonical', 'digest', 'require', 'rehash_approval',
              'approval_mutations', 'worker_probe', 'expect_guard'}
PURE = {'deepcopy': deepcopy, 'hashlib': hashlib, 'json': json}
exec(compile(ast.Module(body=[FUNCTIONS[n] for n in PURE_NAMES], type_ignores=[]),
             str(RUNNER), 'exec'), PURE)
REQUIRED = ast.literal_eval(next(node.value for node in TREE.body
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
    t.id == 'REQUIRED_VARIANTS' for t in node.targets)))
CASES = {
    'G01_DIRECT_DML_GUARD': 'g01', 'G02_IMMUTABLE_IDENTITY_MUTATIONS': 'g02',
    'G04_GUC_FORGERY_NOT_AUTHORITY': 'g04', 'G05_APPROVAL_FULL_CONTENT_VALIDATION': 'g05',
    'S08_OBSERVATION_TRUST_BINDING': 's08', 'W02_LEASE_RETRY_BUDGET_AND_LIVENESS': 'w02',
}


def independent_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def source_contract():
    """Small literal source, independent of runner fixture construction."""
    files = []
    for number in (1, 2):
        row = dict(source_identity=f'google_drive:privateVideo{number:08d}',
            file_id=f'privateVideo{number:08d}', parent='sourceFolder000001',
            version='17', modifiedTime='2026-10-10T10:00:00Z', mime_type='video/mp4',
            size_bytes=2000000, checksum=None, checksums={}, name=f'Lesson {number}.mp4',
            trashed=False, duration_ms=None, estimated_cost_usd=None)
        row['source_fingerprint'] = independent_digest({k: v for k, v in row.items()
            if k not in ('name', 'trashed', 'duration_ms', 'estimated_cost_usd')})
        files.append(row)
    queue = [dict(sequence=i, **{k: row[k] for k in
        ('file_id', 'name', 'mime_type', 'size_bytes', 'checksum')})
        for i, row in enumerate(files, 1)]
    contract = dict(schema='universal-video-subset-contract-v1', plan_sha256='a' * 64,
        subset_sha256=independent_digest(files), subset_sequence=1,
        processing_profile='bridge_3_1_free', algorithm_revision='3.1-free-r25.16',
        canary_source_file_id=files[0]['file_id'], files=files,
        execution_allowed=False, release_allowed=False,
        database_persistence_allowed=False, canonical_promotion_allowed=False)
    keys = ('plan_sha256', 'subset_sha256', 'subset_sequence', 'processing_profile',
            'algorithm_revision', 'canary_source_file_id')
    manifest = dict(request_key='subset-v1:' + independent_digest({k: contract[k] for k in keys}),
        source_folder_id='sourceFolder000001', output_folder_id='outputFolder000001',
        work_folder_id='workFolder00000001', processing_profile=contract['processing_profile'],
        algorithm_revision=contract['algorithm_revision'], canary_source_file_id=files[0]['file_id'],
        inventory_sha256=independent_digest(queue), expected_count=2, total_size_bytes=4000000,
        files=queue, result_mode='SHADOW_REVIEW_ONLY', canonical_promotion_allowed=False,
        database_persistence_allowed=False)
    contract['enqueue_manifest'] = manifest
    contract['queue_manifest_sha256'] = independent_digest(manifest)
    contract['contract_sha256'] = independent_digest(contract)
    return contract


def test_required_inventory_is_bounded_unique_and_linked_to_existing_plan():
    plan = json.loads((ROOT / 'database/tests/plans/fixed_subset_admission_v1.json').read_text(encoding='utf-8'))
    assert set(CASES) <= {case['id'] for case in plan['cases']}
    assert [len(REQUIRED[case]) for case in CASES] == [6, 52, 5, 48, 29, 39]
    ids = [item for case in CASES for item in REQUIRED[case]]
    assert len(ids) == len(set(ids)) == 179
    for case, function in CASES.items():
        assert all(item.startswith(case[:3] + '_') for item in REQUIRED[case])
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == 'variant' for node in ast.walk(FUNCTIONS[function]))


def test_mutations_rehash_every_present_anchor_without_mutating_source():
    source = source_contract()
    original = deepcopy(source)
    variants = PURE['approval_mutations'](source)
    assert source == original
    assert len(variants) == len({name for name, _, _ in variants}) == 25
    request_keys = ('plan_sha256', 'subset_sha256', 'subset_sequence', 'processing_profile',
                    'algorithm_revision', 'canary_source_file_id')
    for label, value, code in variants:
        assert value['contract_sha256'] == independent_digest({k: v for k, v in value.items() if k != 'contract_sha256'})
        if 'files' in value:
            assert value['subset_sha256'] == independent_digest(value['files'])
        if 'enqueue_manifest' in value:
            manifest = value['enqueue_manifest']
            assert value['queue_manifest_sha256'] == independent_digest(manifest)
            assert manifest['inventory_sha256'] == independent_digest(manifest['files'])
            if all(k in value for k in request_keys):
                assert manifest['request_key'] == 'subset-v1:' + independent_digest({k: value[k] for k in request_keys})
        assert code.startswith('SUBSET_') and code != 'SUBSET_'
        assert 'G05_intake_' + label in REQUIRED['G05_APPROVAL_FULL_CONTENT_VALIDATION']
    # Mutated nested lists/dicts also cannot alias each other or the input.
    variants[3][1]['files'][0]['name'] = 'fixture isolation probe'
    assert source == original
    assert all(v['files'][0]['name'] != 'fixture isolation probe'
               for _, v, _ in variants[4:] if 'files' in v)


def test_semantic_mutations_survive_rehash_instead_of_becoming_hash_only_failures():
    mutations = {name: value for name, value, _ in PURE['approval_mutations'](source_contract())}
    assert 'files' not in mutations['missing_files']
    assert 'enqueue_manifest' not in mutations['missing_enqueue_manifest']
    assert 'plan_sha256' not in mutations['missing_plan_sha256']
    assert mutations['flag']['execution_allowed'] is True
    assert mutations['manifest_name']['enqueue_manifest']['files'][0]['name'] == 'Not approved.mp4'
    assert mutations['manifest_checksum']['enqueue_manifest']['files'][0]['checksum'] == 'md5:' + 'a' * 32
    assert [r['sequence'] for r in mutations['manifest_order']['enqueue_manifest']['files']] == [2, 1]
    assert mutations['manifest_sequence']['enqueue_manifest']['files'][0]['sequence'] == 9
    assert mutations['source_fingerprint']['files'][0]['source_fingerprint'] == '0' * 64
    assert len(mutations['source_removed']['files']) == 1
    assert len(mutations['source_added']['files']) == 3
    assert mutations['source_duplicate']['files'][-1] == mutations['source_duplicate']['files'][-2]
    assert mutations['wrong_canary']['canary_source_file_id'] == 'privateVideo00000003'
    assert mutations['invalid_plan_anchor']['plan_sha256'] == 'invalid-plan-anchor'
    assert type(mutations['scalar_plan_sha256']['plan_sha256']) is int
    assert mutations['scalar_files_0_parent']['files'][0]['parent'] is None
    assert type(mutations['scalar_files_0_version']['files'][0]['version']) is int
    assert mutations['cost_fraction']['files'][0]['estimated_cost_usd'] == 1.5
    assert type(mutations['cost_float_integer']['files'][0]['estimated_cost_usd']) is float
    assert '1e+30' in PURE['canonical'](mutations['cost_exponent'])


def table_columns(path, table):
    sql = path.read_text(encoding='utf-8')
    body = re.search(r'CREATE TABLE video_queue\.' + table + r' \((.*?)\n\);', sql, re.S).group(1)
    return set(re.findall(r'^    ([a-z_][a-z0-9_]*) (?:uuid|text|integer|bigint|boolean|timestamptz|jsonb)\b', body, re.M))


def test_g02_probes_every_immutable_sql_column_and_mapping():
    assignment = next(n for n in FUNCTIONS['g02'].body if isinstance(n, ast.Assign)
                      and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'edits')
    probe_fields = {key.value: {keyword.arg for keyword in value.keywords}
                    for key, value in zip(assignment.value.keys, assignment.value.values)}
    baseline = ROOT / 'database/migrations/0056_universal_video_queue.sql'
    admission = ROOT / 'database/migrations/0060_fixed_subset_admission.sql'
    progress = {'job': {'status', 'attempt_count', 'lease_owner', 'lease_token', 'lease_expires_at',
                       'next_attempt_at', 'error_code', 'output', 'updated_at', 'completed_at'},
                'batch': {'status', 'updated_at', 'completed_at'}}
    for table in ('job', 'batch', 'subset_admission', 'source_reservation'):
        expected = table_columns(baseline if table in progress else admission, table) - progress.get(table, set())
        assert probe_fields[table] == expected
        assert {f'G02_{table}_{field}' for field in expected} <= set(REQUIRED['G02_IMMUTABLE_IDENTITY_MUTATIONS'])
        assert f'G02_{table}_delete' in REQUIRED['G02_IMMUTABLE_IDENTITY_MUTATIONS']
    assert 'ARRAY[%s::uuid]' in ast.unparse(FUNCTIONS['g02'])


def test_guard_capability_probes_cannot_be_short_circuited_by_untrusted_session():
    source = ast.unparse(FUNCTIONS['grant_guard_probe'])
    assert 'session_user' in source and 'rolsuper OR rolbypassrls OR rolcreaterole' in source
    assert 'GRANT SELECT,INSERT,UPDATE,DELETE' in source
    assert 'GRANT video_subset_' not in source and 'SET ROLE' not in source
    assert 'SUBSET_OBSERVATION_BINDING_HOLD' in ast.unparse(FUNCTIONS['g01'])
    assert {'G01_missing_observation', 'G01_wrong_reservation_owner', 'G01_complete_untrusted_fields',
            'G01_unbound_batch', 'G01_missing_reservation', 'G01_invented_batch_binding'} == set(REQUIRED['G01_DIRECT_DML_GUARD'])
    assert {'G04_' + suffix for suffix in ('extra_source', 'duplicate_job_identity',
            'foreign_job_identity', 'foreign_batch_identity', 'forged_worker_claim')} == set(REQUIRED['G04_GUC_FORGERY_NOT_AUTHORITY'])


class Recorder:
    def __init__(self):
        self.calls = []

    def q(self, session, statement, params):
        self.calls.append((session, statement, params))
        return 'actual boundary return'

    def output(self, claim):
        return {'proof_for': claim['job_id']}


@pytest.mark.parametrize('operation,arity', [('heartbeat', 3), ('retry', 4), ('finish', 6), ('attempt', 4)])
@pytest.mark.parametrize('token', [None, 'stale-token'])
def test_worker_probe_preserves_null_and_stale_arguments_at_actual_boundary(operation, arity, token):
    harness = Recorder()
    claim = {'job_id': 'job-id', 'lease_token': 'current-token'}
    result = PURE['worker_probe'](harness, operation, claim, token=token, owner='wrong-owner', observation='wrong-observation')
    assert result == 'actual boundary return'
    assert len(harness.calls) == 1
    session, sql, args = harness.calls[0]
    assert session == 'worker'
    assert args[:3] == ('job-id', token, 'wrong-owner')
    assert len(args) == sql.count('%s') == arity
    if operation in ('finish', 'attempt'):
        assert args[-1] == 'wrong-observation'
    else:
        assert 'wrong-observation' not in args


def test_w02_full_api_cross_product_and_three_attempt_liveness_are_required():
    required = set(REQUIRED['W02_LEASE_RETRY_BUDGET_AND_LIVENESS'])
    cross = {f'W02_{defect}_{api}' for defect in ('null_token', 'stale_token', 'wrong_owner',
        'expired_token', 'wrong_attempt_domain') for api in ('heartbeat', 'retry', 'finish', 'attempt')}
    assert len(cross) == 20 and cross <= required
    assert {'W02_wrong_observation_attempt_attempt', 'W02_wrong_observation_attempt_finish'} <= required
    assert not any('wrong_observation_attempt_' + api in item for api in ('heartbeat', 'retry') for item in required)
    assert {'W02_raw_max4_acl', 'W02_raw_max4_guard', 'W02_attempt_counter_reset',
            'W02_reclaim_second_attempt', 'W02_claim_third_attempt', 'W02_third_retry_terminal',
            'W02_managed_exhausted_QUEUED', 'W02_managed_exhausted_LEASED',
            'W02_historical_exhausted_QUEUED', 'W02_historical_exhausted_LEASED'} <= required


def test_s08_nonce_scope_sources_and_trust_are_separate_requirements():
    ids = set(REQUIRED['S08_OBSERVATION_TRUST_BINDING'])
    assert {'S08_duplicate_nonce_same_approval', 'S08_consumed_observation_new_request_replay',
            'S08_recovery_observation_independent'} <= ids
    assert {'S08_collector_' + x for x in ('missing_source', 'extra_source', 'duplicate_source',
            'wrong_parent', 'trashed', 'future', 'expired')} <= ids
    assert {'S08_untrusted_' + x for x in ('collector_acl', 'update_acl', 'collector_guard', 'update_guard')} <= ids
    assert {'S08_attempt_scope_' + x for x in ('lease_token', 'attempt_count', 'job_id',
            'worker_key', 'approval_id', 'contract_sha256', 'request_key', 'consumed_at')} <= ids
    assert 'source_observation_approval_id_nonce_key' in ast.unparse(FUNCTIONS['s08'])


def test_expect_guard_rejects_different_guard_even_with_same_sqlstate():
    class Harness:
        def expect_error(self, call, *, codes, states):
            assert codes == ('SUBSET_LEASE_LOST',) and states == ('P0001',)
            return {'message': 'SUBSET_CAPABILITY_DENIED'}
    with pytest.raises(AssertionError, match='wrong guard reached'):
        PURE['expect_guard'](Harness(), lambda: None, 'SUBSET_LEASE_LOST')


def test_negative_contexts_require_unchanged_state_and_do_not_seed_fault_inside_snapshot():
    negatives = 0
    for name in CASES.values():
        for node in ast.walk(FUNCTIONS[name]):
            if not isinstance(node, ast.With):
                continue
            ctx = node.items[0].context_expr
            if not isinstance(ctx, ast.Call) or not isinstance(ctx.func, ast.Attribute) or ctx.func.attr != 'variant':
                continue
            guard_calls = [child for child in ast.walk(node) if isinstance(child, ast.Call) and (
                isinstance(child.func, ast.Name) and child.func.id == 'expect_guard' or
                isinstance(child.func, ast.Attribute) and child.func.attr == 'expect_error')]
            if guard_calls:
                negatives += 1
                assert any(k.arg == 'unchanged' and isinstance(k.value, ast.Constant) and k.value.value is True for k in ctx.keywords)
                assert not any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                               and child.func.attr == 'fixture_change' for child in ast.walk(node))
    assert negatives >= 20
