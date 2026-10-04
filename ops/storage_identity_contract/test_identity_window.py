import contextlib,copy,hashlib,io,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import diagnostic_guest as g
import prepare_driver as d

class IdentityTests(unittest.TestCase):
 def disk(self):
  prefix=g.PROVIDER_DISK_ID[:20]
  d={'vda':{'major':253,'minor':0},'vdd':{'size_bytes':260000000000,'major':253,'minor':48,'inode':140,'partition_marker_present':False,'serial':{'state':'PRESENT','value':prefix}}}
  links=[{'target':'/dev/vdd','serial_prefix':prefix,'basename':'virtio-'+prefix,'link_stable':True}]
  return d,links
 def test_exact_disk_correlation_and_drift(self):
  d,l=self.disk();r=g.correlate_disk(d,d,l,l);self.assertEqual(r['state'],'CORRELATED');self.assertFalse(r['content_or_format_permission'])
  for field,value in [('size_bytes',1),('partition_marker_present',True),('serial',{'state':'ABSENT'})]:
   b=copy.deepcopy(d);b['vdd'][field]=value;self.assertEqual(g.correlate_disk(b,b,l,l)['state'],'UNKNOWN')
  b=copy.deepcopy(d);b['vdd']['minor']=49;self.assertEqual(g.correlate_disk(d,b,l,l)['reason'],'IDENTITY_OR_LINK_DRIFT')
 def test_no_ambiguous_or_short_serial(self):
  d,l=self.disk()
  for links in (l+l,[dict(l[0],link_stable=False)],[dict(l[0],serial_prefix=g.PROVIDER_DISK_ID[:15])],l+[dict(l[0],target='/dev/vda')]):
   self.assertEqual(g.correlate_disk(d,d,links,links)['state'],'UNKNOWN')
 def raw(self,rows):return json.dumps({'type':'a(sasbttttuii)','data':rows})
 def row(self):return ['/usr/bin/test',['SECRET_SENTINEL','${TOKEN}; }'],False,1,2,3,4,5,1,203]
 def test_typed_prestart_scalars_and_empty(self):
  r=g.parse_exec_pre(self.raw([self.row(),self.row()]));self.assertEqual(len(r['exec_start_pre']),2);self.assertEqual(r['exec_start_pre'][0]['status'],203);self.assertNotIn('SECRET_SENTINEL',json.dumps(r));self.assertTrue(g.parse_exec_pre(self.raw([]))['empty'])
 def test_invalid_typed_prestart(self):
  invalid=['{}',json.dumps({'type':'s','data':[]}),self.raw([self.row()]*17)]
  for index,value in [(2,0),(3,True),(4,-1),(5,2**64),(7,2**32),(8,2**31),(9,'secret'),(1,['x']*257)]:
   row=self.row();row[index]=value;invalid.append(self.raw([row]))
  for raw in invalid:
   with self.assertRaises(ValueError):g.parse_exec_pre(raw)
 def test_uv_race_and_failure_phase(self):
  with patch.object(g,'units',side_effect=[{g.UV:{'InvocationID':'a'*32}},{g.UV:{'InvocationID':'b'*32}}]),patch.object(g,'command',return_value=self.raw([self.row()])):r=g.uv_startup()
  self.assertTrue(r['sample_raced']);self.assertFalse(r['error_attribution_verified']);self.assertEqual(r['state'],'OBSERVED')
  for outcome,phase in [(g.CommandFailure(1),'ACQUIRE'),('SECRET_SENTINEL','PARSE')]:
   kwargs={'side_effect':outcome} if isinstance(outcome,Exception) else {'return_value':outcome}
   with patch.object(g,'units',return_value={g.UV:{}}),patch.object(g,'command',**kwargs):r=g.uv_startup()
   self.assertEqual(r['failure']['phase'],phase);self.assertNotIn('SECRET_SENTINEL',json.dumps(r))
 def test_journal_hint_only_and_identity(self):
  invocation='a'*32;row={'_SYSTEMD_UNIT':g.UV,'_SYSTEMD_INVOCATION_ID':invocation,'MESSAGE':'SECRET_SENTINEL permission denied','ERRNO':'13','_EXE':'/usr/bin/test'}
  with patch.object(g,'units',return_value={g.UV:{'InvocationID':'b'*32}}),patch.object(g,'command',return_value=json.dumps(row)) as c:r=g.uv_journal({'after':{'InvocationID':invocation}})
  self.assertNotIn('SECRET_SENTINEL',json.dumps(r));self.assertFalse(r['same_invocation_after_read']);self.assertTrue(r['rows'][0]['message_classification_is_hint']);self.assertIn('_SYSTEMD_INVOCATION_ID='+invocation,c.call_args.args[0])
 def test_generator_path_is_scoped(self):
  valid='/run/systemd/generator/'+g.MOUNT;self.assertEqual(g.safe_unit_path(valid),valid)
  for path in ('/etc/systemd/system/'+g.MOUNT,'/run/systemd/generator/../secret','/run/systemd/generator/a\\q.mount'):
   self.assertEqual(g.safe_unit_path(path),'REDACTED_PATH')
 def test_both_graph_payloads_survive_instability_and_late_output_limit(self):
  a={'sha256':'a'*64,'payload':'a'*110000};b={'sha256':'b'*64,'payload':'b'*110000}
  with contextlib.ExitStack() as stack:
   stack.enter_context(patch.object(g.socket,'gethostname',return_value='synthetic-compute'))
   stack.enter_context(patch.object(g.os,'geteuid',return_value=0,create=True))
   stack.enter_context(patch.object(g.signal,'SIGALRM',14,create=True));stack.enter_context(patch.object(g.signal,'signal'));stack.enter_context(patch.object(g.signal,'alarm',create=True));stack.enter_context(patch.object(g.time,'sleep'))
   stack.enter_context(patch.object(g,'read',return_value=b''));stack.enter_context(patch.object(g,'mounts',return_value=[{'target':'/','fstype':'ext4'}]))
   stack.enter_context(patch.object(g,'full_graph',side_effect=[a,b]));stack.enter_context(patch.object(g,'containers',return_value={'overflow':'x'*120000}))
   for name in ('fstab_rows','expected_link','block_inventory','units','file_hashes','frame','disk_correlation','uv_startup','uv_journal','path_metadata','settle','vdd_signatures','command','resolve_without_automount','process_paths','identity_comparison','journal_classes','kernel_classes'):
    stack.enter_context(patch.object(g,name,return_value='' if name=='command' else {}))
   stack.enter_context(patch.object(g,'jobs',return_value=[]));stack.enter_context(patch.object(g,'graph_recheck',return_value={'same_as_before':False}))
   out=io.StringIO()
   with contextlib.redirect_stdout(out):g.main()
  raw=out.getvalue().encode();rows,rejected=d.parse_observations(raw);self.assertEqual(rejected,0);self.assertLess(len(raw),300000)
  probes={r['probe']:r for r in rows if 'probe' in r};self.assertEqual(probes['full_unit_graph']['value'],a);self.assertEqual(probes['full_unit_graph_after']['value'],b)
  self.assertFalse(probes['graph_recheck']['value']['same_as_before']);self.assertEqual(probes['containers']['error'],'TOTAL_OUTPUT_LIMIT')

