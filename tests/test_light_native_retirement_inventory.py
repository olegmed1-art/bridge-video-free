"""Retained-input observation: real validators, no DB/provider or write ports."""
import base64
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('issuer_inventory_retirement',
    Path(__file__).resolve().parents[1]/'ops/light_native_retirement.py')
r=importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class ReadOnlyView:
    def __init__(self,files):
        self.files=files
        self.checked=0
        self.drift=False
        self.accessed=[]

    def read(self,path,pin=None,*,optional=False,limit=262144):
        self.accessed.append(path)
        if path not in self.files:
            if optional:return None
            raise r.Refusal('LANE_RETIREMENT_MISSING')
        raw=self.files[path]
        r.need(type(raw) is bytes and len(raw)<=limit,'LANE_RETIREMENT_SIZE')
        r.need(pin is None or r.sha(raw)==pin,'LANE_RETIREMENT_NOT_ACCEPTED')
        return raw

    def names(self,path):
        prefix=path+'/'
        return {key[len(prefix):].split('/')[0] for key in self.files if key.startswith(prefix)}

    def check(self):
        self.checked+=1
        r.need(not self.drift,'LANE_RETIREMENT_DRIFT')


def fixture():
    source='1'*40
    runtime=r.encoded({'version':1,'synthetic_runtime':'NO_PROVIDER'})
    runtime_pin=r.sha(runtime)
    plan=r.encoded(dict(source=source,repository='example/synthetic',
                        work_key='PRIVATE_SENTINEL',target_pr=1234))
    plan_pin=r.sha(plan)
    plan_entry=dict(plan_base64=base64.b64encode(plan).decode(),accepted_plan_sha256=plan_pin)
    predecessor=dict(sequence=2,plan_sha256='2'*64,terminal_sha256='3'*64)
    policy=dict(version=1,action='issue',source='4'*40,accepted_controller_sha256='5'*64,
                accepted_runtime_sha256=runtime_pin,not_before='2000-01-01T00:00:00Z',
                expires_at='2000-01-01T01:00:00Z',predecessor=predecessor,
                plans=[plan_entry],authority={'synthetic':True})
    policy_raw=r.encoded(policy)
    policy_pin=r.sha(policy_raw)
    prepare=dict(source=policy['source'],accepted_controller_sha256=policy['accepted_controller_sha256'],
                 accepted_runtime_sha256=runtime_pin,predecessor=predecessor,**plan_entry)
    cycle=dict(version=1,action='cycle',prepare=prepare)
    entry='issuers/'+policy_pin+'/0000'
    files={
        'issuers/'+policy_pin+'/policy.json':policy_raw,
        entry+'/intent.json':r.encoded(dict(start=1,cycle=cycle)),
        plan_pin+'/plan.json':plan,
        'cycles/'+plan_pin+'/intent.json':r.encoded(cycle),
        'cycles/'+plan_pin+'/contain-intent.json':b'{}',
        'cycles/'+plan_pin+'/incident.json':b'{"state":"UNKNOWN"}',
        plan_pin+'/contain-intent.json':b'{}',
    }
    for name in ('baseline.json','before.json','contained.json','prepare.json'):
        files[plan_pin+'/'+name]=r.encoded({'synthetic':name,'private':'PRIVATE_SENTINEL'})
    files[plan_pin+'/runtime-package.json']=runtime
    files[plan_pin+'/wheels.tar']=b'SYNTHETIC_NOT_AN_ARCHIVE'
    return ReadOnlyView(files),(policy_pin,0,source,runtime_pin),entry,plan_pin


def replace_policy(view,args,change):
    old=args[0];policy=r.parse(view.files['issuers/'+old+'/policy.json'])
    change(policy)
    raw=r.encoded(policy);new=r.sha(raw)
    view.files={key.replace('issuers/'+old+'/','issuers/'+new+'/',1):value
                for key,value in view.files.items()}
    view.files['issuers/'+new+'/policy.json']=raw
    return (new,*args[1:])


