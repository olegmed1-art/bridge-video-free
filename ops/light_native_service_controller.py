"""One accepted native pilot request, durable supervisor, and HOLD restoration.

This module has no network or database writer. A fixed, source-pinned launcher
must supply independently accepted request and package digests. Any uncertain
launch leaves its private ledger in place; this module has no retry/reset API.
"""
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import time

from ops import light_native_pilot_release as release
from ops import light_native_service_plan as plan
from ops import light_native_service_switch as switch
from ops import oracle_light_active_hold_attest as hold
from ops.native_maintenance_agreement import Agreement


require = plan.require
CLAIM = plan.LIGHT / 'runtime/native-single-pilot'
PACKAGE_HELPERS = tuple(name for name in release.HELPERS if name not in release.MARKERS)
REQUEST_KEYS = {'version', 'source', 'package_sha256', 'permit_b64',
                'permit_sha256', 'agreement', 'accepted_agreement_sha256',
                'scope', 'duration_seconds', 'dispatch_id', 'baseline_sha256'}
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return release.encoded(value)


def strict_json(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit, 'PILOT_REQUEST_SIZE')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'PILOT_REQUEST_DUPLICATE_KEY')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: require(False, 'PILOT_REQUEST_NONFINITE'))
    require(canonical(value) == raw, 'PILOT_REQUEST_NONCANONICAL')
    return value


def verified_package(package_raw, source, accepted):
    plan.source_path(source)
    require(type(accepted) is str and SHA256.fullmatch(accepted)
                and type(package_raw) is bytes and len(package_raw) <= 3*1024*1024
                and digest(package_raw) == accepted,
                'PILOT_PACKAGE_NOT_ACCEPTED')
    package = strict_json(package_raw, 3*1024*1024)
    require(type(package) is dict and set(package) == {'version','source','runtime','helpers'}
                and package['version'] == 1 and type(package['version']) is int
                and package['source'] == source and type(package['helpers']) is dict
                and set(package['helpers']) == set(release.HELPERS),
                'PILOT_PACKAGE_CLOSURE')
    release.validate(package['runtime'], source, package['runtime']['sha256'])
    for name in PACKAGE_HELPERS:
        require(package['helpers'][name] == (Path(__file__).parent / Path(name).name).read_text(),
                    'PILOT_PACKAGE_HELPER_CHANGED')
        require(package['runtime']['files'][name] == package['helpers'][name],
                'PILOT_PACKAGE_RUNTIME_CHANGED')
    return package


class Request:
    """Pure validation. The accepted digest must arrive from a separate path."""
    def __init__(self, raw, accepted_digest, package_raw):
        require(type(accepted_digest) is str and SHA256.fullmatch(accepted_digest)
                and digest(raw) == accepted_digest, 'PILOT_REQUEST_NOT_ACCEPTED')
        value = strict_json(raw, 262144)
        require(type(value) is dict and set(value) == REQUEST_KEYS
                and type(value['version']) is int and value['version'] == 1,
                'PILOT_REQUEST_SHAPE')
        source = value['source']
        package = verified_package(package_raw, source, value['package_sha256'])
        require(type(value['permit_b64']) is str and type(value['permit_sha256']) is str
                and SHA256.fullmatch(value['permit_sha256']), 'PILOT_PERMIT_SHAPE')
        permit = base64.b64decode(value['permit_b64'], validate=True)
        require(0 < len(permit) <= 65536 and digest(permit) == value['permit_sha256']
                and base64.b64encode(permit).decode() == value['permit_b64'],
                'PILOT_PERMIT_DIGEST')
        permit_value = strict_json(permit, 65536)
        require(type(permit_value) is dict and type(permit_value.get('issued_at')) is int,
                'PILOT_PERMIT_TIME')
        require(type(value['scope']) is dict and type(value['agreement']) is dict
                and type(value['accepted_agreement_sha256']) is str
                and SHA256.fullmatch(value['accepted_agreement_sha256'])
                and type(value['duration_seconds']) is int
                and 60 <= value['duration_seconds'] <= 1800
                and type(value['dispatch_id']) is str
                and type(value['baseline_sha256']) is str
                and SHA256.fullmatch(value['baseline_sha256'])
                and re.fullmatch(r'[0-9a-fA-F-]{36}', value['dispatch_id']),
                'PILOT_REQUEST_SCOPE')
        self.raw, self.digest, self.value = raw, accepted_digest, value
        self.package_raw, self.package, self.permit = package_raw, package, permit
        self.permit_value = permit_value
        self.permit_issued_at = permit_value['issued_at']

    def agreement(self):
        agreement = Agreement(self.value['agreement'],
                              self.value['accepted_agreement_sha256'], self.value['scope'])
        agreement.assert_held(digest(canonical(self.value['scope'])))
        return agreement


