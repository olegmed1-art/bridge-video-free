"""Owner credential probe on Oracle. Read-only; no grants or manifest approval."""
from contextlib import contextmanager
import base64
import fcntl
import importlib
import json
import os
from pathlib import Path
import sys
import time

from ops import native_maintenance_driver as driver
from ops.oracle_autopilot_source_preflight import connection_parameters
from ops.oracle_light_active_hold_attest import HoldIdentity, attest, require


@contextmanager
def verified_runtime(payload):
    identity = driver.python_identity()
    files = driver.decode(payload)
    parent = driver.PARENT / driver.NAME
    driver.trusted_parent(driver.PARENT)
    parent_info = driver.private_directory(parent)
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = None
    try:
        require((os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) ==
                (parent_info.st_dev, parent_info.st_ino), 'OWNER_RUNTIME_PARENT_CHANGED')
        # Existing install only: this probe neither creates nor repairs a runtime.
        lock = driver.open_file(descriptor, 'lock', os.O_RDWR)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime_id = driver.sha(driver.encoded(identity))
        root = parent / runtime_id
        driver.verify_tree(root, files, identity)
        yield root, runtime_id
        driver.verify_tree(root, files, identity)
        require(driver.python_identity() == identity, 'OWNER_PYTHON_CHANGED')
        after = driver.private_directory(parent)
        require((after.st_dev, after.st_ino) == (parent_info.st_dev, parent_info.st_ino),
                'OWNER_RUNTIME_PARENT_CHANGED')
    finally:
        if lock is not None:
            os.close(lock)
        os.close(descriptor)


@contextmanager
def loaded_runtime(wheels):
    """Load only the byte-verified private driver, retaining its lifetime lock."""
    with verified_runtime(wheels) as (root, runtime_id):
        site = root / 'site'
        require(all(name not in sys.modules for name in ('psycopg', 'psycopg_binary', 'typing_extensions')),
                'OWNER_DRIVER_ALREADY_IMPORTED')
        sys.path.insert(0, str(site))
        os.environ['PSYCOPG_IMPL'] = 'binary'
        modules = [importlib.import_module(name) for name in ('psycopg', 'psycopg_binary', 'typing_extensions')]
        require(all(Path(m.__file__).resolve().is_relative_to(site) for m in modules), 'OWNER_DRIVER_ORIGIN')
        psycopg = modules[0]
        require(psycopg.__version__ == '3.3.4' and psycopg.pq.__impl__ == 'binary', 'OWNER_DRIVER_VERSION')
        require(all(not name.startswith('psycopg') or
                    (getattr(module, '__file__', None) and Path(module.__file__).resolve().is_relative_to(site))
                    for name, module in tuple(sys.modules.items())), 'OWNER_DRIVER_SUBMODULE_ORIGIN')
        yield psycopg, runtime_id


def observe(wheels, credential, *, candidate=False):
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'OWNER_HOST')
    require(type(credential) is str and 0 < len(credential) <= 8192, 'OWNER_CREDENTIAL_SIZE')
    connection_parameters(credential, 'neondb_owner')  # Reject unrelated credentials before host/DB work.
    start = time.monotonic()
    before = attest()
    with loaded_runtime(wheels) as (psycopg, runtime_id):
        from ops import native_maintenance_owner_attest as owner
        if candidate:
            report, manifest = owner.candidate(psycopg.connect, credential)
        else:
            report = owner.observe(psycopg.connect, credential)
    require(attest() == before, 'OWNER_HOLD_CHANGED')
    result = dict(audit='NATIVE_OWNER_HOST_READ_ONLY_PASS', runtime_id=runtime_id,
                snapshot_digest=report['snapshot_digest'], snapshot_approved=False,
                hold_unchanged=True, native_enabled=False, receipts=0, nonterminal_tasks=0,
                light_native_execute=0, production_mutations=False,
                elapsed_ms=int((time.monotonic() - start) * 1000))
    return (result, manifest) if candidate else result


