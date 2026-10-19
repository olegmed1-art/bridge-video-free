"""Actual execution/session definitions with synthetic IO; no PID1 proof here.

The production readback parser, pairing, clock, first-entry and run_once code
execute unchanged. Only filesystem, clock, systemctl/proc and external legacy
ports are synthetic; disposable Linux PID1 evidence is a separate fixture.
"""
import ast
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from ops import light_native_service_unit as real_unit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'f82d58efabf21ba62fd242a4fa02a8e7cfda1d23'
BOOT = '12345678-1234-1234-1234-123456789abc'
NANO = 10**9


def require(ok, code):
    if not ok:raise RuntimeError(code)


def actual(path, namespace, *, names=None):
    module=ModuleType('_actual_session_contract')
    module.__dict__.update(namespace)
    nodes=[]
    for node in ast.parse((ROOT/path).read_bytes()).body:
        if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and (names is None or node.name in names):
            nodes.append(node)
        elif isinstance(node,ast.Assign) and (names is None or any(
                isinstance(target,ast.Name) and target.id in names for target in node.targets)):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),path,'exec'),module.__dict__)
    return module


@dataclass(frozen=True)
class Prior:
    hostname: str='autopilot-lite-vnic'
    pid: int=55
    invocation_id: str='a'*32
    release: str='/synthetic-old-release'
    fingerprint: str='synthetic-private'