def directory(path, uid, gid, mode):
    row = path.lstat()
    require(stat.S_ISDIR(row.st_mode) and row.st_uid == uid and row.st_gid == gid
            and stat.S_IMODE(row.st_mode) == mode, 'PILOT_PRIVATE_DIRECTORY')


def parent(path, uid=0):
    row = path.lstat()
    require(stat.S_ISDIR(row.st_mode) and row.st_uid == uid
            and not row.st_mode & 0o022, 'PILOT_PARENT_CHANGED')


def new_directory(path, uid, gid, mode, *, parent_uid=0):
    parent(path.parent, parent_uid)
    require(not path.exists() and not path.is_symlink(), 'PILOT_LEDGER_ALREADY_EXISTS')
    os.mkdir(path, mode)
    os.chown(path, uid, gid)
    os.chmod(path, mode)
    release.staging.fsync_directory(path.parent)
    directory(path, uid, gid, mode)


def retained(path, raw, mode=0o600, gid=0):
    require(type(raw) is bytes and 0 < len(raw) <= 4*1024*1024, 'PILOT_RETAIN_SIZE')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchown(stream.fileno(), 0, gid)
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    release.staging.fsync_directory(path.parent)
    require(hold.read(path, mode, 4*1024*1024) == raw, 'PILOT_RETAIN_READBACK')


def read(path, limit=4*1024*1024):
    return hold.read(path, 0o600, limit)


def permit_probe(request):
    """Validate actual Permit using the service venv; never print permit text."""
    user = pwd.getpwnam('school-autopilot')
    candidate = plan.source_path(request.value['source'])
    code = '''import os,sys
sys.path.insert(0,sys.argv[1])
from oracle_autopilot.light_native_pilot import Permit
raw=sys.stdin.buffer.read(65537)
assert len(raw)<=65536
p=Permit(raw,sys.argv[2],lambda:raw,sys.argv[3])
assert p.value['dispatch']['dispatch_id']==sys.argv[4]
assert p.value['owner_preflight']['agreement_sha256']==sys.argv[5]
print('PILOT_PERMIT_VALID')
'''
    def identity():
        os.setgroups([]); os.setgid(user.pw_gid); os.setuid(user.pw_uid)
    result = subprocess.run([plan.PYTHON, '-I', '-B', '-c', code, str(candidate),
                             request.value['permit_sha256'], request.value['source'],
                             request.value['dispatch_id'],
                             request.value['accepted_agreement_sha256']],
                            input=request.permit, capture_output=True, cwd=candidate,
                            env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
                            preexec_fn=identity, timeout=25)
    require(result.returncode == 0 and result.stdout == b'PILOT_PERMIT_VALID\n',
            'PILOT_PERMIT_REFUSED')


def supervisor_source(source):
    candidate = plan.source_path(source)
    return ('''import sys
try:
 sys.path.insert(0,%r)
 from ops.light_native_service_controller import main
 main()
except BaseException:
 print('{"audit":"LIGHT_NATIVE_SUPERVISOR_REFUSED"}',flush=True)
 raise SystemExit(2) from None
''' % str(candidate)).encode()


def stage_observation(source, package):
    """Bind the candidate to the successful, create-only stage record."""
    candidate = plan.source_path(source)
    release.staging.verify_release(candidate, package['runtime'])
    staged = strict_json(read(release.ROOT / source / 'staged.json', 262144), 262144)
    require(type(staged) is dict and set(staged) == {
            'version','source','bundle_sha256','hold','readonly_probe'}
            and staged['version'] == 1 and staged['source'] == source
            and staged['bundle_sha256'] == package['runtime']['sha256']
            and staged['readonly_probe'] is True,
            'PILOT_RELEASE_NOT_STAGED')
    prior_record = strict_json(read(release.ROOT / source / 'before.json', 262144), 262144)
    prior = hold.HoldIdentity(**prior_record['hold'])
    plan.check_prior(prior)
    require(staged['hold'] == asdict(prior)
            and prior_record['source'] == source
            and prior_record['bundle_sha256'] == package['runtime']['sha256'],
            'PILOT_RELEASE_HOLD_RECORD')
    return prior


