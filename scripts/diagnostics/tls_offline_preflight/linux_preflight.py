"""Proposed Linux-only offline test. Does not authorize or perform a TLS probe."""
import sys
if sys.platform!='linux':
    print('{"schema":"tls-offline-process-preflight-v1","status":"NOT_RUN_PLATFORM"}')
    raise SystemExit(2)
import hashlib,importlib.util,json,os,subprocess,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
WORKER=ROOT/'worker.py'
PYTHON=str(Path(sys.executable).resolve())
EXPECTED_PROBE='df1c9efb51063f76abad196e223b15b74b22e4cd23ab2b30fb6e7a460dbdd530'
CHILDREN=[]
REAL_POPEN=subprocess.Popen
ACTIVE_MODE=None
def audit(event,args):
    if event.startswith(('socket.','ctypes.','os.exec','os.spawn','os.posix_spawn')) or event in ('os.fork','os.system'):
        raise RuntimeError('offline_only')
    if event=='subprocess.Popen':
        expected=[PYTHON,'-I','-S','-B',str(WORKER),ACTIVE_MODE]
        if type(args[1]) is not list or args[1]!=expected or ACTIVE_MODE not in ('success','hang','malformed','late_exit'):
            raise RuntimeError('fixture_process_only')
sys.addaudithook(audit)
def isolated_context(previous):
    status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    assert os.getuid()!=0 and os.geteuid()==os.getuid() and os.getgroups()==[]
    assert os.getpid()==1  # namespace init: its termination also ends the fixed worker
    assert os.readlink('/proc/self/ns/net')!=previous
    # /proc/net is tied to this process's current network namespace; sysfs need not be.
    devices=Path('/proc/net/dev').read_text().splitlines()
    assert [line.split(':',1)[0].strip() for line in devices[2:]]==['lo']
    routes=[line.split() for line in Path('/proc/net/route').read_text().splitlines() if line.strip()]
    assert routes in ([],[['Iface','Destination','Gateway','Flags','RefCnt','Use','Metric','Mask','MTU','Window','IRTT']])
    assert not Path('/proc/net/if_inet6').read_text().strip()
    assert status['NoNewPrivs'].strip()=='1'
    assert all(int(status[key],16)==0 for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))
    assert dict(os.environ)=={'LC_ALL':'C.UTF-8'}
def absent_group(pid):
    try:os.killpg(pid,0)
    except ProcessLookupError:return True
    return False
def fixture_popen(argv,**kwargs):
    assert len(argv)==7 and argv[:4]==[sys.executable,'-I','-S','-B']
    assert argv[4]==str(ROOT/'probe.py')
    assert argv[5]=='--internal-worker'
    assert kwargs['env']=={'LC_ALL':'C','LANG':'C'}
    assert kwargs['start_new_session'] is True and kwargs['close_fds'] is True
    assert kwargs['stdin']==kwargs['stderr']==subprocess.DEVNULL
    assert kwargs['stdout']==subprocess.PIPE
    p=REAL_POPEN([PYTHON,'-I','-S','-B',str(WORKER),ACTIVE_MODE],**kwargs)
    CHILDREN.append(p)
    return p
def main(previous):
    global ACTIVE_MODE
    isolated_context(previous)
    assert hashlib.sha256((ROOT/'probe.py').read_bytes()).hexdigest()==EXPECTED_PROBE
    spec=importlib.util.spec_from_file_location('offline_probe_fixture',ROOT/'probe.py')
    probe=importlib.util.module_from_spec(spec);sys.modules[spec.name]=probe;spec.loader.exec_module(probe)
    assert probe.LIVE_RELEASE_ENABLED is False
    rows=[]
    for mode in ('success','hang','malformed','late_exit'):
        ACTIVE_MODE=mode
        start=time.monotonic()
        # Only in this test module; the unchanged probe source remains closed.
        # Every launch is redirected to the fixed network-denied fixture.
        with patch.object(probe,'LIVE_RELEASE_ENABLED',True),patch.object(probe.subprocess,'Popen',fixture_popen):
            result=probe.supervise(launch=probe.PosixChild,clock=time.monotonic)
        assert probe.LIVE_RELEASE_ENABLED is False
        assert len(CHILDREN)==len(rows)+1
        child=CHILDREN[-1]
        reaped=child.poll() is not None
        gone=absent_group(child.pid)
        complete=result['counters_complete']
        expected={'success':'tls_success','hang':'dns_supervisor_timeout','malformed':'worker_protocol_failure','late_exit':'tls_handshake_supervisor_timeout'}[mode]
        ok=result['status']==expected and result['worker_reaped'] and reaped and gone and complete==(mode=='success')
        rows.append({'fixture':mode,'pass':ok,'direct_child_reaped':reaped,'group_absent':gone,'elapsed_ms':round((time.monotonic()-start)*1000)})
        if not ok:break
    assert probe.LIVE_RELEASE_ENABLED is False
    return {'schema':'tls-offline-process-preflight-v1','status':'PASS' if len(rows)==4 and all(x['pass']for x in rows) else 'FAIL',
            'fixtures':rows,'real_fixture_processes':len(CHILDREN),'synthetic_protocol_only':True,
            'live_dns_calls':0,'live_connections':0,'tls_verified':False,'auth_verified':False,'production_gate_enabled':False}
if __name__=='__main__':
    output={'schema':'tls-offline-process-preflight-v1','status':'FAIL','tls_verified':False,'production_gate_enabled':False}
    try:
        assert len(sys.argv)==2
        output=main(sys.argv[1])
    except BaseException:pass
    finally:
        # Unexpected fixture failure cannot leave its own child running.
        for child in CHILDREN:
            if child.poll() is None:
                try:os.killpg(child.pid,9)
                except ProcessLookupError:pass
                try:child.wait(timeout=1)
                except BaseException:output['status']='FAIL'
    print(json.dumps(output,sort_keys=True,separators=(',',':')))
    raise SystemExit(0 if output['status']=='PASS' else 1)
