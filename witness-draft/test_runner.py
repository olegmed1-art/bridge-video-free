import json
import unittest
from unittest.mock import patch
import runner


class RunnerTests(unittest.TestCase):
    def test_cli_disabled_before_io(self):
        with patch('runner.invoke') as call:
            with self.assertRaises(SystemExit):
                runner.main()
            call.assert_not_called()

    def test_bad_sha_stops_before_calls(self):
        with patch('runner.invoke') as call:
            r = runner.execute('$(invalid)', '/unused/key', '/unused/hosts', call=call)
            call.assert_not_called()
            self.assertEqual(r['failed_stage'], 'input_validation')

    def test_actions_use_get_only(self):
        calls = []
        def call(args):
            calls.append(args)
            return json.dumps({'total_count': 0, 'workflow_runs': []})
        self.assertEqual(runner.github_snapshot(call), 'NO_ACTIVE_OBSERVED')
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(args[:2] == ['gh', 'api'] and len(args) == 3 for args in calls))

    def test_actions_inconsistency_unknown(self):
        def call(args):
            return json.dumps({'total_count': 1, 'workflow_runs': []})
        self.assertEqual(runner.github_snapshot(call), 'UNKNOWN')
