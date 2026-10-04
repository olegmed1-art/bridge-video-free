"""Read-only candidate; requires pinned staged core, no Store/worker invocation."""
import hashlib, json, os, re, signal, socket, stat, sys, time
from pathlib import Path
sys.dont_write_bytecode = True

CODE = Path('/nonexistent/synthetic-maint-v3-code/5d3f5fa2bf5f')
PIN = '99d30302f7d6f843b6feefd9704b762049524c7032474ce02d208c3db99af9d4'
UNIT = re.compile(r'[A-Za-z0-9_.@\\:-]{1,256}\Z')

def dependency_report(rows, targets, base):
    out = []
    checks = {
        'Requires': set(targets + base + ('docker.service',)),
        'Requisite': set(targets + base + ('docker.service',)),
        'Wants': set(targets + base + ('docker.service',)),
        **{k: set(targets) for k in ('BindsTo','PartOf','RequiredBy','RequisiteOf',
             'BoundBy','ConsistsOf','PropagatesStopTo','StopPropagatedFrom','TriggeredBy')},
        'Triggers': set(),
    }
    for unit in targets:
        row = rows[unit]
        for prop, allowed in checks.items():
            values = row.get(prop, '').split()
            if any(not UNIT.fullmatch(x) for x in values):
                out.append({'unit':unit,'property':prop,'error':'INVALID_UNIT_TOKEN'})
            else:
                out.append({'unit':unit,'property':prop,'actual':values,
                            'unexpected':sorted(set(values)-allowed), 'present':prop in row})
    return out

def runtime_inventory(g, guest, unit, row):
    from urllib.parse import urlsplit
    pid=int(row['MainPID']);assert pid>0
    env=dict(x.split(b'=',1) for x in guest.bounded('/proc/%d/environ'%pid).split(b'\0') if b'=' in x)
    out={'pid':pid}
    if unit in guest.TARGETS[:2]:
        parsed=urlsplit(env.get(b'ASSISTANT_LAB_DATABASE_URL',b'').decode())
        out['database_matches']=(parsed.hostname=='synthetic.invalid' and parsed.path=='/syntheticdb' and parsed.username=='synthetic_principal')
        key=b'ASSISTANT_LAB_WORKER_ID' if unit==guest.TARGETS[0] else b'ASSISTANT_LAB_CONTROL_BRIDGE_ID'
        ident=env.get(key,b'').decode() or (socket.gethostname()+':'+str(pid) if unit==guest.TARGETS[0] else 'oracle-control-bridge-'+socket.gethostname())
        out['effective_id']=ident if re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',ident) else 'INVALID'
    else:
        root=env.get(b'ASSISTANT_LAB_OBSERVER_STATE_ROOT',b'/nonexistent/synthetic-school/synthetic-lab-observer').decode()
        argv=guest.bounded('/proc/%d/cmdline'%pid).decode().split('\0')
        for i,arg in enumerate(argv):
            if arg=='--state-root':root=argv[i+1]
            if arg.startswith('--state-root='):root=arg.split('=',1)[1]
        out['expected_root_matches']=root==g.root
        if re.fullmatch(r'/nonexistent/synthetic-school/[A-Za-z0-9_./-]{1,180}',root) and '..' not in Path(root).parts:
            out['effective_root']=root
            directories={}
            for suffix in ('','jobs','jobs/pending','jobs/running'):
                p=Path(root)/suffix
                unsafe_ancestor=any(ancestor.is_symlink() for ancestor in reversed(p.parents))
                if unsafe_ancestor:
                    directories[suffix or '.']={'blocked':'SYMLINK_ANCESTOR'}
                    continue
                symlink=p.is_symlink()
                if symlink:
                    directories[suffix or '.']={'blocked':'SYMLINK_LEAF'}
                    continue
                item={'directory':p.is_dir(),'symlink':symlink}
                if p.is_dir() and not symlink and suffix in ('jobs/pending','jobs/running'):
                    count=0
                    with os.scandir(p) as entries:
                        for unused in entries:
                            count+=1
                            if count>=1000:break
                    item.update(entry_count_capped=count,empty=count==0)
                directories[suffix or '.']=item
            out['directories']=directories
        else:out['effective_root']='UNEXPECTED_PATH_REDACTED'
    group=row['ControlGroup']
    assert re.fullmatch(r'/system.slice/[A-Za-z0-9_.@\\:-]{1,256}',group)
    pids=set();count=0
    for f in (Path('/sys/fs/cgroup')/group.lstrip('/')).rglob('cgroup.procs'):
        count+=1;assert count<=64
        pids.update(int(x) for x in guest.bounded(f).split())
    out.update(cgroup_files=count,cgroup_process_count=len(pids),only_main_pid=pids=={pid})
    out['pid_still_matches']=int(g.command(['/usr/bin/systemctl','show','--value','--property=MainPID',unit]).strip())==pid
    return out