def environment_record(request):
    raw = read(release.ROOT / request.value['source'] / 'environment.json', 4096)
    record = strict_json(raw, 4096)
    permit = request.permit_value
    require(type(record) is dict and set(record) == {'version','source',
            'environment_id','service_user','command','output_sha256'}
            and record['version'] == 1 and record['source'] == request.value['source']
            and record['environment_id'] == release.CLOUD_ENVIRONMENT_ID
            and record['environment_id'] == permit.get('environment_id')
            and record['service_user'] == 'school-autopilot'
            and record['command'] == 'cloud list --env ' + release.CLOUD_ENVIRONMENT_ID
                    + ' --limit 1 --json'
            and type(record['output_sha256']) is str
            and SHA256.fullmatch(record['output_sha256'])
            and digest(raw) == permit.get('environment_evidence_sha256'),
            'PILOT_ENVIRONMENT_NOT_ACCEPTED')
    return record


def prepare_baseline(source, accepted_package_sha256, package_raw,
                     agreement_record, accepted_agreement_sha256, scope):
    """Full HOLD/empty-queue observation before any shared task is created."""
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic',
            'PILOT_HOST_IDENTITY')
    package = verified_package(package_raw, source, accepted_package_sha256)
    agreement = Agreement(agreement_record, accepted_agreement_sha256, scope)
    agreement.assert_held(digest(canonical(scope)))
    release.staging.require_current_main(source)
    prior = stage_observation(source, package)
    require(asdict(hold.attest()) == asdict(prior), 'PILOT_FULL_HOLD_CHANGED')
    switch.attest_hardening()
    switch.unit_absent(plan.SUPERVISOR_UNIT)
    switch.unit_absent(plan.PILOT_UNIT)
    protected = switch.protect_snapshot(prior)
    protected_digest = digest(canonical(protected))
    parent(plan.ROOT.parent)
    require(not plan.ROOT.exists() and not plan.ROOT.is_symlink(),
            'PILOT_EXISTING_LEDGER_REQUIRES_RECONCILIATION')
    new_directory(plan.ROOT, 0, 0, 0o700)
    baseline = {'version':1,'source':source,'package_sha256':accepted_package_sha256,
                'agreement_sha256':accepted_agreement_sha256,
                'scope_sha256':digest(canonical(scope)),
                'prior':asdict(prior),'protected':protected,
                'protected_sha256':protected_digest,'observed_at':int(time.time())}
    raw = canonical(baseline)
    retained(plan.ROOT / 'baseline.json', raw)
    agreement.assert_held(digest(canonical(scope)))
    require(asdict(hold.attest()) == asdict(prior), 'PILOT_BASELINE_HOLD_CHANGED')
    return {'audit':'LIGHT_NATIVE_PILOT_BASELINE_HELD','source':source,
            'baseline_sha256':digest(raw),'production_mutations':False}


def baseline_record(request):
    directory(plan.ROOT, 0, 0, 0o700)
    raw = read(plan.ROOT / 'baseline.json', 262144)
    record = strict_json(raw, 262144)
    require(digest(raw) == request.value['baseline_sha256']
            and type(record) is dict and set(record) == {'version','source','package_sha256',
            'agreement_sha256','scope_sha256','prior','protected','protected_sha256',
            'observed_at'} and record['version'] == 1
            and record['source'] == request.value['source']
            and record['package_sha256'] == request.value['package_sha256']
            and record['agreement_sha256'] == request.value['accepted_agreement_sha256']
            and record['scope_sha256'] == digest(canonical(request.value['scope']))
            and type(record['observed_at']) is int
            and record['observed_at'] <= request.permit_issued_at,
            'PILOT_BASELINE_CHANGED')
    prior = hold.HoldIdentity(**record['prior'])
    plan.check_prior(prior)
    protected = record['protected']
    require(digest(canonical(protected)) == record['protected_sha256'],
            'PILOT_BASELINE_PROTECTED_CHANGED')
    return prior, protected, record['protected_sha256']


