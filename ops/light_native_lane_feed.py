"""Publish an accepted serial dispatch to the installed lane under unchanged HOLD.

Called only by a reviewed owner controller with fresh DB/PR access. This module
neither creates work nor enables SQL controls, credentials, network or RUN.
An interrupted publication is retained for reconciliation, never retried.
"""
import json
import os
import pwd
import stat

from oracle_autopilot import light_native_lane as lane
from oracle_autopilot import light_native_preflight as preflight
from ops import light_native_lane_install as install
from ops import light_native_pilot_release as release

require = release.require


def root_record(path, limit=65536):
    """Exact root control metadata; no permissive fallback for old records."""
    install.root_parent(path.parent)
    row = path.lstat()
    user = pwd.getpwnam('school-autopilot')
    require(stat.S_ISREG(row.st_mode) and row.st_uid == 0
            and row.st_gid == user.pw_gid and row.st_nlink == 1
            and stat.S_IMODE(row.st_mode) == 0o640, 'LANE_FEED_CONTROL_METADATA')
    return install.hold.read(path,0o640,limit)


def completed_history(source):
    """Read immutable completed records while the dormant process holds its lock.

    Caller must attest HOLD before/after use. Do not instantiate Journal here:
    its global flock belongs to the dormant service. The consumer still runs
    its original complete history/lock check before any provider effect.
    """
    user = pwd.getpwnam('school-autopilot')
    def directory(path, parent=None):
        fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        row = os.fstat(fd)
        if not (row.st_uid == user.pw_uid and stat.S_IMODE(row.st_mode) == 0o700):
            os.close(fd)
            raise RuntimeError('LANE_FEED_HISTORY_DIRECTORY')
        return fd, (row.st_dev,row.st_ino)
    def read(fd,name):
        leaf = os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(leaf,'rb') as stream:
            row = os.fstat(stream.fileno())
            require(stat.S_ISREG(row.st_mode) and row.st_uid == user.pw_uid
                    and row.st_nlink == 1 and stat.S_IMODE(row.st_mode) == 0o600
                    and 0 < row.st_size <= 262144, 'LANE_FEED_HISTORY_FILE')
            raw = stream.read(262145)
        require(0 < len(raw) <= 262144, 'LANE_FEED_HISTORY_FILE')
        return raw
    fd,identity = directory(install.STATE)
    try:
        names = set(os.listdir(fd))
        numbers = sorted({int(lane.RECORD.fullmatch(n)[1]) for n in names if lane.RECORD.fullmatch(n)})
        require(numbers and len(numbers) < lane.MAX_JOBS
                and numbers == list(range(len(numbers))), 'LANE_FEED_HISTORY_GAP')
        expected = {'pilot.lock'}
        previous = None
        seen = set()
        history = []
        for sequence in numbers:
            start,done = f'{sequence:08d}-intent.json',f'{sequence:08d}-terminal.json'
            intent,terminal = read(fd,start),read(fd,done)
            entry = lane.entry(intent,source)
            require(entry['sequence'] == sequence and entry['previous_terminal_sha256'] == previous
                    and entry['dispatch_id'] not in seen, 'LANE_FEED_HISTORY_CHAIN')
            accepted = lane.acceptance_record(intent,terminal)
            record = lane.parse(terminal)
            claim_name = 'claim-'+entry['dispatch_id']
            claim,claim_identity = directory(claim_name,fd)
            try:
                require(read(claim,'request.json') == lane.encoded(record['request']), 'LANE_FEED_HISTORY_CLAIM')
                permit = read(claim,'permit.json')
                require(lane.digest(permit) == entry['permit_sha256']
                        and lane.parse(permit)['dispatch'] == {k:v for k,v in record['request'].items() if k!='reservation_id'},
                        'LANE_FEED_HISTORY_CLAIM')
                current = os.stat(claim_name,dir_fd=fd,follow_symlinks=False)
                require((current.st_dev,current.st_ino)==claim_identity, 'LANE_FEED_HISTORY_CHANGED')
            finally:
                os.close(claim)
            job = install.CONTROL/'jobs'/entry['dispatch_id']
            require(root_record(job/'permit.json') == permit
                    and root_record(job/'accepted-terminal.json',4096) == lane.encoded(accepted),
                    'LANE_FEED_HISTORY_ACCEPTANCE')
            require(read(fd,start)==intent and read(fd,done)==terminal, 'LANE_FEED_HISTORY_CHANGED')
            expected.update((start,done,claim_name))
            seen.add(entry['dispatch_id']);previous=lane.digest(terminal)
            history.append((intent,terminal))
        current = install.STATE.lstat()
        require(names == expected == set(os.listdir(fd))
                and (current.st_dev,current.st_ino)==identity, 'LANE_FEED_HISTORY_CHANGED')
        return history
    finally:
        os.close(fd)


