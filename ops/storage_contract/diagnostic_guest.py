"""Prepared read-only guest collector. Inline execution only after parent admission."""
import hashlib,json,os,re,signal,socket,stat,subprocess,time,selectors,ctypes,posixpath
from pathlib import Path,PurePosixPath

UUID='11111111-2222-3333-4444-555555555555'
MOUNT=r'mnt-bridge\x2dscratch.mount'
DEVICE=r'dev-disk-by\x2duuid-11111111\x2d2222\x2d3333\x2d4444\x2d555555555555.device'
FSCK='systemd-fsck@'+DEVICE[:-7]+'.service'
TARGETS=('synthetic-lab.service','synthetic-lab-control-bridge.service','synthetic-lab-control.service','synthetic-lab-observer.service')
SEEDS=TARGETS+(MOUNT,DEVICE,FSCK,'-.mount','local-fs.target','local-fs-pre.target','sysinit.target','basic.target','multi-user.target','cloud-init.target','cloud-final.service','docker.service','synthetic-watch.service','synthetic-watch-healthcheck.timer','synthetic-video-container.service')
EDGES=('Requires','Wants','Requisite','BindsTo','PartOf','RequiredBy','WantedBy','RequisiteOf','BoundBy','ConsistsOf','Before','After','PropagatesStopTo','StopPropagatedFrom','Upholds','UpheldBy','Conflicts','ConflictedBy')
PROPS=('Id','Names','LoadState','ActiveState','SubState','Result','FragmentPath','SourcePath','DropInPaths','UnitFileState','Job','MainPID','ControlPID','InvocationID','WorkingDirectory','RootDirectory','RequiresMountsFor','ReadOnlyPaths','ReadWritePaths','BindPaths','BindReadOnlyPaths','Where','What','Type','Options','JobTimeoutUSec','JobRunningTimeoutUSec','JobTimeoutAction','TimeoutUSec','TimeoutStartUSec','StopWhenUnneeded','SysFSPath','ExecMainCode','ExecMainStatus','ExecMainStartTimestampMonotonic','ExecMainExitTimestampMonotonic','ActiveEnterTimestampMonotonic','InactiveEnterTimestampMonotonic')+EDGES
UNIT=re.compile(r'[A-Za-z0-9_.@\\:-]{1,256}\Z')
PATH=re.compile(r'/[A-Za-z0-9_./:+@ -]{0,400}\Z')
LIMIT=2097152

def read(path):
 with open(path,'rb') as f:raw=f.read(LIMIT+1)
 if len(raw)>LIMIT:raise ValueError('READ_LIMIT')
 return raw

def command(argv):
 # argv is selected internally; no shell or mutation command accepted.
 parent=os.getpid()
 def child_guard():
  if ctypes.CDLL(None).prctl(1,signal.SIGKILL)!=0 or os.getppid()!=parent:os._exit(125)
 p=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,preexec_fn=child_guard)
 selector=selectors.DefaultSelector();selector.register(p.stdout,selectors.EVENT_READ)
 raw=bytearray();deadline=time.monotonic()+4
 try:
  while True:
   left=deadline-time.monotonic()
   if left<=0:raise TimeoutError('COMMAND_TIMEOUT')
   if not selector.select(min(left,.1)):continue
   chunk=os.read(p.stdout.fileno(),min(65536,LIMIT+1-len(raw)))
   if not chunk:break
   raw.extend(chunk)
   if len(raw)>LIMIT:raise ValueError('COMMAND_OUTPUT_LIMIT')
  if p.wait(timeout=max(.001,deadline-time.monotonic())):raise ValueError('COMMAND_FAILED')
  return raw.decode('utf-8','strict')
 finally:
  selector.close();p.stdout.close()
  if p.poll() is None:p.kill();p.wait(timeout=1)

def safe_path(value):
 return value if isinstance(value,str) and PATH.fullmatch(value) and '..' not in Path(value).parts else 'REDACTED_PATH'

def source(value):
 if re.fullmatch(r'UUID=[0-9a-fA-F-]{8,64}',value or ''):return value
 if (value or '').startswith('/dev/'):return safe_path(value)
 return 'NONLOCAL_OR_REDACTED_SOURCE'

