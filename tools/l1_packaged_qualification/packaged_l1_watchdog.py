"""One admitted local Docker run; never builds, pulls, installs or starts a daemon.

--execute is mandatory. No Docker command runs on import or during offline tests.
The caller verifies this delivery's hashes and independent review before use.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

_module_spec = importlib.util.spec_from_file_location('packaged_l1_driver',
    Path(__file__).resolve().with_name('packaged_l1_driver.py'))
driver = importlib.util.module_from_spec(_module_spec)
_module_spec.loader.exec_module(driver)

IMAGE = re.compile(r'sha256:[0-9a-f]{64}')
CID = re.compile(r'[0-9a-f]{64}')
LABEL = 'school.qualification-run'


@dataclass
class Captured:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    overflow: bool = False


def capture(argv, timeout=5, out_limit=262144, err_limit=65536, env=None):
    """Drain two bounded pipes; terminate/kill only this exact child on a fault."""
    driver.require(type(argv) is list and all(type(x) is str for x in argv), 'COMMAND_ARGV')
    driver.require(type(timeout) in (int, float) and timeout > 0, 'CAPTURE_BUDGET')
    deadline = time.monotonic() + timeout
    run_deadline = deadline - min(0.25, timeout / 4)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, shell=False, env=env, creationflags=flags)
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    def read(pipe, index, limit):
        try:
            while True:
                chunk = pipe.read1(4096)
                if not chunk:
                    return
                remaining = limit - len(buffers[index])
                buffers[index].extend(chunk[:max(0, remaining)])
                if len(chunk) > remaining:
                    overflow.set()
        finally:
            pipe.close()
    readers = [threading.Thread(target=read, args=(proc.stdout, 0, out_limit), daemon=True),
               threading.Thread(target=read, args=(proc.stderr, 1, err_limit), daemon=True)]
    for thread in readers:
        thread.start()
    expired = False
    while proc.poll() is None:
        expired = time.monotonic() >= run_deadline
        if expired or overflow.is_set():
            proc.kill()
            break
        time.sleep(min(0.01, max(0.001, run_deadline - time.monotonic())))
    try:
        proc.wait(timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        proc.kill()
        raise driver.Rejected('CHILD_STOP_UNCONFIRMED')
    for index, thread in enumerate(readers):
        thread.join(timeout=max(0, deadline-time.monotonic())/(len(readers)-index))
    driver.require(not any(thread.is_alive() for thread in readers), 'CHILD_PIPE_NOT_CLOSED')
    return Captured(proc.returncode, bytes(buffers[0]), bytes(buffers[1]),
                    expired or time.monotonic() > deadline, overflow.is_set())


def decode_one(result):
    driver.require(result.returncode == 0 and not result.timed_out and not result.overflow, 'DOCKER_COMMAND_FAILED')
    rows = driver.strict_json(result.stdout)
    driver.require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, 'INSPECT_SHAPE')
    return rows[0]


def verify_image(image, admission):
    driver.require(image.get('Id') == admission['image_id'] and image.get('Os') == 'linux'
                   and image.get('Architecture') == 'amd64', 'IMAGE_IDENTITY')
    config = image['Config']
    driver.require(config.get('User') == '65532:65532' and config.get('WorkingDir') == '/app'
        and config.get('Cmd') == ['uvicorn', 'bridge_school_api.main:app', '--host', '0.0.0.0', '--port', '8000']
        and not config.get('Volumes') and not config.get('Entrypoint'), 'IMAGE_CONFIG')
    labels = config.get('Labels') or {}
    for key, value in {'org.opencontainers.image.revision': driver.SOURCE,
                       'school.source-tree': driver.TREE,
                       'school.context-sha256': admission['context_sha256'],
                       'school.dependencies-sha256': admission['dependencies_sha256']}.items():
        driver.require(labels.get(key) == value, 'IMAGE_PROVENANCE_LABELS')
    for item in config.get('Env') or []:
        key = item.partition('=')[0]
        driver.require(not (key.startswith('PG') or key.endswith('DATABASE_URL') or
            'PROXY' in key.upper() or key.startswith('VERCEL') or
            any(x in key.upper() for x in ('TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL'))), 'BAKED_ENVIRONMENT')


def verify_daemon(info):
    driver.require(info.get('OSType') == 'linux' and info.get('CgroupVersion') == '2' and
        info.get('Architecture') in ('amd64', 'x86_64'), 'DAEMON_PLATFORM')
    for name in ('MemoryLimit', 'SwapLimit', 'CpuCfsQuota', 'PidsLimit'):
        driver.require(info.get(name) is True, 'DAEMON_CONTROLLER')
    driver.require(type(info.get('NCPU')) is int and info['NCPU'] >= 1 and
        type(info.get('MemTotal')) is int and info['MemTotal'] >= 1073741824, 'DAEMON_CAPACITY')


def owned(container, token, image_id):
    return (type(container) is dict and CID.fullmatch(container.get('Id', '')) is not None
        and (container.get('Config', {}).get('Labels') or {}).get(LABEL) == token
        and container.get('Image') == image_id
        and container.get('Name') == '/l1-packaged-' + token)


def verify_container(container, token, image_id, driver_path, spec_path):
    driver.require(owned(container, token, image_id), 'CONTAINER_OWNERSHIP')
    state = container.get('State', {})
    driver.require(state.get('Running') is False and state.get('Status') == 'created', 'CONTAINER_NOT_STOPPED_CREATED')
    hc, config = container['HostConfig'], container['Config']
    expected = {'Memory': 536870912, 'MemorySwap': 536870912, 'NanoCpus': 1000000000,
                'PidsLimit': 64, 'ReadonlyRootfs': True, 'NetworkMode': 'none',
                'Privileged': False, 'PublishAllPorts': False, 'CgroupnsMode': 'private'}
    for key, value in expected.items():
        driver.require(type(hc.get(key)) is type(value) and hc[key] == value, 'CONTAINER_LIMIT_' + key)
    driver.require(not hc.get('CapAdd') and hc.get('CapDrop') == ['ALL'] and
        hc.get('SecurityOpt') in (['no-new-privileges'], ['no-new-privileges=true']) and
        not hc.get('PortBindings') and hc.get('PidMode', '') == '' and
        hc.get('IpcMode', 'private') in ('', 'private') and hc.get('UsernsMode', '') != 'host', 'CONTAINER_ISOLATION')
    driver.require(config.get('User') == '65532:65532' and config.get('WorkingDir') == '/app'
                   and not config.get('Tty') and not config.get('OpenStdin'), 'CONTAINER_CONFIG')
    command = create_arguments(image_id, token, driver_path, spec_path)
    driver.require(config.get('Entrypoint') == ['python'] and
        config.get('Cmd') == command[command.index(image_id) + 1:], 'CONTAINER_COMMAND')
    mounts = container.get('Mounts', [])
    expected_mounts = {'/qualification/driver.py': str(driver_path), '/qualification/spec.json': str(spec_path)}
    driver.require(len(mounts) == 2, 'CONTAINER_MOUNTS')
    observed = {}
    for mount in mounts:
        driver.require(mount.get('Type') == 'bind' and mount.get('RW') is False and
            mount.get('Destination') not in observed, 'CONTAINER_MOUNT_TYPE')
        observed[mount['Destination']] = mount.get('Source')
    driver.require(observed == expected_mounts, 'CONTAINER_MOUNT_PATHS')
    env = dict(item.split('=', 1) for item in config.get('Env', []) if '=' in item)
    driver.verify_environment(env)


def create_arguments(image_id, token, driver_path, spec_path):
    driver.require(IMAGE.fullmatch(image_id) is not None and re.fullmatch('[0-9a-f]{32}', token), 'CREATE_IDENTITY')
    for path in (driver_path, spec_path):
        driver.require(path.is_absolute() and ',' not in str(path) and '\n' not in str(path), 'INPUT_PATH')
    return ['create', '--pull=never', '--name', 'l1-packaged-' + token,
        '--label', LABEL + '=' + token, '--network=none', '--read-only', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--user=65532:65532', '--cpus=1',
        '--memory=512m', '--memory-swap=512m', '--pids-limit=64', '--cgroupns=private',
        '--workdir=/app', '--mount', 'type=bind,source=' + str(driver_path) + ',target=/qualification/driver.py,readonly',
        '--mount', 'type=bind,source=' + str(spec_path) + ',target=/qualification/spec.json,readonly',
        '--env', 'BRIDGE_API_TOKEN=' + driver.TOKEN, '--entrypoint=python', image_id,
        '-I', '-B', '/qualification/driver.py', '--spec', '/qualification/spec.json',
        '--image-id', image_id, '--source-commit', driver.SOURCE, '--source-tree', driver.TREE]


def verify_run_result(result, admission, spec):
    driver.require(result.get('schema') == 'packaged-l1-six-v1' and
        result.get('status') == 'PASS_PACKAGED_ASGI_ONLY', 'DRIVER_RESULT')
    for key, value in {'image_id': admission['image_id'], 'source_commit': driver.SOURCE,
                       'source_tree': driver.TREE, 'driver_sha256': admission['driver_sha256'],
                       'spec_sha256': driver.SPEC_SHA, 'packaged_root': '/app',
                       'ASGI_object': 'bridge_school_api.main:app', 'machine': 'x86_64'}.items():
        driver.require(result.get(key) == value, 'RESULT_IDENTITY')
    driver.require(result.get('bytecode_cache_mode') == 'absent-readonly-prefix', 'RESULT_BYTECODE_MODE')
    for key in ('files_before', 'files_after'):
        driver.require(result.get(key) == spec['packaged_source_sha256'], 'RESULT_FILE_PINS')
    driver.require(result.get('module_locations') == {key: '/app/' + key for key in
        spec['packaged_source_sha256']}, 'RESULT_MODULE_LOCATIONS')
    driver.require(result.get('dependencies') == {'fastapi': '0.141.1', 'uvicorn': '0.52.2',
        'psycopg': '3.3.4', 'starlette': '1.9.0'}, 'RESULT_DEPENDENCIES')
    counts = result.get('public_evaluate_counts')
    driver.require(type(counts) is dict and set(counts) == {'v1','v2','v3','v4'} and
        all(type(value) is int and value > 0 for value in counts.values()), 'RESULT_EVALUATE_PATHS')
    driver.verify_cgroups(result['cgroups'])
    for key, value in {'dependency_overrides': 0, 'DB_connections': 0, 'source_mount': False,
                       'listener_started': False, 'deployed_transport_qualified': False,
                       'learning_session_qualified': False, 'release_hold': True}.items():
        driver.require(type(result.get(key)) is type(value) and result[key] == value, 'RESULT_BOUNDARY')
    driver.require(result.get('guards') == {'controls': dict(network=1, database=1, write=1, process=1),
                                          'application': {}}, 'RESULT_GUARDS')
    rows = result.get('observations')
    driver.require(type(rows) is list and len(rows) == 6, 'RESULT_REQUEST_COUNT')
    for case, row in zip(spec['cases'], rows):
        driver.require(row.get('id') == case['id'], 'RESULT_REQUEST_ORDER')
        driver.verify_response(case, row, spec)


class DockerRun:
    def __init__(self, call, admission, spec, driver_path, spec_path, token):
        self.call, self.admission, self.spec = call, admission, spec
        self.driver_path, self.spec_path, self.token = driver_path, spec_path, token
        self.cid = None
        self.cleanup = 'NOT_CREATED'
        self.create_attempted = False
        self.active_started = None
        self.active_deadline = None
        self.preflight_deadline = None
        self.cleanup_deadline = None

    def invoke(self, command, timeout):
        deadline = self.active_deadline or self.cleanup_deadline or self.preflight_deadline
        remaining = deadline - time.monotonic()
        driver.require(remaining > 0, 'TOTAL_DEADLINE_EXHAUSTED')
        return self.call(command, timeout=min(timeout, remaining))

    def inspect_owned(self, identifier):
        value = decode_one(self.invoke(['inspect', identifier], timeout=1))
        driver.require(owned(value, self.token, self.admission['image_id']), 'CONTAINER_OWNERSHIP')
        return value

    def run(self):
        outcome = {'status': 'FAIL_CLOSED', 'code': 'NOT_STARTED'}
        name = 'l1-packaged-' + self.token
        self.preflight_deadline = time.monotonic() + 20
        try:
            info = self.invoke(['info', '--format', '{{json .}}'], timeout=5)
            driver.require(info.returncode == 0 and not info.timed_out and not info.overflow, 'DAEMON_UNAVAILABLE')
            verify_daemon(driver.strict_json(info.stdout))
            image = decode_one(self.invoke(['image', 'inspect', self.admission['image_id']], timeout=5))
            verify_image(image, self.admission)
            self.create_attempted = True
            created = self.invoke(create_arguments(self.admission['image_id'], self.token,
                                self.driver_path, self.spec_path), timeout=5)
            driver.require(created.returncode == 0 and not created.timed_out and not created.overflow,
                           'CREATE_OUTCOME_UNCERTAIN')
            cid = created.stdout.decode('ascii').strip()
            driver.require(CID.fullmatch(cid) is not None, 'CREATE_ID_INVALID')
            self.cid = cid
            container = self.inspect_owned(cid)
            verify_container(container, self.token, self.admission['image_id'], self.driver_path, self.spec_path)
            self.active_started = time.monotonic()
            self.active_deadline = self.active_started + 55
            active = self.invoke(['start', '--attach', cid], timeout=50)
            if active.timed_out or active.overflow:
                raise driver.Rejected('ACTIVE_TIMEOUT_OR_OUTPUT_CAP')
            driver.require(active.returncode == 0, 'DRIVER_EXIT')
            value = driver.strict_json(active.stdout)
            verify_run_result(value, self.admission, self.spec)
            stopped = self.inspect_owned(cid)
            driver.require(stopped.get('State', {}).get('Running') is False and
                stopped['State'].get('Status') == 'exited' and
                stopped['State'].get('OOMKilled') is False and stopped['State'].get('ExitCode') == 0,
                'CONTAINER_EXIT')
            outcome = {'status': 'PASS_PACKAGED_ASGI_ONLY', 'driver': value,
                       'image_id': self.admission['image_id'], 'container_id': cid}
        except Exception as exc:
            outcome = {'status': 'FAIL_CLOSED', 'code': str(exc) if isinstance(exc, driver.Rejected) else 'UNEXPECTED_FAILURE'}
        finally:
            # One readonly lookup after an uncertain create. Never repeat create.
            # Cleanup by exact ID only after fresh matching token/name/image proof.
            try:
                if not self.create_attempted:
                    outcome['cleanup'] = 'NOT_CREATED'
                    return outcome
                self.cleanup_deadline = time.monotonic() + 5
                current = self.inspect_owned(self.cid or name)
                self.cid = current['Id']
                if current.get('State', {}).get('Running') is True:
                    killed = self.invoke(['kill', self.cid], timeout=1)
                    driver.require(killed.returncode == 0 and not killed.timed_out and not killed.overflow,
                                   'STOP_UNCONFIRMED')
                current = self.inspect_owned(self.cid)
                driver.require(current.get('State', {}).get('Running') is False, 'STOP_UNCONFIRMED')
                removed = self.invoke(['rm', self.cid], timeout=1)
                driver.require(removed.returncode == 0 and not removed.timed_out and not removed.overflow,
                               'CLEANUP_UNCONFIRMED')
                self.cleanup = 'OWNED_STOPPED_CONTAINER_REMOVED'
            except Exception:
                self.cleanup = 'NO_CONFIRMED_OWNED_CLEANUP'
                if self.cid is not None:
                    outcome = {'status': 'FAIL_CLOSED', 'code': 'CLEANUP_OR_STOP_UNCONFIRMED',
                               'container_id': self.cid, 'previous_status': outcome['status']}
            outcome['cleanup'] = self.cleanup
            outcome['owned_name'] = name
            outcome['create_attempted'] = self.create_attempted
            outcome['uncertain_outcome'] = self.create_attempted and self.cleanup != 'OWNED_STOPPED_CONTAINER_REMOVED'
            if self.active_started is not None:
                elapsed = time.monotonic() - self.active_started
                outcome['active_and_cleanup_elapsed_seconds'] = round(elapsed, 6)
                if elapsed > 55:
                    outcome['status'] = 'FAIL_CLOSED'
                    outcome['code'] = 'ACTIVE_CLEANUP_BOUND_EXCEEDED'
        return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--admission', type=Path, required=True)
    parser.add_argument('--docker', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    driver.require(args.execute and sys.platform == 'linux', 'EXPLICIT_LINUX_EXECUTION_REQUIRED')
    here = Path(__file__).resolve().parent
    spec_path, driver_path = here / 'PACKAGED_L1_CHECK_SPEC.json', here / 'packaged_l1_driver.py'
    spec = driver.load_spec(spec_path)
    admission = driver.strict_json(args.admission.read_bytes())
    for field in ('context_sha256', 'dependencies_sha256', 'driver_sha256', 'docker_sha256'):
        driver.require(re.fullmatch('[0-9a-f]{64}', admission.get(field, '')) is not None, 'ADMISSION_HASH')
    driver.require(IMAGE.fullmatch(admission.get('image_id', '')) is not None and
        admission.get('source_commit') == driver.SOURCE and admission.get('source_tree') == driver.TREE and
        admission['driver_sha256'] == hashlib.sha256(driver_path.read_bytes()).hexdigest(), 'ADMISSION_IDENTITY')
    docker = args.docker.resolve(strict=True)
    driver.require(docker.is_file() and docker.is_absolute() and os.access(docker, os.X_OK), 'DOCKER_EXECUTABLE')
    driver.require(hashlib.sha256(docker.read_bytes()).hexdigest() == admission['docker_sha256'], 'DOCKER_BINARY_PIN')
    token = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='l1-docker-config-') as config:
        env = {'PATH': '/usr/bin:/bin', 'DOCKER_CONFIG': config}
        prefix = [str(docker), '--host', 'unix:///var/run/docker.sock']
        def call(command, timeout):
            return capture(prefix + command, timeout=timeout, env=env)
        # No remote context, user Docker credentials or implicit daemon start.
        result = DockerRun(call, admission, spec, driver_path, spec_path, token).run()
    args.output.write_text(json.dumps(result, ensure_ascii=True, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if result['status'] == 'PASS_PACKAGED_ASGI_ONLY' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except driver.Rejected as exc:
        print(json.dumps({'status': 'FAIL_CLOSED', 'code': str(exc)}), file=sys.stderr)
        raise SystemExit(2)