def verify_serial_hold(source, previous_cursor):
    user = pwd.getpwnam('school-autopilot')
    install.verify_unit(source)
    before = install.verify_hold_process(source,user)
    require(install.hold.read(install.UNIT_FILE,0o644,8192)==install.render(source)
            and root_record(install.CONTROL/'admission',16)==b'HOLD\n'
            and root_record(install.CONTROL/'current.json',4096)==previous_cursor,
            'LANE_FEED_SERIAL_HOLD')
    history = completed_history(source)
    require(history[-1][0] == previous_cursor, 'LANE_FEED_SERIAL_CURSOR')
    require(install.verify_hold_process(source,user)==before
            and root_record(install.CONTROL/'admission',16)==b'HOLD\n', 'LANE_FEED_SERIAL_HOLD')
    return before,history


def publish_first(conn, package_raw, accepted_package, permit_raw, accepted_permit,
                  controller_source, read_pr, plan_raw, accepted_plan,
                  receipt_raw, accepted_receipt, *, previous_cursor=None):
    """Create an immutable permit before atomically publishing the next sequence.

    Accepted digests are supplied by the independent controller review, never
    derived here as a substitute for acceptance. read_pr is the controller's
    authenticated read-only GitHub port. No service/SQL mutation is performed.
    """
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic',
            'LANE_FEED_HOST')
    require(type(package_raw) is bytes and len(package_raw) <= 3*1024*1024
            and release.source.identifier(accepted_package,64)
            and release.hashlib.sha256(package_raw).hexdigest() == accepted_package,
            'LANE_FEED_PACKAGE_NOT_ACCEPTED')
    package = json.loads(package_raw)
    source = package['source']
    require(package['version'] == 1 and source == install.RETAINED_SOURCE,
            'LANE_FEED_SOURCE')
    release.validate(package['runtime'],source,package['runtime']['sha256'])
    release.staging.verify_release(install.plan.source_path(source),package['runtime'])
    permit = lane.Permit(permit_raw,accepted_permit,lambda:permit_raw,source)
    value, dispatch = permit.value, permit.value['dispatch']
    from database.light_native_pilot_intake import Plan
    plan = Plan(plan_raw,accepted_plan)
    require(type(receipt_raw) is bytes and len(receipt_raw) <= 65536
            and release.source.identifier(accepted_receipt,64)
            and release.hashlib.sha256(receipt_raw).hexdigest() == accepted_receipt,
            'LANE_FEED_RECEIPT_NOT_ACCEPTED')
    receipt = lane.parse(receipt_raw)
    require(plan.value['source'] == source and receipt['plan_sha256'] == plan.digest
            and receipt['dispatch_id'] == dispatch['dispatch_id']
            and receipt['assignment'] == dispatch['assignment']
            and dispatch['assignment']['role'] == 'AUTOPILOT'
            and receipt['task_id'] == value['owner_preflight']['task_id']
            and receipt['work_item_id'] == value['owner_preflight']['work_item_id']
            and receipt['goal_json_sha256'] == value['owner_preflight']['goal_json_sha256']
            and all(receipt['dispatch'][k] == dispatch[k] for k in
                ('dispatch_id','expected_head_sha','mode','target_pr','task_fingerprint'))
            and dispatch['branch'] == plan.value['branch']
            and dispatch['target_pr'] == plan.value['target_pr']
            and dispatch['expected_head_sha'] == plan.value['expected_head_sha']
            and all(dispatch['assignment']['task_spec_json'].get(k) == v
                    for k,v in plan.value['task_spec_json'].items()), 'LANE_FEED_PLAN_BINDING')
    cloud = install.hold.read(release.ROOT/source/'environment.json',0o600,4096)
    require(value['environment_id'] == release.CLOUD_ENVIRONMENT_ID
            and value['environment_evidence_sha256'] == lane.digest(cloud),
            'LANE_FEED_ENVIRONMENT')
    user = pwd.getpwnam('school-autopilot')
    history = None
    if previous_cursor is None:
        before = install.verify_running(source,user)
    else:
        before,history = verify_serial_hold(source,previous_cursor)
    prior = install.hold.service_hold_identity()
    state = install.STATE.lstat()
    require(stat.S_ISDIR(state.st_mode) and state.st_uid == user.pw_uid
            and stat.S_IMODE(state.st_mode) == 0o700
            and (history is not None or set(os.listdir(install.STATE)) == {'pilot.lock'}), 'LANE_FEED_NOT_PRISTINE')
    installed = json.loads(install.hold.read(install.LEDGER/'installed.json',0o600,4096))
    require(installed.get('source') == source and installed.get('stop_rehearsal') is True
            and (history is not None or installed.get('invocation') == before['InvocationID'])
            and installed.get('unit_sha256') == release.hashlib.sha256(install.render(source)).hexdigest(),
            'LANE_FEED_INSTALLATION')
    sequence = len(history) if history is not None else 0
    require(not history or dispatch['dispatch_id'] not in {lane.parse(item[0])['dispatch_id'] for item in history},
            'LANE_FEED_REUSED_DISPATCH')
    raw = lane.encoded(dict(version=1,source=source,sequence=sequence,
        dispatch_id=dispatch['dispatch_id'],permit_sha256=accepted_permit,
        previous_terminal_sha256=lane.digest(history[-1][1]) if history else None))
    lane.entry(raw,source)

    def fresh():
        permit.check()
        release.staging.require_current_main(controller_source)
        require(install.hold.service_hold_identity() == prior, 'LANE_FEED_LEGACY_CHANGED')
        pr = read_pr(dispatch['target_pr'])
        require(pr['number'] == dispatch['target_pr'] and pr['state'] == 'open'
                and pr['merged'] is False and pr['head']['sha'] == dispatch['expected_head_sha']
                and pr['head']['ref'] == dispatch['branch']
                and pr['head']['repo']['full_name'] == pr['base']['repo']['full_name']
                    == 'olegmed1-art/bridge-video-free', 'LANE_FEED_TARGET_CHANGED')
        evidence = preflight.observe(conn,dispatch,issued_at=value['issued_at'],
            expires_at=value['expires_at'],
            agreement_sha256=value['owner_preflight']['agreement_sha256'])
        require(evidence == value['owner_preflight'], 'LANE_FEED_DATABASE_CHANGED')
        require(install.show(install.UNIT,list(before)) == before
                and install.hold.read(install.CONTROL/'admission',0o640,16) == b'HOLD\n'
                and (set(os.listdir(install.STATE)) == {'pilot.lock'} if history is None
                     else verify_serial_hold(source,previous_cursor) == (before,history)), 'LANE_FEED_HOLD_CHANGED')

    fresh()
    # One create-only intent arbitrates publishers of any dispatch identity.
    # A retained directory/permit after any failure prevents a blind retry.
    if history is None:
        require(not (install.CONTROL/'current.json').exists(), 'LANE_FEED_ALREADY_PUBLISHED')
    job = install.CONTROL/'jobs'/dispatch['dispatch_id']
    expected_jobs = {lane.parse(item[0])['dispatch_id'] for item in history} if history else set()
    require(set(os.listdir(job.parent)) == expected_jobs, 'LANE_FEED_RECONCILIATION_REQUIRED')
    intent_name = 'first-feed-intent.json' if history is None else f'{sequence:08d}-feed-intent.json'
    install.write_new(install.LEDGER/intent_name,raw,0o600)
    install.fresh_directory(job,0o750,gid=user.pw_gid)
    install.write_new(job/'permit.json',permit_raw,0o640,user.pw_gid)
    fresh()
    # Write/fsync a new leaf, then hard-link without replacing any current cursor.
    # Both paths have root-controlled ancestors; failure retains staging evidence.
    staged = job/'cursor.json'
    install.write_new(staged,raw,0o640,user.pw_gid)
    if history is None:
        os.link(staged,install.CONTROL/'current.json',follow_symlinks=False)
        staged.unlink()  # root_bytes requires a single link before any later RUN.
    else:
        # All phase writers share the authenticated owner-driver flock. Require
        # the exact predecessor under HOLD immediately before atomic publication.
        fresh()
        require(root_record(install.CONTROL/'current.json',4096)==previous_cursor, 'LANE_FEED_CURSOR_CHANGED')
        os.replace(staged,install.CONTROL/'current.json')
    release.staging.fsync_directory(job)
    release.staging.fsync_directory(install.CONTROL)
    for path, expected, limit in ((install.CONTROL/'current.json',raw,4096),
                                  (job/'permit.json',permit_raw,65536)):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and info.st_gid == user.pw_gid and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o640
                and install.hold.read(path,0o640,limit) == expected, 'LANE_FEED_READBACK')
    return dict(audit='LIGHT_NATIVE_FIRST_FEED',state='STAGED_HOLD',
        source=source,dispatch_id=dispatch['dispatch_id'],cursor_sha256=lane.digest(raw),
        task_started=False)
