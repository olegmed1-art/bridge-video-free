import contextlib,io,json,os,signal,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import diagnostic_guest as g
import prepare_driver as d
import watcher as w
import test_watcher as fixtures

END={'state':'READONLY_STORAGE_COLLECTION_ENDED','all_top_level_probes_returned':True,'failed_probes':[],'guest_application_writes':False,'service_actions':False,'evidence_complete_asserted':False,'graph_and_path_completeness_require_review':True}

class BoundaryTests(unittest.TestCase):
 def test_partial_invalid_lines_preserve_only_valid_observations(self):
  safe={'probe':'jobs_before','ok':True,'value':[]}
  raw=(json.dumps(safe)+'\n'+json.dumps({'probe':'arbitrary','secret':'NEVER_EMIT'})+'\n{broken').encode()
  rows,rejected=d.parse_observations(raw);self.assertEqual(rows,[safe]);self.assertEqual(rejected,2);self.assertNotIn('NEVER_EMIT',str(rows))
 def test_success_has_no_safety_or_completeness_assertion(self):
  rows,n=d.parse_observations(json.dumps(END).encode());self.assertEqual(n,0);self.assertFalse(rows[-1]['evidence_complete_asserted'])
 def test_options_never_emit_assignment_values(self):
  result=g.options('rw,x-systemd.device-timeout=90s,errors=remount-ro,password=SECRET')
  self.assertEqual(result,{'allowlisted':['rw'],'other_count':3})
 def test_telemetry_records_actual_call_count_and_only_scalars(self):
  c=fixtures.FakeClock();events=[];api=w.GitHub(w.Budget(c),lambda **kw:events.append(kw))
  p=fixtures.HTTPQuotaTests().response('59')
  with patch.object(w.subprocess,'run',return_value=p):api.get('workflows/42/runs')
  self.assertEqual(api.calls,1);self.assertEqual([r['phase'] for r in events],['request_started','response_metadata'])
  self.assertEqual(events[1],dict(phase='response_metadata',call=1,endpoint_class='run_list',status=200,remaining=59,reset=2000000000))
 def test_timeout_still_records_attempt_without_inventing_response(self):
  c=fixtures.FakeClock();events=[];api=w.GitHub(w.Budget(c),lambda **kw:events.append(kw))
  with patch.object(w.subprocess,'run',side_effect=subprocess.TimeoutExpired('fake',4)):
   with self.assertRaises(subprocess.TimeoutExpired):api.get('workflows/42/runs')
  self.assertEqual(api.calls,1);self.assertEqual(len(events),1)

@unittest.skipUnless(sys.platform=='linux','Linux primitives required')
class CollectorLinuxTests(unittest.TestCase):
 def test_real_bounded_pipe_and_timeout(self):
  self.assertEqual(g.command(['/usr/bin/python3','-c','print("scalar")']),'scalar\n')
  with patch.object(g,'LIMIT',8192):
   with self.assertRaises(ValueError):g.command(['/usr/bin/python3','-c','import os;os.write(1,b"x"*20000)'])
  started=time.monotonic()
  with self.assertRaises(TimeoutError):g.command(['/usr/bin/python3','-c','import time;time.sleep(30)'])
  self.assertLess(time.monotonic()-started,5.5)
 def test_resolver_symlink_and_autofs_guards(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'actual').mkdir();(root/'link').symlink_to(root/'actual');(root/'actual'/'file').write_text('synthetic')
   mi=[{'target':'/','fstype':'ext4'}]
   value=g.resolve_without_automount(str(root/'link'/'file'),mi)
   self.assertEqual(value['resolved'],str(root/'actual'/'file'));self.assertTrue(value['exists'])
   mi.append({'target':str(root/'actual'),'fstype':'autofs'})
   value=g.resolve_without_automount(str(root/'link'/'file'),mi);self.assertEqual(value['reason'],'AUTOFS_NOT_TRAVERSED')
 def test_collector_parent_death_kills_command(self):
  with tempfile.TemporaryDirectory() as tmp:
   pidfile=Path(tmp)/'pid'
   child='import os,time;from pathlib import Path;Path('+repr(str(pidfile))+').write_text(str(os.getpid()));time.sleep(30)'
   code='import diagnostic_guest as g;g.command('+repr(['/usr/bin/python3','-c',child])+')'
   parent=subprocess.Popen(['/usr/bin/python3','-B','-c',code],cwd=Path(__file__).parent,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   try:
    deadline=time.monotonic()+3
    while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.02)
    self.assertTrue(pidfile.exists());pid=int(pidfile.read_text());parent.kill();parent.wait(timeout=2)
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
     p=Path('/proc')/str(pid)/'stat'
     if not p.exists() or p.read_text().rsplit(')',1)[1].split()[0]=='Z':break
     time.sleep(.02)
    else:self.fail('collector child survived parent death')
   finally:
    if parent.poll() is None:parent.kill();parent.wait(timeout=2)

if __name__=='__main__':unittest.main()