def options(value):
 flags={'defaults','auto','noauto','nofail','ro','rw','discard','nodiscard','nosuid','nodev','noexec','exec','suid','dev','user','users','nouser','sync','async','relatime','noatime','norelatime','_netdev','x-systemd.automount','x-systemd.makefs','x-systemd.growfs'}
 out=[];hidden=0
 for x in value.split(','):
  if x in flags:out.append(x)
  else:hidden+=1
 return {'allowlisted':out,'other_count':hidden}

def fstab_rows(raw):
 rows=[]
 for i,line in enumerate(raw.decode().splitlines(),1):
  x=line.split()
  if not x or x[0].startswith('#'):continue
  if len(x)<6:continue
  if x[1]!='/mnt/bridge-scratch' and UUID not in x[0]:continue
  rows.append({'line':i,'source':source(x[0]),'target':safe_path(x[1]),'fstype':x[2] if re.fullmatch('[A-Za-z0-9_.-]+',x[2]) else 'REDACTED','options':options(x[3]),'dump':int(x[4]) if x[4].isdigit() else None,'pass':int(x[5]) if x[5].isdigit() else None})
 return {'sha256':hashlib.sha256(raw).hexdigest(),'matching_rows':rows}

def mounts(raw):
 rows=[]
 for line in raw.splitlines():
  x=line.split();i=x.index('-')
  rows.append({'id':int(x[0]),'parent':int(x[1]),'major_minor':x[2],'root':safe_path(x[3]),'target':safe_path(x[4]),'fstype':x[i+1],'source':source(x[i+2])})
 return rows

def covering(path,rows):
 matches=[x for x in rows if path==x['target'] or path.startswith(x['target'].rstrip('/')+'/')]
 return max(matches,key=lambda x:len(x['target'])) if matches else None

def resolve_without_automount(path,rows):
 # Never traverse an autofs mount, even while resolving a symlink target.
 pending=PurePosixPath(path).parts[1:];current='/';links=0
 while pending:
  current=posixpath.normpath(posixpath.join(current,pending[0]));pending=pending[1:]
  mounted=covering(current,rows)
  if mounted and mounted['fstype']=='autofs':return {'path':path,'resolved':None,'reason':'AUTOFS_NOT_TRAVERSED'}
  try:s=os.lstat(current)
  except FileNotFoundError:return {'path':path,'resolved':safe_path(posixpath.join(current,*pending)),'exists':False,'covering':covering(current,rows)}
  if stat.S_ISLNK(s.st_mode):
   links+=1
   if links>16:raise ValueError('SYMLINK_LIMIT')
   target=os.readlink(current);target=posixpath.normpath(target if target.startswith('/') else posixpath.join(posixpath.dirname(current),target))
   pending=PurePosixPath(target).parts[1:]+tuple(pending);current='/'
 return {'path':path,'resolved':safe_path(current),'exists':True,'covering':covering(current,rows)}

def file_hashes(graph,mi):
 paths={r[k] for r in graph.values() for k in ('FragmentPath',) if r.get(k,'').startswith(('/run/systemd/generator/','/etc/systemd/system/','/usr/lib/systemd/system/'))}
 out=[]
 for p in sorted(paths)[:100]:
  resolved=resolve_without_automount(p,mi)
  if not resolved.get('exists') or resolved.get('resolved')!=p:out.append({'path':p,'omitted':'UNRESOLVED_OR_SYMLINK'});continue
  fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  try:
   st=os.fstat(fd)
   if not stat.S_ISREG(st.st_mode) or st.st_size>65536:raise ValueError('FILE_TYPE_SIZE')
   raw=os.read(fd,65537);assert len(raw)<=65536
   out.append({'path':p,'sha256':hashlib.sha256(raw).hexdigest()})
  finally:os.close(fd)
 return {'files':out,'complete':len(paths)<=100}