class Fixture:
    def __init__(self):
        self.events=[];self.files={};self.writes=[]
        self.wall=1800000000*NANO+NANO;self.mono=101*NANO;self.boot=BOOT
        self.show_rows=[];self.proc_rows=[];self.write_fault=None
        self.prior=Prior()
        self.original=dict(ActiveState='active',SubState='running',MainPID='55',InvocationID='a'*32,NRestarts='0')
        self.clock=SimpleNamespace(time_ns=lambda:self.wall,monotonic_ns=lambda:self.mono,
            time=lambda:self.wall/NANO,monotonic=lambda:self.mono/NANO,sleep=lambda _:None)
        install=SimpleNamespace(require=require,RETAINED_SOURCE=SOURCE,LAUNCH='synthetic launch',
            root_parent=lambda _:None,write_new=self.write,
            hold=SimpleNamespace(HoldIdentity=Prior,read=self.read,service_hold_identity=lambda:self.prior),
            release=SimpleNamespace(source=SimpleNamespace(identifier=lambda value,n:
                type(value) is str and re.fullmatch('[0-9a-f]{'+str(n)+'}',value) is not None),
                staging=SimpleNamespace(require_current_main=lambda _:self.events.append('main'))))
        switch=SimpleNamespace(duration_seconds=lambda raw: sum(int(n)*{'s':1,'min':60}[unit]
                for n,unit in re.findall(r'(\d+)(min|s)',raw)))
        helper=SimpleNamespace(_process=self.process,_read_command=self.show)
        os_port=SimpleNamespace(getpid=lambda:4242,geteuid=lambda:0,
            uname=lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
        self.e=actual('ops/light_native_lane_execution.py',dict(asdict=asdict,hashlib=hashlib,json=json,
            os=os_port,Path=Path,re=re,stat=stat,time=self.clock,install=install,switch=switch,
            pwd=SimpleNamespace(getpwnam=lambda _:SimpleNamespace(pw_uid=0,pw_gid=0)),unit_readback=helper,
            plan=SimpleNamespace(PYTHON='/usr/bin/python3',source_path=lambda source:Path('/release')/source,
                                 pilot_properties=lambda *args,**kwargs:{})))
        self.e._boot_id=lambda:self.boot
        self.e._session_read=self.read
        self.value=dict(version=3,source=SOURCE,controller_source='d'*40,seconds=120,
            expires_at=1800000120,sequence=0,prior=asdict(self.prior),protected={},protected_sha256='e'*64,
            cursor_sha256='f'*64,dispatch_id='synthetic-dispatch',original=self.original,
            supervisor_sha256='',owner_context_sha256='',python_sha256='9'*64,
            clock=dict(boot_id=BOOT,wall_ns=1800000000*NANO,monotonic_start_ns=100*NANO,
                       monotonic_end_ns=220*NANO,run_guard_deadline_ns=230*NANO))
        self.context=dict(owner_plan_sha256='1'*64,controller_sha256='2'*64,wheels_sha256='3'*64,
                          run_id=123,attempt=1)
        self.script=b'# synthetic reviewed supervisor bytes\n'
        self.bind()

    def bind(self):
        e=self.e
        context=e.encoded(self.context)
        self.value.update(supervisor_sha256=hashlib.sha256(self.script).hexdigest(),
                          owner_context_sha256=hashlib.sha256(context).hexdigest())
        raw=e.encoded(self.value);self.digest=hashlib.sha256(raw).hexdigest()
        self.directory=e.ROOT/self.digest
        self.pointer=e.OWNER_ROOT/self.context['owner_plan_sha256']/'execution.json'
        self.files={self.directory/'request.json':raw,self.directory/'supervisor.py':self.script,
                    self.directory/'owner-context.json':context,
                    self.pointer:e.encoded(dict(request_sha256=self.digest))}
        argv=('/usr/bin/python3','-I','-S','-B',str(self.directory/'supervisor.py'),'run',self.digest)
        self.identity=real_unit.ProcessSnapshot(4242,0,0,1,10000,BOOT,'9'*64,argv,
            '0::/system.slice/'+e.SUPERVISOR,'/','b'*32)
        def command(arguments,pid):
            return ('{ path=/usr/bin/python3 ; argv[]='+' '.join(arguments)+
                ' ; ignore_errors=no ; start_time='+('[Mon 2026-10-19 12:00:00 UTC]' if pid else '[n/a]')+
                ' ; stop_time=[n/a] ; pid='+str(pid)+' ; code=(null) ; status=0/0 }')
        self.loaded=dict(LoadState='loaded',ActiveState='active',SubState='running',MainPID='4242',
            InvocationID='b'*32,NRestarts='0',Transient='yes',NeedDaemonReload='no',DropInPaths='',
            User='root',Group='root',Restart='no',NoNewPrivileges='yes',KillMode='control-group',
            ExecStart=command(argv,4242),ExecStopPost=command((*argv[:-2],'restore',self.digest),0),
            RuntimeMaxUSec='110s',TimeoutStopUSec='2min',ControlGroup='/system.slice/'+e.SUPERVISOR,
            ControlPID='0',Type='exec',RuntimeRandomizedExtraUSec='0',ActiveEnterTimestampMonotonic='100500000')

    def read(self,path,expected=None,legacy_limit=None,*,limit=262144,mode=0o600):
        # install.hold.read passes (path, mode, limit), _session_read uses a pin.
        if type(expected) is int:expected=None
        if legacy_limit is not None:limit=legacy_limit
        raw=self.files[path]
        require(0<len(raw)<=limit and (expected is None or hashlib.sha256(raw).hexdigest()==expected),
                'SYNTHETIC_PROTECTED_READ')
        return raw

    def write(self,path,raw,mode):
        self.events.append('write:'+path.name)
        if path in self.files:raise FileExistsError()
        self.files[path]=raw;self.writes.append(path.name)
        if self.write_fault==path.name:raise RuntimeError('SYNTHETIC_LOST_ACK')

    def show(self,command,*,deadline,limit):
        self.events.append('show')
        require(command==('/usr/bin/systemctl','--no-pager','show','--all',self.e.SUPERVISOR,
            '--property='+','.join(self.e._SESSION_PROPERTIES)) and limit==65536,
            'SYNTHETIC_COMMAND_SCOPE')
        require(self.mono/NANO<deadline,'SYNTHETIC_COMMAND_DEADLINE')
        row=self.show_rows.pop(0) if self.show_rows else self.loaded
        return row if type(row) is bytes else ''.join(k+'='+v+'\n' for k,v in row.items()).encode('ascii')

    def process(self,pid):
        self.events.append('proc')
        require(pid==4242,'SYNTHETIC_PROC_SELECTOR')
        return self.proc_rows.pop(0) if self.proc_rows else self.identity

    def enter(self):
        return self.e.ExecutionSession.enter(self.digest)

    def runtime(self):
        """External legacy/service ports only; session implementation is real."""
        e=self.e;install=e.install
        install.UNIT='synthetic-native.service'
        install.CONTROL=Path('/control');install.UNIT_FILE=Path('/native.service')
        install.STATE=SimpleNamespace(lstat=lambda:SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0))
        e.os.listdir=lambda _:['pilot.lock']
        install.render=lambda _:b'fixed native unit'
        self.files[install.UNIT_FILE]=install.render(SOURCE)
        self.files[install.CONTROL/'admission']=b'HOLD\n'
        install.verify_unit=lambda _:self.events.append('native-unit')
        install.show=lambda unit,fields:{key:self.original[key] for key in fields}
        install.verify_hold_process=lambda *args:deepcopy(self.original)
        install.stopped=lambda:self.events.append('native-drained')
        e.switch.unit_absent=lambda _:self.events.append('worker-absent')
        e.switch.unchanged_files=lambda *args:self.events.append('legacy-protected')
        e.arm=lambda *args:self.events.append('arm')
        e.feed=lambda _:SimpleNamespace(check=lambda:self.events.append('permit'))
        e.owned=lambda *args:True
        e.process=lambda _:dict(ActiveState='active',SubState='running',MainPID='777',InvocationID='c'*32,NRestarts='0')
        lines=iter([['{"audit":"LIGHT_NATIVE_LANE","state":"HOLD"}'],
            ['{"audit":"LIGHT_NATIVE_LANE","state":"AWAITING_OWNER","terminal_sha256":"'+'a'*64+'"}']])
        e.journal=lambda _:next(lines)
        def admission(value):
            self.events.append(value);self.files[install.CONTROL/'admission']=value
        e.admission=admission
        e.switch.command=lambda *args:self.events.append(args)
        self.live=lambda:self.events.append('gha-live')


