"""ACL regression for the reviewed PR #2168 0060 bytes in isolated PG18.

Called by the existing disposable/CI rehearsal after 0060/0061/0062 install.
The probe creates roles, schema and functions inside a savepoint and rolls back.
This module has no connection string or production entry point of its own.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

BASELINE_0060_SHA256 = '27932f2b5f22f383d9f84341c0ae6e88dc346c7940ec92490edc0a3d37358dfe'
DEFAULT_ACL_0060_SHA256 = 'cfd5bf6088761dcc1d38a4d0febb6fb24f3d4f396fd67ca08568ff44994c44bc'

SIXTY_SIGNATURES = {
    'subset_json': 'video_queue.subset_json(jsonb)',
    'subset_sha': 'video_queue.subset_sha(jsonb)',
    'subset_shape': 'video_queue.subset_shape(jsonb,text[])',
    'subset_capability': 'video_queue.subset_capability(text)',
    'subset_disabled': 'video_queue.subset_disabled()',
    'subset_validate_contract': 'video_queue.subset_validate_contract(text)',
    'approve_subset': 'video_queue.approve_subset(uuid,text,jsonb,timestamptz)',
    'set_subset_runtime': 'video_queue.set_subset_runtime(uuid,boolean)',
    'revoke_subset_approval': 'video_queue.revoke_subset_approval(uuid)',
    'subset_observed_sources': 'video_queue.subset_observed_sources(jsonb,jsonb,text)',
    'observe_subset': 'video_queue.observe_subset(uuid,uuid,text,jsonb,timestamptz,uuid,uuid,uuid,integer,text)',
    'subset_build_binding': 'video_queue.subset_build_binding(text)',
    'subset_assert_final': 'video_queue.subset_assert_final(text)',
    'recover_subset_admission': 'video_queue.recover_subset_admission(text,text,text)',
    'admit_approved_subset': 'video_queue.admit_approved_subset(uuid,text,text,text,uuid)',
    'subset_guard': 'video_queue.subset_guard()',
    'subset_deferred_guard': 'video_queue.subset_deferred_guard()',
    'subset_worker_eligible': 'video_queue.subset_worker_eligible(uuid)',
    'subset_lock_lease': 'video_queue.subset_lock_lease(uuid,uuid,text)',
    'authorize_subset_attempt': 'video_queue.authorize_subset_attempt(uuid,uuid,text,uuid)',
    'heartbeat_subset_job': 'video_queue.heartbeat_subset_job(uuid,uuid,text,integer)',
    'retry_subset_job': 'video_queue.retry_subset_job(uuid,uuid,text,text,integer)',
    'finish_subset_job': 'video_queue.finish_subset_job(uuid,uuid,text,text,jsonb,uuid,text)',
    'claim_job': 'video_queue.claim_job(text,integer,text,text)',
    'subset_claim_job': 'video_queue.subset_claim_job(uuid,text,integer,text,text)',
}

PROPOSAL_SIGNATURES = {
    'approve_one_job_staging_attempt_v1': 'video_queue.approve_one_job_staging_attempt_v1(uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text)',
    'revoke_one_job_staging_v1': 'video_queue.revoke_one_job_staging_v1(uuid)',
    'check_one_job_staging_scope_v1': 'video_queue.check_one_job_staging_scope_v1(uuid,uuid,uuid,text,integer,text,text)',
    'assert_one_job_staging_v1': 'video_queue.assert_one_job_staging_v1(uuid,uuid,uuid,text,integer,text)',
    'attest_one_job_staging_receipt_v1': 'video_queue.attest_one_job_staging_receipt_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text)',
    'assert_one_job_terminal_evidence_v1': 'video_queue.assert_one_job_terminal_evidence_v1(uuid,jsonb)',
    'finish_one_job_staging_v1': 'video_queue.finish_one_job_staging_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)',
    'stage_one_job_private_result_v1': 'video_queue.stage_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,text,text,text)',
    'read_one_job_private_result_v1': 'video_queue.read_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid)',
    'finish_one_job_private_result_v1': 'video_queue.finish_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)',
}

APPROVER_PROPOSALS = frozenset({
    'approve_one_job_staging_attempt_v1', 'revoke_one_job_staging_v1',
})
WORKER_PROPOSALS = frozenset({
    'stage_one_job_private_result_v1', 'read_one_job_private_result_v1',
    'finish_one_job_private_result_v1',
})

SIXTY_APPROVER = frozenset({
    'approve_subset', 'set_subset_runtime', 'revoke_subset_approval',
})
SIXTY_COLLECTOR = frozenset({'observe_subset'})
SIXTY_ADMITTER = frozenset({'admit_approved_subset', 'recover_subset_admission'})
SIXTY_WORKER = frozenset({
    'claim_job', 'subset_claim_job', 'authorize_subset_attempt',
    'heartbeat_subset_job', 'retry_subset_job', 'finish_subset_job',
})


def selected_candidate(path: Path) -> bool:
    """Keep the existing green rehearsal unchanged; reject any third variant."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest == BASELINE_0060_SHA256:
        return False
    if digest == DEFAULT_ACL_0060_SHA256:
        return True
    raise RuntimeError(f'ONE_JOB_0060_ACL_VARIANT_UNREVIEWED:{digest}')