class IssuerInventoryTests(unittest.TestCase):
    def observe(self,view,args):
        return r.inventory_retained_issuer(view,*args)

    def test_missing_proposal_is_observed_without_minting_authority(self):
        view,args,_,_=fixture()
        before=deepcopy(view.files)
        result=self.observe(view,args)
        self.assertEqual(result['actual_proposal_state'],'ABSENT')
        self.assertIsNone(result['actual_proposal_sha256'])
        self.assertEqual(set(result['journal_sha256']),r.JOURNAL_KEYS)
        self.assertEqual(result['journal_map_sha256'],r.sha(r.encoded(result['journal_sha256'])))
        for key in ('history_verified','controls_verified','incident_closed',
                    'execution_acknowledged','new_task_authorized'):
            self.assertIs(result[key],False)
        self.assertNotIn(b'PRIVATE_SENTINEL',r.encoded(result))
        self.assertLess(len(r.encoded(result)),4096)
        self.assertEqual(view.files,before)
        self.assertEqual(view.checked,1)

    def test_partial_or_conflicting_proposal_is_not_absence_or_success(self):
        for raw in (b'',b'{',b'{"incident_closed":true}'):
            with self.subTest(raw=raw):
                view,args,entry,_=fixture()
                view.files[entry+'/'+r.NAME]=raw
                result=self.observe(view,args)
                self.assertEqual(result['actual_proposal_state'],'CONFLICT')
                self.assertEqual(result['actual_proposal_sha256'],r.sha(raw))
                self.assertIs(result['incident_closed'],False)
                self.assertEqual(view.files[entry+'/'+r.NAME],raw)

    def test_exact_existing_proposal_is_only_an_observation(self):
        view,args,entry,plan_pin=fixture()
        observed=self.observe(view,args)
        policy=r.parse(view.files['issuers/'+args[0]+'/policy.json'])
        intent=r.parse(view.files[entry+'/intent.json'])
        proposed=r.encoded(dict(version=1,kind='FAILED_PREPARE_RETIREMENT_PROPOSAL',
            requested_disposition='RETIRE_FAILED_PREPARE_AND_CANCEL_OLD_POLICY_REMAINDER',
            policy_sha256=args[0],index=0,plan_sha256=plan_pin,repository='example/synthetic',
            failed_work_key='PRIVATE_SENTINEL',failed_target_pr=1234,
            predecessor=intent['cycle']['prepare']['predecessor'],
            incident_sha256=observed['journal_sha256']['incident.json'],
            historical_controller_source=policy['source'],historical_controller_sha256=policy['accepted_controller_sha256'],
            retained_runtime_source=args[2],retained_runtime_sha256=args[3],original_failure='UNKNOWN',
            interpretation='EXPLICIT_NEW_POLICY_HASH_ALLOWLIST_AND_FRESH_GUARDS_REQUIRED',
            **{key:False for key in r.FALSE_FLAGS}))
        self.assertEqual(r.sha(proposed),observed['proposal_sha256'])
        view.files[entry+'/'+r.NAME]=proposed
        result=self.observe(view,args)
        self.assertEqual(result['actual_proposal_state'],'EXACT')
        self.assertIs(result['incident_closed'],False)
        self.assertIs(result['new_task_authorized'],False)

    def test_untrusted_selectors_refuse_before_read(self):
        for position,value in ((0,'../escape'),(1,True),(1,-1),(1,8),(2,'x'),(3,'x')):
            with self.subTest(position=position,value=value):
                view,args,_,_=fixture();bad=list(args);bad[position]=value
                with self.assertRaises(r.Refusal):self.observe(view,bad)
                self.assertEqual(view.accessed,[])

    def test_missing_input_and_pin_drift_remain_refusals(self):
        for mode in ('missing','policy','plan','runtime','cycle','directory','snapshot'):
            with self.subTest(mode=mode):
                view,args,entry,plan_pin=fixture()
                if mode=='missing':del view.files[plan_pin+'/before.json']
                elif mode=='policy':view.files['issuers/'+args[0]+'/policy.json']+=b' '
                elif mode=='plan':view.files[plan_pin+'/plan.json']+=b' '
                elif mode=='runtime':view.files[plan_pin+'/runtime-package.json']+=b' '
                elif mode=='cycle':view.files['cycles/'+plan_pin+'/intent.json']=b'{}'
                elif mode=='directory':view.files[entry+'/unknown.json']=b'{}'
                else:view.drift=True
                before=deepcopy(view.files)
                with self.assertRaises(r.Refusal):self.observe(view,args)
                self.assertEqual(view.files,before)

    def test_bad_embedded_plan_selector_or_encoding_is_rejected(self):
        for field,value in (('accepted_plan_sha256','../escape'),
                            ('plan_base64','%%%'),('plan_base64',base64.b64encode(b'wrong').decode())):
            with self.subTest(field=field,value=value):
                view,args,_,_=fixture()
                args=replace_policy(view,args,lambda p:p['plans'][0].update({field:value}))
                with self.assertRaises(r.Refusal):self.observe(view,args)
                self.assertFalse(any('escape' in path for path in view.accessed))

    def test_each_prepare_binding_mismatch_is_rejected(self):
        for field in ('source','accepted_controller_sha256','accepted_runtime_sha256',
                      'accepted_plan_sha256','plan_base64'):
            with self.subTest(field=field):
                view,args,entry,_=fixture()
                intent=r.parse(view.files[entry+'/intent.json'])
                intent['cycle']['prepare'][field]='changed'
                view.files[entry+'/intent.json']=r.encoded(intent)
                with self.assertRaises(r.Refusal):self.observe(view,args)

    def test_index_one_requires_the_existing_prefix_and_refuses_later_entries(self):
        view,args,_,_=fixture()
        args=replace_policy(view,args,lambda p:p['plans'].append(deepcopy(p['plans'][0])))
        first='issuers/'+args[0]+'/0000'
        second='issuers/'+args[0]+'/0001'
        view.files[second+'/intent.json']=view.files[first+'/intent.json']
        result=self.observe(view,(args[0],1,*args[2:]))
        self.assertEqual(result['index'],1)
        self.assertIs(result['history_verified'],False)
        with self.assertRaises(r.Refusal):self.observe(view,args)
        del view.files[first+'/intent.json']
        with self.assertRaises(r.Refusal):self.observe(view,(args[0],1,*args[2:]))

    def test_oversized_proposal_or_selected_metadata_refuses(self):
        for kind in ('proposal','plan','policy','journal'):
            with self.subTest(kind=kind):
                view,args,entry,plan_pin=fixture()
                path={'proposal':entry+'/'+r.NAME,'plan':plan_pin+'/plan.json',
                      'policy':'issuers/'+args[0]+'/policy.json','journal':plan_pin+'/wheels.tar'}[kind]
                size={'proposal':4097,'plan':262145,'policy':262145,'journal':16*1024*1024+1}[kind]
                view.files[path]=b'x'*size
                with self.assertRaises(r.Refusal):self.observe(view,args)

    def test_public_result_refuses_extra_fields_and_promoted_or_unbound_states(self):
        view,args,_,_=fixture();original=self.observe(view,args)
        changes=[('private','PRIVATE_SENTINEL'),('version',True),('index',False),
                 ('audit','changed'),('state','LOCAL_CHECKED'),('policy_sha256','0'*64),
                 ('journal_map_sha256','0'*64),('proposal_sha256','bad'),
                 ('actual_proposal_state','EXACT'),('actual_proposal_sha256','0'*64)]
        changes.extend((key,True) for key in ('history_verified','controls_verified',
                       'incident_closed','execution_acknowledged','new_task_authorized'))
        for field,value in changes:
            with self.subTest(field=field):
                result=deepcopy(original);result[field]=value
                with self.assertRaises(r.Refusal):r.public_issuer_inventory(result,args[0],0)
        result=deepcopy(original);result['journal_sha256']['PRIVATE_SENTINEL']='0'*64
        with self.assertRaises(r.Refusal):r.public_issuer_inventory(result,args[0],0)

    @unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda:1)()==0,
                         'real Snapshot metadata integration requires Linux root')
    def test_real_snapshot_composition_preserves_private_bytes_and_absence(self):
        memory,args,_,_=fixture()
        with tempfile.TemporaryDirectory(prefix='native-inventory-') as directory:
            root=Path(directory);root.chmod(0o700)
            for relative,raw in memory.files.items():
                path=root
                for part in relative.split('/')[:-1]:
                    path=path/part
                    path.mkdir(mode=0o700,exist_ok=True)
                file=root/relative;file.write_bytes(raw);file.chmod(0o600)
            before={name:(root/name).read_bytes() for name in memory.files}
            with r.Snapshot(root,'unused') as view:
                result=self.observe(view,args)
                self.assertEqual(result['actual_proposal_state'],'ABSENT')
            self.assertEqual(before,{name:(root/name).read_bytes() for name in memory.files})


if __name__=='__main__':unittest.main()