class ExecutionSessionContracts(unittest.TestCase):
    def test_first_binding_is_durable_before_run_once_effects(self):
        f=Fixture();f.runtime();session=f.enter()
        result=f.e.run_once(f.digest,lambda _:True,live_guard=f.live,session=session)
        self.assertEqual(result['state'],'READBACK_REQUIRED')
        first=f.events.index('write:session-first.json')
        stop=f.events.index(('/usr/bin/systemctl','stop',f.e.install.UNIT)) if hasattr(f.e.install,'UNIT') else -1
        self.assertGreater(stop,first)
        self.assertLess(f.events.index('gha-live'),stop)
        self.assertIn(b'RUN\n',f.events)
        self.assertIn('launch-intent.json',f.writes)

    def test_reentry_and_new_invocation_never_replace_first_binding(self):
        for changed in (False,True):
            f=Fixture();f.enter();before=deepcopy(f.files)
            if changed:f.identity=replace(f.identity,start_ticks=10001,invocation_id='c'*32)
            with self.subTest(changed=changed),self.assertRaisesRegex(RuntimeError,'REENTRY'):
                f.enter()
            self.assertEqual(f.files,before)

    def test_partial_intent_or_orphan_first_is_a_replay_barrier(self):
        for name in ('session-intent.json','session-first.json'):
            f=Fixture();f.files[f.directory/name]=b'{'  # durable partial attempt
            with self.subTest(name=name),self.assertRaises((RuntimeError,FileExistsError)):
                f.enter()
            self.assertEqual(f.files[f.directory/name],b'{')

    def test_lost_write_ack_retains_barrier_and_cannot_retry(self):
        for name in ('session-intent.json','session-first.json'):
            f=Fixture();f.write_fault=name
            with self.subTest(name=name),self.assertRaisesRegex(RuntimeError,'LOST_ACK'):
                f.enter()
            before=deepcopy(f.files);f.write_fault=None
            with self.assertRaisesRegex(RuntimeError,'REENTRY'):f.enter()
            self.assertEqual(f.files,before)

    def test_worker_start_lost_ack_keeps_intent_and_cleanup_ignores_closed_session(self):
        f=Fixture();f.runtime();session=f.enter()
        def lost(*args):
            f.events.append(args)
            if args[0]=='/usr/bin/systemd-run':raise RuntimeError('SYNTHETIC_WORKER_ACK_LOST')
        f.e.switch.command=lost
        with self.assertRaisesRegex(RuntimeError,'WORKER_ACK_LOST'):
            f.e.run_once(f.digest,lambda _:True,live_guard=f.live,session=session)
        self.assertIn('launch-intent.json',f.writes)
        self.assertNotIn(b'RUN\n',f.events)
        first=f.files[f.directory/'session-first.json']
        session.failed=True;f.mono=1000*NANO;f.wall=1900000000*NANO
        f.e.stopped=lambda:f.events.append('worker-drained')
        f.e.install.release.retain=lambda path,raw:f.files.setdefault(path,raw)
        f.e.ExecutionSession.enter=lambda _:(_ for _ in ()).throw(AssertionError('cleanup cannot enter session'))
        result=f.e.restore(f.digest)
        self.assertEqual(result['state'],'ORIGINAL_HOLD')
        self.assertEqual(f.files[f.directory/'session-first.json'],first)
        self.assertLess(f.events.index(b'HOLD\n'),f.events.index(('/usr/bin/systemctl','stop',f.e.UNIT)))

    def test_original_monotonic_end_is_not_rebased_by_wall_rollback(self):
        f=Fixture();session=f.enter();end=session.monotonic_end_ns
        f.wall-=NANO;f.mono=end
        with self.assertRaisesRegex(RuntimeError,'SESSION_CLOSED'):session.assert_current()
        self.assertEqual(session.monotonic_end_ns,end)
        self.assertTrue(session.failed)

    def test_wall_monotonic_and_boot_drift_close_stickily(self):
        for change in ('wall','mono','boot','expired'):
            f=Fixture();session=f.enter()
            if change=='wall':f.wall-=1
            if change=='mono':f.mono-=1  # still greater than original start
            if change=='boot':f.boot='f'*8+'-1234-1234-1234-123456789abc'
            if change=='expired':f.mono=session.monotonic_end_ns
            with self.subTest(change=change),self.assertRaisesRegex(RuntimeError,'SESSION_CLOSED'):
                session.assert_current()
            f.wall+=NANO;f.mono=102*NANO;f.boot=BOOT
            with self.assertRaisesRegex(RuntimeError,'SESSION_CLOSED'):session.assert_current()

    def test_clock_shape_refuses_renewal_and_typed_aliases(self):
        for key,item in (('monotonic_end_ns',221*NANO),('monotonic_start_ns',True),
                         ('run_guard_deadline_ns',219*NANO),('wall_ns',1800000121*NANO)):
            f=Fixture();f.value['clock'][key]=item;f.bind()
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'SESSION_CLOCK'):f.enter()
            self.assertFalse(f.writes)

    def test_old_requests_are_restore_only(self):
        for version in (1,2):
            f=Fixture();f.value.pop('clock');f.value.pop('python_sha256');f.value['version']=version
            if version==1:f.value.pop('sequence')
            else:f.value['sequence']=1
            f.bind()
            with self.subTest(version=version),self.assertRaisesRegex(RuntimeError,'LANE_EXEC_REQUEST'):
                f.e.request(f.digest)
            value,_,_=f.e.request(f.digest,for_restore=True)
            self.assertEqual(value['version'],version)

    def test_original_inputs_and_parent_pointer_remain_bound(self):
        for name in ('request.json','supervisor.py','owner-context.json','pointer','session-intent.json','session-first.json'):
            f=Fixture();session=f.enter()
            path=f.pointer if name=='pointer' else f.directory/name
            f.files[path]=b'{}'
            with self.subTest(name=name),self.assertRaisesRegex(RuntimeError,'SESSION_CLOSED'):
                session.assert_current()

    def test_wrong_valid_parent_pointer_and_host_context_refuse(self):
        f=Fixture();f.files[f.pointer]=f.e.encoded(dict(request_sha256='f'*64))
        with self.assertRaisesRegex(RuntimeError,'SESSION_POINTER'):f.enter()
        f=Fixture();f.context['kind']='HOST';f.bind()
        with self.assertRaisesRegex(RuntimeError,'SESSION_CONTEXT'):f.enter()
        self.assertFalse(f.writes)

    def test_each_equal_but_wrong_proc_pair_refuses_policy(self):
        changes=dict(uid=1,gid=1,no_new_privs=0,pid=999,start_ticks=0,boot_id='f'*36,
            exe_sha256='8'*64,argv=('/foreign',),cgroup='0::/foreign',cwd='/foreign',invocation_id='wrong')
        for key,value in changes.items():
            f=Fixture();f.identity=replace(f.identity,**{key:value})
            with self.subTest(key=key),self.assertRaises(RuntimeError):f.enter()
            self.assertNotIn('session-first.json',f.writes)

    def test_pid_reuse_after_first_binding_closes(self):
        f=Fixture();session=f.enter();f.identity=replace(f.identity,start_ticks=10001)
        with self.assertRaisesRegex(RuntimeError,'SESSION_CLOSED'):session.assert_current()

    def test_loaded_unit_cannot_substitute_for_independent_process(self):
        for key,value in dict(MainPID='999',InvocationID='c'*32,NRestarts='1',Transient='no',
            NeedDaemonReload='yes',DropInPaths='/run/foreign.conf',User='nobody',Group='nobody',
            Restart='always',NoNewPrivileges='no',ControlGroup='/foreign',ControlPID='999').items():
            f=Fixture();f.loaded[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):f.enter()
            self.assertNotIn('session-first.json',f.writes)

    def test_pair_drift_and_final_postcollector_clock_fail_closed(self):
        for change in ('loaded','proc','time'):
            f=Fixture()
            if change=='loaded':
                changed=deepcopy(f.loaded);changed['InvocationID']='c'*32
                f.show_rows=[f.loaded,changed]
            elif change=='proc':f.proc_rows=[f.identity,replace(f.identity,start_ticks=10001)]
            else:
                prior=f.e.unit_readback._process
                def advanced(pid):
                    result=prior(pid);f.mono=220*NANO;return result
                f.e.unit_readback._process=advanced
            with self.subTest(change=change),self.assertRaises(RuntimeError):f.enter()
            self.assertNotIn('session-first.json',f.writes)

    def test_timer_origin_and_randomization_cannot_extend_original_end(self):
        for key,value in (('RuntimeMaxUSec','120s'),('RuntimeMaxUSec','29s'),
            ('ActiveEnterTimestampMonotonic','110000001'),('ActiveEnterTimestampMonotonic','099000000'),
            ('ActiveEnterTimestampMonotonic','0'),('Type','notify'),('RuntimeRandomizedExtraUSec','1s')):
            f=Fixture();f.loaded[key]=value
            with self.subTest(key=key,value=value),self.assertRaisesRegex(RuntimeError,'SESSION_TIMER'):f.enter()
        f=Fixture();f.loaded['RuntimeMaxUSec']='119s';f.loaded['ActiveEnterTimestampMonotonic']='101000000'
        f.enter()  # exact original end, no extension

    def test_loaded_timer_start_and_accounting_remain_first_bound(self):
        for key,value in (('RuntimeMaxUSec','109s'),('ActiveEnterTimestampMonotonic','100600000'),
            ('ExecStart','foreign command')):
            f=Fixture();session=f.enter();f.loaded[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'SESSION_CLOSED'):session.assert_current()

    def test_fixed_show_parser_refuses_partial_duplicate_unknown_and_oversize(self):
        f=Fixture();raw=f.show(('/usr/bin/systemctl','--no-pager','show','--all',f.e.SUPERVISOR,
            '--property='+','.join(f.e._SESSION_PROPERTIES)),deadline=200,limit=65536)
        for bad in (raw.replace(b'DropInPaths=\n',b''),raw+b'MainPID=4242\n',raw+b'Foreign=1\n',
                    raw.rstrip(b'\n'),b'a'*65537):
            f.show_rows=[bad]
            with self.subTest(size=len(bad)),self.assertRaises(RuntimeError):f.e._session_show(200)

    def test_supervisor_command_uses_conservative_original_remaining(self):
        f=Fixture();clock=f.value['clock'];f.mono=110*NANO+NANO//2
        command=f.e.supervisor_command(f.digest,120,clock=clock)
        self.assertIn('--property=RuntimeMaxSec=104',command)
        self.assertIn('--property=RuntimeRandomizedExtraSec=0',command)
        f.mono=clock['monotonic_end_ns']-30*NANO
        with self.assertRaisesRegex(RuntimeError,'SESSION_TIMER'):
            f.e.supervisor_command(f.digest,120,clock=clock)

    def test_capture_requires_github_origin_and_preserves_guard_ceiling(self):
        f=Fixture()
        class LaneRunBinding:
            deadline=112.9
            def assert_running(self):f.events.append('origin-authenticated')
        guard=LaneRunBinding()
        fake=SimpleNamespace(LaneRunBinding=LaneRunBinding)
        binary=Path('/synthetic-python');f.files[binary]=b'actual-binary-fixture'
        with patch.dict(sys.modules,{'ops.light_native_lane_run_guard':fake}), \
             patch.object(f.e,'Path',return_value=SimpleNamespace(resolve=lambda strict:binary)):
            clock,digest=f.e.capture_clock(120,1800000120,guard)
            self.assertEqual(clock['monotonic_end_ns'],int(guard.deadline*NANO))
            self.assertEqual(digest,hashlib.sha256(f.files[binary]).hexdigest())
            self.assertIn('origin-authenticated',f.events)
            for wrong in (SimpleNamespace(deadline=112.9,assert_running=lambda:None),None):
                with self.assertRaisesRegex(RuntimeError,'SESSION_ORIGIN'):
                    f.e.capture_clock(120,1800000120,wrong)

    def test_driver_import_is_after_session_and_github_authentication(self):
        # Actual supervisor body, synthetic imports. A rejected GHA must never
        # enter the driver context; restore must not construct a session.
        f=Fixture();session=f.enter();events=[]
        f.e.ExecutionSession.enter=lambda digest:events.append('session') or session
        e=f.e;e.restore=lambda digest:events.append('restore') or dict(state='STOPPED_HOLD')
        imports={'ops.native_maintenance_owner_host':SimpleNamespace(loaded_runtime=lambda _:events.append('driver')),
            'ops.light_native_lane_run_guard':SimpleNamespace(authenticated=lambda *args:(_ for _ in ()).throw(RuntimeError('GHA_REFUSED'))),
            'ops.light_native_pilot_owner':SimpleNamespace(observed_target=lambda *args:None),
            'ops.native_maintenance_run_guard':SimpleNamespace(API=object)}
        f.files[Path('/owner')/f.context['owner_plan_sha256']/'wheels.tar']=b'wheels'
        c=actual('ops/light_native_lane_controller.py',dict(execution=e,encoded=e.encoded,parse=e.parse,
            release=e.install.release,
            read=lambda path,*args:f.files[path],verify_helpers=lambda *args:events.append('helpers'),
            ROOT=Path('/owner'),require=require,json=json,sys=SimpleNamespace(stdin=SimpleNamespace(
                buffer=SimpleNamespace(read=lambda _:e.encoded(dict(credential='private',token='private')))))),
            names={'supervisor'})
        with patch.dict(sys.modules,imports):
            with self.assertRaisesRegex(RuntimeError,'GHA_REFUSED'):c.supervisor('run',f.digest)
        self.assertEqual(events,['session','helpers'])
        with redirect_stdout(io.StringIO()):c.supervisor('restore',f.digest)
        self.assertEqual(events[-1],'restore')

    def test_actual_launch_retains_v3_clock_and_pointer_before_transport(self):
        f=Fixture();e=f.e;events=[]
        f.wall+=NANO//2
        del f.files[f.pointer]  # launch has no preceding execution attempt
        class LaneRunBinding:
            deadline=500.0
            run_id=123
            attempt=1
            def assert_running(self):events.append('gha-live')
        guard=LaneRunBinding();binary=Path('/synthetic-python');f.files[binary]=b'binary'
        directory=e.OWNER_ROOT/f.context['owner_plan_sha256']
        f.files[directory/'permit.json']=e.encoded(dict(expires_at=1800001000))
        e.install.fresh_directory=lambda *args:events.append('directory')
        e.install.verify_hold_process=lambda *args:deepcopy(f.original)
        def retain(path,raw):f.write(path,raw,0o600)
        def transport(command,**kwargs):
            events.append('transport')
            pointer=e.parse(f.files[directory/'execution.json'])
            root=e.ROOT/pointer['request_sha256']
            raw=f.files[root/'request.json'];value=e.parse(raw)
            self.assertEqual(hashlib.sha256(raw).hexdigest(),pointer['request_sha256'])
            self.assertEqual(value['version'],3)
            self.assertEqual(value['clock']['run_guard_deadline_ns'],500*NANO)
            self.assertLessEqual(value['clock']['monotonic_end_ns'],500*NANO)
            self.assertIn('--property=RuntimeMaxSec=114',command)
            self.assertEqual(e.parse(kwargs['input']),dict(credential='password-not-retained',token='token-not-retained'))
            f.files[root/'stopped-hold.json']=e.encoded(dict(state='STOPPED_HOLD'))
            return SimpleNamespace(returncode=0)
        c=actual('ops/light_native_lane_controller.py',dict(execution=e,install=e.install,encoded=e.encoded,
            parse=e.parse,sha=lambda raw:hashlib.sha256(raw).hexdigest(),asdict=asdict,time=f.clock,
            FIRST_EXECUTION_SECONDS=120,read=lambda path,*args:f.files[path],require=require,
            retain=retain,supervisor_source=lambda _:f.script,pwd=e.pwd,
            subprocess=SimpleNamespace(run=transport)),names={'launch','sequence'})
        with patch.dict(sys.modules,{'ops.light_native_lane_run_guard':SimpleNamespace(LaneRunBinding=LaneRunBinding)}), \
             patch.object(e,'Path',return_value=SimpleNamespace(resolve=lambda strict:binary)):
            result=c.launch(b'wheels','password-not-retained','token-not-retained',SimpleNamespace(digest=f.context['owner_plan_sha256']),
                directory,dict(version=1,source='d'*40,accepted_plan_sha256=f.context['owner_plan_sha256'],
                               accepted_controller_sha256='2'*64),f.prior,
                dict(protected={},protected_sha256='e'*64),dict(cursor_sha256='f'*64,dispatch_id='fixture'),guard)
        self.assertEqual(result['state'],'STOPPED_HOLD')
        self.assertEqual(events[0],'gha-live')
        self.assertEqual(events[-1],'transport')
        self.assertFalse(any(b'password-not-retained' in raw or b'token-not-retained' in raw for raw in f.files.values()))

    def test_all_six_historical_package_sets_remain_exact(self):
        release=actual('ops/light_native_pilot_release.py',dict(json=json),names={
            'SUPERVISOR_HELPERS','OWNER_HELPERS','EXTRA','HELPERS','MARKERS'})
        release.source=SimpleNamespace(identifier=lambda value,n:re.fullmatch('[0-9a-f]{'+str(n)+'}',value))
        e=Fixture().e
        c=actual('ops/light_native_lane_controller.py',dict(release=release,parse=e.parse,require=require,
            sha=lambda raw:hashlib.sha256(raw).hexdigest()),names={'EXTRA','RETIREMENT_HELPERS','validate_package'})
        expected=set((*release.HELPERS,*c.EXTRA));old=expected-{'ops/light_native_service_unit.py'}
        variants=(old,old-c.RETIREMENT_HELPERS,old-c.RETIREMENT_HELPERS-{'ops/light_native_lane_issuer.py'},
            old-c.RETIREMENT_HELPERS-{'ops/light_native_lane_cycle.py','ops/light_native_lane_issuer.py'},
            old-{'ops/light_native_lane_issuer.py'},old-{'ops/light_native_lane_cycle.py','ops/light_native_lane_issuer.py'})
        # Independently pinned from controller/release at baseline 6c4b8387;
        # changing current helper constants cannot redefine historical truth.
        expected_digests=(
            '616224c8e749552ead50a43f15413fc2a1837c69266ee33d0afc038303c99947',
            'c1712eef5c3da93de4d8192dda45d10aa91dab827f21a54bb58009ae3b25a9ec',
            '6fbaa9eddfa159445779d8c4649cca6e3a3b4e86e53ce9b78c408da439a39ca4',
            '9bab4047bfabe469862321b13e775afa77194a2f019f6fb789ca128dee13eb8c',
            'a6b3c8a1e398efd650ef1bf85e64ea9e125eaeb2289181d0a488e3ef9bba438b',
            'f2dd1cd5071074cff8c0ba41ca067742f40d7afa9eea49273bd2012cd5c5a4f8')
        self.assertEqual(tuple(hashlib.sha256(json.dumps(sorted(names),separators=(',',':')).encode()).hexdigest()
                               for names in variants),expected_digests)
        current=e.encoded(dict(version=1,kind='LIGHT_LANE_CONTROLLER',source='a'*40,
                              helpers=dict.fromkeys(expected,'# complete current package')))
        c.validate_package(current,'a'*40,hashlib.sha256(current).hexdigest())
        for names in variants:
            raw=e.encoded(dict(version=1,kind='LIGHT_LANE_CONTROLLER',source='a'*40,
                               helpers=dict.fromkeys(names,'# historical exact member')))
            with self.subTest(missing=sorted(expected-names)):
                c.validate_package(raw,'a'*40,hashlib.sha256(raw).hexdigest(),allow_legacy=True)
                with self.assertRaisesRegex(RuntimeError,'LANE_OWNER_PACKAGE'):
                    c.validate_package(raw,'a'*40,hashlib.sha256(raw).hexdigest())
                if names!=old:
                    hybrid=e.encoded(dict(version=1,kind='LIGHT_LANE_CONTROLLER',source='a'*40,
                        helpers=dict.fromkeys(names|{'ops/light_native_service_unit.py'},'# unreviewed hybrid')))
                    with self.assertRaisesRegex(RuntimeError,'LANE_OWNER_PACKAGE'):
                        c.validate_package(hybrid,'a'*40,hashlib.sha256(hybrid).hexdigest(),allow_legacy=True)


if __name__=='__main__':unittest.main()
