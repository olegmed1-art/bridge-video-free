"""Fixed continuation of the already published Light native pilot task.

This controller changes only the private root ledger. The original ledger is
renamed intact; no task, broker publication, permit, pilot, or DB writer runs.
An interrupted two-rename switch is terminal and requires exact readback.
"""
import base64
from dataclasses import asdict
import os
from pathlib import Path
import stat
import time

SOURCE = '8bbc1d61010ef70c3fca02b5151ac86fce02a144'
PACKAGE = '46acb2672ba58dc369c3ccf70f45bb4beff37cb4efad2bfe3b34ae4a170c0c94'
PLAN = '3af45c61ece27fa57f6ebf3f26c304e46667d3585ce732392989143850ff06ab'
SCOPE = '2684a5be633619d55ac2e6c3c56a82209633a5c615a3b4a09c68e9c2ecb11051'
BASELINE = 'dd62c96c552b81e23d661b366c6e2d1a23293295943f9b5b44021e6c99befe1a'
INTAKE = '92c4e1386cc88a5aa180356084520be1905a5fc1642f709b7b6a77c51ed517e3'
DISCOVERY = 'e74056f1625cacb8f5aaf2b4c58a7756dc09c97c3676cdadd2b8dee488c144f2'
PUBLICATION = '8ac1bf89168ecbf9f0df64556bcd8222aef978cd1c5bde90f6832266d88aa413'
PERMIT = '40e6ba4d282ff03e3fe06f23b9138c6aa8eeac81c1d3b15b5a255eb3c6890e18'
DISPATCH = '9289ad56-f0aa-4683-be0c-101ec820d412'
TASK = 'd8595f4c-4c02-43d0-9de7-9f377a7aa0c4'
WORK = '6bda2dca-d0a1-4783-bbb2-d4006ed3e536'
ARCHIVE = 'bridge-light-native-pilot-archive-20260928-' + BASELINE[:12]
FILES = frozenset(('plan.json', 'intake-intent.json', 'before.json', 'intake.json',
    'publication-intent.json', 'broker.json', 'discovery.json', 'publication.json', 'permit.json'))
COPIED = FILES - {'permit.json'}


def require(value, code):
    if not value:
        raise RuntimeError(code)


def _absent(path, code):
    require(not path.exists() and not path.is_symlink(), code)


def _read_old(control):
    root = control.plan.ROOT
    control.directory(root, 0, 0, 0o700)
    control.directory(root / 'intake', 0, 0, 0o700)
    require(set(os.listdir(root)) == {'baseline.json', 'intake'}
            and set(os.listdir(root / 'intake')) == FILES, 'CONTINUE_OLD_INVENTORY')
    baseline = control.read(root / 'baseline.json', 262144)
    data = {name: control.read(root / 'intake' / name, 262144) for name in FILES}
    require(control.digest(baseline) == BASELINE
            and control.digest(data['plan.json']) == PLAN
            and control.digest(data['intake.json']) == INTAKE
            and control.digest(data['discovery.json']) == DISCOVERY
            and control.digest(data['publication.json']) == PUBLICATION
            and control.digest(data['permit.json']) == PERMIT, 'CONTINUE_OLD_DIGEST')
    return baseline, data, root.stat().st_ino


