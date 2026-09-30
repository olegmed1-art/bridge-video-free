"""One incident-only parent repair before a single native pilot prepare.

The caller loads byte-verified source 8bbc helpers and authenticates the
execution run. This module never calls prepare, launch, or any DB writer.
"""
import base64
from dataclasses import asdict
import os
from pathlib import Path
import pwd
import stat

SOURCE = '8bbc1d61010ef70c3fca02b5151ac86fce02a144'
PACKAGE = '46acb2672ba58dc369c3ccf70f45bb4beff37cb4efad2bfe3b34ae4a170c0c94'
PREPARE = '1d9528122add35370aae088ec15fb4e6d8c8e0efe7b781faa768f14675e1d221'
PERMIT = '40e6ba4d282ff03e3fe06f23b9138c6aa8eeac81c1d3b15b5a255eb3c6890e18'
BASELINE = 'dd62c96c552b81e23d661b366c6e2d1a23293295943f9b5b44021e6c99befe1a'
SCOPE = '2684a5be633619d55ac2e6c3c56a82209633a5c615a3b4a09c68e9c2ecb11051'
DISPATCH = '9289ad56-f0aa-4683-be0c-101ec820d412'
PARENT = Path('/etc/bridge-school')


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def _request(control, payload):
    value = control.strict_json(payload, 262144)
    require(type(value) is dict and set(value) == {'accepted_permit_sha256', 'request'}
            and value['accepted_permit_sha256'] == PERMIT
            and type(value['request']) is dict
            and 'permit_b64' not in value['request'], 'PARENT_INPUT')
    body = value['request']
    require(body.get('source') == SOURCE and body.get('package_sha256') == PACKAGE
            and body.get('permit_sha256') == PERMIT and body.get('baseline_sha256') == BASELINE
            and body.get('dispatch_id') == DISPATCH and body.get('duration_seconds') == 720
            and control.digest(control.canonical(body['scope'])) == SCOPE, 'PARENT_SCOPE')
    permit = control.read(control.plan.ROOT / 'intake' / 'permit.json', 262144)
    require(control.digest(permit) == PERMIT, 'PARENT_PERMIT')
    raw = control.canonical(dict(body, permit_b64=base64.b64encode(permit).decode()))
    return raw, control.digest(raw)


def _absent(path, code):
    require(not path.exists() and not path.is_symlink(), code)


def _trusted_etc():
    row = PARENT.parent.lstat()
    require(stat.S_ISDIR(row.st_mode) and row.st_uid == 0
            and not row.st_mode & 0o022, 'PARENT_ETC_UNTRUSTED')
    _absent(PARENT, 'PARENT_ALREADY_EXISTS')


def _create_parent(live_guard):
    """Create only the missing parent, anchored to the trusted /etc inode."""
    fd = os.open(PARENT.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    child = None
    try:
        row = os.fstat(fd)
        require(stat.S_ISDIR(row.st_mode) and row.st_uid == 0
                and not row.st_mode & 0o022, 'PARENT_ETC_UNTRUSTED')
        _absent(PARENT, 'PARENT_ALREADY_EXISTS')
        live_guard()
        os.mkdir(PARENT.name, 0o755, dir_fd=fd)
        child = os.open(PARENT.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.fchown(child, 0, 0)
        os.fchmod(child, 0o755)
        os.fsync(child)
        os.fsync(fd)
        after = os.fstat(child)
        linked = os.stat(PARENT.name, dir_fd=fd, follow_symlinks=False)
        require((after.st_dev, after.st_ino) == (linked.st_dev, linked.st_ino)
                and stat.S_ISDIR(after.st_mode) and after.st_uid == after.st_gid == 0
                and stat.S_IMODE(after.st_mode) == 0o755
                and not os.listdir(child), 'PARENT_READBACK')
        live_guard()
        return after.st_ino
    finally:
        if child is not None:
            os.close(child)
        os.close(fd)


def repair(package_raw, prepare_payload_raw, accepted_digest, live_guard):
    """Return safe evidence after the original prepare gates and one mkdir.

    A refusal after mkdir leaves the new directory for inspection; no retry or
    delete operation exists here.
    """
    from ops import light_native_pilot_release as release
    from ops import light_native_service_controller as control
    from ops import light_native_service_switch as switch
    from ops import oracle_light_active_hold_attest as hold

    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic'
            and callable(live_guard), 'PARENT_HOST')
    require(control.digest(package_raw) == PACKAGE
            and control.digest(prepare_payload_raw) == PREPARE
            and accepted_digest == PREPARE, 'PARENT_ACCEPTANCE')
    package = control.verified_package(package_raw, SOURCE, PACKAGE)
    raw, request_digest = _request(control, prepare_payload_raw)
    request = control.Request(raw, request_digest, package_raw)
    require(request.value['dispatch_id'] == DISPATCH, 'PARENT_REQUEST')
    agreement = request.agreement()

    def active_guard():
        live_guard()
        agreement.assert_held(SCOPE)

    active_guard()
    release.staging.require_current_main(SOURCE)
    prior, protected, protected_digest = control.baseline_record(request)
    require(asdict(hold.service_hold_identity()) == asdict(prior), 'PARENT_HOLD_CHANGED')
    switch.unchanged_files(protected, prior, protected_digest)
    require(asdict(control.stage_observation(SOURCE, package)) == asdict(prior),
            'PARENT_STAGE_CHANGED')
    control.environment_record(request)
    switch.attest_hardening()
    switch.unit_absent(control.plan.SUPERVISOR_UNIT)
    switch.unit_absent(control.plan.PILOT_UNIT)
    control.permit_probe(request)
    service = pwd.getpwnam('school-autopilot')
    control.parent(control.CLAIM.parent, service.pw_uid)
    control.directory(control.plan.ROOT, 0, 0, 0o700)
    _absent(control.plan.ROOT / request_digest, 'PARENT_REQUEST_EXISTS')
    _absent(switch.CONTROL, 'PARENT_CONTROL_EXISTS')
    _absent(control.CLAIM, 'PARENT_CLAIM_EXISTS')
    _trusted_etc()
    inode = _create_parent(active_guard)
    return {'audit': 'LIGHT_PILOT_PARENT_REPAIRED', 'source': SOURCE,
            'request_sha256': request_digest, 'parent_inode': inode,
            'service_changed': False, 'pilot_submitted': False}
