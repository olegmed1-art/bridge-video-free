"""Root/Linux disposable tests of the maintenance route lock and real clients."""
import fcntl
import json
import os
from pathlib import Path
import tempfile

from ops.native_permission_route_fence import RouteMaintenanceFence, RouteFenceError
from database.fixtures.native_route_drain_rehearsal import (
    setup, start_lease, finish_lease, prove_busy,
)


def refused(action, code):
    try:
        action()
    except RouteFenceError as exc:
        if str(exc) != code:
            raise
    else:
        raise AssertionError('EXPECTED_' + code)


def main():
    if os.geteuid() != 0:
        raise RuntimeError('DISPOSABLE_ROOT_REQUIRED')
    with tempfile.TemporaryDirectory(prefix='native-route-drain-') as temporary:
        root = Path(temporary) / 'protocol'
        setup(root)
        expected = dict(version=1, backend='neon', database='autopilot', epoch=1)
        client, record = start_lease(root)
        try:
            if record['route'] != expected:
                raise AssertionError('WRONG_CLIENT_ROUTE')
            refused(lambda: RouteMaintenanceFence(expected, root=root).__enter__(),
                    'ROUTE_CONSUMERS_ACTIVE')
        finally:
            finish_lease(client)
        with RouteMaintenanceFence(expected, root=root) as fence:
            prove_busy(root)
            fence.assert_held()
            refused(lambda: RouteMaintenanceFence(expected, root=root).__enter__(),
                    'ROUTE_CONSUMERS_ACTIVE')
            fcntl.flock(fence.lock, fcntl.LOCK_UN)
            refused(fence.assert_held, 'ROUTE_FENCE_LOST')
            # Loss detection must not silently reacquire the lock.
            client, record = start_lease(root)
            try:
                if record['route'] != expected:
                    raise AssertionError('LOST_LOCK_REACQUIRED')
            finally:
                finish_lease(client)
        with RouteMaintenanceFence(expected, root=root) as fence:
            fence.deadline = 0
            refused(fence.assert_held, 'ROUTE_WINDOW_EXPIRED')
        with RouteMaintenanceFence(expected, root=root) as fence:
            route = root / 'route.json'
            route.write_text(json.dumps(dict(expected, epoch=2)))
            refused(fence.assert_held, 'ROUTE_VALUE_CHANGED')
            route.write_text(json.dumps(expected))
        with RouteMaintenanceFence(expected, root=root) as fence:
            lock = root / 'route.lock'
            lock.rename(root / 'old.lock')
            lock.touch(mode=0o644)
            refused(fence.assert_held, 'ROUTE_LOCK_CHANGED')
            lock.unlink()
            (root / 'old.lock').rename(lock)
        with RouteMaintenanceFence(expected, root=root) as fence:
            alternate = root.with_name('moved')
            root.rename(alternate)
            root.mkdir(mode=0o755)
            try:
                refused(fence.assert_held, 'ROUTE_DIRECTORY_CHANGED')
            finally:
                root.rmdir()
                alternate.rename(root)
        for filename, code in (('route.lock', 'ROUTE_LOCK_CHANGED'),
                               ('route.json', 'ROUTE_FILE_UNTRUSTED'),
                               ('lock-identity.json', 'ROUTE_FILE_UNTRUSTED')):
            path = root / filename
            backup = root / (filename + '.original')
            path.rename(backup)
            os.mkfifo(path, 0o644)
            try:
                refused(lambda: RouteMaintenanceFence(expected, root=root).__enter__(), code)
            finally:
                path.unlink()
                backup.rename(path)
        try:
            with RouteMaintenanceFence(expected, root=root):
                raise RuntimeError('INJECTED_BODY_FAILURE')
        except RuntimeError as exc:
            if str(exc) != 'INJECTED_BODY_FAILURE':
                raise
        client, record = start_lease(root)
        try:
            if record['route'] != expected:
                raise AssertionError('LOCK_NOT_RELEASED')
        finally:
            finish_lease(client)
    print('NATIVE_MAINTENANCE_ROUTE_FENCE_PASS')


if __name__ == '__main__':
    main()
