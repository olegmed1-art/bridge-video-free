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

class CommandFailure(ValueError):
 def __init__(self,returncode):
  self.returncode=returncode;super().__init__("COMMAND_FAILED")

def command(argv,accepted=(0,)):
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
  rc=p.wait(timeout=max(.001,deadline-time.monotonic()))
  if rc not in accepted:raise CommandFailure(rc)
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
  except FileNotFoundError:return {'path':path,'resolved':safe_unit_path(posixpath.join(current,*pending)),'exists':False,'covering':covering(current,rows)}
  if stat.S_ISLNK(s.st_mode):
   links+=1
   if links>16:raise ValueError('SYMLINK_LIMIT')
   target=os.readlink(current);target=posixpath.normpath(target if target.startswith('/') else posixpath.join(posixpath.dirname(current),target))
   pending=PurePosixPath(target).parts[1:]+tuple(pending);current='/'
 return {'path':path,'resolved':safe_unit_path(current),'exists':True,'covering':covering(current,rows)}

def file_hashes(graph,mi):
 paths={r[k] for r in graph.values() for k in ('FragmentPath',) if r.get(k,'').startswith(('/run/systemd/generator/','/etc/systemd/system/','/usr/lib/systemd/system/'))}
 paths.update(p for row in graph.values() for p in row.get('DropInPaths',[]) if p.startswith(('/etc/systemd/system/','/run/systemd/system/','/usr/lib/systemd/system/')))
 paths.add('/etc/tmpfiles.d/bridge-school-universal-video.conf')
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

def unit_words(value):
 values=[]
 for token in value.split():
  parsed=json.loads(token) if token.startswith('"') else token
  if not isinstance(parsed,str) or not UNIT.fullmatch(parsed):raise ValueError('UNIT_WORD_SCHEMA')
  values.append(parsed)
 return values

def units(names,properties=PROPS):
 raw=command(['/usr/bin/systemctl','show','--all','--no-pager','--property='+','.join(properties),'--',*sorted(names)])
 result={};covered=set()
 for block in raw.strip().split('\n\n'):
  pairs=[x.split('=',1) for x in block.splitlines() if '=' in x]
  assert len({x[0] for x in pairs})==len(pairs) and all(k in properties for k,v in pairs)
  row=dict(pairs);ident=row.get('Id');assert isinstance(ident,str) and UNIT.fullmatch(ident)
  aliases=set(unit_words(row.get('Names','')))|{ident};assert all(UNIT.fullmatch(n) for n in aliases)
  assert aliases&names;covered.update(aliases&names)
  safe={}
  for k,v in row.items():
   if k in EDGES or k=='Names':
    safe[k]=unit_words(v)
   elif k=='Options':safe[k]=options(v)
   elif k=='What':safe[k]=source(v)
   elif k in ('DropInPaths','RequiresMountsFor','ReadOnlyPaths','ReadWritePaths','BindPaths','BindReadOnlyPaths'):safe[k]=[safe_path(x) for x in v.split()]
   elif k in ('FragmentPath','SourcePath','WorkingDirectory','RootDirectory','Where','SysFSPath'):safe[k]=safe_unit_path(v) if k=='FragmentPath' and v else safe_path(v) if v else ''
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
  if not graph or u not in graph or 'MainPID' not in graph[u]:
   out[u]={'state':'UNKNOWN','reason':'MISSING_UNIT_METADATA'};continue
  pid=graph[u]['MainPID']
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


def identity_comparison(before,after):
 out={}
 for u in TARGETS+('docker.service','synthetic-watch.service','synthetic-video-container.service'):
  if u not in before or u not in after:
   out[u]={'state':'UNKNOWN','reason':'MISSING_UNIT_METADATA'};continue
  out[u]={k+'_unchanged':before[u].get(k)==after[u].get(k) if k in before[u] and k in after[u] else None for k in ('MainPID','InvocationID')}
 return out