def main():
    signal.signal(signal.SIGALRM, signal.SIG_DFL); signal.alarm(55)
    assert socket.gethostname() == 'synthetic-compute' and os.geteuid() == 0
    h=hashlib.sha256()
    for parent in (CODE.parent,CODE):
        st=parent.lstat(); assert stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not(st.st_mode&0o022)
    for n in ('runner.py','protocol.py','durable.py','guest.py'):
        p=CODE/n;st=p.lstat()
        assert stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_nlink==1 and not(st.st_mode&0o022)
        raw=p.read_bytes();assert len(raw)<131072;h.update(n.encode()+b'\0'+raw)
    assert h.hexdigest()==PIN
    sys.path.insert(0,str(CODE))
    import guest, runner
    g=guest.Guest(time.monotonic()+50)
    failed=[]
    def probe(name, fn):
        start=time.monotonic()
        try: value={'ok':True,'value':fn()}
        except Exception as e:
            value={'ok':False,'reason':str(e) if isinstance(e,guest.Refused) else type(e).__name__}
            failed.append(name)
        value['elapsed_seconds']=round(time.monotonic()-start,3)
        print(json.dumps({'probe':name,**value},sort_keys=True),flush=True)
    def ledger():
        root=Path('/nonexistent/synthetic-maint-v3')
        result={}
        for relative in ('mutation-intent.json','synthetic-operation-002/baseline.json',
                         'synthetic-operation-002/stop-intent.json','synthetic-operation-002/complete.json'):
            p=root/relative
            if os.path.lexists(p):
                st=p.lstat();result[relative]={'exists':True,'regular':stat.S_ISREG(st.st_mode),'uid':st.st_uid,'size':st.st_size}
            else:result[relative]={'exists':False}
        return result
    probe('ledger',ledger)
    rows=g.units()
    # Scalar allowlist excludes Exec*, environment, arbitrary descriptions and logs.
    scalar=('LoadState','NeedDaemonReload','RefuseManualStop','StopWhenUnneeded',
            'FailureAction','SuccessAction','JobTimeoutAction','ActiveState','SubState','UnitFileState',
            'ControlPID','InvocationID','ExecMainCode','ExecMainStatus','Result')
    def safe_row(row):
        result={k:(row.get(k) if re.fullmatch(r'[A-Za-z0-9_-]{0,64}',row.get(k,'')) else 'INVALID') for k in scalar}
        result['missing_static']=[k for k in guest.STATIC if k not in row]
        for k in ('Names','OnFailure','OnSuccess'):
            a=row.get(k,'').split();result[k]=a if all(UNIT.fullmatch(x) for x in a) else ['INVALID']
        return result
    probe('units',lambda:{u:safe_row(row) for u,row in rows.items()})
    probe('dependencies',lambda:dependency_report(rows,guest.TARGETS,guest.BASE))
    def outside_dependencies():
        names=sorted({name for item in dependency_report(rows,guest.TARGETS,guest.BASE)
                      for name in item.get('unexpected',[])})
        assert len(names)<=64
        if not names:return {}
        fields=('Id','LoadState','ActiveState','StopWhenUnneeded','RefuseManualStop')+guest.DEPS
        raw=g.command(['/usr/bin/systemctl','show','--all','--no-pager','--property='+','.join(fields),'--',*names])
        result=[]
        seen=set()
        for block in raw.strip().split('\n\n'):
            row=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
            assert row.get('Id') in names
            assert row['Id'] not in seen
            seen.add(row['Id'])
            result.append({k:(v.split() if all(UNIT.fullmatch(x) for x in v.split()) else ['INVALID']) for k,v in row.items() if k in fields})
        return {'rows':result,'missing':sorted(set(names)-seen)}
    probe('outside_dependency_metadata',outside_dependencies)
    def unit_files():
        result={}
        for unit,row in rows.items():
            facts=[]
            for name in filter(None,[row.get('FragmentPath','')]+row.get('DropInPaths','').split()):
                if not name.startswith(('/etc/systemd/','/run/systemd/','/usr/lib/systemd/','/lib/systemd/')) or not re.fullmatch(r'[A-Za-z0-9_/.,@:-]{1,512}',name):
                    facts.append({'path':'UNEXPECTED_REDACTED'});continue
                p=Path(name);st=p.stat()
                resolved=p.resolve()
                allowed=[Path(x).resolve() for x in ('/etc/systemd','/run/systemd','/usr/lib/systemd','/lib/systemd')]
                if '..' in p.parts or not any(resolved.is_relative_to(root) for root in allowed) or not stat.S_ISREG(st.st_mode):
                    facts.append({'path':'UNEXPECTED_REDACTED'});continue
                facts.append({'path':name,'uid':st.st_uid,'mode':oct(stat.S_IMODE(st.st_mode)),
                              'sha256':hashlib.sha256(guest.bounded(p)).hexdigest()})
            result[unit]=facts
        return result
    probe('unit_file_metadata',unit_files)
    probe('config_guard',lambda:{'executed':False,'reason':'READONLY_FIELDS_ONLY_NO_PREFLIGHT_PASS'})
    for unit in guest.TARGETS:
        probe('hooks:'+unit,lambda unit=unit:g.stop_hook_proof(unit,rows[unit]))
    probe('links',g.links)
    probe('manager_jobs',g.jobs)
    probe('expected_observer_idle',g.local_idle)
    # Binding validator emits only approved IDs/paths. Exact mismatch reasons retained.
    probe('runtime_bindings',lambda:g.runtime_bindings(rows))
    for unit in guest.TARGETS:
        probe('runtime_inventory:'+unit,lambda unit=unit:runtime_inventory(g,guest,unit,rows[unit]))
    probe('diagnostics',lambda:runner.diagnostics(g))
    print(json.dumps({'state':'READONLY_COLLECTION_COMPLETE','service_mutations':False,
                      'preflight_pass_asserted':False,'failed_probes':failed}),flush=True)

if __name__=='__main__':main()
