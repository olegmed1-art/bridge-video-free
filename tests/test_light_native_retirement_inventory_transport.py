"""Exact read-only authority and credential-free privileged envelope contracts."""
import ast
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


r=load('inventory_reference_retirement','ops/light_native_retirement.py')
runner=load('inventory_reference_runner','ops/light_native_issuer_inventory_runner.py')


def reference():
    value=dict(version=1,action='inventory-issuer-reference',source='a'*40,
        accepted_controller_sha256='b'*64,accepted_runtime_sha256='c'*64,
        policy_sha256='d'*64,index=0)
    agreement=dict(version=1,owner='olegmed1-art',
        operation_digest=r.sha(r.encoded(r.issuer_inventory_scope(value))),
        not_before='2000-01-01T00:00:00Z',expires_at='2000-01-01T00:10:00Z',
        coverage=['direct_owner_sql','host_administration','workflow_administration','workflow_reruns','main_pushes'],
        evidence='OWNER_ACCEPTED_READ_ONLY_ISSUER_INPUT_INVENTORY')
    return dict(**value,agreement=agreement,accepted_agreement_sha256=r.sha(r.encoded(agreement)))


class InventoryTransportTests(unittest.TestCase):
    def test_distinct_action_and_operation_bound_agreement_shape(self):
        value=reference();raw=r.encoded(value)
        self.assertEqual(r.issuer_inventory_reference(raw),value)
        self.assertEqual(r.issuer_inventory_scope(value)['operation'],
                         'READ_ONLY_INVENTORY_RETAINED_ISSUER_INPUTS')
        for key,bad in (('action','retire-prepare-reference'),('index',True),
                        ('index',8),('source','../escape'),('policy_sha256','bad'),
                        ('accepted_controller_sha256','bad'),('accepted_runtime_sha256','bad')):
            with self.subTest(key=key):
                changed=deepcopy(value);changed[key]=bad
                with self.assertRaises(r.Refusal):r.issuer_inventory_reference(r.encoded(changed))
        for key in ('agreement','accepted_agreement_sha256'):
            changed=deepcopy(value);del changed[key]
            with self.assertRaises(r.Refusal):r.issuer_inventory_reference(r.encoded(changed))

    def test_hashing_a_different_agreement_or_scope_does_not_make_it_accepted(self):
        original=reference()
        for fault in ('evidence','operation','scope','agreement_hash'):
            with self.subTest(fault=fault):
                value=deepcopy(original)
                if fault=='evidence':value['agreement']['evidence']='OTHER_OPERATION'
                elif fault=='operation':value['agreement']['operation_digest']='0'*64
                elif fault=='scope':value['policy_sha256']='0'*64
                if fault!='agreement_hash':
                    value['accepted_agreement_sha256']=r.sha(r.encoded(value['agreement']))
                else:value['accepted_agreement_sha256']='0'*64
                with self.assertRaises(r.Refusal):r.issuer_inventory_reference(r.encoded(value))

    def test_noncanonical_or_extra_private_input_is_rejected(self):
        value=reference()
        with self.assertRaises(r.Refusal):r.issuer_inventory_reference(json.dumps(value).encode())
        value['credential']='PRIVATE_SENTINEL'
        with self.assertRaises(r.Refusal):r.issuer_inventory_reference(r.encoded(value))

    def test_bootstrap_has_fixed_credential_free_wire_and_authentication(self):
        code=runner.bootstrap('a'*40,'b'*64,'c'*64,'d'*64,123,1)
        compile(code,'inventory-bootstrap','exec')
        self.assertIn("{'controller','runtime','payload','token'}",code)
        self.assertIn('read_only=True',code)
        self.assertIn('guard.assert_running()',code)
        self.assertIn('public_inventory_failure',code)
        self.assertIn('public_issuer_inventory',code)
        self.assertNotIn('credential',code)
        self.assertNotIn('driver',code)
        self.assertNotIn('loaded_runtime',code)
        for position,bad in ((0,'a'*39),(1,'../bad'),(2,'x'),(3,'x'),
                             (4,True),(4,0),(4,10**20),(5,False),(5,10**6)):
            with self.subTest(position=position,bad=bad):
                values=['a'*40,'b'*64,'c'*64,'d'*64,123,1];values[position]=bad
                with self.assertRaises(ValueError):runner.bootstrap(*values)

    def test_only_fixed_refusal_codes_cross_egress(self):
        for code in ('LANE_RETIREMENT_MISSING','LANE_RETIREMENT_METADATA',
                     'LANE_ISSUER_INVENTORY_BINDING','COORDINATION_AGREEMENT_EXPIRED'):
            value=r.issuer_inventory_failure(RuntimeError(code))
            self.assertEqual(r.public_inventory_failure(value)['reason'],code)
        result=r.issuer_inventory_failure(RuntimeError('PRIVATE_SENTINEL'))
        self.assertEqual(result['reason'],'OBSERVATION_REFUSED')
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(result))
        for value in ({'audit':'LIGHT_NATIVE_ISSUER_INPUT_REFUSED','reason':'PRIVATE_SENTINEL'},
                      {**result,'exception':'PRIVATE_SENTINEL'}):
            with self.assertRaises(r.Refusal):r.public_inventory_failure(value)

    def test_inventory_owner_path_has_no_database_or_service_effect_port(self):
        tree=ast.parse((ROOT/'ops/light_native_retirement_live.py').read_bytes())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                  and n.name=='inventory_issuer_inputs')
        text=ast.unparse(node)
        for forbidden in ('psycopg','loaded_runtime','credential','subprocess','systemctl',
                          'write_proposal','intake.prepare','provider'):
            # The explanatory docstring names capabilities only; code cannot use them.
            body=ast.Module(body=node.body[1:],type_ignores=[])
            self.assertNotIn(forbidden,ast.unparse(body))
        self.assertIn('Agreement(',text)
        self.assertIn('agreement.assert_held',text)
        self.assertIn('observation_locks',text)

    def test_workflow_inventory_branch_cannot_load_database_secret_or_driver(self):
        text=(ROOT/'.github/workflows/light-native-retirement-observe.yml').read_text()
        inventory=text.split('      - name: Inventory retained issuer inputs',1)[1].split(
            '      - name: Observe retirement',1)[0]
        self.assertNotIn('DATABASE_URL',inventory)
        self.assertNotIn('pip download',inventory)
        self.assertNotIn('LIGHT_MAINTENANCE',inventory)
        self.assertIn("steps.observation.outputs.inventory == 'true'",inventory)
        guard=ast.parse((ROOT/'ops/light_native_lane_run_guard.py').read_bytes())
        pin=next(ast.literal_eval(node.value) for node in guard.body if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id=='OBSERVATION_WORKFLOW_SHA256'
                    for target in node.targets))
        self.assertEqual(pin,r.sha(text.replace('\r\n','\n').encode()))


if __name__=='__main__':unittest.main()
