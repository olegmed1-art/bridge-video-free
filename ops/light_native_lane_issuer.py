"""Issue one new finite READ_ONLY cycle from an independently accepted catalogue.

The policy, not a hash computed by the issuer, is the authority to derive a new
Agreement. No schedule, credential store, unknown-outcome retry or HOLD release
is installed. Existing owner/cycle checks remain authoritative at every phase.
The parent is stdlib-only; database imports stay in the verified driver child.
"""
import base64
from datetime import datetime, timezone
import os
import time

from ops import light_native_lane_controller as owner
from ops import light_native_lane_cycle as cycle
from ops.native_maintenance_agreement import Agreement, COVERAGE
from ops.native_permission_hold_guard import EXPECTED_TARGET

require = owner.require
POLICY_KEYS = {'version', 'action', 'source', 'accepted_controller_sha256',
               'accepted_runtime_sha256', 'not_before', 'expires_at',
               'predecessor', 'plans', 'authority'}


def validate(raw, accepted, controller, runtime, guard):
    require(type(raw) is bytes and len(raw) <= 196608 and owner.sha(raw) == accepted,
            'LANE_ISSUER_NOT_ACCEPTED')
    policy = owner.parse(raw)
    require(type(policy) is dict and set(policy) == POLICY_KEYS
            and type(policy['version']) is int and policy['version'] == 1
            and policy['action'] == 'issue', 'LANE_ISSUER_SCOPE')
    authority = policy['authority']
    require(type(authority) is dict and set(authority) == {'owner', 'coverage', 'delegation', 'evidence'}
            and authority['owner'] == 'olegmed1-art' and authority['coverage'] == COVERAGE
            and authority['delegation'] == 'FINITE_ISSUER_AGREEMENTS'
            and type(authority['evidence']) is str and 1 <= len(authority['evidence']) <= 1024,
            'LANE_ISSUER_AUTHORITY')
    owner.validate_package(controller, policy['source'], policy['accepted_controller_sha256'])
    require(owner.sha(runtime) == policy['accepted_runtime_sha256'], 'LANE_ISSUER_SCOPE')
    start, end = (Agreement.timestamp(policy[k]) for k in ('not_before', 'expires_at'))
    require(0 < end-start <= 86400 and start <= time.time() < end, 'LANE_ISSUER_EXPIRED')
    require(type(policy['plans']) is list and 1 <= len(policy['plans']) <= 8,
            'LANE_ISSUER_SCOPE')
    identities = [set(), set(), set()]
    for entry in policy['plans']:
        require(type(entry) is dict and set(entry) == {'plan_base64', 'accepted_plan_sha256'},
                'LANE_ISSUER_SCOPE')
        raw_plan = base64.b64decode(entry['plan_base64'], validate=True)
        require(len(raw_plan) <= 16384 and owner.sha(raw_plan) == entry['accepted_plan_sha256'],
                'LANE_ISSUER_SCOPE')
        plan = owner.parse(raw_plan)
        # Full Plan and zero-budget validation is repeated inside the driver child.
        require(type(plan) is dict and plan.get('source') == owner.install.RETAINED_SOURCE
                and type(plan.get('work_key')) is str and type(plan.get('target_pr')) is int,
                'LANE_ISSUER_SCOPE')
        for seen, value in zip(identities, (entry['accepted_plan_sha256'], plan['work_key'], plan['target_pr'])):
            require(value not in seen, 'LANE_ISSUER_DUPLICATE')
            seen.add(value)
        owner.sequence(dict(version=2, predecessor=policy['predecessor'],
                            accepted_plan_sha256=entry['accepted_plan_sha256']))
    require(policy['predecessor']['sequence'] + len(policy['plans']) < 10000,
            'LANE_ISSUER_SCOPE')
    guard.assert_current()
    owner.release.staging.require_current_main(policy['source'])
    return policy