@unittest.skipUnless(sys.platform=='linux','real Linux metadata and D-Bus')
class LinuxIdentityTests(unittest.TestCase):
 def test_real_busctl_typed_property_existing_loaded_unit(self):
  raw=g.command(['/usr/bin/busctl','--system','--json=short','--auto-start=no','--allow-interactive-authorization=no','--timeout=3s','get-property','org.freedesktop.systemd1','/org/freedesktop/systemd1/unit/systemd_2djournald_2eservice','org.freedesktop.systemd1.Service','ExecStartPre'])
  self.assertEqual(json.loads(raw)['data'],[])
  result=g.parse_exec_pre(raw);self.assertEqual(result['signature'],'a(sasbttttuii)');self.assertTrue(result['argv_omitted'])
 def test_real_busctl_nonempty_prestart_existing_ssh_unit(self):
  raw=g.command(['/usr/bin/busctl','--system','--json=short','--auto-start=no','--allow-interactive-authorization=no','--timeout=3s','get-property','org.freedesktop.systemd1','/org/freedesktop/systemd1/unit/ssh_2eservice','org.freedesktop.systemd1.Service','ExecStartPre'])
  self.assertEqual(len(json.loads(raw)['data'][0]),10)
  result=g.parse_exec_pre(raw);self.assertFalse(result['empty']);self.assertTrue(all(row['argv_omitted'] for row in result['exec_start_pre']))
 def test_small_read_refuses_symlink_and_preserves_content(self):
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp)/'file';p.write_bytes(b'original');link=Path(temp)/'link';link.symlink_to(p)
   self.assertEqual(g.small_read(p),b'original')
   with self.assertRaises(OSError):g.small_read(link)
   self.assertEqual(p.read_bytes(),b'original')

if __name__=='__main__':unittest.main()