def full_graph():
 names=set(SEEDS)
 for line in command(['/usr/bin/systemctl','list-units','--all','--full','--plain','--no-legend','--no-pager']).splitlines():
  name=line.split()[0];assert UNIT.fullmatch(name);names.add(name)
 rows={};aliases=set()
 for iteration in range(6):
  todo=names-aliases
  if not todo:break
  if len(names)>768:raise ValueError('GRAPH_LIMIT')
  batch=units(todo,('Id','Names','LoadState')+EDGES)
  rows.update(batch)
  for ident,row in batch.items():
   aliases.update(row.get('Names',[]));aliases.add(ident)
   names.update(x for k in EDGES for x in row.get(k,[]))
 if names-aliases:raise ValueError('GRAPH_INCOMPLETE')
 ids=sorted(set(rows)|names|aliases);index={n:i for i,n in enumerate(ids)}
 edges=[[index[u],k,[index[x] for x in sorted(row.get(prop,[]))]] for u,row in sorted(rows.items()) for k,prop in enumerate(EDGES) if row.get(prop)]
 missing={u:[k for k in EDGES if k not in row] for u,row in rows.items() if any(k not in row for k in EDGES)}
 payload={'missing_edge_properties':missing,'unit_names':ids,'edge_properties':list(EDGES),'edges':edges,'present':[index[u] for u in sorted(rows)],'aliases':[[index[u],[index[x] for x in sorted(row.get('Names',[]))]] for u,row in sorted(rows.items())],'load_states':[[index[u],row.get('LoadState','UNKNOWN')] for u,row in sorted(rows.items())]}
 digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 return dict(payload,sha256=digest,closure_complete=not missing,scope='CURRENT_MANAGER_UNITS_AND_REFERENCED_CLOSURE',atomic_snapshot=False,future_configuration_complete=False)


FSTAB_BASELINE='3333333333333333333333333333333333333333333333333333333333333333'
def noauto_candidate(raw,baseline=FSTAB_BASELINE):
 if hashlib.sha256(raw).hexdigest()!=baseline:raise ValueError('FSTAB_BASELINE_DRIFT')
 matches=[];offset=0
 for line in raw.splitlines(keepends=True):
  tokens=list(re.finditer(rb'\S+',line))
  if tokens and not tokens[0].group().startswith(b'#') and any(t.group() in (('UUID='+UUID).encode(),b'/mnt/bridge-scratch') for t in tokens[:2]):
   if [t.group() for t in tokens[:6]]!=[('UUID='+UUID).encode(),b'/mnt/bridge-scratch',b'ext4',b'defaults,nofail,noatime',b'0',b'2']:raise ValueError('FSTAB_ROW_DRIFT')
   matches.append(offset+tokens[3].end())
  offset+=len(line)
 if len(matches)!=1:raise ValueError('FSTAB_ROW_COUNT')
 at=matches[0];candidate=raw[:at]+b',noauto'+raw[at:]
 return {'baseline_sha256':baseline,'candidate_sha256':hashlib.sha256(candidate).hexdigest(),'insert_offset':at,'insert_bytes_ascii':',noauto','other_bytes_unchanged':True,'applied':False,'dependency_approval_required':True}

def executable(name):
 for parent in ('/usr/sbin','/usr/bin','/sbin','/bin'):
  p=parent+'/'+name
  if os.path.isfile(p) and os.access(p,os.X_OK):return p
 return None

