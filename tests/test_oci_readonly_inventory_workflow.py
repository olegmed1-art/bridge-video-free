"""Offline security contract for the review-only existing dispatch harness."""
import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/oracle-epoch-readonly-probe.yml'


class WorkflowContract(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding='utf-8-sig')

    def test_only_manual_event_and_one_job(self):
        events = self.text.split('on:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(re.findall(r'^  ([\w]+):', events, re.M), ['workflow_dispatch'])
        jobs = self.text.split('\njobs:\n', 1)[1]
        self.assertEqual(re.findall(r'^  ([\w]+):', jobs, re.M), ['inventory'])
        self.assertIn('timeout-minutes: 5', jobs)
        self.assertNotIn('issues: write', self.text)

    def test_exact_gate_and_checkout_precede_secrets(self):
        for gate in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'olegmed1-art/bridge-video-free'",
            "github.actor == 'olegmed1-art'",
            "github.triggering_actor == 'olegmed1-art'",
            "github.ref == 'refs/heads/review/oci-readonly-inventory-20261001'",
            "inputs.source_run_id == format('oci-readonly-inventory-v1:{0}', github.sha)",
            '[[ "$GITHUB_WORKFLOW_SHA" == "$GITHUB_SHA" ]]',
            'ref: ${{ github.sha }}',
            'persist-credentials: false',
        ):
            self.assertIn(gate, self.text)
            self.assertLess(self.text.index(gate), self.text.index('secrets.'))
        self.assertIn('REVIEW_GATE: ${{ inputs.source_run_id }}', self.text)
        self.assertNotRegex(self.text, r'run:.*\$\{\{\s*inputs\.')

    def test_credentials_and_executable_surface(self):
        self.assertEqual(set(re.findall(r'secrets\.([A-Z_]+)', self.text)), {
            'OCI_READONLY_CLI_USER', 'OCI_READONLY_CLI_TENANCY',
            'OCI_READONLY_CLI_FINGERPRINT', 'OCI_READONLY_CLI_KEY_CONTENT',
            'OCI_READONLY_CLI_REGION',
        })
        for forbidden in ('upload-artifact', 'gh issue', 'gh api', 'curl ', 'sudo ',
                          'OCI_CLI_', 'ibm', 'backup', 'source_run_id=$'):
            self.assertNotIn(forbidden, self.text)
        self.assertIn("'oci==2.187.1'", self.text)
        self.assertIn('run: python ops/oci_readonly_inventory.py', self.text)


if __name__ == '__main__':
    unittest.main()
