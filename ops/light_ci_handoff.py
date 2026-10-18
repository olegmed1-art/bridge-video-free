"""Versioned registration-file transport for the existing finite CI runner.

Preparation is offline. Launch calls the existing root supervisor exactly once;
this module never registers a database task, retries it, or changes permanent HOLD.
Inspection reads retained root records and is never a launch/admission decision.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops import light_ci_once as ci
from ops import light_ci_once_admin as admin

KIND = 'GITHUB_CI_READ_ONLY_HANDOFF'
VERSION = 1
MANIFEST = 'HANDOFF_V1.json'
REGISTRATION = 'registration.json'
LIMIT = 16384
FIELDS = {'kind', 'version', 'package_sha256', 'plan_sha256',
          'registration_sha256', 'task_id'}


def read_local(path, limit=1048576):
    """Bounded ordinary-file read for offline preparation, without root rights."""
    path = Path(path)
    before = path.lstat()
    ci.need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'HANDOFF_INPUT_TYPE')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                 getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        ci.need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit
                and (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino),
                'HANDOFF_INPUT_METADATA')
        raw = stream.read(limit + 1)
    ci.need(len(raw) <= limit, 'HANDOFF_INPUT_SIZE')
    return raw


def read_private_root(path, limit=LIMIT):
    """The owner stages both receipt files, never a service-writable input."""
    raw = ci.read_root(path, limit=limit)
    info = Path(path).lstat()
    ci.need(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1, 'HANDOFF_PRIVATE_METADATA')
    return raw


def registration_value(raw, plan):
    value = ci.strict_json(raw)
    ci.registration(value, plan)
    ci.need(all(type(value[k]) is int for k in
                ('version', 'attempts', 'lease_epoch', 'max_attempts', 'cost_cap_microusd')),
            'HANDOFF_REGISTRATION_TYPES')
    return value


def prepare_handoff(package, accepted_package, accepted_plan, registration_path, output):
    """Write two create-only files; no database, service, or network calls."""
    package, output = Path(package), Path(output)
    control = admin.accepted_control(package, accepted_package, accepted_plan, reader=read_local)
    registration = registration_value(read_local(registration_path, LIMIT), control['plan'])
    registration_raw = ci.canonical(registration) + b'\n'
    manifest = {'kind': KIND, 'version': VERSION, 'package_sha256': accepted_package,
                'plan_sha256': accepted_plan, 'registration_sha256': ci.sha(registration_raw),
                'task_id': registration['task_id']}
    # A partial directory is evidence, not a resumable preparation transaction.
    output.mkdir(mode=0o700, exist_ok=False)
    admin.write_new(output/REGISTRATION, registration, 0o600)
    admin.write_new(output/MANIFEST, manifest, 0o600)
    return {'status': 'HANDOFF_PREPARED_NOT_EXECUTED', 'version': VERSION,
            'handoff_sha256': ci.sha(ci.canonical(manifest) + b'\n'),
            'package_sha256': accepted_package, 'plan_sha256': accepted_plan,
            'task_id': registration['task_id'], 'database_calls': 0, 'units_started': 0}


def load_handoff(package, directory, trusted_sha, *, live, reader=None):
    """Authenticate the external manifest digest before trusting any binding."""
    reader = reader or read_private_root
    package, directory = Path(package), Path(directory)
    ci.need(isinstance(trusted_sha, str) and re.fullmatch('[0-9a-f]{64}', trusted_sha),
            'HANDOFF_EXTERNAL_DIGEST')
    raw = reader(directory/MANIFEST)
    ci.need(ci.sha(raw) == trusted_sha, 'HANDOFF_DIGEST')
    manifest = ci.strict_json(raw)
    ci.need(type(manifest) is dict and set(manifest) == FIELDS
            and manifest['kind'] == KIND and type(manifest['version']) is int
            and manifest['version'] == VERSION, 'HANDOFF_SCHEMA')
    ci.need(all(isinstance(manifest[k], str) and re.fullmatch('[0-9a-f]{64}', manifest[k])
                for k in ('package_sha256', 'plan_sha256', 'registration_sha256')),
            'HANDOFF_BINDING_DIGESTS')
    ci.need(raw == ci.canonical(manifest) + b'\n', 'HANDOFF_CANONICAL')
    # The ordinary production calls use the unchanged root-owned package reader.
    control = admin.accepted_control(package, manifest['package_sha256'],
                                     manifest['plan_sha256'], live=live)
    registration_raw = reader(directory/REGISTRATION)
    ci.need(ci.sha(registration_raw) == manifest['registration_sha256'],
            'HANDOFF_REGISTRATION_DIGEST')
    registration = registration_value(registration_raw, control['plan'])
    ci.need(registration_raw == ci.canonical(registration) + b'\n'
            and registration['task_id'] == manifest['task_id'], 'HANDOFF_REGISTRATION_BINDING')
    return manifest, control, registration


def require_owner():
    ci.need(os.name == 'posix' and os.geteuid() == 0
            and os.uname().nodename == 'autopilot-lite-vnic', 'EXISTING_ROOT_ADMIN_REQUIRED')


def launch_handoff(package, directory, trusted_sha):
    require_owner()
    manifest, _, _ = load_handoff(package, directory, trusted_sha, live=True)
    # No new writer, DB registration, shell, retry or cleanup path. The original
    # supervisor rechecks the live plan/preflight/main/HOLD under its route lock
    # and persists its no-replay intent before the only systemd invocation.
    return admin.supervise('launch', Path(package), manifest['package_sha256'],
                           manifest['plan_sha256'], Path(directory)/REGISTRATION)


def inspect_handoff(package, directory, trusted_sha):
    """Safe projection of retained records; no current DB/HOLD/exit attestation."""
    require_owner()
    manifest, control, registration = load_handoff(package, directory, trusted_sha, live=False)
    private = ci.ROOT/'control'/ci.sha(control['plan']['task_key'].encode())
    observed, records = {}, {}
    for name in ('launch-intent.json', 'launch-exit.json', 'runtime-result.json'):
        try:
            raw = read_private_root(private/name)
            records[name] = ci.strict_json(raw)
            observed[name] = 'RETAINED'
        except FileNotFoundError:
            observed[name] = 'ABSENT'
        except Exception:
            observed[name] = 'UNKNOWN'
    result = {'status': 'INSPECT_ONLY_NO_REPLAY', 'version': VERSION,
              'task_id': registration['task_id'], 'plan_sha256': manifest['plan_sha256'],
              'records': observed, 'read_only': True, 'launch_permitted_by_this_report': False,
              'independent_owner_acceptance_required': True}
    terminal = records.get('runtime-result.json')
    exit_proof = records.get('launch-exit.json')
    intent = records.get('launch-intent.json')
    good_intent = (type(intent) is dict and set(intent) == {'plan_sha256', 'at'}
                   and intent['plan_sha256'] == manifest['plan_sha256'])
    if good_intent:
        try:
            good_intent = (ci.timestamp(control['plan']['not_before']) <= ci.timestamp(intent['at'])
                           <= ci.timestamp(control['plan']['expires_at']) - dt.timedelta(seconds=180))
        except (ValueError, TypeError, ci.Blocked):
            good_intent = False
    if (all(v == 'RETAINED' for v in observed.values()) and type(terminal) is dict
            and set(terminal) == {'plan_sha256', 'task_id', 'status', 'content_sha256',
                                 'independent_owner_acceptance_required', 'model_cost_microusd'}
            and terminal['plan_sha256'] == manifest['plan_sha256']
            and terminal['task_id'] == registration['task_id']
            and terminal['status'] == 'DONE_RUNTIME_READBACK'
            and terminal['independent_owner_acceptance_required'] is True
            and type(terminal['model_cost_microusd']) is int and terminal['model_cost_microusd'] == 0
            and isinstance(terminal['content_sha256'], str)
            and re.fullmatch('[0-9a-f]{64}', terminal['content_sha256'])
            and type(exit_proof) is dict and set(exit_proof) == {'unit', 'invocation_id', 'cgroup_empty', 'main_pid'}
            and exit_proof['unit'] == control['plan']['unit']
            and exit_proof['cgroup_empty'] is True and type(exit_proof['main_pid']) is int
            and exit_proof['main_pid'] == 0
            and isinstance(exit_proof['invocation_id'], str)
            and re.fullmatch('[0-9a-f]{32}', exit_proof['invocation_id'])
            and good_intent):
        result.update(status='RUNTIME_RESULT_RETAINED_OWNER_ACCEPTANCE_REQUIRED',
                      content_sha256=terminal['content_sha256'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'launch', 'inspect'])
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--handoff', type=Path, required=True)
    parser.add_argument('--accept-handoff-sha256')
    parser.add_argument('--accept-package-sha256')
    parser.add_argument('--accept-plan-sha256')
    parser.add_argument('--registration-json', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.operation == 'prepare':
            ci.need(args.accept_package_sha256 and args.accept_plan_sha256 and args.registration_json,
                    'HANDOFF_PREPARATION_ARGUMENTS')
            result = prepare_handoff(args.package, args.accept_package_sha256,
                                     args.accept_plan_sha256, args.registration_json, args.handoff)
        else:
            ci.need(args.accept_handoff_sha256, 'HANDOFF_EXTERNAL_DIGEST')
            action = launch_handoff if args.operation == 'launch' else inspect_handoff
            result = action(args.package, args.handoff, args.accept_handoff_sha256)
        print(json.dumps(result, sort_keys=True))
        return 3 if result['status'] == 'INSPECT_ONLY_NO_REPLAY' else 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, ci.Blocked) and re.fullmatch('[A-Z0-9_]{1,96}', str(exc)) else ('HANDOFF_EXISTS_INSPECT_ONLY'
               if isinstance(exc, FileExistsError) else 'UNCLASSIFIED_INSPECT_ONLY')
        print(json.dumps({'status': 'NOT_ACCEPTED_INSPECT_ONLY', 'code': code}, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
