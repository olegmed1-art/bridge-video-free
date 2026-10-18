"""Run the finite stdlib suite from a source checkout, with external I/O denied."""
from pathlib import Path
import json
import socket
import subprocess
import sys
import unittest
import urllib.request
from unittest.mock import patch


def main():
    # The documented command avoids site packages, user imports and .pyc writes.
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        print('Run: python -I -B -S scripts/run_light_ci_offline.py', file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    tests = root/'tests'
    sys.path.insert(0, str(root))
    attempts = []

    def deny(*args, **kwargs):
        attempts.append('external_entrypoint')
        raise AssertionError('OFFLINE_SUITE_EXTERNAL_IO_DENIED')

    with patch.object(socket, 'create_connection', side_effect=deny), \
         patch.object(socket.socket, 'connect', side_effect=deny), \
         patch.object(socket.socket, 'connect_ex', side_effect=deny), \
         patch.object(urllib.request.OpenerDirector, 'open', side_effect=deny), \
         patch.object(subprocess, 'Popen', side_effect=deny):
        suite = unittest.TestSuite()
        required = (('test_light_ci_once.py', 26), ('test_light_ci_handoff.py', 21))
        for name, count in required:
            path = tests/name
            if not path.is_file() or path.is_symlink():
                print(json.dumps({'status': 'OFFLINE_SUITE_MISSING_TEST_MODULE', 'module': name}))
                return 2
            module = unittest.defaultTestLoader.discover(str(tests), pattern=name)
            if module.countTestCases() != count:
                print(json.dumps({'status': 'OFFLINE_SUITE_INVENTORY_MISMATCH', 'module': name,
                                  'expected': count, 'actual': module.countTestCases()}))
                return 2
            suite.addTests(module)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    historical = 'test_light_ci_handoff.ClosedCycleRegression.test_already_accepted_cycle_is_compatible_evidence_only'
    unexpected_skips = [test.id() for test, _ in result.skipped if test.id() != historical]
    passed = result.wasSuccessful() and not attempts and not unexpected_skips and result.testsRun == 47
    print(json.dumps({'status': 'OFFLINE_SUITE_PASS' if passed else 'OFFLINE_SUITE_FAIL',
                      'tests': result.testsRun, 'failures': len(result.failures),
                      'errors': len(result.errors), 'skipped': len(result.skipped),
                      'external_entrypoint_attempts': len(attempts),
                      'unexpected_skips': unexpected_skips}, sort_keys=True))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