def prepare(raw, accepted_digest, package_raw):
    """Bind one accepted published task to the prior full-HOLD baseline."""
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic',
            'PILOT_HOST_IDENTITY')
    request = Request(raw, accepted_digest, package_raw)
    request.agreement()
    source = request.value['source']
    release.staging.require_current_main(source)
    prior, protected, protected_digest = baseline_record(request)
    require(asdict(hold.service_hold_identity()) == asdict(prior),
            'PILOT_PRIOR_SERVICE_CHANGED')
    switch.unchanged_files(protected, prior, protected_digest)
    require(asdict(stage_observation(source, request.package)) == asdict(prior),
            'PILOT_STAGE_CHANGED')
    environment_record(request)
    switch.attest_hardening()
    switch.unit_absent(plan.SUPERVISOR_UNIT)
    switch.unit_absent(plan.PILOT_UNIT)
    permit_probe(request)
    service = pwd.getpwnam('school-autopilot')
    parent(switch.CONTROL.parent)
    parent(CLAIM.parent, service.pw_uid)
    require(not switch.CONTROL.exists() and not switch.CONTROL.is_symlink()
            and not CLAIM.exists() and not CLAIM.is_symlink(),
            'PILOT_EXISTING_LEDGER_REQUIRES_RECONCILIATION')
    scope = plan.ROOT / accepted_digest
    new_directory(scope, 0, 0, 0o700)
    retained(scope / 'request.json', raw)
    retained(scope / 'package.json', package_raw)
    retained(scope / 'supervisor.py', supervisor_source(source))
    new_directory(switch.CONTROL, 0, service.pw_gid, 0o750)
    retained(switch.CONTROL / 'permit.json', request.permit, 0o640, service.pw_gid)
    retained(switch.CONTROL / 'accepted-sha256', request.value['permit_sha256'].encode()+b'\n',
             0o640, service.pw_gid)
    retained(switch.CONTROL / 'admission', b'HOLD\n', 0o640, service.pw_gid)
    new_directory(CLAIM, service.pw_uid, service.pw_gid, 0o700,
                  parent_uid=service.pw_uid)
    release.staging.require_current_main(source)
    require(asdict(hold.service_hold_identity()) == asdict(prior),
            'PILOT_PREPARE_SERVICE_CHANGED')
    switch.unchanged_files(protected, prior, protected_digest)
    request.agreement()
    return {'audit':'LIGHT_NATIVE_PILOT_PREPARED_HOLD',
            'source':source,'request_sha256':accepted_digest,
            'protected_sha256':protected_digest,'production_mutations':False}


def ledger(request_digest):
    require(type(request_digest) is str and SHA256.fullmatch(request_digest),
            'PILOT_REQUEST_DIGEST')
    directory(plan.ROOT, 0, 0, 0o700)
    scope = plan.ROOT / request_digest
    directory(scope, 0, 0, 0o700)
    raw = read(scope / 'request.json', 262144)
    package_raw = read(scope / 'package.json')
    request = Request(raw, request_digest, package_raw)
    prior, protected, accepted_protected = baseline_record(request)
    require(accepted_protected == digest(canonical(protected))
            and read(scope / 'supervisor.py', 8192) == supervisor_source(request.value['source']),
            'PILOT_LEDGER_CHANGED')
    return request, prior, protected, accepted_protected, scope


def launch(request_digest):
    """One launch only. A failed/lost systemd ACK is UNKNOWN, never retried."""
    request, prior, protected, accepted_protected, scope = ledger(request_digest)
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic',
            'PILOT_HOST_IDENTITY')
    request.agreement()
    switch.unchanged_files(protected, prior, accepted_protected)
    require(asdict(hold.service_hold_identity()) == asdict(prior),
            'PILOT_LAUNCH_SERVICE_CHANGED')
    switch.unit_absent(plan.SUPERVISOR_UNIT)
    switch.unit_absent(plan.PILOT_UNIT)
    release.staging.require_current_main(request.value['source'])
    environment_record(request)
    permit_probe(request)
    retained(scope / 'supervisor-intent.json', canonical({'request_sha256':request_digest,
        'source':request.value['source'],'unit':plan.SUPERVISOR_UNIT}))
    switch.command(*plan.supervisor_command(request.value['source'], request_digest,
                                             request.value['duration_seconds']))
    return {'audit':'LIGHT_NATIVE_SUPERVISOR_DISPATCHED',
            'request_sha256':request_digest,'terminal_verified':False}


def restored_receipt(request, prior, protected, protected_digest, scope):
    """Allow a repeated restore after an ACK loss, without touching live HOLD."""
    path = scope / 'restore.json'
    if not path.exists():
        return None
    value = strict_json(read(path, 262144), 262144)
    require(type(value) is dict and set(value) == {'version','request_sha256',
            'prior','restored','protected_sha256'} and value['version'] == 1
            and value['request_sha256'] == request.digest
            and value['prior'] == asdict(prior)
            and value['protected_sha256'] == protected_digest,
            'PILOT_RESTORE_RECEIPT_CHANGED')
    actual = hold.service_hold_identity()
    require(asdict(actual) == value['restored'], 'PILOT_RESTORED_HOLD_CHANGED')
    switch.unchanged_files(protected, prior, protected_digest)
    switch.attest_hardening()
    require(hold.read(switch.CONTROL/'admission',0o640,16) == b'HOLD\n',
            'PILOT_RESTORE_ADMISSION_CHANGED')
    switch.no_processes(plan.PILOT_UNIT)
    return value


