"""Guard the known continuous GitHub consumers against bypassing the DB route.

This is a repository contract, not evidence that source writers are fenced.
Legacy/manual workflows and resident systemd services require separate cutover checks.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'
ROUTES = {
    'autopilot-reconcile-diagnostic.yml': {'diagnostics'},
    'autopilot-chatgpt-role-callback.yml': {'role-callback'},
    'autopilot-codex-event-callback.yml': {'codex-ack', 'codex-terminal', 'codex-publication'},
    'autopilot-paused-reconcile.yml': {'diagnostics', 'reconcile', 'next-step'},
    'autopilot-mailbox-pre-rotation.yml': {'mailbox'},
    'database-health-monitor.yml': {'health'},
}
COMMAND = r'^\s*(?:run:\s*)?(?:row="\$\()?python\s+-m\s+'
ROUTE_CALL = re.compile(COMMAND + r'ops\.github_autopilot_db_route\s+([a-z-]+)\b', re.MULTILINE)
DIRECT_CALL = re.compile(COMMAND + r'oracle_autopilot\.', re.MULTILINE)


def verify(workflows=WORKFLOWS):
    for name, expected in ROUTES.items():
        source = (workflows / name).read_text(encoding='utf-8')
        observed = set(ROUTE_CALL.findall(source))
        if observed != expected:
            raise AssertionError(f'{name}: route selection drift: {observed} != {expected}')
        # A second direct module invocation could silently write to Neon while
        # the route lease holds the Oracle writer. Require review if introduced.
        if DIRECT_CALL.search(source):
            raise AssertionError(f'{name}: direct autopilot module bypasses routing lease')
        if 'refs/heads/main' not in source:
            raise AssertionError(f'{name}: main-only execution guard missing')


class CutoverRoutingInventory(unittest.TestCase):
    def test_known_continuous_consumers(self):
        verify()

    def test_missing_route_and_direct_bypass_fail(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            temporary = Path(directory)
            for name in ROUTES:
                (temporary / name).write_text((WORKFLOWS / name).read_text(encoding='utf-8'))
            name = 'autopilot-paused-reconcile.yml'
            path = temporary / name
            original = path.read_text()
            path.write_text(original.replace('ops.github_autopilot_db_route reconcile', 'oracle_autopilot.paused_reconcile'))
            with self.assertRaises(AssertionError):
                verify(temporary)
            path.write_text(original + '\n          python -m oracle_autopilot.paused_reconcile\n')
            with self.assertRaises(AssertionError):
                verify(temporary)
            path.write_text(original.replace('ops.github_autopilot_db_route reconcile',
                                             'ignored.route reconcile') +
                            '\n# python -m ops.github_autopilot_db_route reconcile\n')
            with self.assertRaises(AssertionError):
                verify(temporary)


if __name__ == '__main__':
    unittest.main()
