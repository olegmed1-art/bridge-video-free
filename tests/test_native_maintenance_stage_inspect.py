"""Read-only reconciliation boundaries; no network, SQL or production paths."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_snapshot as snapshot
from ops import native_maintenance_stage_inspect as inspect
from ops import native_maintenance_stage_unit as stage_unit
from ops.native_maintenance_workflow_pause import Journal, digest, encoded
from test_native_maintenance_stage_launcher import request_fixture


SOURCE = 'a' * 40


class API:
    def __init__(self, source, ref):
        from ops.native_maintenance_run_guard import OWNER, OWNER_ID, REPOSITORY
        principal = {'id': OWNER_ID, 'login': OWNER}
        self.run = dict(id=ref['run_id'], run_attempt=ref['attempt'], head_sha=source,
                        head_branch='main', event='workflow_dispatch', status='completed',
                        conclusion='failure', path='.github/workflows/native-maintenance-stages.yml',
                        repository={'full_name': REPOSITORY},
                        head_repository={'full_name': REPOSITORY},
                        actor=principal, triggering_actor=principal)
        self.jobs = dict(total_count=2, jobs=[
            dict(id=123, name='contract', run_id=ref['run_id'],
                 run_attempt=ref['attempt'], head_sha=source,
                 status='completed', conclusion='success'),
            dict(id=ref['job_id'], name='stage', run_id=ref['run_id'],
                 run_attempt=ref['attempt'], head_sha=source,
                 status='completed', conclusion='failure')])

    def get(self, suffix):
        return copy.deepcopy(self.jobs if '/jobs?' in suffix else self.run)


class Store:
    def __init__(self, head=None, archive=None):
        self.head, self.archive = head, archive
        self.objects = {}
        self.reads = 0
        self.private_checks = 0

    def assert_private(self):
        self.private_checks += 1

    def _read(self, key, limit):
        self.reads += 1
        value = self.objects.get(key)
        return None if value is None else (value, 'revision')

    def read_head(self, scope):
        self.reads += 1
        return self.head

    def read_archive(self, scope, sha, limit):
        self.reads += 1
        return self.archive


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.ref = dict(run_id=123, attempt=2, job_id=456)
        self.input = dict(version=1, mode='stage_inspect', source=SOURCE,
                          failed_source=SOURCE, failed_request_digest='b'*64,
                          failed_run=self.ref)

    def test_failed_stage_is_bound_to_exact_authenticated_attempt_and_job(self):
        api = API(SOURCE, self.ref)
        inspect.failed_run(api, self.input)
        cases = (
            ('run', 'head_sha', '0'*40),
            ('run', 'run_attempt', 3),
            ('run', 'status', 'in_progress'),
            ('run', 'head_branch', 'other'),
            ('jobs', 'total_count', 1),
        )
        for owner, key, value in cases:
            bad = API(SOURCE, self.ref)
            getattr(bad, owner)[key] = value
            with self.subTest(owner=owner, key=key), self.assertRaises(Exception):
                inspect.failed_run(bad, self.input)
        for key, value in (('id', 999), ('run_id', 999), ('status', 'queued')):
            bad = API(SOURCE, self.ref)
            bad.jobs['jobs'][1][key] = value
            with self.subTest(job=key), self.assertRaises(Exception):
                inspect.failed_run(bad, self.input)
        bad = API(SOURCE, self.ref)
        bad.jobs['jobs'][0]['conclusion'] = 'failure'
        with self.assertRaises(Exception):
            inspect.failed_run(bad, self.input)

    def test_canonical_diagnostic_input_has_no_approval_or_extra_field(self):
        raw = encoded(self.input)
        self.assertEqual(inspect.input_value(raw, digest(self.input), SOURCE), self.input)
        for changed in ({**self.input, 'approve': True},
                        {**self.input, 'failed_source': '0'*40}):
            with self.assertRaises(Exception):
                inspect.input_value(encoded(changed), digest(self.input), SOURCE)

    def test_missing_and_dangling_lock_never_get_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'journal'
            root.mkdir(mode=0o700)
            with self.assertRaises(FileNotFoundError):
                Journal(root, create_lock=False)
            self.assertEqual(list(root.iterdir()), [])
            (root/'lock').symlink_to(root/'absent')
            with self.assertRaises(OSError):
                Journal(root, create_lock=False)
            self.assertTrue((root/'lock').is_symlink())
            (root/'lock').unlink()
            fd = os.open(root/'lock', os.O_CREAT|os.O_EXCL|os.O_RDWR, 0o600)
            os.close(fd)
            with Journal(root, create_lock=False) as journal:
                self.assertEqual(journal.records, [])
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['lock'])

    def test_private_presence_and_read_refuse_symlink_or_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'private'
            root.mkdir(mode=0o700)
            target = root/'record'
            target.write_bytes(b'private')
            target.chmod(0o600)
            with patch.object(inspect.storage, 'trusted_parent'):
                self.assertEqual(inspect.private_read(target, 7), b'private')
                self.assertFalse(inspect.present_nofollow(root/'missing'))
                (root/'dangling').symlink_to(root/'missing')
                with self.assertRaises(Exception):
                    inspect.present_nofollow(root/'dangling')
                with self.assertRaises(OSError):
                    inspect.private_read(root/'dangling', 7)
                os.link(target, root/'other')
                with self.assertRaises(Exception):
                    inspect.private_read(target, 7)
                self.assertEqual(target.read_bytes(), b'private')

    def pair(self):
        request, _ = request_fixture()
        scope, plan = request['packet']['scope'], request['packet']['plan']
        scope['origin_run'] = self.ref
        binding = digest(scope)
        operation = dict(previous=None, event=dict(kind='BOUND', scope=scope))
        pause = dict(previous=None, event=dict(kind='PLAN', digest=digest(plan),
                                               plan=plan, operation_scope_digest=binding))
        rows = dict(operation=[encoded(operation).decode()], pause=[encoded(pause).decode()])
        raw = encoded(dict(version=1, scope_digest=binding, journals=rows))
        snapshot._parse(raw)
        return binding, rows, raw

    def unit(self, scope, stage, ref):
        return dict(version=1, kind='NATIVE_STAGE_UNIT',source=SOURCE,
                    scope_digest=scope,stage=stage,run=ref,
                    supervisor=dict(unit='bridge-native-ro-'+SOURCE[:12]+'-'+str(ref['run_id'])+'-'+str(ref['attempt'])+'-'+('c'*16)+'.service',
                                    invocation='d'*32,cgroup_inode=123))

    def test_failed_execute_preserves_origin_and_accepts_only_bound_prior_units(self):
        value,_=request_fixture()
        origin=dict(run_id=100,attempt=1,job_id=200)
        packet=value['packet']
        packet['scope']['origin_run']=origin
        packet['stage']='execute'
        packet['prior_units']=[self.unit(digest(packet['scope']),'prepare',origin)]
        request=SimpleNamespace(value=value,source=SOURCE)
        derived=inspect.inspection_packet(request,self.ref)
        self.assertEqual(derived['scope']['origin_run'],origin)
        self.assertEqual(derived,packet)
        for damage in ('source','scope','missing_origin','failed_run'):
            bad=copy.deepcopy(value)
            unit=bad['packet']['prior_units'][0]
            if damage=='source':unit['source']='f'*40
            if damage=='scope':unit['scope_digest']='f'*64
            if damage=='missing_origin':bad['packet']['prior_units']=[]
            if damage=='failed_run':unit['run']=self.ref
            with self.subTest(damage=damage), self.assertRaises(Exception):
                inspect.inspection_packet(SimpleNamespace(value=bad,source=SOURCE),self.ref)

    def test_execute_readback_reports_all_units_without_accepting_any(self):
        scope,rows,raw=self.pair()
        prior=self.unit(scope,'prepare',dict(run_id=100,attempt=1,job_id=200))
        failed=self.unit(scope,'execute',self.ref)
        observed=dict(scope_digest=scope,claimed=True,scope_present=True,
                      manifest_digest='e'*64,units=[prior,failed],journals=rows,failed_stage='execute')
        store=Store()
        for unit in (prior,failed):
            store.objects[stage_unit.path(unit)]=encoded(unit)
        result=inspect.compare(observed,store,self.input)
        self.assertEqual({u['stage'] for u in result['units']},{'prepare','execute'})
        self.assertTrue(all(u['local_present'] and u['remote_present'] for u in result['units']))
        self.assertEqual(result['local_pair_digest'],checkpoint.sha(raw))
        self.assertFalse(result['resume_authorized'])
        observed['units'].append(failed)
        with self.assertRaisesRegex(Exception,'INSPECT_DUPLICATE_UNIT'):
            inspect.compare(observed,store,self.input)

    def test_no_head_reports_observation_without_implicit_acceptance(self):
        scope, rows, raw = self.pair()
        store = Store()
        observed = dict(scope_digest=scope, claimed=True, scope_present=True,
                        manifest_digest='e'*64, units=[], journals=rows)
        with patch.object(checkpoint, 'accepted_latest', side_effect=AssertionError('approval')):
            result = inspect.compare(observed, store, self.input)
        self.assertEqual(result['relation'], 'no_head')
        self.assertEqual(result['local_pair_digest'], checkpoint.sha(raw))
        self.assertIsNone(result['head_digest'])
        self.assertFalse(result['resume_authorized'])
        self.assertFalse(result['production_mutations'])
        self.assertGreater(store.private_checks, 0)

    def test_exact_remote_pair_and_changed_head(self):
        scope, rows, raw = self.pair()
        head = encoded(dict(version=1, scope_digest=scope, sequence=1, previous=None,
                            archive_digest=checkpoint.sha(raw)))
        observed = dict(scope_digest=scope, claimed=True, scope_present=True,
                        manifest_digest='e'*64, units=[], journals=rows)
        store = Store((head, 'etag'), raw)
        self.assertEqual(inspect.compare(observed, store, self.input)['relation'], 'exact_pair')
        class Changed(Store):
            def read_head(self, scope):
                self.reads += 1
                return (head, str(self.reads))
        with self.assertRaisesRegex(Exception, 'INSPECT_REMOTE_HEAD_CHANGED'):
            inspect.compare(observed, Changed((head, 'etag'), raw), self.input)

    def test_remote_only_unit_is_observed_without_becoming_accepted(self):
        scope, _, _ = self.pair()
        expected = dict(source=SOURCE, scope_digest=scope, stage='prepare', run=self.ref)
        unit = dict(version=1, kind='NATIVE_STAGE_UNIT', **expected,
                    supervisor=dict(unit='bridge-native-ro-'+SOURCE[:12]+'-123-2-'+('c'*16)+'.service',
                                    invocation='d'*32, cgroup_inode=123))
        observed = dict(scope_digest=scope, claimed=True, scope_present=False,
                        manifest_digest=None, units=[], journals=None)
        store = Store()
        store.objects[stage_unit.path(expected)] = encoded(unit)
        with patch.object(checkpoint, 'accepted_latest', side_effect=AssertionError('approval')):
            report = inspect.compare(observed, store, self.input)
        self.assertFalse(report['units'][0]['local_present'])
        self.assertTrue(report['units'][0]['remote_present'])
        self.assertEqual(report['units'][0]['remote_digest'], digest(unit))
        self.assertEqual(report['relation'], 'no_head')
        self.assertFalse(report['resume_authorized'])
        # A remote receipt is evidence to review, never a new local unit or ACK.
        self.assertEqual(observed['units'], [])


if __name__ == '__main__':
    unittest.main()
