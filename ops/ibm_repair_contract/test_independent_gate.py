"""Independent finite conjunction oracle; fake data only, no system adapters."""
import ast,copy,itertools,json,unittest
from pathlib import Path
import repair_gate as gate

def fixture(phase='CONFIG_WRITE'):
    p=gate.Policy(host='synthetic-host',boot='synthetic-boot',run='synthetic-run',artifact_sha256='a'*64,candidate_sha256='b'*64,
        scratch_uuid='synthetic-uuid',device_unit='synthetic.device',fsck_unit='synthetic-fsck.service',mount_unit='synthetic.mount',
        config_hashes=(('/synthetic/config','c'*64),),dependency_edges=(('synthetic.mount','Requires','synthetic.device'),),
        protected_units=('synthetic-worker.service',),protected_containers=('synthetic-container',),t0_wall=1000,t0_mono=100,
        admission_wall=1010,admission_mono=110,reserve_seconds=40)
    uv=dict(active='inactive',sub='dead',main_pid=0,control_pid=0,invocation='',restarts=2,job=None)
    if phase=='PRE_QUIESCE':uv.update(active='activating',sub='auto-restart',invocation='d'*32)
    row=dict(host=p.host,boot=p.boot,run=p.run,artifact_sha256=p.artifact_sha256,wall=1057,mono=157,
        jobs=[dict(id=1,unit=p.device_unit,type='start',state='running'),dict(id=2,unit=p.fsck_unit,type='start',state='waiting'),dict(id=3,unit=p.mount_unit,type='start',state='waiting')],
        missing_uuid=p.scratch_uuid,uuid_present=False,config_hashes=dict(p.config_hashes),dependency_edges=[list(e) for e in p.dependency_edges],
        units={'synthetic-worker.service':dict(active='active',sub='running',main_pid=123,control_pid=0,invocation='e'*32,restarts=0,job=None)},
        containers={'synthetic-container':dict(id='f'*64,running=True,pid=456,restarts=0,oom=False)},uv=uv,
        runtime_directory=dict(state='ABSENT',ancestors_verified=True,empty=None,owner=None,group=None,mode=None))
    a=copy.deepcopy(row);b=copy.deepcopy(row);b.update(wall=1059,mono=159)
    q=dict(host=p.host,boot=p.boot,run=p.run,wall=1059,mono=159,queued=0,running=0,query_complete=True,read_only=True)
    auth=dict(host=p.host,boot=p.boot,run=p.run,operation=gate.OPERATION,candidate_sha256=p.candidate_sha256,uv_stop=True,config_write=True)
    receipt=None if phase=='PRE_QUIESCE' else dict(host=p.host,boot=p.boot,run=p.run,unit=gate.UV,completed=True,wall=1050,mono=150)
    return [p,phase,[a,b],q,auth,receipt,1060,160]

def corrupt(args,which):
    s=args[2][1]
    if which==0:s['jobs'][0]['unit']='unexpected.device'
    elif which==1:s['dependency_edges'].append(['x','Wants','y'])
    elif which==2:s['config_hashes']['/synthetic/config']='0'*64
    elif which==3:args[3]['running']=1
    elif which==4:args[3]['wall']=1040
    elif which==5:s['boot']='other-boot'
    elif which==6:s['units']['synthetic-worker.service']['main_pid']+=1
    elif which==7:s['uv']['main_pid']=77
    elif which==8:args[4]['uv_stop']=False
    elif which==9:s['runtime_directory']['ancestors_verified']=False
    elif which==10:args[6]=1231;args[7]=331

