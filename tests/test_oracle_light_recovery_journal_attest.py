import json
import unittest
from pathlib import Path
from ops import oracle_light_recovery_journal_attest as audit


class JournalAttestContract(unittest.TestCase):
    def test_emits_only_fixed_markers_and_no_raw_secret(self):
        events = [
            {'MESSAGE':'Traceback (most recent call last): password=private-secret'},
            {'MESSAGE':'Failed to load environment files, database URL=private-secret'},
        ]
        classified = audit.classify(b'\n'.join(json.dumps(e).encode() for e in events))
        self.assertEqual(classified['bounded_records'], 2)
        self.assertTrue(classified['markers']['worker_traceback'])
        self.assertTrue(classified['markers']['systemd_environment_failure'])
        self.assertNotIn('private-secret', json.dumps(classified))

    def test_workflow_runs_production_inspection_only_after_test_on_main_push(self):
        workflow = Path('.github/workflows/oracle-light-recovery-journal-attest.yml').read_text()
        self.assertIn('needs: test', workflow)
        self.assertIn("github.ref == 'refs/heads/main' && github.event_name == 'push'", workflow)
        self.assertIn('ops/oracle_light_post_recovery_attest.py', workflow)
        self.assertNotIn('workflow_dispatch:', workflow)


if __name__ == '__main__':
    unittest.main()
