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
         'serial':serial_value('/sys/class/block/'+name+'/device/serial'),
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
 if not isinstance(data,list) or len(data)!=1 or not isinstance(data[0],list) or len(data[0])>16:raise ValueError('DBUS_ARRAY_SCHEMA')
 entries=[]
 for row in data[0]:
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
