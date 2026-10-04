"""Independent finite oracle for identity helpers; no guest/transport execution.

Imports only declarations from the actual collector, then current helper definitions.
The graph oracle is a separate set model, not the collector's decoder.
Run with python -B independent_checker.py. No network, subprocess or host probes.
"""
import ast
import copy
import hashlib
import io
import itertools
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
COUNTS = {}

def load():
    tree = ast.parse((ROOT / 'diagnostic_guest.py').read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef, ast.ClassDef))]
    ns = {'__name__': 'independent_declarations'}
    exec(compile(tree, '<collector-declarations>', 'exec'), ns)
    exec(compile((ROOT / 'identity_helpers.py').read_text(encoding='utf-8'), '<actual-helpers>', 'exec'), ns)
    return ns

def encode(ns, edges, aliases=(), states=None, names=('a.service','b.service'), missing=None):
    states = states or {n: 'loaded' for n in names}
    index = {n:i for i,n in enumerate(names)}
    grouped = {}
    for a,p,b in edges:
        grouped.setdefault((index[a],ns['EDGES'].index(p)), []).append(index[b])
    ali = {n:[] for n in names}
    for a,b in aliases: ali[a].append(index[b])
    payload = dict(missing_edge_properties=missing or {},unit_names=list(names),edge_properties=list(ns['EDGES']),
                   edges=[[a,p,sorted(v)] for (a,p),v in sorted(grouped.items())],
                   present=list(range(len(names))), aliases=[[index[n],sorted(ali[n])] for n in names],
                   load_states=[[index[n],states[n]] for n in names])
    return dict(payload,sha256=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest(),closure_complete=not missing)

def settle_fixture(ns):
    consumers = ns['TARGETS'] + ('docker.service','synthetic-watch.service',ns['UV'])
    units={u:dict(LoadState='loaded',ActiveState='active',SubState='running',Result='success',MainPID='11',ControlPID='0',InvocationID='a'*32,NRestarts='0') for u in consumers}
    units['cloud-final.service']=dict(LoadState='loaded',ActiveState='active',SubState='exited',Result='success',ExecMainCode='1',ExecMainStatus='0',ExecMainExitTimestampMonotonic='200')
    frames=[dict(boot_id='b',boot_id_after='b',jobs=[],units=copy.deepcopy(units),manager_state='running') for _ in range(4)]
    config=dict(files=[dict(path='/unit',sha256='a'*64)],complete=True,fstab_sha256='f'*64)
    mounts=[dict(target='/',fstype='ext4',source='/dev/vda1')]
    return frames, dict(closure_complete=True,same_as_before=True), config, copy.deepcopy(config), mounts, copy.deepcopy(mounts)