def _require(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(f'ONE_JOB_0060_ACL_REGRESSION:{label}')


def _validate_function_rows(rows, signatures: dict[str, str], owner: str,
                            resolved: dict[str, int | None]) -> dict[str, int]:
    names = set(signatures)
    _require(len(rows) == len(names) and {row['proname'] for row in rows} == names,
             f'{owner}_function_inventory')
    for row in rows:
        name = row['proname']
        _require(resolved[name] is not None and row['oid'] == resolved[name],
                 f'{name}_signature')
        _require(row['prokind'] == 'f', f'{name}_prokind')
        _require(row['owner'] == owner, f'{name}_owner')
        _require(not row['public_execute'], f'{name}_public_execute')
    return {row['proname']: row['oid'] for row in rows}


def _functions(db, signatures: dict[str, str], owner: str) -> dict[str, int]:
    rows = db.execute(
        "SELECT p.proname, p.oid, p.prokind, r.rolname AS owner,"
        " EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,"
        " acldefault('f',p.proowner))) AS a"
        " WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_execute"
        " FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid=p.pronamespace"
        " JOIN pg_roles AS r ON r.oid=p.proowner"
        " WHERE n.nspname='video_queue' AND p.proname=ANY(%s)",
        (sorted(signatures),)).fetchall()
    resolved = {name: db.execute('SELECT to_regprocedure(%s)::oid AS oid',
                                 (signature,)).fetchone()['oid']
                for name, signature in signatures.items()}
    return _validate_function_rows(rows, signatures, owner, resolved)


def _can_execute(db, role: str, oid: int) -> bool:
    return db.execute('SELECT has_function_privilege(%s,%s::oid,\'EXECUTE\') AS allowed',
                      (role, oid)).fetchone()['allowed']


def _assert_matrix(db, functions: dict[str, int], roles: dict[str, frozenset[str]]) -> None:
    for role, allowed in roles.items():
        for name, oid in functions.items():
            _require(_can_execute(db, role, oid) == (name in allowed),
                     f'{role}_{name}_execute')


def _expect_call(db, function: str, *, allowed: bool) -> None:
    from psycopg.errors import InsufficientPrivilege

    db.execute('SAVEPOINT acl_probe_call')
    try:
        db.execute('SET ROLE one_job_acl_outsider')
        try:
            value = db.execute(f'SELECT one_job_acl_probe.{function}() AS result') \
                .fetchone()['result']
        except InsufficientPrivilege:
            _require(not allowed, f'{function}_unexpected_denial')
        else:
            _require(allowed and value == 1, f'{function}_unexpected_access')
    finally:
        db.execute('ROLLBACK TO SAVEPOINT acl_probe_call')
        db.execute('RELEASE SAVEPOINT acl_probe_call')


def _probe_creation_and_transfer(db) -> None:
    db.execute('SAVEPOINT one_job_acl_probe')
    try:
        db.execute('CREATE ROLE one_job_acl_outsider NOLOGIN')
        db.execute('CREATE SCHEMA one_job_acl_probe')
        db.execute('GRANT USAGE ON SCHEMA one_job_acl_probe TO one_job_acl_outsider')
        db.execute('GRANT USAGE, CREATE ON SCHEMA one_job_acl_probe TO video_subset_owner')
        db.execute('SET ROLE video_subset_owner')
        db.execute("CREATE FUNCTION one_job_acl_probe.owner_closed() RETURNS integer"
                   " LANGUAGE sql AS 'SELECT 1'")
        db.execute('RESET ROLE')
        row = db.execute(
            "SELECT p.proowner::regrole::text AS owner,"
            " has_function_privilege('one_job_acl_outsider',p.oid,'EXECUTE') AS allowed"
            " FROM pg_proc AS p WHERE p.oid="
            " 'one_job_acl_probe.owner_closed()'::regprocedure").fetchone()
        _require(row == {'owner': 'video_subset_owner', 'allowed': False},
                 'owner_created_default_acl')
        _expect_call(db, 'owner_closed', allowed=False)

        # An installer's explicit PUBLIC grant must survive ownership transfer;
        # default ACLs are not retroactive. An explicit REVOKE must then remove it.
        db.execute("CREATE FUNCTION one_job_acl_probe.installer_open() RETURNS integer"
                   " LANGUAGE sql AS 'SELECT 1'")
        db.execute('GRANT EXECUTE ON FUNCTION one_job_acl_probe.installer_open() TO PUBLIC')
        db.execute('ALTER FUNCTION one_job_acl_probe.installer_open()'
                   ' OWNER TO video_subset_owner')
        owner = db.execute(
            "SELECT p.proowner::regrole::text AS owner FROM pg_proc AS p"
            " WHERE p.oid='one_job_acl_probe.installer_open()'::regprocedure"
        ).fetchone()['owner']
        _require(owner == 'video_subset_owner', 'installer_function_owner_transfer')
        _require(db.execute(
            "SELECT has_function_privilege('one_job_acl_outsider',"
            " 'one_job_acl_probe.installer_open()'::regprocedure,'EXECUTE') AS allowed"
        ).fetchone()['allowed'], 'owner_transfer_removed_public_execute')
        _expect_call(db, 'installer_open', allowed=True)
        db.execute('REVOKE EXECUTE ON FUNCTION one_job_acl_probe.installer_open() FROM PUBLIC')
        _require(not db.execute(
            "SELECT has_function_privilege('one_job_acl_outsider',"
            " 'one_job_acl_probe.installer_open()'::regprocedure,'EXECUTE') AS allowed"
        ).fetchone()['allowed'], 'explicit_revoke_failed')
        _expect_call(db, 'installer_open', allowed=False)
    finally:
        db.execute('ROLLBACK TO SAVEPOINT one_job_acl_probe')
        db.execute('RELEASE SAVEPOINT one_job_acl_probe')


def before_test_grants(db) -> None:
    """Run after SQL installation and before synthetic login/function grants."""
    row = db.execute(
        "SELECT current_setting('server_version_num')::integer AS version,"
        " current_user AS installer"
    ).fetchone()
    _require(180000 <= row['version'] < 190000 and row['installer'] == 'postgres',
             'isolated_pg18_installer')
    defaults = db.execute(
        "SELECT d.oid, EXISTS(SELECT 1 FROM aclexplode(d.defaclacl) AS a"
        " WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_execute"
        " FROM pg_default_acl AS d JOIN pg_roles AS r ON r.oid=d.defaclrole"
        " WHERE r.rolname='video_subset_owner' AND d.defaclnamespace=0"
        " AND d.defaclobjtype='f'").fetchall()
    _require(len(defaults) == 1 and not defaults[0]['public_execute'],
             'global_owner_default_acl')
    sixty = _functions(db, SIXTY_SIGNATURES, 'video_subset_owner')
    proposals = _functions(db, PROPOSAL_SIGNATURES, 'postgres')
    _assert_matrix(db, proposals, {
        'video_subset_approver': frozenset(),
        'video_subset_collector': frozenset(),
        'video_subset_admitter': frozenset(),
        'video_subset_worker_v2': frozenset(),
    })
    _assert_matrix(db, sixty, {
        'video_subset_approver': SIXTY_APPROVER,
        'video_subset_collector': SIXTY_COLLECTOR,
        'video_subset_admitter': SIXTY_ADMITTER,
        'video_subset_worker_v2': SIXTY_WORKER,
    })
    _probe_creation_and_transfer(db)


def after_test_grants(db) -> None:
    """Run after the existing rehearsal has provisioned synthetic login roles."""
    proposals = _functions(db, PROPOSAL_SIGNATURES, 'postgres')
    _assert_matrix(db, proposals, {
        'onejob_approver': APPROVER_PROPOSALS,
        'onejob_worker': WORKER_PROPOSALS,
        'onejob_dual': APPROVER_PROPOSALS | WORKER_PROPOSALS,
        'onejob_collector': frozenset(),
        'onejob_admitter': frozenset(),
    })
    sixty = _functions(db, SIXTY_SIGNATURES, 'video_subset_owner')
    _assert_matrix(db, sixty, {
        'onejob_approver': SIXTY_APPROVER,
        'onejob_collector': SIXTY_COLLECTOR,
        'onejob_admitter': SIXTY_ADMITTER,
        'onejob_worker': SIXTY_WORKER,
        'onejob_dual': SIXTY_APPROVER | SIXTY_WORKER,
    })