class IndependentGateTests(unittest.TestCase):
    def test_exhaustive_conjunction_both_phases(self):
        count=0
        for phase in ('PRE_QUIESCE','CONFIG_WRITE'):
            for bits in itertools.product((False,True),repeat=11):
                args=fixture(phase)
                for i,bad in enumerate(bits):
                    if bad:corrupt(args,i)
                if any(bits):
                    with self.assertRaises(gate.Refused):gate.evaluate(*args)
                else:
                    result=gate.evaluate(*args)
                    self.assertEqual(result['config_write_admitted'],phase=='CONFIG_WRITE')
                    for k in ('global_settle_asserted','format_admitted','mount_admitted','start_admitted','cleanup_admitted','permission_relaxation_admitted','lease_or_lock_asserted'):self.assertIs(result[k],False)
                count+=1
        self.assertEqual(count,4096)

    def test_stop_receipt_and_scope_negative_cases(self):
        for kind in ('missing','wrong_run','wrong_unit','unfinished','after_samples','unauthorized_write','wrong_candidate'):
            args=fixture()
            if kind=='missing':args[5]=None
            elif kind=='wrong_run':args[5]['run']='other'
            elif kind=='wrong_unit':args[5]['unit']='protected.service'
            elif kind=='unfinished':args[5]['completed']=False
            elif kind=='after_samples':args[5]['wall']=1060
            elif kind=='unauthorized_write':args[4]['config_write']=False
            else:args[4]['candidate_sha256']='0'*64
            with self.assertRaises(gate.Refused):gate.evaluate(*args)

    def test_no_blanket_jobs_exception(self):
        for kind in ('extra','missing','different_id','different_state','duplicate','boolean_id'):
            args=fixture();jobs=args[2][1]['jobs']
            if kind=='extra':jobs.append(dict(id=4,unit='cloud-final.service',type='start',state='running'))
            elif kind=='missing':jobs.pop()
            elif kind=='different_id':jobs[0]['id']=99
            elif kind=='different_state':jobs[1]['state']='running'
            elif kind=='duplicate':jobs[1]=copy.deepcopy(jobs[0])
            else:jobs[0]['id']=True
            with self.assertRaises(gate.Refused):gate.evaluate(*args)

    def test_directory_preservation_and_exact_mode(self):
        for bad in (None,'owner','group','mode','nonempty','symlink'):
            args=fixture()
            for s in args[2]:
                s['runtime_directory'].update(state='DIRECTORY',empty=True,owner='universal-video',group='universal-video',mode=0o750)
                if bad=='owner':s['runtime_directory']['owner']='root'
                if bad=='group':s['runtime_directory']['group']='root'
                if bad=='mode':s['runtime_directory']['mode']=0o755
                if bad=='nonempty':s['runtime_directory']['empty']=False
                if bad=='symlink':s['runtime_directory']['state']='SYMLINK'
            if bad:
                with self.assertRaises(gate.Refused):gate.evaluate(*args)
            else:self.assertTrue(gate.evaluate(*args)['config_write_admitted'])

    def test_real_code_mutants_detected(self):
        source=Path(gate.__file__).read_text()
        for reason,case in (('UNEXPECTED_JOB',0),('QUEUES_NOT_IDLE',3),('UV_NOT_QUIESCENT',7)):
            tree=ast.parse(source)
            class Mutator(ast.NodeTransformer):
                def visit_Expr(self,node):
                    if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='require' and len(node.value.args)==2 and isinstance(node.value.args[1],ast.Constant) and node.value.args[1].value==reason:return ast.Pass()
                    return self.generic_visit(node)
            ns={'__name__':'repair_gate'};exec(compile(ast.fix_missing_locations(Mutator().visit(tree)),'<actual-gate-mutant>','exec'),ns)
            args=fixture();args[0]=ns['Policy'](**args[0].__dict__);corrupt(args,case)
            if case==0:args[2][0]['jobs'][0]['unit']='unexpected.device'
            # For UV mutate both samples identically, isolating the quiescence guard.
            if case==7:args[2][0]['uv']['main_pid']=77
            self.assertTrue(ns['evaluate'](*args)['config_write_admitted'],reason)

if __name__=='__main__':unittest.main(verbosity=2)