class IndependentTests(unittest.TestCase):
    def setUp(self): self.ns=load()

    def test_graph_all_property_edge_pairs(self):
        n=self.ns; count=0
        for prop in n['EDGES']:
            possible=[(a,prop,b) for a in ('a.service','b.service') for b in ('a.service','b.service')]
            for left,right in itertools.product(range(16),repeat=2):
                a={e for i,e in enumerate(possible) if left>>i&1}; b={e for i,e in enumerate(possible) if right>>i&1}
                actual=n['graph_recheck'](encode(n,a),encode(n,b))
                self.assertEqual(actual['delta']['edges'],dict(added=sorted(b-a),removed=sorted(a-b)))
                self.assertEqual(actual['same_as_before'],a==b)
                count+=1
        COUNTS['graph_pairs']=count

    def test_graph_alias_state_missing_and_full_preservation(self):
        n=self.ns
        for missing,alias,state in itertools.product((False,True),repeat=3):
            a=encode(n,set()); b=encode(n,set(),aliases=[('a.service','b.service')] if alias else (),states={'a.service':'not-found' if state else 'loaded','b.service':'loaded'},missing={'a.service':['Requires']} if missing else None)
            saved=copy.deepcopy((a,b)); r=n['graph_recheck'](a,b)
            self.assertEqual(r['closure_complete'],not missing)
            self.assertEqual(bool(r['delta']['aliases']['added']),alias)
            self.assertEqual(bool(r['delta']['load_states']),state)
            self.assertEqual((a,b),saved)
        self.assertEqual(n['graph_recheck'](None,None)['state'],'UNKNOWN')
        COUNTS['graph_metadata_cases']=8

    def test_disk_independent_truth_table(self):
        n=self.ns; prefix=n['PROVIDER_DISK_ID'][:20]; count=0
        for drift,linkdrift,sizebad,partition,serialbad,duplicate,other,unstable in itertools.product((False,True),repeat=8):
            disk=dict(size_bytes=1 if sizebad else 260000000000,partition_marker_present=partition,major=253,minor=48,serial=dict(state='PRESENT',value='wrong' if serialbad else prefix))
            a={'vdd':disk,'vda':{'identity':'root'}}; b=copy.deepcopy(a)
            if drift:b['vdd']['minor']=99
            links=[dict(target='/dev/vdd',serial_prefix=prefix,basename='virtio-'+prefix,link_stable=not unstable)]
            if duplicate:links.append(copy.deepcopy(links[0]))
            if other:links.append(dict(target='/dev/vda',serial_prefix=prefix,basename='other',link_stable=True))
            after=copy.deepcopy(links)
            if linkdrift:after.append({'target':'/dev/vda'})
            actual=n['correlate_disk'](a,b,links,after)
            self.assertEqual(actual['state']=='CORRELATED',not any((drift,linkdrift,sizebad,partition,serialbad,duplicate,other,unstable)))
            if actual['state']=='CORRELATED':self.assertIs(actual['content_or_format_permission'],False)
            count+=1
        COUNTS['disk_cases']=count

    def test_settle_independent_conjunction(self):
        n=self.ns;count=0
        for bits in itertools.product((False,True),repeat=7):
            args=list(settle_fixture(n)); frames,comparison,ca,cb,ma,mb=args
            boot,jobs,cloud,graph,consumer,config,backing=bits
            if boot:frames[2]['boot_id_after']='different'
            if jobs:frames[1]['jobs']=[{'unit':'pending.service'}]
            if cloud:frames[3]['units']['cloud-final.service']['ExecMainStatus']='1'
            if graph:comparison['same_as_before']=False
            if consumer:frames[2]['units'][n['UV']]['InvocationID']='c'*32
            if config:cb['files'][0]['sha256']='b'*64
            if backing:mb[0]['source']='/dev/other'
            out=n['settle'](*args)
            self.assertEqual(out['state']=='SAMPLED_SETTLED',not any(bits)); self.assertIs(out['mutation_or_global_health_permission'],False)
            count+=1
        COUNTS['settle_cases']=count

    def test_missing_fstab_cannot_be_settled(self):
        args=list(settle_fixture(self.ns));args[2]['fstab_sha256']=None;args[3]['fstab_sha256']=None
        self.assertNotEqual(self.ns['settle'](*args)['state'],'SAMPLED_SETTLED','missing fstab is UNKNOWN, not proven stable')

    def test_typed_shape_secrets_and_scalar_boundaries(self):
        n=self.ns; signature='a(sasbttttuii)'; secret='Authorization=VERY_PRIVATE_SECRET'
        row=['/usr/bin/install',['install',secret],False,0,2**64-1,0,1,2**32-1,-2**31,2**31-1]
        def call(rows):return n['parse_exec_pre'](json.dumps(dict(type=signature,data=[rows])))
        actual=call([row]);self.assertNotIn(secret,json.dumps(actual));self.assertTrue(actual['argv_omitted'])
        self.assertTrue(call([])['empty'])
        bad=[]
        for i,value in ((2,0),(3,-1),(3,2**64),(4,True),(7,-1),(7,2**32),(8,-2**31-1),(9,2**31)):
            x=copy.deepcopy(row);x[i]=value;bad.append([x])
        bad.extend(([row[:-1]], [row]*17))
        for value in bad:
            with self.assertRaises((ValueError,TypeError)):call(value)
        for value in (dict(type=signature,data=[]),dict(type='s',data=[[]]),dict(type=signature,data=[[]],extra=secret)):
            with self.assertRaises(ValueError):n['parse_exec_pre'](json.dumps(value))
        COUNTS['typed_negative_cases']=len(bad)+3

    def test_acquisition_parse_separation_and_cached_status(self):
        n=self.ns;n['units']=lambda *a:{n['UV']:{'InvocationID':'a'*32}}
        def fail(*a):raise TimeoutError('DO_NOT_EMIT_PRIVATE_DETAIL')
        n['command']=fail; out=n['uv_startup']();self.assertEqual(out['failure']['phase'],'ACQUIRE')
        self.assertNotIn('DO_NOT_EMIT',json.dumps(out))
        n['command']=lambda *a:'bad-json';self.assertEqual(n['uv_startup']()['failure']['phase'],'PARSE')
        n['command']=lambda *a:json.dumps(dict(type='a(sasbttttuii)',data=[[]]))
        out=n['uv_startup']();self.assertIs(out['error_attribution_verified'],False)
        self.assertEqual(out['status_scope'],'LAST_REPORTED_COMMAND_EXECUTION')

    def test_mutation_sensitivity(self):
        # Code-level mutants of actual functions, checked against independent cases.
        n=self.ns;text=(ROOT/'identity_helpers.py').read_text(encoding='utf-8');caught=[]
        mutations=[('disk drift bypass',"if before!=after or links_before!=links_after:","if False:"),
                   ('jobs bypass',"empty=all(f['jobs']==[] for f in frames)","empty=True"),
                   ('edge delta reversal',"sorted(b[field]-a[field])","sorted(a[field]-b[field])")]
        for label,old,new in mutations:
            self.assertIn(old,text); mutant=load();exec(compile(text.replace(old,new,1),'<mutant>','exec'),mutant)
            if label=='jobs bypass':
                args=list(settle_fixture(mutant));args[0][0]['jobs']=[{}];detected=mutant['settle'](*args)['state']=='SAMPLED_SETTLED'
            elif label=='edge delta reversal':
                a=encode(mutant,set());b=encode(mutant,{('a.service','Requires','b.service')});detected=mutant['graph_recheck'](a,b)['delta']['edges']['added']==[]
            else:
                prefix=n['PROVIDER_DISK_ID'][:20];a={'vdd':dict(partition_marker_present=False,size_bytes=260000000000,major=1,minor=2,serial=dict(state='PRESENT',value=prefix))};b=copy.deepcopy(a);b['vdd']['minor']=3
                links=[dict(target='/dev/vdd',serial_prefix=prefix,basename='virtio-'+prefix,link_stable=True)]
                detected=mutant['correlate_disk'](a,b,links,links)['state']=='CORRELATED'
            self.assertTrue(detected,label);caught.append(label)
        COUNTS['code_mutants_detected']=caught

    def test_generated_definitions_and_manifest_pins(self):
        guest=ast.parse((ROOT/'diagnostic_guest.py').read_text(encoding='utf-8'))
        definitions={n.name:ast.dump(n,include_attributes=False) for n in guest.body if isinstance(n,ast.FunctionDef)}
        for filename in ('identity_helpers.py','identity_main.py'):
            for node in ast.parse((ROOT/filename).read_text(encoding='utf-8')).body:
                if isinstance(node,ast.FunctionDef):self.assertEqual(definitions[node.name],ast.dump(node,include_attributes=False))
        manifest=json.loads((ROOT/'manifest.json').read_text())
        for name,digest in manifest.items():self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest)
        driver=ast.parse((ROOT/'prepare_driver.py').read_text())
        pins={n.targets[0].id:ast.literal_eval(n.value) for n in driver.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='GUEST_SHA'}
        self.assertEqual(pins['GUEST_SHA'],manifest['diagnostic_guest.py'])
        self.assertIn('safe_unit_path(current)',ast.unparse(guest))
        self.assertIn('safe_unit_path(posixpath.join(current, *pending))',ast.unparse(guest))

    def test_guest_readonly_syntax_envelope_and_mutants(self):
        text=(ROOT/'diagnostic_guest.py').read_text(encoding='utf-8')
        def envelope(source):
            tree=ast.parse(source)
            forbidden={'write','write_bytes','write_text','unlink','remove','rename','replace','mkdir','makedirs','rmdir','chmod','chown','truncate','system','exec','eval','execv','execve','connect','send','sendall','bind','listen'}
            for node in ast.walk(tree):
                if isinstance(node,ast.Attribute) and node.attr in ('O_WRONLY','O_RDWR','O_CREAT','O_TRUNC','O_APPEND'):raise ValueError('WRITE_FLAG')
                if not isinstance(node,ast.Call):continue
                fn=node.func;name=fn.attr if isinstance(fn,ast.Attribute) else fn.id if isinstance(fn,ast.Name) else None
                if name in forbidden:raise ValueError('WRITE_OR_NETWORK')
                if name=='open':
                    if isinstance(fn,ast.Name):
                        if len(node.args)!=2 or not isinstance(node.args[1],ast.Constant) or node.args[1].value!='rb':raise ValueError('FILE_MODE')
                    elif ast.unparse(node.args[1])!='os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK':raise ValueError('FILE_FLAGS')
                if isinstance(fn,ast.Attribute) and isinstance(fn.value,ast.Name) and fn.value.id=='subprocess':
                    if fn.attr!='Popen' or ast.unparse(node.args[0])!='argv':raise ValueError('PROCESS_ESCAPE')
                if name=='command' and isinstance(node.args[0],ast.List):
                    argv=node.args[0].elts
                    if isinstance(argv[0],ast.Name):
                        if argv[0].id!='exe':raise ValueError('CLIENT_ESCAPE')
                        continue # Fixed signature_probe, additionally checked below.
                    first=ast.literal_eval(argv[0])
                    if first not in ('/usr/bin/systemctl','/usr/bin/busctl','/usr/bin/docker','/usr/bin/lsblk','/usr/bin/journalctl'):raise ValueError('CLIENT_ESCAPE')
                    if first=='/usr/bin/systemctl' and ast.literal_eval(argv[1]) not in ('show','list-jobs','list-units'):raise ValueError('UNIT_ACTION')
                    if first=='/usr/bin/docker' and ast.literal_eval(argv[1]) not in ('inspect','info'):raise ValueError('DOCKER_ACTION')
                    if first=='/usr/bin/busctl' and 'get-property' not in [x.value for x in argv if isinstance(x,ast.Constant)]:raise ValueError('DBUS_ACTION')
            return True
        self.assertTrue(envelope(text))
        mutants=['os.remove("x")','open("x","wb")','os.open("x",os.O_RDWR)','subprocess.run(["mount"])','command(["/usr/bin/systemctl","start","x"])']
        for mutant in mutants:
            with self.assertRaises(ValueError):envelope(text+'\n'+mutant)
        COUNTS['syntax_mutants_detected']=len(mutants)
        # Static pinned bounds are not evidence of runtime kernel enforcement.
        self.assertIn('signal.alarm(55)',text)
        self.assertIn("'--no-act'",text)
        driver=(ROOT/'prepare_driver.py').read_text()
        for marker in ("'60s'",'timeout=65','min(75,270-elapsed)'):self.assertIn(marker,driver)

    def test_generated_escaped_fragment_allowlist(self):
        safe=self.ns['safe_unit_path']
        for parent in ('generator','generator.early','generator.late'):
            path='/run/systemd/'+parent+r'/mnt-bridge\x2dscratch.mount'
            self.assertEqual(safe(path),path)
        for path in (r'/run/systemd/generator/../secret',r'/run/systemd/generator/evil\q.mount',r'/private/secret\x2d.mount'):
            self.assertEqual(safe(path),'REDACTED_PATH')

def main():
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(IndependentTests))
    report=dict(status='PASS' if result.wasSuccessful() else 'FAIL',tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),counts=COUNTS,
                sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ('identity_helpers.py','identity_main.py','build_guest.py','diagnostic_guest.py','prepare_driver.py')},
                scope='Independent bounded algorithms only; same-model reviewer I1. No OS, provider, disk-content, Drive durability or deployment proof.',log=stream.getvalue())
    (ROOT/'INDEPENDENT-IDENTITY-RESULT.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2));return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