def _db_observe(psycopg, credential, plan, receipt, dispatch, agreement_sha, agreement_end):
    from database import light_native_pilot_intake as intake
    from ops.native_maintenance_owner_attest import parameters
    from oracle_autopilot import light_native_preflight as preflight

    with psycopg.connect(**parameters(credential), autocommit=True) as conn:
        conn.read_only = True
        intake.engine.identity(conn, intake.target())
        now = int(time.time())
        evidence = preflight.observe(conn, dispatch, issued_at=now,
            expires_at=int(agreement_end), agreement_sha256=agreement_sha)
        require(evidence['task_id'] == TASK and evidence['work_item_id'] == WORK
                and evidence['goal_json_sha256'] == receipt['goal_json_sha256'],
                'CONTINUE_DB_IDENTITY')
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            config = intake.one(conn, 'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
            role = intake.one(conn, "SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
            native = conn.execute('SELECT count(*) FROM autopilot.native_cli_receipt').fetchone()[0]
            active = conn.execute("SELECT count(*) FROM autopilot.task WHERE status IN "
                "('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')").fetchone()[0]
            mapped = conn.execute('SELECT count(*) FROM autopilot.project_work_task '
                'WHERE work_item_id=%s::uuid', (WORK,)).fetchone()[0]
            successors = conn.execute("SELECT count(*) FROM autopilot.task WHERE "
                "goal_json->>'origin_task_id'=%s", (TASK,)).fetchone()[0]
            outboxes = conn.execute('SELECT count(*) FROM autopilot.role_dispatch_outbox '
                'WHERE task_id=%s::uuid', (TASK,)).fetchone()[0]
        require(config == receipt['applied_config'] and role == receipt['applied_role']
                and native == 0 and active == 1 and mapped == 1
                and successors == 0 and outboxes == 1, 'CONTINUE_DB_COMPETITOR')
    return evidence


def _stage(control, data, old_baseline, old_inode, agreement, evidence, run_guard):
    from ops import light_native_service_switch as switch
    from ops import oracle_light_active_hold_attest as hold

    old = control.strict_json(old_baseline, 262144)
    require(type(old) is dict and old['source'] == SOURCE and old['package_sha256'] == PACKAGE
            and old['scope_sha256'] == SCOPE, 'CONTINUE_BASELINE_IDENTITY')
    prior = hold.HoldIdentity(**old['prior'])
    require(asdict(hold.service_hold_identity()) == asdict(prior), 'CONTINUE_HOLD')
    switch.unchanged_files(old['protected'], prior, old['protected_sha256'])
    protected = switch.protect_snapshot(prior)
    require(protected == old['protected'], 'CONTINUE_PROTECTED')
    baseline = dict(old, agreement_sha256=agreement.accepted, observed_at=int(time.time()))
    baseline_raw = control.canonical(baseline)
    hashes = {name: control.digest(raw) for name, raw in data.items()}
    sidecar = dict(version=1, kind='LIGHT_PILOT_SAME_TASK_CONTINUATION', source=SOURCE,
        plan_sha256=PLAN, scope_sha256=SCOPE, dispatch_id=DISPATCH, task_id=TASK,
        work_item_id=WORK, old_baseline_sha256=BASELINE, old_root_inode=old_inode,
        old_files_sha256=hashes, archive_name=ARCHIVE, new_agreement_sha256=agreement.accepted,
        new_baseline_sha256=control.digest(baseline_raw),
        db_evidence=evidence, db_evidence_sha256=control.digest(control.canonical(evidence)),
        queue_zero_reobserved=False, continuation_of_original_queue_zero_baseline=BASELINE,
        pilot_submitted=False)
    sidecar_raw = control.canonical(sidecar)
    stage = control.plan.ROOT.parent / ('bridge-light-native-pilot-staged-20260928-' + agreement.accepted[:12])
    archive = control.plan.ROOT.parent / ARCHIVE
    _absent(stage, 'CONTINUE_STAGE_EXISTS')
    _absent(archive, 'CONTINUE_ARCHIVE_EXISTS')
    run_guard.assert_running()
    agreement.assert_held(SCOPE)
    control.new_directory(stage, 0, 0, 0o700)
    control.retained(stage / 'baseline.json', baseline_raw)
    control.retained(stage / 'continuation.json', sidecar_raw)
    control.new_directory(stage / 'intake', 0, 0, 0o700)
    for name in sorted(COPIED):
        control.retained(stage / 'intake' / name, data[name])
    require(set(os.listdir(stage)) == {'baseline.json', 'continuation.json', 'intake'}
            and set(os.listdir(stage / 'intake')) == COPIED, 'CONTINUE_STAGE_INVENTORY')
    require(control.read(stage / 'baseline.json', 262144) == baseline_raw
            and control.read(stage / 'continuation.json', 262144) == sidecar_raw
            and all(control.read(stage / 'intake' / name, 262144) == data[name]
                    for name in COPIED), 'CONTINUE_STAGE_READBACK')
    return stage, archive, baseline_raw, sidecar_raw


def _switch(control, stage, archive, old_inode, old_baseline, data,
            baseline_raw, sidecar_raw, run_guard, agreement):
    from ops import light_native_service_switch as switch
    from ops import oracle_light_active_hold_attest as hold

    root = control.plan.ROOT
    parent = root.parent
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and not info.st_mode & 0o022, 'CONTINUE_PARENT')
        control.directory(root, 0, 0, 0o700)
        control.directory(stage, 0, 0, 0o700)
        before_baseline, before_data, before_inode = _read_old(control)
        require(before_inode == old_inode and before_baseline == old_baseline
                and before_data == data, 'CONTINUE_OLD_CHANGED')
        old_stat = os.stat(root.name, dir_fd=fd, follow_symlinks=False)
        staged_stat = os.stat(stage.name, dir_fd=fd, follow_symlinks=False)
        require(stat.S_ISDIR(old_stat.st_mode) and stat.S_ISDIR(staged_stat.st_mode)
                and old_stat.st_dev == staged_stat.st_dev
                and old_stat.st_ino == old_inode, 'CONTINUE_SAME_FILESYSTEM')
        stage_inode = staged_stat.st_ino
        _absent(archive, 'CONTINUE_ARCHIVE_EXISTS')
        run_guard.assert_running()
        agreement.assert_held(SCOPE)
        old = control.strict_json(old_baseline, 262144)
        prior = hold.HoldIdentity(**old['prior'])
        require(asdict(hold.service_hold_identity()) == asdict(prior), 'CONTINUE_HOLD')
        switch.unchanged_files(old['protected'], prior, old['protected_sha256'])
        switch.unit_absent(control.plan.SUPERVISOR_UNIT)
        switch.unit_absent(control.plan.PILOT_UNIT)
        _absent(switch.CONTROL, 'CONTINUE_CONTROL_EXISTS')
        _absent(control.CLAIM, 'CONTINUE_CLAIM_EXISTS')
        os.rename(root.name, archive.name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        require(archive.stat().st_ino == old_inode and not root.exists(),
                'CONTINUE_ARCHIVE_READBACK')
        run_guard.assert_running()
        agreement.assert_held(SCOPE)
        os.rename(stage.name, root.name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        require(root.stat().st_ino == stage_inode and archive.stat().st_ino == old_inode,
                'CONTINUE_INSTALL_READBACK')
        control.directory(root, 0, 0, 0o700)
        control.directory(root / 'intake', 0, 0, 0o700)
        control.directory(archive, 0, 0, 0o700)
        control.directory(archive / 'intake', 0, 0, 0o700)
        require(set(os.listdir(root)) == {'baseline.json','continuation.json','intake'}
                and set(os.listdir(root / 'intake')) == COPIED
                and set(os.listdir(archive)) == {'baseline.json','intake'}
                and set(os.listdir(archive / 'intake')) == FILES,
                'CONTINUE_INSTALL_INVENTORY')
        require(control.read(root / 'baseline.json', 262144) == baseline_raw
                and control.read(root / 'continuation.json', 262144) == sidecar_raw
                and control.read(archive / 'baseline.json', 262144) == old_baseline
                and all(control.read(archive / 'intake' / name, 262144) == data[name]
                        for name in FILES)
                and all(control.read(root / 'intake' / name, 262144) == data[name]
                        for name in COPIED),
                'CONTINUE_INSTALL_READBACK')
        run_guard.assert_running()
        agreement.assert_held(SCOPE)
        return stage_inode
    finally:
        os.close(fd)


def _loaded(package_raw, payload, old_baseline, data, old_inode,
            psycopg, credential, token, run_guard, control, release, switch):
    from database import light_native_pilot_intake as intake
    from oracle_autopilot import codex_cli_bridge as bridge
    from ops import light_native_pilot_owner as owner
    from ops.native_maintenance_run_guard import API
    from ops.native_maintenance_agreement import Agreement

    plan = intake.Plan(data['plan.json'], PLAN)
    require(plan.scope_digest == SCOPE and plan.value['source'] == SOURCE
            and plan.value['target_pr'] == 2025 and plan.value['expected_head_sha'] ==
            'aa286f693bc903fd2b93c555d9da898652fbdb26', 'CONTINUE_PLAN')
    agreement = Agreement(payload['agreement'], payload['accepted_agreement_sha256'], plan.scope)
    require(agreement.end - time.time() > 1200, 'CONTINUE_WINDOW_TOO_SHORT')
    old_record = control.strict_json(old_baseline, 262144)
    old_permit = control.strict_json(data['permit.json'], 262144)
    require(agreement.accepted != old_record['agreement_sha256']
            and old_permit['expires_at'] <= int(time.time()), 'CONTINUE_FRESH_AUTHORITY')
    receipt = control.strict_json(data['intake.json'], 262144)
    publication = control.strict_json(data['publication.json'], 262144)
    require(receipt['plan_sha256'] == PLAN and receipt['dispatch_id'] == DISPATCH
            and receipt['task_id'] == TASK and receipt['work_item_id'] == WORK
            and receipt['published'] is False and receipt['pilot_authorized'] is False
            and publication['dispatch_id'] == DISPATCH and publication['published'] is True
            and publication['github_pull_request'] == 2048, 'CONTINUE_SAME_TASK')
    before = intake.engine.load_manifest(control.plan.ROOT / 'intake' / 'before.json',
        receipt['snapshot_sha256'])
    require(before['version'] == 1 and before['plan_sha256'] == PLAN
            and before['target'] == intake.EXPECTED_TARGET
            and before['native_config']['enabled'] is False
            and receipt['applied_config']['enabled'] is True
            and receipt['applied_role']['can_repair'] is False,
            'CONTINUE_SNAPSHOT')
    dispatch = {k:receipt['dispatch'][k] for k in
        ('dispatch_id','expected_head_sha','mode','target_pr','task_fingerprint')}
    dispatch.update(branch=plan.value['branch'], assignment=receipt['assignment'])
    run_guard.assert_running()
    release.staging.require_current_main(SOURCE)
    prior = control.stage_observation(SOURCE, control.verified_package(package_raw, SOURCE, PACKAGE))
    require(asdict(prior) == control.strict_json(old_baseline, 262144)['prior'],
            'CONTINUE_STAGE')
    switch.attest_hardening()
    switch.unit_absent(control.plan.SUPERVISOR_UNIT)
    switch.unit_absent(control.plan.PILOT_UNIT)
    _absent(switch.CONTROL, 'CONTINUE_CONTROL_EXISTS')
    _absent(control.CLAIM, 'CONTINUE_CLAIM_EXISTS')
    _absent(bridge.LIGHT_ROOT / 'runtime/codex-dispatch' / (DISPATCH + '.json'),
            'CONTINUE_PROVIDER_JOURNAL')
    api = API(token)
    owner.observed_target(api, plan)
    require(owner.discovery(api, control.strict_json(data['broker.json'], 65536), receipt)
            == control.strict_json(data['discovery.json'], 65536), 'CONTINUE_DISCOVERY')
    evidence = _db_observe(psycopg, credential, plan, receipt, dispatch,
        agreement.accepted, agreement.end)
    stage, archive, baseline_raw, sidecar_raw = _stage(control, data, old_baseline,
        old_inode, agreement, evidence, run_guard)
    run_guard.assert_running()
    agreement.assert_held(SCOPE)
    _db_observe(psycopg, credential, plan, receipt, dispatch, agreement.accepted, agreement.end)
    stage_inode = _switch(control, stage, archive, old_inode, old_baseline, data,
        baseline_raw, sidecar_raw, run_guard, agreement)
    return {'audit':'LIGHT_PILOT_SAME_TASK_CONTINUED', 'source':SOURCE,
        'old_baseline_sha256':BASELINE, 'new_baseline_sha256':control.digest(baseline_raw),
        'continuation_sha256':control.digest(sidecar_raw), 'intake_sha256':INTAKE,
        'dispatch_id':DISPATCH, 'task_id':TASK, 'archive_inode':old_inode,
        'new_root_inode':stage_inode, 'db_writes':False, 'pilot_submitted':False}


def reconcile(package_raw, payload_raw, accepted_payload, wheels, credential, token, run_guard):
    """Move only the private ledger after exact same-task read-only proof."""
    from ops import light_native_pilot_release as release
    from ops import light_native_service_controller as control
    from ops import light_native_service_switch as switch
    from ops.native_maintenance_owner_host import loaded_runtime

    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic'
            and callable(getattr(run_guard, 'assert_running', None))
            and callable(getattr(run_guard, 'assert_current', None)), 'CONTINUE_HOST')
    require(control.digest(package_raw) == PACKAGE and control.digest(payload_raw) == accepted_payload,
            'CONTINUE_INPUT')
    control.verified_package(package_raw, SOURCE, PACKAGE)
    payload = control.strict_json(payload_raw, 4096)
    require(type(payload) is dict and set(payload) == {'version','action','agreement','accepted_agreement_sha256'}
            and payload['version'] == 1 and type(payload['version']) is int
            and payload['action'] == 'continue-published', 'CONTINUE_PAYLOAD')
    old_baseline, data, old_inode = _read_old(control)
    with loaded_runtime(wheels) as (psycopg, _):
        return _loaded(package_raw, payload, old_baseline, data, old_inode,
            psycopg, credential, token, run_guard, control, release, switch)
