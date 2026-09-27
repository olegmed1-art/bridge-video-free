"""Candidate assembly cannot become execution or approve observed state."""
import copy
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import unittest
from ops import native_maintenance_grant_candidate as c
from ops.native_maintenance_workflow_pause import encoded, digest
from test_native_maintenance_runtime import packet_fixture
from test_native_maintenance_executor import HOLD, SOURCE
from test_native_maintenance_coordination import agreement_record


def fixture():
    packet,manifest=packet_fixture()
    packet['scope'].pop('origin_run')
    v=dict(version=1,mode='grant_request_candidate',source=SOURCE,runtime_digest='a'*64,
        assets=dict(source_digest='b'*64,manifest_digest=packet['scope']['manifest_digest'],
                    baseline_digest=packet['baseline_digest'],envelope_digest='c'*64),
        plan=packet['plan'],stage='prepare',scope_digest=None,prior_units=[],accepted_head_digest=None,
        expected_outcome=None,agreement=None,request_id='d'*32)
    return v,manifest,packet['scope']


class CandidateTests(unittest.TestCase):

    def test_preview_is_observation_only_and_does_not_disclose_hold(self):
        v,manifest,scope=fixture()
        c.request_value(encoded(v),digest(v),SOURCE)
        with patch.object(c,'staged_hold',return_value=(HOLD,'e'*64)),patch.object(c.hold,'attest',return_value=HOLD),patch.object(c,'submit_candidate') as submit:
            report,raw=c.assemble(v,manifest,[],Mock(spec=['assert_running']))
        assert raw is None and report['request_digest'] is None and report['approved'] is False
        assert report['scope_digest']==digest(scope)
        assert 'hold' not in report and HOLD.fingerprint not in json.dumps(report)
        submit.assert_not_called()


    def test_exact_agreement_creates_candidate_but_never_claims_or_executes(self):
        v,manifest,scope=fixture()
        v.update(scope_digest=digest(scope),agreement=agreement_record(scope))
        saved=[]
        def retain(raw): saved.append(raw);return c.bundle.digest(raw)
        with patch.object(c,'staged_hold',return_value=(HOLD,'e'*64)), \
             patch.object(c.hold,'attest',return_value=HOLD),patch.object(c,'submit_candidate',side_effect=retain), \
             patch.object(c,'read_request',side_effect=lambda sha:saved[0]):
            report,raw=c.assemble(v,manifest,[],Mock(spec=['assert_running']))
        result=json.loads(raw)
        assert report['approved'] is False and report['production_mutations'] is False
        assert result['packet']['scope']==scope and result['packet']['agreement']==v['agreement']
        assert 'origin_run' not in result['packet']['scope']
        assert report['request_digest']==c.bundle.digest(raw)
        assert report['request_digest']!=digest(v)


    def test_drift_refuses_before_submission(self):
        for change in ('scope','manifest','agreement','hold'):
            self._drift(change)

    def _drift(self,change):
        v,manifest,scope=fixture()
        v.update(scope_digest=digest(scope),agreement=agreement_record(scope))
        observed=HOLD
        if change=='scope':v['scope_digest']='f'*64
        if change=='manifest':manifest+=b' '
        if change=='agreement':v['agreement']['operation_digest']='f'*64
        if change=='hold':observed=object()
        with patch.object(c,'staged_hold',return_value=(HOLD,'e'*64)),patch.object(c.hold,'attest',return_value=observed), \
             patch.object(c,'submit_candidate') as submit,self.assertRaises(Exception):
            c.assemble(v,manifest,[],Mock(spec=['assert_running']))
        submit.assert_not_called()


    def test_cancelled_guard_never_retains_candidate(self):
        v,manifest,scope=fixture();v.update(scope_digest=digest(scope),agreement=agreement_record(scope))
        guard=Mock(spec=['assert_running']);guard.assert_running.side_effect=RuntimeError('RUN_CANCELLED')
        with patch.object(c,'submit_candidate') as submit,self.assertRaisesRegex(RuntimeError,'RUN_CANCELLED'):
            c.assemble(v,manifest,[],guard)
        submit.assert_not_called()


    def test_input_rejects_unbound_or_unsupported_fields(self):
        for field,value in [('source','f'*40),('scope_digest','wrong'),('prior_units',[{}]),('stage','rollback'),('assets',{}),('approved',True),('accepted_head_digest','a'*64)]:
            self._reject(field,value)

    def _reject(self,field,value):
        v,_,_=fixture();v[field]=value
        with self.assertRaises(Exception):c.request_value(encoded(v),digest(v),SOURCE)


    def test_canonical_external_acceptance_and_stdlib_import(self):
        v,_,_=fixture()
        with self.assertRaises(Exception):c.request_value(encoded(v), 'f'*64,SOURCE)
        with self.assertRaises(Exception):c.request_value(encoded(v)+b'\n',c.bundle.digest(encoded(v)+b'\n'),SOURCE)
        repo=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable,'-I','-B','-S','-c',
            "import sys;sys.path.insert(0,sys.argv[1]);import ops.native_maintenance_grant_candidate;assert 'psycopg' not in sys.modules",str(repo)],capture_output=True)
        assert result.returncode==0,result.stderr


    def test_safe_drain_vocabulary_refuses_raw_metadata(self):
        from ops.native_maintenance_coordination import validate_diagnostic_groups
        row=dict(user_class='owner',backend_class='client',state_class='idle',has_xact=False,age='ge10m_or_unknown',count=1)
        validate_diagnostic_groups([row])
        for changed in ({**row,'query':'private SQL'},{**row,'user_class':'raw-user'},{**row,'count':True}):
            with self.assertRaises(Exception):validate_diagnostic_groups([changed])

    def test_resume_uses_exact_accepted_local_and_oci_unit(self):
        for stage in ('execute','restore'):
            for fault in (None,'local','missing','scope','unit_digest'):
                with self.subTest(stage=stage,fault=fault):
                    self._resume(stage,fault)

    def _resume(self,stage,fault):
        v,manifest,_=fixture()
        packet,_=packet_fixture();scope=packet['scope']
        run=scope['origin_run']
        unit=dict(version=1,kind='NATIVE_STAGE_UNIT',stage='prepare',source=SOURCE,
            scope_digest=digest(scope),run=run,supervisor=dict(
            unit='bridge-native-ro-'+SOURCE[:12]+'-123-2-'+('a'*16)+'.service',
            invocation='b'*32,cgroup_inode=42))
        unit_raw=encoded(unit)
        v.update(stage=stage,scope_digest=digest(scope),agreement=agreement_record(scope),
            accepted_head_digest='f'*64,expected_outcome='AFTER' if stage=='restore' else None,
            prior_units=[dict(stage='prepare',run=run,digest=digest(unit))])
        if fault=='scope':v['scope_digest']='0'*64
        if fault=='unit_digest':v['prior_units'][0]['digest']='0'*64
        c.request_value(encoded(v),digest(v),SOURCE)
        saved=[]
        def retain(raw):saved.append(raw);return c.bundle.digest(raw)
        with patch.object(c,'staged_hold',return_value=(HOLD,'e'*64)), \
             patch.object(c.hold,'attest',return_value=HOLD), \
             patch.object(c.storage,'private_directory'),patch.object(c.storage,'persistent_mount'), \
             patch.object(c.hold,'read',side_effect=FileNotFoundError() if fault=='missing' else None,
                          return_value=b'changed' if fault=='local' else unit_raw), \
             patch.object(c,'submit_candidate',side_effect=retain) as submit, \
             patch.object(c,'read_request',side_effect=lambda _:saved[0]):
            if fault:
                with self.assertRaises(Exception):c.assemble(v,manifest,[unit_raw],Mock(spec=['assert_running']))
                submit.assert_not_called()
            else:
                report,raw=c.assemble(v,manifest,[unit_raw],Mock(spec=['assert_running']))
                result=json.loads(raw)
                assert result['packet']['scope']==scope
                assert result['packet']['prior_units']==[unit]
                assert result['packet']['accepted_head_digest']=='f'*64
                assert result['packet']['expected_outcome']==v['expected_outcome']
                assert report['approved'] is False