def units(names):
 raw=command(['/usr/bin/systemctl','show','--all','--no-pager','--property='+','.join(PROPS),*sorted(names)])
 result={};covered=set()
 for block in raw.strip().split('\n\n'):
  pairs=[x.split('=',1) for x in block.splitlines() if '=' in x]
  assert len({x[0] for x in pairs})==len(pairs) and all(k in PROPS for k,v in pairs)
  row=dict(pairs);ident=row.get('Id');assert isinstance(ident,str) and UNIT.fullmatch(ident)
  aliases=set(row.get('Names','').split())|{ident};assert all(UNIT.fullmatch(n) for n in aliases)
  assert aliases&names;covered.update(aliases&names)
  safe={}
  for k,v in row.items():
   if k in EDGES or k=='Names':
    values=v.split();assert all(UNIT.fullmatch(x) for x in values);safe[k]=values
   elif k=='Options':safe[k]=options(v)
   elif k=='What':safe[k]=source(v)
   elif k in ('DropInPaths','RequiresMountsFor','ReadOnlyPaths','ReadWritePaths','BindPaths','BindReadOnlyPaths'):safe[k]=[safe_path(x) for x in v.split()]
   elif k in ('FragmentPath','SourcePath','WorkingDirectory','RootDirectory','Where','SysFSPath'):safe[k]=safe_path(v) if v else ''
   elif re.fullmatch(r'[A-Za-z0-9_ .:/\\@=-]{0,512}',v):safe[k]=v
   else:safe[k]='REDACTED_VALUE'
  assert ident not in result or result[ident]==safe
  result[ident]=safe
 assert covered==set(names)
 return result

def jobs():
 out=[]
 for line in command(['/usr/bin/systemctl','list-jobs','--no-legend','--no-pager','--plain']).splitlines():
  x=line.split();assert len(x)==4 and x[0].isdigit() and UNIT.fullmatch(x[1])
  assert x[2] in ('start','stop','restart','reload','verify-active','reload-or-start','try-restart','nop') and x[3] in ('running','waiting')
  out.append(dict(id=int(x[0]),unit=x[1],type=x[2],state=x[3]))
 return out

def block_inventory():
 obj=json.loads(command(['/usr/bin/lsblk','--json','--bytes','--output','NAME,KNAME,TYPE,SIZE,RO,MAJ:MIN,FSTYPE,UUID,MOUNTPOINTS']))
 def scrub(rows):
  out=[]
  for x in rows:
   row={k:v for k,v in x.items() if k in ('size','ro') and type(v) in (int,bool)}
   for k in ('name','kname','type','maj:min','fstype','uuid'):
    v=x.get(k);row[k]=v if v is None or isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{0,128}',v) else 'REDACTED'
   row['mountpoints']=[safe_path(v) for v in x.get('mountpoints',[]) if v]
   row['children']=scrub(x.get('children',[]));out.append(row)
  return out
 return scrub(obj['blockdevices'])

def expected_link(mi):
 p=Path('/dev/disk/by-uuid')/UUID
 resolved=resolve_without_automount(str(p),mi)
 if not resolved.get('exists'):return resolved
 target=resolved['resolved'];out={'exists':True,'resolved':source(target)}
 if target.startswith('/dev/'):
  try:
   t=os.lstat(target);out.update(block=stat.S_ISBLK(t.st_mode),major=os.major(t.st_rdev),minor=os.minor(t.st_rdev))
  except FileNotFoundError:out['dangling']=True
 return out

def containers():
 out={}
 fmt='{{json .Id}} {{json .State.Status}} {{json .State.Running}} {{json .State.ExitCode}} {{json .State.OOMKilled}} {{json .RestartCount}} {{json .State.Pid}}'
 mountfmt='{{range .Mounts}}{{json .Type}} {{json .Source}} {{json .Destination}} {{json .RW}}{{println}}{{end}}'
 for name in ('synthetic-dds-runtime','synthetic-video-container'):
  try:
   state=command(['/usr/bin/docker','inspect','--format',fmt,name]).strip()
   assert re.fullmatch(r'"[a-f0-9]{64}" "[a-z]+" (true|false) [0-9]+ (true|false) [0-9]+ [0-9]+',state)
   m=[]
   for line in command(['/usr/bin/docker','inspect','--format',mountfmt,name]).splitlines():
    if not line.strip():continue
    decoder=json.JSONDecoder();values=[];tail=line.strip()
    while tail:v,n=decoder.raw_decode(tail);values.append(v);tail=tail[n:].strip()
    assert len(values)==4 and values[0] in ('bind','volume','tmpfs') and type(values[3]) is bool
    m.append({'type':values[0],'source':safe_path(values[1]),'destination':safe_path(values[2]),'rw':values[3]})
   pid=int(state.rsplit(' ',1)[1]);namespace=mounts(read('/proc/'+str(pid)+'/mountinfo').decode()) if pid else None
   after=command(['/usr/bin/docker','inspect','--format',fmt,name]).strip()
   out[name]={'state_scalars':state,'mounts':m,'process_mountinfo':namespace,'state_pid_identity_still_matches':state==after,'atomic_snapshot':False}
  except Exception as e:out[name]={'unavailable':type(e).__name__}
 return out

