import copy
import datetime
import json
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runner
from evidence_contract import (ACTIVE, INSTANCE, REPO, WITNESS_WORKFLOW,
                               actions_summary, resident_summary, self_run_identity)

SHA = 'e389b644556bc841739c0b2aa1022a03199ef817'


def run_detail():
    return dict(id=12345, head_sha=SHA, run_attempt=1, event='workflow_dispatch',
                path=WITNESS_WORKFLOW, status='in_progress',
                repository={'full_name': REPO}, head_repository={'full_name': REPO},
                actor={'login': 'olegmed1-art'}, triggering_actor={'login': 'olegmed1-art'})


def resident():
    return dict(observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        resident_target='UNKNOWN', inode_match='YES', container_recreation_required='UNKNOWN',
        PRECANARY_READY='UNKNOWN', production_queue_access='UNKNOWN', queue_idle='UNKNOWN',
        conflicting_work='UNKNOWN', server_health='UNKNOWN', vm_state='UNKNOWN', data_mutated=False,
        status='BLOCKED_CAPABILITY', BLOCKER='effective_worker_configuration_and_full_server_witness_unproved',
        NEXT_STEP='independent_review_and_complete_missing_observers_before_any_GO',
        observation_stable=True, host_device_inode=[1, 200], resident_device_inode=[1, 200],
        service=dict(ActiveState='active', SubState='running', NRestarts='0', MainPID='50'),
        container=dict(Running=True, Restarting=False, ExitCode=0, OOMKilled=False, Pid=60),
        resident_mounted_credential_probe=dict(production=True, schema=True, function=True, claimable=0, leased=0))


def validate(e):
    now = datetime.datetime.now(datetime.timezone.utc)
    return resident_summary(json.dumps(e), now - datetime.timedelta(seconds=10), now + datetime.timedelta(seconds=1))


