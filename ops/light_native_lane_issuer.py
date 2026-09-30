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


def progress(directory, policy, accepted):
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
        require(set(p.name for p in path.iterdir()) == {'intent.json', 'done.json'},
                'LANE_ISSUER_RECONCILIATION_REQUIRED')
        intent = owner.parse(owner.read(path/'intent.json'))
        require(type(intent) is dict and set(intent) == {'start', 'cycle'}, 'LANE_ISSUER_HISTORY')
        expected = derive(policy, accepted, number, predecessor, intent['start'])
        require(intent['cycle'] == expected, 'LANE_ISSUER_HISTORY')
        prepare = expected['prepare']
        root = cycle.location(prepare)
        require(owner.read(root/'intent.json') == owner.encoded(expected), 'LANE_ISSUER_HISTORY')
        result = owner.parse(owner.read(root/'complete.json'))
        require(owner.parse(owner.read(path/'done.json')) == result
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