def journal_classes():
 argv=['/usr/bin/journalctl','--boot','--no-pager','--output=json','--lines=300']
 for u in (MOUNT,DEVICE,FSCK,'cloud-final.service'):argv+=['--unit',u]
 rows=[]
 for line in command(argv).splitlines():
  x=json.loads(line);message=x.get('MESSAGE','');message=message if isinstance(message,str) else ''
  classes=[name for name,pattern in (('DEVICE_TIMEOUT',r'timed out waiting for device'),('DEPENDENCY_FAILED',r'dependency failed'),('NOT_FOUND',r'no such file|not found|does not exist'),('IO_ERROR',r'I/O error|input/output error'),('FSCK',r'fsck|file system check'),('MOUNT_FAILED',r'failed to mount'),('WAITING',r'waiting')) if re.search(pattern,message,re.I)]
  unit=x.get('_SYSTEMD_UNIT',x.get('UNIT',''));unit=unit if isinstance(unit,str) and UNIT.fullmatch(unit) else None
  timestamp=x.get('__REALTIME_TIMESTAMP');timestamp=timestamp if isinstance(timestamp,str) and timestamp.isdigit() else None
  rows.append({'unit':unit,'timestamp_us':timestamp,'classes':classes,'message_omitted':True})
 return {'rows':rows,'limit':300,'may_be_truncated':len(rows)==300}

def kernel_classes():
 rows=[]
 for line in command(['/usr/bin/journalctl','--boot','--dmesg','--no-pager','--output=json','--lines=500']).splitlines():
  x=json.loads(line);message=x.get('MESSAGE','');message=message if isinstance(message,str) else ''
  classes=[name for name,pattern in (('BLOCK_IO_ERROR',r'I/O error|Buffer I/O|blk_update_request'),('FILESYSTEM_ERROR',r'EXT4-fs error|XFS.*[Ee]rror|BTRFS.*error'),('BLOCK_ATTACHMENT',r'virtio_blk|nvme|Attached SCSI disk'),('DEVICE_RESET',r'(nvme|scsi|virtio).*reset')) if re.search(pattern,message)]
  if classes:rows.append({'classes':classes,'message_omitted':True})
 return {'classes':rows,'last_lines_limit':500,'absence_does_not_exclude_earlier_errors':True}

def process_paths(graph):
 out={}
 for u in TARGETS+('docker.service','synthetic-watch.service','synthetic-video-container.service'):
  pid=graph.get(u,{}).get('MainPID','0')
  if not pid.isdigit() or int(pid)==0:out[u]={'running_pid':False};continue
  root='/proc/'+pid
  try:
   before=read(root+'/stat').decode().rsplit(')',1)[1].split()[19];namespace=os.readlink(root+'/ns/mnt')
   row={'pid':int(pid),'cwd':safe_path(os.readlink(root+'/cwd')),'root':safe_path(os.readlink(root+'/root')),'mountinfo':mounts(read(root+'/mountinfo').decode())}
   after=read(root+'/stat').decode().rsplit(')',1)[1].split()[19]
   row.update(starttime_still_matches=before==after,mount_namespace_still_matches=namespace==os.readlink(root+'/ns/mnt'),atomic_snapshot=False)
   out[u]=row
  except Exception as e:out[u]={'pid':int(pid),'unavailable':type(e).__name__}
 return out