def already_restored(request, prior, protected, protected_digest):
    """Recover a completed restore whose final receipt write lost its ACK."""
    state = switch.show(plan.PILOT_UNIT,
                        ['LoadState','WorkingDirectory','Description'])
    if state['LoadState'] != 'not-found':
        require(state['WorkingDirectory'] == str(plan.source_path(request.value['source']))
                and state['Description'] == 'Bridge native pilot ' + request.digest,
                'PILOT_RESTORE_FOREIGN_UNIT')
        switch.attest_hardening(plan.PILOT_UNIT, pilot=True)
        switch.attest_pilot_execution(request.value['source'], prior,
                                      request.value['duration_seconds'], request.digest)
    try:
        observed = hold.service_hold_identity()
    except hold.Blocked:
        return None
    require(observed.release == prior.release, 'PILOT_RESTORE_WRONG_RELEASE')
    require(hold.read(switch.CONTROL/'admission', 0o640, 16) == b'HOLD\n',
            'PILOT_RESTORE_ADMISSION_CHANGED')
    switch.no_processes(plan.PILOT_UNIT)
    switch.unchanged_files(protected, prior, protected_digest)
    switch.attest_hardening()
    return observed


def restore(request_digest):
    """Run from ExecStopPost even after agreement/permit expiry."""
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic',
            'PILOT_HOST_IDENTITY')
    require(type(request_digest) is str and SHA256.fullmatch(request_digest),
            'PILOT_REQUEST_DIGEST')
    switch.deny_admission()
    request, prior, protected, protected_digest, scope = ledger(request_digest)
    previous = restored_receipt(request, prior, protected, protected_digest, scope)
    if previous is not None:
        return {'audit':'LIGHT_NATIVE_HOLD_RESTORED',
                'request_sha256':request_digest,'outcome':'RECONCILE'}
    pilot_path = scope / 'pilot-unit.json'
    pilot = strict_json(read(pilot_path, 4096), 4096) if pilot_path.exists() else None
    invocation = pilot['InvocationID'] if pilot is not None else None
    observed = already_restored(request, prior, protected, protected_digest)
    if observed is None:
        observed = switch.restore(prior, protected, plan.source_path(request.value['source']),
                                  protected_digest=protected_digest,
                                  request_digest=request_digest,
                                  pilot_seconds=request.value['duration_seconds'],
                                  pilot_invocation=invocation)
    require(type(observed) is hold.ServiceHoldIdentity, 'PILOT_RESTORE_IDENTITY_TYPE')
    receipt = {'version':1,'request_sha256':request_digest,'prior':asdict(prior),
               'restored':asdict(observed),'protected_sha256':protected_digest}
    retained(scope / 'restore.json', canonical(receipt))
    return {'audit':'LIGHT_NATIVE_HOLD_RESTORED',
            'request_sha256':request_digest,'outcome':'RECONCILE'}


def run(request_digest):
    request, prior, protected, protected_digest, scope = ledger(request_digest)
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic',
            'PILOT_HOST_IDENTITY')
    agreement = request.agreement()
    expected = canonical({'request_sha256':request_digest,
                          'source':request.value['source'],'unit':plan.SUPERVISOR_UNIT})
    require(read(scope / 'supervisor-intent.json', 4096) == expected,
            'PILOT_SUPERVISOR_NOT_REQUESTED')
    retained(scope / 'run-started.json', canonical({'request_sha256':request_digest,
        'source':request.value['source']}))
    result = switch.run_once(prior=prior, protected=protected,
                             protected_digest=protected_digest,
                             source=request.value['source'], request_digest=request_digest,
                             seconds=request.value['duration_seconds'], agreement=agreement,
                             scope=digest(canonical(request.value['scope'])),
                             dispatch_id=request.value['dispatch_id'], directory=scope)
    return {'audit':'LIGHT_NATIVE_PILOT_OUTCOME', 'request_sha256':request_digest,
            'state':result['state'],'terminal_verified':False}


def main():
    require(len(sys.argv) == 3 and sys.argv[1] in ('run','restore'), 'PILOT_SUPERVISOR_MODE')
    result = run(sys.argv[2]) if sys.argv[1] == 'run' else restore(sys.argv[2])
    print(json.dumps(result, sort_keys=True), flush=True)