def derive(policy, accepted, index, predecessor, start):
    """Fixed explicit policy delegation, with fresh bounded nonrenewable time."""
    require(type(index) is int and 0 <= index < len(policy['plans']) and type(start) is int,
            'LANE_ISSUER_SCOPE')
    end = min(start + 1800, int(Agreement.timestamp(policy['expires_at'])))
    require(start >= Agreement.timestamp(policy['not_before']) and end-start >= 900,
            'LANE_ISSUER_EXPIRED')
    entry = policy['plans'][index]
    scope = dict(version=1, operation='native_single_pilot', source=owner.install.RETAINED_SOURCE,
                 target=EXPECTED_TARGET, plan_sha256=entry['accepted_plan_sha256'])
    stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    agreement = dict(version=1, owner='olegmed1-art', operation_digest=owner.sha(owner.encoded(scope)),
                     not_before=stamp(start), expires_at=stamp(end), coverage=COVERAGE,
                     evidence=f'Explicit finite issuer policy {accepted}; catalogue entry {index}. '
                              'No unlisted work, renewal, repair, spending or unknown-outcome replay.')
    prepare = dict(version=2, action='prepare', source=policy['source'],
                   accepted_controller_sha256=policy['accepted_controller_sha256'],
                   accepted_runtime_sha256=policy['accepted_runtime_sha256'], **entry,
                   agreement=agreement, accepted_agreement_sha256=owner.sha(owner.encoded(agreement)),
                   accepted_receipt_sha256=None, accepted_discovery_sha256=None,
                   accepted_permit_sha256=None, accepted_terminal_sha256=None, predecessor=predecessor)
    owner.sequence(prepare)
    return dict(version=1, action='cycle', prepare=prepare)


def progress(directory, policy, accepted, *, unacknowledged=None):
    """Read all entries, refusing holes, partial writes and unknown outcomes."""
    predecessor = policy['predecessor']
    index = 0
    names = {'policy.json'} | {f'{i:04d}' for i in range(len(policy['plans']))}
    require(set(p.name for p in directory.iterdir()) <= names, 'LANE_ISSUER_HISTORY')
    for number, entry in enumerate(policy['plans']):
        path = directory / f'{number:04d}'
        if not path.exists():
            require(not path.is_symlink(), 'LANE_ISSUER_HISTORY')
            continue
        require(number == index, 'LANE_ISSUER_HISTORY')
        owner.install.root_parent(path)
        names = set(p.name for p in path.iterdir())
        missing = number == unacknowledged and names == {'intent.json'}
        require(missing or names == {'intent.json', 'done.json'},
                'LANE_ISSUER_RECONCILIATION_REQUIRED')
        intent = owner.parse(owner.read(path/'intent.json'))
        require(type(intent) is dict and set(intent) == {'start', 'cycle'}, 'LANE_ISSUER_HISTORY')
        expected = derive(policy, accepted, number, predecessor, intent['start'])
        require(intent['cycle'] == expected, 'LANE_ISSUER_HISTORY')
        prepare = expected['prepare']
        root = cycle.location(prepare)
        require(owner.read(root/'intent.json') == owner.encoded(expected), 'LANE_ISSUER_HISTORY')
        result = owner.parse(owner.read(root/'complete.json'))
        require((missing or owner.parse(owner.read(path/'done.json')) == result)
                and result.get('audit') == 'LIGHT_LANE_CYCLE'
                and result.get('state') == 'COMPLETE_HOLD' and result.get('controls_restored') is True
                and result.get('plan_sha256') == entry['accepted_plan_sha256']
                and result.get('sequence') == owner.sequence(prepare), 'LANE_ISSUER_HISTORY')
        terminal = owner.read(owner.ROOT/entry['accepted_plan_sha256']/'terminal.json',
                              result.get('terminal_sha256'))
        require(owner.sha(terminal) == result.get('terminal_sha256'), 'LANE_ISSUER_HISTORY')
        # The isolated preflight freshly verifies restored controls and actual
        # Cloud/DB predecessor before any next issue; these bytes alone don't.
        predecessor = dict(plan_sha256=entry['accepted_plan_sha256'],
                           terminal_sha256=result['terminal_sha256'], sequence=result['sequence'])
        index += 1
    return index, predecessor