def candidate_main(wheels, credential):
    """Private SSH response only. The reviewed runner must never print it."""
    try:
        report, manifest = observe(wheels, credential, candidate=True)
        print(json.dumps(dict(report=report, manifest=base64.b64encode(manifest).decode('ascii'))))
    except BaseException:
        # No traceback or private data even if serialization or transmission fails.
        raise SystemExit(2) from None


def main(wheels, credential):
    try:
        report = observe(wheels, credential)
    except BaseException:
        # No exception text, traceback, credential, private HOLD identity or snapshot.
        print(json.dumps(dict(audit='NATIVE_OWNER_HOST_READ_ONLY_REFUSED', production_mutations=False)))
        raise SystemExit(2) from None
    print(json.dumps(report, sort_keys=True))


# Separate private metadata input; never add files to the verified driver tree.
INITIALIZED_BASELINE_PARENT = Path('/var/lib/bridge-native-owner-observation')


def read_initialized_baseline(pin):
    require(type(pin) is str and len(pin) == 64
            and all(c in '0123456789abcdef' for c in pin), 'OWNER_BASELINE_PIN_REQUIRED')
    parent = INITIALIZED_BASELINE_PARENT
    driver.trusted_parent(parent.parent)
    identity = driver.private_directory(parent)
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        require((os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) ==
                (identity.st_dev, identity.st_ino), 'OWNER_BASELINE_PARENT_CHANGED')
        fd = driver.open_file(descriptor, 'initialized-baseline.json', os.O_RDONLY)
        before = os.fstat(fd)
        require(0 < before.st_size <= 512, 'OWNER_BASELINE_SIZE')
        raw = os.read(fd, 513)
        after = os.fstat(fd)
        require(len(raw) == before.st_size and driver.sha(raw) == pin
                and (before.st_dev, before.st_ino, before.st_size,
                     before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_dev, after.st_ino, after.st_size,
                     after.st_mtime_ns, after.st_ctime_ns), 'OWNER_BASELINE_CHANGED')
        value = json.loads(raw, object_pairs_hook=driver.unique)
        require(driver.encoded(value) == raw, 'OWNER_BASELINE_NONCANONICAL')
        final = driver.private_directory(parent)
        require((final.st_dev, final.st_ino) == (identity.st_dev, identity.st_ino),
                'OWNER_BASELINE_PARENT_CHANGED')
        return value
    finally:
        if fd is not None:
            os.close(fd)
        os.close(descriptor)


def observe_initialized(wheels, credential, baseline_sha):
    """Existing-runtime read-only qualification, never initial-install authority."""
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'OWNER_HOST')
    require(type(credential) is str and 0 < len(credential) <= 8192, 'OWNER_CREDENTIAL_SIZE')
    connection_parameters(credential, 'neondb_owner')
    start = time.monotonic()
    baseline = read_initialized_baseline(baseline_sha)
    before = attest()
    require(type(before) is HoldIdentity, 'OWNER_FULL_HOLD_REQUIRED')
    with loaded_runtime(wheels) as (psycopg, runtime_id):
        from ops import native_maintenance_owner_attest as owner
        report = owner.observe_initialized(psycopg.connect, credential, baseline)
    after = attest()
    require(type(after) is HoldIdentity and after == before, 'OWNER_HOLD_CHANGED')
    require(read_initialized_baseline(baseline_sha) == baseline, 'OWNER_BASELINE_CHANGED')
    require(report == owner.initialized_report(), 'OWNER_INITIALIZED_REPORT')
    return dict(report, audit='NATIVE_OWNER_HOST_INITIALIZED_READ_ONLY_PASS',
                runtime_id=runtime_id, hold_unchanged=True,
                elapsed_ms=int((time.monotonic() - start) * 1000))


def initialized_main(wheels, credential, baseline_sha):
    try:
        report = observe_initialized(wheels, credential, baseline_sha)
    except BaseException:
        print(json.dumps(dict(audit='NATIVE_OWNER_HOST_INITIALIZED_READ_ONLY_REFUSED',
                              production_mutations=False)))
        raise SystemExit(2) from None
    print(json.dumps(report, sort_keys=True))