def main():
 assert socket.gethostname()=='synthetic-compute' and os.geteuid()==0
 signal.signal(signal.SIGALRM,signal.SIG_DFL);signal.alarm(55)
 failed=[];output_bytes=0
 def emit(probe,fn):
  nonlocal output_bytes
  try:value=fn();obj={'probe':probe,'ok':True,'value':value}
  except Exception as e:failed.append(probe);obj={'probe':probe,'ok':False,'error':type(e).__name__}
  line=json.dumps(obj,sort_keys=True)
  if output_bytes+len(line.encode())>300000:
   failed.append(probe);obj={'probe':probe,'ok':False,'error':'TOTAL_OUTPUT_LIMIT'};line=json.dumps(obj)
  output_bytes+=len(line.encode())+1
  print(line,flush=True);return obj.get('value')
 emit('identity',lambda:{'boot_id':read('/proc/sys/kernel/random/boot_id').decode().strip(),'epoch':time.time()})
 mi=emit('mountinfo',lambda:mounts(read('/proc/self/mountinfo').decode())) or []
 emit('fstab',lambda:fstab_rows(read('/etc/fstab')))
 if mi:emit('expected_uuid_link',lambda:expected_link(mi))
 else:failed.append('expected_uuid_link_no_mountinfo')
 emit('block_inventory',block_inventory)
 initial=emit('jobs_before',jobs) or []
 graph=emit('seed_units',lambda:units(set(SEEDS)|{x['unit'] for x in initial})) or {}
 neighbors={x for row in graph.values() for key in EDGES for x in row.get(key,[])}-set(graph)
 if len(neighbors)<=160:
  extra=emit('neighbor_units',lambda:units(neighbors)) if neighbors else {};graph.update(extra or {})
 else:emit('neighbor_units',lambda:{'complete':False,'reason':'GRAPH_LIMIT','count':len(neighbors)})
 emit('graph_boundary',lambda:sorted({x for row in graph.values() for key in EDGES for x in row.get(key,[])}-set(graph)))
 cs=emit('containers',containers) or {}
 root=emit('docker_root',lambda:safe_path(command(['/usr/bin/docker','info','--format','{{.DockerRootDir}}']).strip()))
 paths={'/mnt/bridge-scratch','/nonexistent/synthetic-school/bridge-video-free','/nonexistent/synthetic-school/synthetic-lab','/nonexistent/synthetic-school/synthetic-lab-observer','/nonexistent/synthetic-maint-v3','/nonexistent/synthetic-maint-v3-code','/var/lib/docker','/run/synthetic-school','/etc/systemd/system'}
 if root and root!='REDACTED_PATH':paths.add(root)
 for value in cs.values():
  for m in value.get('mounts',[]):
   if m['source']!='REDACTED_PATH':paths.add(m['source'])
 for row in graph.values():
  if row.get('WorkingDirectory','').startswith('/'):paths.add(row['WorkingDirectory'])
 # Lexical mapping explicitly avoids stat/realpath that could trigger automount.
 emit('path_mount_coverage',lambda:[{'path':p,'covering':covering(p,mi),'lexical_only':True,'symlink_resolution_unproven':True} for p in sorted(paths)])
 if mi:
  emit('resolved_path_coverage',lambda:[resolve_without_automount(p,mi) for p in sorted(paths)])
  emit('unit_file_hashes',lambda:file_hashes(graph,mi))
 else:failed.append('path_resolution_no_mountinfo')
 emit('process_paths',lambda:process_paths(graph))
 after=emit('runtime_units_after',lambda:units(set(TARGETS)|{'docker.service','synthetic-watch.service','synthetic-video-container.service'})) or {}
 emit('runtime_identity_comparison',lambda:{u:{'main_pid_unchanged':bool(row.get('MainPID','').isdigit() and int(row['MainPID'])>0 and graph.get(u,{}).get('MainPID')==row['MainPID']),'invocation_unchanged':bool(re.fullmatch('[0-9a-f]{32}',row.get('InvocationID','')) and graph.get(u,{}).get('InvocationID')==row['InvocationID'])} for u,row in after.items()})
 emit('journal_classes',journal_classes)
 emit('kernel_classes',kernel_classes)
 ledger=Path('/nonexistent/synthetic-maint-v3')
 if mi:emit('ledger_presence',lambda:{p:resolve_without_automount(str(ledger/p),mi) for p in ('mutation-intent.json','synthetic-isolation-001/baseline.json','synthetic-isolation-001/stop-intent.json','synthetic-isolation-001/complete.json')})
 else:failed.append('ledger_presence_no_mountinfo')
 emit('jobs_after',jobs)
 print(json.dumps({'state':'READONLY_STORAGE_COLLECTION_ENDED','all_top_level_probes_returned':not failed,'failed_probes':failed,'guest_application_writes':False,'service_actions':False,'evidence_complete_asserted':False,'graph_and_path_completeness_require_review':True}),flush=True)

if __name__=='__main__':main()
