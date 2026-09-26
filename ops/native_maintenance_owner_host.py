"""Owner credential probe on Oracle. Read-only; no grants or manifest approval."""
from contextlib import contextmanager
import fcntl
import importlib
import json
import os
from pathlib import Path
import sys
import time

from ops import native_maintenance_driver as driver
from ops.oracle_autopilot_source_preflight import connection_parameters
from ops.oracle_light_active_hold_attest import attest, require


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


def observe(wheels, credential):
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'OWNER_HOST')
    require(type(credential) is str and 0 < len(credential) <= 8192, 'OWNER_CREDENTIAL_SIZE')
    connection_parameters(credential, 'neondb_owner')  # Reject unrelated credentials before host/DB work.
    start = time.monotonic()
    before = attest()
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
        from ops import native_maintenance_owner_attest as owner
        require(all(not name.startswith('psycopg') or
                    (getattr(module, '__file__', None) and Path(module.__file__).resolve().is_relative_to(site))
                    for name, module in tuple(sys.modules.items())), 'OWNER_DRIVER_SUBMODULE_ORIGIN')
        report = owner.observe(psycopg.connect, credential)
    require(attest() == before, 'OWNER_HOLD_CHANGED')
    return dict(audit='NATIVE_OWNER_HOST_READ_ONLY_PASS', runtime_id=runtime_id,
                snapshot_digest=report['snapshot_digest'], snapshot_approved=False,
                hold_unchanged=True, native_enabled=False, receipts=0, nonterminal_tasks=0,
                light_native_execute=0, production_mutations=False,
                elapsed_ms=int((time.monotonic() - start) * 1000))


def main(wheels, credential):
    try:
        report = observe(wheels, credential)
    except BaseException:
        # No exception text, traceback, credential, private HOLD identity or snapshot.
        print(json.dumps(dict(audit='NATIVE_OWNER_HOST_READ_ONLY_REFUSED', production_mutations=False)))
        raise SystemExit(2) from None
    print(json.dumps(report, sort_keys=True))