class Integration(unittest.TestCase):
    def test_verified_self_only_excluded(self):
        detail = run_detail()
        identity = self_run_identity(detail, detail['id'], SHA)
        pages = {s: dict(total_count=0, workflow_runs=[]) for s in ACTIVE}
        pages['in_progress'] = dict(total_count=1, workflow_runs=[detail])
        self.assertEqual(actions_summary(pages, identity), 'NO_ACTIVE_OBSERVED')
        other = dict(detail, id=12346)
        pages['in_progress'] = dict(total_count=2, workflow_runs=[detail, other])
        self.assertEqual(actions_summary(pages, identity), 'BUSY')

    def test_self_forgery_rejected(self):
        for delta in [dict(run_attempt=2), dict(id=True), dict(head_sha='a'*40),
                      dict(path='other.yml'), dict(event='push'), dict(status='queued'),
                      dict(actor={'login': 'other'}), dict(triggering_actor={'login': 'other'}),
                      dict(head_repository={'full_name': 'fork/repo'})]:
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                self_run_identity(run_detail() | delta, 12345, SHA)

    def test_self_missing_or_attempt_race_unknown(self):
        identity = self_run_identity(run_detail(), 12345, SHA)
        pages = {s: dict(total_count=0, workflow_runs=[]) for s in ACTIVE}
        self.assertEqual(actions_summary(pages, identity), 'UNKNOWN')
        pages['in_progress'] = dict(total_count=1, workflow_runs=[run_detail() | {'run_attempt': 2}])
        self.assertEqual(actions_summary(pages, identity), 'UNKNOWN')

    def test_resident_fields_strict(self):
        self.assertEqual(validate(resident())['inode_match'], 'YES')
        for delta in [dict(secret='SECRET_CANARY'), dict(PRECANARY_READY='YES'),
                      dict(data_mutated=True), dict(inode_match='NO'), dict(observation_stable=False),
                      dict(observed_at='2000-01-01T00:00:00+00:00'),
                      dict(observed_at='2000-01-01T00:00:00'),
                      dict(service={'secret': 'SECRET_CANARY'}), dict(cpu_count=True),
                      dict(load=[float('nan'), 0, 0])]:
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                validate(resident() | delta)

    def test_resident_duplicate_json_rejected(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        with self.assertRaises(ValueError):
            resident_summary('{"x":1,"x":2}', now, now)

    def run_mocked(self, change=None, postfailure=False, wrong_source=False):
        calls = []
        oci_count = 0
        def call(args, body=None):
            nonlocal oci_count
            calls.append(args)
            if args[0] == 'ssh-keygen':
                return '256 ' + runner.FINGERPRINT + ' target (ED25519)'
            if args[0] == 'oci':
                oci_count += 1
                if postfailure and oci_count == 2:
                    raise RuntimeError('SECRET_CANARY')
                return json.dumps({'data': {'id': INSTANCE, 'lifecycle-state': 'RUNNING',
                              'shape-config': {'ocpus': 4, 'memory-in-gbs': 12}}})
            if args[:2] == ['gh', 'api']:
                if '/contents/' in args[2]:
                    data = Path(__file__).with_name('witness.py').read_bytes()
                    if wrong_source:
                        data += b'\n# altered\n'
                    return json.dumps(dict(type='file', path='witness-draft/witness.py',
                         encoding='base64', size=len(data), content=base64.b64encode(data).decode(),
                         sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()))
                if 'git/ref' in args[2]:
                    return json.dumps({'object': {'sha': SHA}})
                if args[2].endswith('/12345'):
                    return json.dumps(run_detail())
                status = args[2].split('status=')[1].split('&')[0]
                rows = [run_detail()] if status == 'in_progress' else []
                return json.dumps({'total_count': len(rows), 'workflow_runs': rows})
            if args[0] == 'timeout':
                e = resident()
                if change:
                    change(e)
                return json.dumps(e)
            raise AssertionError('unexpected local command')
        with patch('runner.Path.is_file', return_value=True), \
             patch('runner.os.stat', return_value=SimpleNamespace(st_mode=0o600)):
            result = runner.execute(SHA, '/fixture/key', '/fixture/hosts', call=call, run_id=12345)
        return result, calls

    def test_full_mocked_roundtrip_stays_unknown(self):
        r, calls = self.run_mocked()
        self.assertEqual(r['window_stable'], 'UNKNOWN')
        self.assertTrue(r['sampled_metadata_equal'])
        self.assertEqual(r['inode_match'], 'YES')
        self.assertEqual(r['resident_target'], 'UNKNOWN')
        self.assertEqual(r['status'], 'BLOCKED_CAPABILITY')
        self.assertEqual(len([x for x in calls if x[0] == 'timeout']), 1)

    def test_mismatch_requires_completed_postcheck(self):
        def change(e):
            e['resident_device_inode'] = [1, 201]
            e['inode_match'] = 'NO'
        r, _ = self.run_mocked(change)
        self.assertEqual(r['status'], 'BLOCKED_CAPABILITY')
        self.assertEqual(r['container_recreation_required'], 'UNKNOWN')
        r, _ = self.run_mocked(change, postfailure=True)
        self.assertEqual(r['status'], 'BLOCKED_CAPABILITY')
        self.assertEqual(r['inode_match'], 'UNKNOWN')
        self.assertNotIn('resident_observation', r)
        self.assertNotIn('SECRET_CANARY', str(r))

    def test_source_mismatch_stops_before_ssh(self):
        r, calls = self.run_mocked(wrong_source=True)
        self.assertEqual(r['failed_stage'], 'resident_probe')
        self.assertFalse(any(x[0] == 'timeout' for x in calls))

    def test_unexpected_payload_not_published(self):
        def change(e):
            e['secret'] = 'SECRET_CANARY'
        r, _ = self.run_mocked(change)
        self.assertEqual(r['failed_stage'], 'resident_probe')
        self.assertNotIn('SECRET_CANARY', str(r))


if __name__ == '__main__':
    unittest.main()
