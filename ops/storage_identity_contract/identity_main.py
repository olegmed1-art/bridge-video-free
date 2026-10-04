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