def preflight(policy, accepted, proposed, credential, token, psycopg):
    """Live read-only registry/head/predecessor admission; called in driver child."""
    from database.light_native_pilot_intake import Plan
    from database import light_native_pilot_intake as intake
    from ops.native_maintenance_owner_attest import parameters
    from ops.native_maintenance_run_guard import API
    from ops.light_native_pilot_owner import observed_target
    require(type(proposed) is dict and set(proposed) == {'index', 'start', 'predecessor'},
            'LANE_ISSUER_SCOPE')
    # Validate every allowlisted scope, including entries not selected this time.
    plans = [Plan(base64.b64decode(e['plan_base64'], validate=True), e['accepted_plan_sha256'])
             for e in policy['plans']]
    outer = derive(policy, accepted, **proposed)
    cycle.validate_scope(outer['prepare'])
    plan = plans[proposed['index']]
    observed_target(API(token), plan)
    with psycopg.connect(**parameters(credential), autocommit=True) as conn:
        conn.read_only = True
        intake.engine.identity(conn, intake.target())
        counts = conn.execute("""SELECT
          (SELECT count(*) FROM autopilot.task WHERE status IN
            ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')),
          (SELECT count(*) FROM autopilot.native_cli_receipt WHERE state<>'TERMINAL'),
          (SELECT count(*) FROM autopilot.project_work_item WHERE repository=%s
            AND (target_pr=%s OR work_key=%s)),
          (SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE repository=%s AND target_pr=%s)""",
          (plan.value['repository'], plan.value['target_pr'], plan.value['work_key'],
           plan.value['repository'], plan.value['target_pr'])).fetchone()
        require(counts == (0, 0, 0, 0), 'LANE_ISSUER_QUEUE_OR_DUPLICATE')
        config = intake.one(conn, 'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
        role = intake.one(conn, "SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
        require(config['enabled'] is False and role['enabled'] is True and role['can_repair'] is True,
                'LANE_ISSUER_CONTROLS')
        owner.verify_previous(conn, outer['prepare'])
    return dict(audit='LIGHT_LANE_ISSUER_ADMITTED', cycle_sha256=owner.sha(owner.encoded(outer)))


def run(wheels, credential, token, controller, runtime, raw, accepted, guard):
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'LANE_ISSUER_SCOPE')
    policy = validate(raw, accepted, controller, runtime, guard)
    root = owner.ROOT/'issuers'
    owner.install.root_parent(owner.ROOT)
    if not root.exists():
        owner.install.fresh_directory(root, 0o700)
    # Separate global issuer lock; cycle's lock still serializes phase work.
    with cycle.exclusive(root):
        # A different policy must not sidestep an interrupted earlier issue.
        for previous in root.iterdir():
            if previous.name == 'cycle.lock':
                continue
            require(owner.release.source.identifier(previous.name, 64), 'LANE_ISSUER_HISTORY')
            prior_raw = owner.read(previous/'policy.json', previous.name)
            progress(previous, owner.parse(prior_raw), previous.name)
        directory = root/accepted
        if not directory.exists():
            owner.install.fresh_directory(directory, 0o700)
            owner.retain(directory/'policy.json', raw)
        require(owner.read(directory/'policy.json', accepted) == raw, 'LANE_ISSUER_HISTORY')
        index, predecessor = progress(directory, policy, accepted)
        if index == len(policy['plans']):
            return dict(audit='LIGHT_LANE_ISSUER', state='EXHAUSTED', issued=index)
        proposed = dict(index=index, predecessor=predecessor, start=int(time.time()))
        outer = derive(policy, accepted, **proposed)
        result = cycle.isolated(wheels, credential, token, controller, runtime, raw, accepted,
                                guard, outer=proposed, mode='issuer_validate')
        expected = dict(audit='LIGHT_LANE_ISSUER_ADMITTED', cycle_sha256=owner.sha(owner.encoded(outer)))
        require(result == expected, 'LANE_ISSUER_SCOPE')
        guard.assert_current()
        owner.release.staging.require_current_main(policy['source'])
        # Recheck time immediately before recording an irrevocable issue intent.
        require(Agreement.timestamp(outer['prepare']['agreement']['expires_at'])-time.time() >= 960,
                'LANE_ISSUER_EXPIRED')
        path = directory/f'{index:04d}'
        owner.install.fresh_directory(path, 0o700)
        owner.retain(path/'intent.json', owner.encoded(dict(start=proposed['start'], cycle=outer)))
        # Any exception/lost ACK leaves the intent. There is no automatic retry.
        cycle_raw = owner.encoded(outer)
        result = cycle.run(wheels, credential, token, controller, runtime, cycle_raw,
                           owner.sha(cycle_raw), guard)
        actual = owner.parse(owner.read(cycle.location(outer['prepare'])/'complete.json'))
        require(result == actual and result.get('state') == 'COMPLETE_HOLD'
                and result.get('controls_restored') is True, 'LANE_ISSUER_HISTORY')
        owner.retain(path/'done.json', owner.encoded(result))
        return dict(audit='LIGHT_LANE_ISSUER', state='ISSUED_COMPLETE_HOLD', issued=index+1,
                    remaining=len(policy['plans'])-index-1, cycle=result)


MONITOR_KEYS = {'version', 'action', 'source', 'accepted_controller_sha256',
                'accepted_runtime_sha256', 'policy_sha256', 'index', 'expected_cycle_sha256'}


def monitor_request(raw, accepted, controller, runtime, guard):
    """Current source authority; historical policy need not still issue work."""
    require(type(raw) is bytes and len(raw) <= 4096 and owner.sha(raw) == accepted,
            'LANE_ISSUER_NOT_ACCEPTED')
    value = owner.parse(raw)
    require(type(value) is dict and set(value) == MONITOR_KEYS
            and type(value['version']) is int and value['version'] == 1
            and value['action'] in ('observe-issue', 'reconcile-issue')
            and owner.release.source.identifier(value['policy_sha256'], 64)
            and type(value['index']) is int and 0 <= value['index'] < 8
            and (value['expected_cycle_sha256'] is None and value['action'] == 'observe-issue'
                 or owner.release.source.identifier(value['expected_cycle_sha256'], 64)),
            'LANE_ISSUER_SCOPE')
    owner.validate_package(controller, value['source'], value['accepted_controller_sha256'])
    require(owner.sha(runtime) == value['accepted_runtime_sha256'], 'LANE_ISSUER_SCOPE')
    guard.assert_current()
    owner.release.staging.require_current_main(value['source'])
    return value


def monitor_records(value):
    """Inspect one latest entry; missing completion can never authorize writes."""
    policy_id = value['policy_sha256']
    directory = owner.ROOT/'issuers'/policy_id
    policy = owner.parse(owner.read(directory/'policy.json', policy_id))
    require(type(policy) is dict and set(policy) == POLICY_KEYS
            and policy['action'] == 'issue' and policy['version'] == 1
            and type(policy['plans']) is list and value['index'] < len(policy['plans'])
            and policy['accepted_runtime_sha256'] == value['accepted_runtime_sha256'],
            'LANE_ISSUER_HISTORY')
    index = value['index']
    path = directory/f'{index:04d}'
    owner.install.root_parent(path)
    names = set(p.name for p in path.iterdir())
    require(names in ({'intent.json'}, {'intent.json', 'done.json'}), 'LANE_ISSUER_HISTORY')
    # Prior entries must be complete, selected entry latest, no future journal.
    require(not any((directory/f'{n:04d}').exists() or (directory/f'{n:04d}').is_symlink()
                    for n in range(index+1, len(policy['plans']))), 'LANE_ISSUER_HISTORY')
    intent_raw = owner.read(path/'intent.json')
    intent = owner.parse(intent_raw)
    require(type(intent) is dict and set(intent) == {'start', 'cycle'}, 'LANE_ISSUER_HISTORY')
    prepare = intent['cycle']['prepare']
    require(prepare['accepted_plan_sha256'] == policy['plans'][index]['accepted_plan_sha256'],
            'LANE_ISSUER_HISTORY')
    root = cycle.location(prepare)
    # Incomplete data is diagnostic only, never an invitation to repeat a phase.
    if not (root/'complete.json').exists():
        require(value['action'] == 'observe-issue' and value['expected_cycle_sha256'] is None,
                'LANE_ISSUER_RECONCILIATION_REQUIRED')
        return dict(policy=policy, path=path, state='INCOMPLETE_REQUIRES_RECONCILIATION')
    count, predecessor = progress(directory, policy, policy_id, unacknowledged=index)
    require(count == index+1, 'LANE_ISSUER_HISTORY')
    raw = owner.read(root/'complete.json', value['expected_cycle_sha256'])
    result = owner.parse(raw)
    for action in cycle.STEPS:
        require(owner.read(root/(action+'-intent.json')) == owner.encoded(cycle.derive(prepare, action)),
                'LANE_ISSUER_HISTORY')
        cycle.checked_result(action, owner.parse(owner.read(root/(action+'-done.json'))), prepare)
    terminal = owner.parse(owner.read(owner.ROOT/prepare['accepted_plan_sha256']/'terminal.json'))
    expected_result = dict(audit='LIGHT_LANE_CYCLE', state='COMPLETE_HOLD',
        plan_sha256=prepare['accepted_plan_sha256'], sequence=owner.sequence(prepare),
        dispatch_id=terminal['dispatch_id'], task_id=terminal['task_id'],
        terminal_sha256=owner.sha(owner.encoded(terminal)),
        result_code=terminal['result']['result_code'], controls_restored=True)
    require(result == expected_result, 'LANE_ISSUER_HISTORY')
    # No incidental incident record may be silently disregarded for recovery.
    require(not (root/'incident.json').exists() and not (root/'incident.json').is_symlink(),
            'LANE_ISSUER_RECONCILIATION_REQUIRED')
    return dict(policy=policy, path=path, state='COMPLETE_ACKNOWLEDGED' if 'done.json' in names
                else 'COMPLETE_ACK_MISSING', result=result, complete_raw=raw,
                predecessor=predecessor, intent_sha256=owner.sha(intent_raw))


def monitor_live(value, record, credential, psycopg, runtime):
    """Read-only primary-source proof for the original completed job only."""
    import json
    from database import light_native_pilot_intake as intake
    from ops.native_maintenance_owner_attest import parameters
    retained = json.loads(runtime)
    require(owner.release.encoded(retained) == runtime
            and retained['source'] == owner.install.RETAINED_SOURCE, 'LANE_ISSUER_SCOPE')
    owner.release.validate(retained['runtime'], retained['source'], retained['runtime']['sha256'])
    owner.release.staging.verify_release(owner.execution.plan.source_path(retained['source']), retained['runtime'])
    for entry in record['policy']['plans']:
        intake.Plan(base64.b64decode(entry['plan_base64'], validate=True), entry['accepted_plan_sha256'])
    previous = record['predecessor']
    scope = owner.ROOT/previous['plan_sha256']
    receipt = owner.parse(owner.read(scope/'intake.json'))
    before = intake.engine.load_manifest(scope/'before.json', receipt['snapshot_sha256'])
    require(before['version'] == 1 and before['plan_sha256'] == previous['plan_sha256']
            and before['target'] == intake.EXPECTED_TARGET, 'LANE_ISSUER_HISTORY')
    # verify_previous inspects the existing completed sequence as predecessor.
    # This synthetic identifier is never a Plan and is never submitted/intaken.
    verifier = dict(version=2, predecessor=previous, accepted_plan_sha256=owner.sha(owner.encoded(value)),
                    accepted_runtime_sha256=value['accepted_runtime_sha256'])
    with psycopg.connect(**parameters(credential), autocommit=True) as conn:
        conn.read_only = True
        intake.engine.identity(conn, intake.target())
        counts = conn.execute("""SELECT
          (SELECT count(*) FROM autopilot.task WHERE status NOT IN ('DONE','FAILED_CLOSED')),
          (SELECT count(*) FROM autopilot.native_cli_receipt WHERE state<>'TERMINAL')""").fetchone()
        require(counts == (0, 0), 'LANE_ISSUER_QUEUE_OR_DUPLICATE')
        config = intake.one(conn, 'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
        role = intake.one(conn, "SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
        baseline = before['autopilot_role']
        require(config == before['native_config'] and config['enabled'] is False
                and set(role) == set(baseline)
                and all(role[k] == v for k, v in baseline.items() if k != 'updated_at'),
                'LANE_ISSUER_CONTROLS')
        owner.verify_previous(conn, verifier, readonly=True)
    return dict(audit='LIGHT_LANE_ISSUER_OBSERVED', state=record['state'],
                cycle_sha256=owner.sha(record['complete_raw']), intent_sha256=record['intent_sha256'],
                policy_sha256=value['policy_sha256'], index=value['index'],
                sequence=previous['sequence'], result_code=record['result']['result_code'])


def monitor_child(wire, decoded, expected, guard):
    value = monitor_request(decoded['payload'], expected['payload'], decoded['controller'], decoded['runtime'], guard)
    record = monitor_records(value)
    require(record['state'] != 'INCOMPLETE_REQUIRES_RECONCILIATION', 'LANE_ISSUER_RECONCILIATION_REQUIRED')
    from ops.native_maintenance_owner_host import loaded_runtime
    with loaded_runtime(decoded['driver']) as (psycopg, _):
        result = monitor_live(value, record, wire['credential'], psycopg, decoded['runtime'])
    require(monitor_records(value) == record, 'LANE_ISSUER_HISTORY')
    guard.assert_running()
    return result


def monitor(wheels, credential, token, controller, runtime, raw, accepted, guard):
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'LANE_ISSUER_SCOPE')
    value = monitor_request(raw, accepted, controller, runtime, guard)
    root = owner.ROOT/'issuers'
    owner.install.root_parent(root)
    require((root/'cycle.lock').exists(), 'LANE_ISSUER_HISTORY')
    with cycle.exclusive(root, create=False):
        record = monitor_records(value)
        if record['state'] == 'INCOMPLETE_REQUIRES_RECONCILIATION':
            return dict(audit='LIGHT_LANE_ISSUER_OBSERVED', state=record['state'],
                        policy_sha256=value['policy_sha256'], index=value['index'], live_verified=False)
        proof = cycle.isolated(wheels, credential, token, controller, runtime, raw, accepted,
                               guard, outer=accepted, mode='issuer_monitor')
        require(proof == dict(audit='LIGHT_LANE_ISSUER_OBSERVED', state=record['state'],
            cycle_sha256=owner.sha(record['complete_raw']), intent_sha256=record['intent_sha256'],
            policy_sha256=value['policy_sha256'], index=value['index'],
            sequence=record['predecessor']['sequence'], result_code=record['result']['result_code']),
            'LANE_ISSUER_HISTORY')
        guard.assert_current()
        require(monitor_records(value) == record, 'LANE_ISSUER_HISTORY')
        if value['action'] == 'observe-issue':
            return dict(**proof, live_verified=True)
        require(value['expected_cycle_sha256'] == proof['cycle_sha256'], 'LANE_ISSUER_NOT_ACCEPTED')
        recovery_root = owner.ROOT/'issuer-reconciliations'
        recovery = recovery_root/accepted
        if record['state'] == 'COMPLETE_ACKNOWLEDGED':
            require(owner.read(recovery/'request.json', accepted) == raw, 'LANE_ISSUER_NOT_ACCEPTED')
            expected_binding = owner.encoded(dict(policy_sha256=value['policy_sha256'], index=value['index'],
                cycle_sha256=proof['cycle_sha256'], intent_sha256=proof['intent_sha256']))
            require(owner.read(recovery/'binding.json') == expected_binding, 'LANE_ISSUER_HISTORY')
            return dict(audit='LIGHT_LANE_ISSUER_RECONCILED', state='COMPLETE_ACKNOWLEDGED',
                        policy_sha256=value['policy_sha256'], index=value['index'],
                        cycle_sha256=proof['cycle_sha256'], execution_replayed=False)
        require(record['state'] == 'COMPLETE_ACK_MISSING', 'LANE_ISSUER_RECONCILIATION_REQUIRED')
        # Recovery is solely a missing local ACK. No cycle, phase, DB mutation,
        # provider submission, permit extension or service operation is called.
        recovery_root = owner.ROOT/'issuer-reconciliations'
        if not recovery_root.exists():
            owner.install.fresh_directory(recovery_root, 0o700)
        recovery = recovery_root/accepted
        if not recovery.exists():
            owner.install.fresh_directory(recovery, 0o700)
        owner.remember(recovery/'request.json', raw)
        owner.remember(recovery/'binding.json', owner.encoded(dict(
            policy_sha256=value['policy_sha256'], index=value['index'],
            cycle_sha256=proof['cycle_sha256'], intent_sha256=proof['intent_sha256'])))
        guard.assert_running()
        require(monitor_records(value) == record, 'LANE_ISSUER_HISTORY')
        fresh = cycle.isolated(wheels, credential, token, controller, runtime, raw, accepted,
                               guard, outer=accepted, mode='issuer_monitor')
        require(fresh == proof, 'LANE_ISSUER_HISTORY')
        guard.assert_running()
        require(monitor_records(value) == record, 'LANE_ISSUER_HISTORY')
        owner.remember(record['path']/'done.json', record['complete_raw'])
        require(monitor_records(value)['state'] == 'COMPLETE_ACKNOWLEDGED', 'LANE_ISSUER_HISTORY')
        return dict(audit='LIGHT_LANE_ISSUER_RECONCILED', state='COMPLETE_ACKNOWLEDGED',
                    policy_sha256=value['policy_sha256'], index=value['index'],
                    cycle_sha256=proof['cycle_sha256'], execution_replayed=False)