def signature_probe(name,path):
 exe=executable(name)
 if exe is None:return {'state':'UNKNOWN','reason':'UTILITY_UNAVAILABLE'}
 if name=='blkid':
  raw=command([exe,'-p','-o','export','--',path],accepted=(0,2));out={}
  for line in raw.splitlines():
   k,sep,v=line.partition('=')
   if k in ('TYPE','UUID','PTTYPE','PTUUID','USAGE','PART_ENTRY_TYPE','PART_ENTRY_UUID','PART_ENTRY_SCHEME'):
    out[k]=v if re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}',v) else 'REDACTED'
  return {'recognized_fields':out,'absence_does_not_prove_blank':True}
 if name=='file':
  raw=command([exe,'-s','-b','--',path]);classes=[x for x in ('ext4','XFS','BTRFS','swap','DOS/MBR','GPT','LUKS') if x.lower() in raw.lower()]
  return {'classes':classes,'reported_data':raw.strip()=='data','raw_omitted':True,'absence_does_not_prove_blank':True}
 obj=json.loads(command([exe,'--no-act','--json','--output','DEVICE,OFFSET,TYPE,UUID','--',path]));out=[]
 for row in obj['signatures']:
  out.append({k:v if v is None or isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{0,128}',v) else 'REDACTED' for k,v in row.items() if k in ('device','offset','type','uuid')})
 return {'signatures':out,'absence_does_not_prove_blank':True}

def vdd_signatures():
 path='/dev/vdd';before=os.stat(path)
 if not stat.S_ISBLK(before.st_mode):raise ValueError('NOT_BLOCK_DEVICE')
 def identity(st):
  sectors=read('/sys/dev/block/'+str(os.major(st.st_rdev))+':'+str(os.minor(st.st_rdev))+'/size').decode().strip()
  if not sectors.isdigit():raise ValueError('DEVICE_SIZE_SCHEMA')
  return {'major':os.major(st.st_rdev),'minor':os.minor(st.st_rdev),'inode':st.st_ino,'size_bytes':int(sectors)*512}
 out={'before':identity(before),'tools':{}}
 for name in ('blkid','file','wipefs'):
  try:out['tools'][name]=signature_probe(name,path)
  except Exception as e:
   out['tools'][name]={'state':'UNKNOWN','error':type(e).__name__}
   if isinstance(e,CommandFailure):out['tools'][name]['returncode']=e.returncode
 out['after']=identity(os.stat(path));out['same_identity']=out['before']==out['after'];out['atomic_snapshot']=False
 return out



PROVIDER_DISK_ID='02c7-67e4a9d3-eba3-45d0-b161-ee48b392f985'
PROVIDER_EVIDENCE_SHA='81e97e545202582c3c1ce48c9f2c6b72e4d9605b9ad6cb757324d8cec213f6a1'
SERIAL=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
BY_ID_DIR='/dev/disk/by-id'
UV='synthetic-video-container.service'
UV_OBJECT='/org/freedesktop/systemd1/unit/universal_2dvideo_2dcontainer_2eservice'
FRAME_UNITS=set(TARGETS)|{UV,'docker.service','synthetic-watch.service','cloud-final.service',MOUNT,DEVICE,FSCK}
FRAME_PROPS=('Id','Names','LoadState','ActiveState','SubState','Result','Job','MainPID','ControlPID','InvocationID','NRestarts','Type','ExecMainCode','ExecMainStatus','ExecMainStartTimestampMonotonic','ExecMainExitTimestampMonotonic','JobRunningTimeoutUSec')

def small_read(path,limit=256):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('NOT_REGULAR')
  raw=os.read(fd,limit+1)
  if len(raw)>limit:raise ValueError('SMALL_READ_LIMIT')
  return raw
 finally:os.close(fd)

def serial_value(path):
 try:raw=small_read(path).decode('ascii').strip()
 except FileNotFoundError:return {'state':'ABSENT'}
 except Exception:return {'state':'UNKNOWN','reason':'SERIAL_READ_OR_SCHEMA'}
 if not SERIAL.fullmatch(raw):return {'state':'UNKNOWN','reason':'SERIAL_SCHEMA'}
 return {'state':'PRESENT','value':raw}

def block_identity(name):
 if name not in ('vda','vdd'):raise ValueError('DEVICE_NOT_ALLOWLISTED')
 st=os.lstat('/dev/'+name)
 if not stat.S_ISBLK(st.st_mode):raise ValueError('NOT_BLOCK_DEVICE')
 major,minor=os.major(st.st_rdev),os.minor(st.st_rdev)
 sectors=small_read('/sys/dev/block/'+str(major)+':'+str(minor)+'/size').decode().strip()
 if not sectors.isdigit():raise ValueError('DEVICE_SIZE_SCHEMA')
 return {'name':name,'major':major,'minor':minor,'inode':st.st_ino,'size_bytes':int(sectors)*512,
         'serial':serial_value('/sys/class/block/'+name+'/serial'),
         'partition_marker_present':os.path.lexists('/sys/class/block/'+name+'/partition')}

def by_id_links():
 path=BY_ID_DIR;st=os.lstat(path)
 if not stat.S_ISDIR(st.st_mode):raise ValueError('BY_ID_DIRECTORY_SCHEMA')
 out=[];count=0
 with os.scandir(path) as entries:
  for entry in entries:
   count+=1
   if count>128:raise ValueError('BY_ID_LIMIT')
   if not entry.name.startswith('virtio-') or not SERIAL.fullmatch(entry.name[7:]):continue
   link=path+'/'+entry.name;before=os.lstat(link)
   if not stat.S_ISLNK(before.st_mode):continue
   target=os.readlink(link);resolved=posixpath.normpath(target if target.startswith('/') else posixpath.join(path,target))
   if resolved not in ('/dev/vda','/dev/vdd'):continue
   second=os.readlink(link);after=os.lstat(link)
   out.append({'basename':entry.name,'serial_prefix':entry.name[7:],'target':resolved,
               'link_stable':target==second and (before.st_ino,before.st_dev)==(after.st_ino,after.st_dev)})
 return sorted(out,key=lambda x:x['basename'])

def correlate_disk(before,after,links_before,links_after,provider_id=PROVIDER_DISK_ID):
 result={'state':'UNKNOWN','provider_disk_id':provider_id,'provider_evidence_sha256':PROVIDER_EVIDENCE_SHA,
         'mapping_atomic':False,'same_device_identity':before==after,'same_link_inventory':links_before==links_after}
 if before!=after or links_before!=links_after:return dict(result,reason='IDENTITY_OR_LINK_DRIFT')
 disk=before.get('vdd',{})
 if disk.get('partition_marker_present') or disk.get('size_bytes')!=260000000000:return dict(result,reason='VDD_LAYOUT_OR_SIZE_DRIFT')
 candidates=[r for r in links_before if r.get('target')=='/dev/vdd' and r.get('link_stable') is True
             and isinstance(r.get('serial_prefix'),str) and len(r['serial_prefix'])>=16 and provider_id.startswith(r['serial_prefix'])]
 other=[r for r in links_before if r.get('target')!='/dev/vdd' and isinstance(r.get('serial_prefix'),str)
        and len(r['serial_prefix'])>=16 and provider_id.startswith(r['serial_prefix'])]
 serial=disk.get('serial',{})
 if len(candidates)!=1 or other:return dict(result,reason='NO_UNIQUE_BY_ID_MATCH')
 if serial.get('state')!='PRESENT' or serial.get('value')!=candidates[0]['serial_prefix']:return dict(result,reason='SYSFS_SERIAL_NOT_CONFIRMED')
 return dict(result,state='CORRELATED',guest_device='/dev/vdd',serial_prefix=serial['value'],
             by_id_basename=candidates[0]['basename'],size_bytes=disk['size_bytes'],major=disk['major'],minor=disk['minor'],
             content_or_format_permission=False)

def disk_correlation(mi):
 for path in ('/dev','/dev/disk','/dev/disk/by-id'):
  if stat.S_ISLNK(os.lstat(path).st_mode):return {'state':'UNKNOWN','reason':'DEVICE_METADATA_COMPONENT_SYMLINK'}
 for path,fstype in (('/dev/disk/by-id','devtmpfs'),('/sys/class/block/vdd','sysfs')):
  row=covering(path,mi)
  if not row or row.get('fstype')!=fstype:return {'state':'UNKNOWN','reason':'DEVICE_METADATA_BACKING_UNPROVEN'}
 before={n:block_identity(n) for n in ('vda','vdd')};a=by_id_links()
 b=by_id_links();after={n:block_identity(n) for n in ('vda','vdd')}
 return {'before':before,'after':after,'links_before':a,'links_after':b,
         'correlation':correlate_disk(before,after,a,b),'atomic_snapshot':False}

def safe_unit_path(value):
 if isinstance(value,str) and posixpath.dirname(value) in ('/run/systemd/generator','/run/systemd/generator.early','/run/systemd/generator.late'):
  name=posixpath.basename(value)
  if UNIT.fullmatch(name) and re.fullmatch(r'(?:[A-Za-z0-9_.@:-]|\\x[0-9a-fA-F]{2})+',name):return value
 return safe_path(value)

def parse_exec_pre(raw):
 value=json.loads(raw)
 if not isinstance(value,dict) or set(value)!={'type','data'} or value['type']!='a(sasbttttuii)':raise ValueError('DBUS_TYPE_SCHEMA')
 data=value['data']
 if not isinstance(data,list) or len(data)>16:raise ValueError('DBUS_ARRAY_SCHEMA')
 entries=[]
 for row in data:
  if not isinstance(row,list) or len(row)!=10:raise ValueError('DBUS_ENTRY_SCHEMA')
  if not isinstance(row[0],str) or not isinstance(row[1],list) or len(row[1])>256 or not all(isinstance(x,str) for x in row[1]) or type(row[2]) is not bool:raise ValueError('DBUS_EXEC_SCHEMA')
  if not all(type(x) is int and 0<=x<2**64 for x in row[3:7]):raise ValueError('DBUS_TIME_SCHEMA')
  if type(row[7]) is not int or not 0<=row[7]<2**32 or not all(type(x) is int and -(2**31)<=x<2**31 for x in row[8:]):raise ValueError('DBUS_STATUS_SCHEMA')
  entries.append({'path':safe_path(row[0]),'argv_omitted':True,'ignore_errors':row[2],
                  'start_realtime_us':row[3],'start_monotonic_us':row[4],'exit_realtime_us':row[5],
                  'exit_monotonic_us':row[6],'pid':row[7],'code':row[8],'status':row[9]})
 return {'signature':'a(sasbttttuii)','exec_start_pre':entries,'empty':not entries,'argv_omitted':True}

def uv_startup():
 before=units({UV},FRAME_PROPS).get(UV,{})
 acquired=time.monotonic();value=None;failure=None
 try:
  raw=command(['/usr/bin/busctl','--system','--json=short','--auto-start=no','--allow-interactive-authorization=no','--timeout=3s','get-property',
               'org.freedesktop.systemd1',UV_OBJECT,'org.freedesktop.systemd1.Service','ExecStartPre'])
 except Exception as e:failure={'phase':'ACQUIRE','reason':type(e).__name__}
 if failure is None:
  try:value=parse_exec_pre(raw)
  except Exception:failure={'phase':'PARSE','reason':'UNSUPPORTED_DBUS_SCHEMA'}
 after=units({UV},FRAME_PROPS).get(UV,{})
 same=bool(before.get('InvocationID')) and before.get('InvocationID')==after.get('InvocationID')
 return {'before':before,'after':after,'acquired_monotonic':acquired,'finished_monotonic':time.monotonic(),
         'same_invocation':same,'sample_raced':not same,'typed_status':value,'failure':failure,
         'state':'OBSERVED' if value is not None else 'UNKNOWN','error_attribution_verified':False,
         'status_scope':'LAST_REPORTED_COMMAND_EXECUTION'}

def uv_journal(sample):
 invocation=sample.get('after',{}).get('InvocationID') if sample else None
 if not isinstance(invocation,str) or not re.fullmatch('[0-9a-f]{32}',invocation):return {'state':'UNKNOWN','reason':'INVOCATION_UNAVAILABLE'}
 rows=[]
 argv=['/usr/bin/journalctl','--boot','--no-pager','--output=json','--lines=100','_SYSTEMD_UNIT='+UV,'_SYSTEMD_INVOCATION_ID='+invocation]
 for line in command(argv).splitlines():
  row=json.loads(line)
  if row.get('_SYSTEMD_INVOCATION_ID')!=invocation or row.get('_SYSTEMD_UNIT')!=UV:raise ValueError('JOURNAL_MATCH_SCHEMA')
  message=row.get('MESSAGE','');message=message if isinstance(message,str) else ''
  classes=[name for name,pattern in (('NOT_FOUND',r'no such file|not found|does not exist'),('PERMISSION_DENIED',r'permission denied'),('NO_SPACE',r'no space left'),('READ_ONLY_FILESYSTEM',r'read.only file system')) if re.search(pattern,message,re.I)]
  scalars={k:int(row[k]) for k in ('ERRNO','EXIT_STATUS') if isinstance(row.get(k),str) and row[k].isdigit() and len(row[k])<=5}
  rows.append({'classes':classes,'numeric_fields':scalars,'executable':safe_path(row.get('_EXE','')),
               'message_omitted':True,'message_classification_is_hint':True})
 after=units({UV},FRAME_PROPS).get(UV,{})
 return {'state':'OBSERVED','invocation_id':invocation,'same_invocation_after_read':after.get('InvocationID')==invocation,
         'rows':rows,'limit':100,'may_be_truncated':len(rows)==100,'empty_does_not_prove_no_error':True,'after':after}

def path_metadata(path,mi):
 resolved=resolve_without_automount(path,mi)
 if not resolved.get('exists'):return dict(resolved,state='ABSENT' if resolved.get('exists') is False else 'UNKNOWN')
 row={'path':path,'resolved':resolved.get('resolved'),'covering':resolved.get('covering')}
 try:
  st=os.lstat(path);kind='SYMLINK' if stat.S_ISLNK(st.st_mode) else 'REGULAR' if stat.S_ISREG(st.st_mode) else 'DIRECTORY' if stat.S_ISDIR(st.st_mode) else 'OTHER'
  return dict(row,state=kind,uid=st.st_uid,gid=st.st_gid,mode=stat.S_IMODE(st.st_mode),size=st.st_size)
 except Exception:return dict(row,state='UNKNOWN',reason='LSTAT_FAILED')

def frame():
 begin=time.monotonic();boot=read('/proc/sys/kernel/random/boot_id').decode().strip()
 uptime=read('/proc/uptime').decode().split()[0]
 if not re.fullmatch(r'[0-9]+[.][0-9]+',uptime):raise ValueError('UPTIME_SCHEMA')
 raw=command(['/usr/bin/systemctl','show','--no-pager','--property=SystemState'])
 if not raw.startswith('SystemState=') or raw.strip().split('=',1)[1] not in ('initializing','starting','running','degraded','maintenance','stopping','offline','unknown'):raise ValueError('MANAGER_SCHEMA')
 result={'boot_id':boot,'uptime_s':float(uptime),'begin_monotonic':begin,'manager_state':raw.strip().split('=',1)[1],
         'jobs':jobs(),'units':units(FRAME_UNITS,FRAME_PROPS),'atomic_snapshot':False}
 result['end_monotonic']=time.monotonic();result['boot_id_after']=read('/proc/sys/kernel/random/boot_id').decode().strip()
 return result

def graph_model(g):
 if g is None:raise ValueError('GRAPH_UNAVAILABLE')
 names=g['unit_names'];assert len(names)<=768 and names==sorted(set(names)) and all(UNIT.fullmatch(n) for n in names)
 assert g['edge_properties']==list(EDGES)
 model={'names':set(names),'units':{names[i] for i in g['present']},'aliases':{(names[i],names[j]) for i,targets in g['aliases'] for j in targets},
        'states':{names[i]:s for i,s in g['load_states']},'edges':{(names[i],EDGES[k],names[j]) for i,k,targets in g['edges'] for j in targets}}
 payload={k:g[k] for k in ('missing_edge_properties','unit_names','edge_properties','edges','present','aliases','load_states')}
 assert hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()==g['sha256']
 return model

def graph_recheck(before,after=None):
 if before is None or after is None:return {'state':'UNKNOWN','reason':'GRAPH_SNAPSHOT_UNAVAILABLE'}
 a,b=graph_model(before),graph_model(after);delta={}
 for field in ('names','units','aliases','edges'):
  delta[field]={'added':sorted(b[field]-a[field]),'removed':sorted(a[field]-b[field])}
 delta['load_states']=[{'unit':u,'before':a['states'].get(u),'after':b['states'].get(u)} for u in sorted(set(a['states'])|set(b['states'])) if a['states'].get(u)!=b['states'].get(u)]
 result={'closure_complete':before['closure_complete'] and after['closure_complete'],'sha256':after['sha256'],
         'before_sha256':before['sha256'],'same_as_before':before['sha256']==after['sha256'],'atomic_snapshot':False,'delta':delta,'delta_complete':True}
 if len(json.dumps(delta,separators=(',',':')))>16000:
  result['delta']=None;result['delta_complete']=False;result['reason']='DELTA_DETAIL_LIMIT_OFFLINE_COMPARE_FULL_SNAPSHOTS'
 return result

def settle(frames,comparison,config_before=None,config_after=None,mi_before=None,mi_after=None):
 if len(frames)!=4 or any(f is None for f in frames) or comparison is None:return {'state':'UNKNOWN','reason':'MISSING_BOUNDARY_EVIDENCE'}
 boot=frames[0]['boot_id'];same_boot=all(f['boot_id']==f['boot_id_after']==boot for f in frames)
 empty=all(f['jobs']==[] for f in frames)
 final=[]
 for f in frames:
  u=f['units'].get('cloud-final.service',{})
  final.append(u.get('LoadState')=='loaded' and u.get('Result')=='success' and u.get('ExecMainCode')=='1' and u.get('ExecMainStatus')=='0'
               and u.get('ExecMainExitTimestampMonotonic','0').isdigit() and int(u.get('ExecMainExitTimestampMonotonic','0'))>0
               and (u.get('ActiveState'),u.get('SubState')) in (('active','exited'),('inactive','dead')))
 keys=('LoadState','ActiveState','SubState','Result','MainPID','ControlPID','InvocationID','NRestarts')
 consumers_stable=all(all(f['units'].get(u,{}).get(k)==frames[0]['units'].get(u,{}).get(k) and k in f['units'].get(u,{}) for f in frames for k in keys) for u in TARGETS+('docker.service','synthetic-watch.service',UV))
 graph_stable=comparison.get('closure_complete') is True and comparison.get('same_as_before') is True
 config_stable=bool(config_before) and config_before==config_after and config_before.get('complete') is True and isinstance(config_before.get('fstab_sha256'),str) and re.fullmatch('[0-9a-f]{64}',config_before['fstab_sha256']) is not None and bool(config_before.get('files')) and not any('omitted' in f for f in config_before.get('files',[]))
 paths=('/srv/synthetic-lab-observer-archive','/var/lib/docker','/mnt/bridge-scratch')
 backing_stable=bool(mi_before) and bool(mi_after) and all(covering(p,mi_before) is not None and covering(p,mi_before)==covering(p,mi_after) for p in paths)
 boot_settled=same_boot and empty and all(final) and all(f['manager_state'] in ('running','degraded') for f in frames)
 return {'state':'SAMPLED_SETTLED' if boot_settled and graph_stable and consumers_stable and config_stable and backing_stable else 'SETTLE_NOT_REACHED',
         'same_boot':same_boot,'jobs_empty_at_all_bounds':empty,'cloud_final_success_at_all_bounds':all(final),
         'graph_stable':graph_stable,'consumer_states_and_identity_stable':consumers_stable,
         'sampled_unit_config_stable':config_stable,'sampled_host_backing_stable':backing_stable,
         'manager_degraded_observed':any(f['manager_state']=='degraded' for f in frames),
         'atomic_snapshot':False,'mutation_or_global_health_permission':False}

def main():
 assert socket.gethostname()=='synthetic-compute' and os.geteuid()==0
 signal.signal(signal.SIGALRM,signal.SIG_DFL);signal.alarm(55)
 failed=[];output_bytes=0;began=time.monotonic()
 def emit(probe,fn):
  nonlocal output_bytes
  try:value=fn();obj={'probe':probe,'ok':True,'value':value}
  except Exception as e:
   failed.append(probe);obj={'probe':probe,'ok':False,'error':type(e).__name__}
   if isinstance(e,CommandFailure):obj['returncode']=e.returncode
  line=json.dumps(obj,sort_keys=True,separators=(',',':'))
  if output_bytes+len(line.encode())>300000:
   failed.append(probe);obj={'probe':probe,'ok':False,'error':'TOTAL_OUTPUT_LIMIT'};line=json.dumps(obj,separators=(',',':'))
  output_bytes+=len(line.encode())+1
  print(line,flush=True);return obj.get('value')
 emit('identity',lambda:{'boot_id':read('/proc/sys/kernel/random/boot_id').decode().strip(),'epoch':time.time(),'monotonic':began})
 mi=emit('mountinfo',lambda:mounts(read('/proc/self/mountinfo').decode())) or []
 fstab_a=emit('fstab',lambda:fstab_rows(read('/etc/fstab')))
 if mi:emit('expected_uuid_link',lambda:expected_link(mi))
 emit('block_inventory',block_inventory)
 initial=emit('jobs_before',jobs) or []
 graph=emit('seed_units',lambda:units(set(SEEDS)|{x['unit'] for x in initial})) or {}
 config_a=emit('unit_file_hashes',lambda:file_hashes(graph,mi)) if mi else None
 if config_a is not None:config_a=dict(config_a,fstab_sha256=fstab_a.get('sha256') if fstab_a else None)
 frames=[]
 frames.append(emit('frame_a_before',frame))
 first=emit('full_unit_graph',full_graph);graph_a_end=time.monotonic()
 frames.append(emit('frame_a_after',frame))
 if mi:emit('disk_correlation',lambda:disk_correlation(mi))
 else:failed.append('disk_correlation_no_mountinfo')
 uv=emit('uv_startup',uv_startup)
 emit('uv_journal',lambda:uv_journal(uv))
 if mi:emit('uv_path_metadata',lambda:[path_metadata(p,mi) for p in ('/run/synthetic-school','/etc/tmpfiles.d/bridge-school-universal-video.conf')])
 # A bounded gap inside the existing55s envelope; never wait90s for device timeout.
 delay=max(0,min(5-(time.monotonic()-graph_a_end),40-(time.monotonic()-began)))
 if delay:time.sleep(delay)
 frames.append(emit('frame_b_before',frame))
 second=emit('full_unit_graph_after',full_graph)
 frames.append(emit('frame_b_after',frame))
 mi_after=emit('mountinfo_after',lambda:mounts(read('/proc/self/mountinfo').decode())) or []
 fstab_b=emit('fstab_after',lambda:fstab_rows(read('/etc/fstab')))
 graph_after=emit('seed_units_after',lambda:units(set(SEEDS)|{x['unit'] for x in initial},('Id','Names','LoadState','FragmentPath','DropInPaths'))) or {}
 config_b=emit('unit_file_hashes_after',lambda:file_hashes(graph_after,mi_after)) if mi_after and graph_after else None
 if config_b is not None:config_b=dict(config_b,fstab_sha256=fstab_b.get('sha256') if fstab_b else None)
 comparison=emit('graph_recheck',lambda:graph_recheck(first,second))
 emit('settle',lambda:settle(frames,comparison,config_a,config_b,mi,mi_after))
 # Both complete graph payloads are emitted before lower-priority diagnostics.
 emit('vdd_signatures',vdd_signatures)
 cs=emit('containers',containers) or {}
 root=emit('docker_root',lambda:safe_path(command(['/usr/bin/docker','info','--format','{{.DockerRootDir}}']).strip()))
 paths={'/srv/synthetic-lab-observer-archive','/nonexistent/synthetic-school/universal-video','/var/tmp','/mnt/bridge-scratch','/nonexistent/synthetic-school/bridge-video-free','/nonexistent/synthetic-school/synthetic-lab','/nonexistent/synthetic-school/synthetic-lab-observer','/nonexistent/synthetic-maint-v3','/nonexistent/synthetic-maint-v3-code','/var/lib/docker','/run/synthetic-school','/etc/systemd/system'}
 if root and root!='REDACTED_PATH':paths.add(root)
 for value in cs.values():
  for m in value.get('mounts',[]):
   if m['source']!='REDACTED_PATH':paths.add(m['source'])
 for row in graph.values():
  if row.get('WorkingDirectory','').startswith('/'):paths.add(row['WorkingDirectory'])
 emit('path_mount_coverage',lambda:[{'path':p,'covering':covering(p,mi),'lexical_only':True,'symlink_resolution_unproven':True} for p in sorted(paths)])
 if mi:
  emit('resolved_path_coverage',lambda:[resolve_without_automount(p,mi) for p in sorted(paths)])
 emit('process_paths',lambda:process_paths(graph))
 after=frames[-1]['units'] if frames[-1] else {}
 emit('runtime_identity_comparison',lambda:identity_comparison(graph,after))
 emit('journal_classes',journal_classes)
 emit('kernel_classes',kernel_classes)
 ledger=Path('/nonexistent/synthetic-maint-v3')
 if mi:emit('ledger_presence',lambda:{p:resolve_without_automount(str(ledger/p),mi) for p in ('mutation-intent.json','synthetic-isolation-001/baseline.json','synthetic-isolation-001/stop-intent.json','synthetic-isolation-001/complete.json')})
 emit('jobs_after',jobs)
 print(json.dumps({'state':'READONLY_STORAGE_COLLECTION_ENDED','all_top_level_probes_returned':not failed,'failed_probes':failed,'guest_application_writes':False,'service_actions':False,'evidence_complete_asserted':False,'graph_and_path_completeness_require_review':True}),flush=True)

if __name__=='__main__':main()
