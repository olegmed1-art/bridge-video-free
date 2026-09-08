import unittest
from evidence_contract import INSTANCE, ACTIVE, oci_summary, actions_summary, host_process_summary, draft_report


class Evidence(unittest.TestCase):
    def test_oci_exact_identity(self):
        data = {'id': INSTANCE, 'lifecycle-state': 'RUNNING',
                'shape-config': {'ocpus': 4, 'memory-in-gbs': 12},
                'metadata': {'secret': 'SECRET_CANARY'}}
        result = oci_summary({'data': data})
        self.assertEqual(result['vm_state'], 'RUNNING')
        self.assertNotIn('SECRET_CANARY', str(result))
        data['id'] = 'other-instance'
        self.assertEqual(oci_summary({'data': data})['vm_state'], 'UNKNOWN')

    def test_oci_bad_shape(self):
        for cpu in [True, 0, -1, '4', float('nan'), float('inf')]:
            with self.subTest(cpu=cpu):
                result = oci_summary({'data': {'id': INSTANCE, 'lifecycle-state': 'RUNNING',
                     'shape-config': {'ocpus': cpu, 'memory-in-gbs': 12}}})
                self.assertEqual(result['vm_state'], 'UNKNOWN')

    def test_empty_actions_complete(self):
        pages = {s: {'total_count': 0, 'workflow_runs': []} for s in ACTIVE}
        self.assertEqual(actions_summary(pages), 'IDLE')
        del pages['waiting']
        self.assertEqual(actions_summary(pages), 'UNKNOWN')

    def test_counter_disagrees(self):
        pages = {s: {'total_count': 0, 'workflow_runs': []} for s in ACTIVE}
        pages['queued']['total_count'] = 1
        self.assertEqual(actions_summary(pages), 'UNKNOWN')

    def test_busy_and_changed_state(self):
        pages = {s: {'total_count': 0, 'workflow_runs': []} for s in ACTIVE}
        pages['queued'] = {'total_count': 1, 'workflow_runs': [{'id': 1, 'status': 'queued'}]}
        self.assertEqual(actions_summary(pages), 'BUSY')
        pages['queued']['workflow_runs'][0]['status'] = 'completed'
        self.assertEqual(actions_summary(pages), 'UNKNOWN')

    def test_process_absence_never_idle(self):
        self.assertEqual(host_process_summary([]), 'UNKNOWN')
        self.assertEqual(host_process_summary([{'pid': 3, 'comm': 'python'}]), 'UNKNOWN')
        self.assertEqual(host_process_summary([{'pid': 3, 'comm': 'ffmpeg'}]), 'BUSY')

    def test_draft_cannot_be_live_pass(self):
        result = draft_report()
        self.assertEqual(result['PRECANARY_READY'], 'UNKNOWN')
        self.assertEqual(result['status'], 'BLOCKED_CAPABILITY')
