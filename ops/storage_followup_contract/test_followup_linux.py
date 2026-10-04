import hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import diagnostic_guest as g
import prepare_driver as d

class FollowupTests(unittest.TestCase):
 def test_option_like_name_after_separator(self):
  with patch.object(g,'command',return_value='Id=-.mount\nNames=-.mount\nLoadState=loaded') as call:
   g.units({'-.mount'})
  argv=call.call_args.args[0];self.assertEqual(argv[-2:],['--','-.mount'])
 def test_missing_seed_never_implies_idle(self):
  self.assertTrue(all(r['state']=='UNKNOWN' for r in g.process_paths({}).values()))
  self.assertTrue(all(r['state']=='UNKNOWN' for r in g.identity_comparison({},{}).values()))
  self.assertEqual(g.graph_recheck(None)['state'],'UNKNOWN')
 def test_typed_failure_transport(self):
  row={'probe':'seed_units','ok':False,'error':'CommandFailure','returncode':1}
  self.assertEqual(d.parse_observations(json.dumps(row).encode()),([row],0))
  row['returncode']='secret';self.assertEqual(d.parse_observations(json.dumps(row).encode())[1],1)
 def test_precise_noauto_diff_and_restore(self):
  for separator in (' ','\t','  '):
   for ending in ('\n','\r\n'):
    raw=('# keep'+ending+separator.join(['UUID='+g.UUID,'/mnt/bridge-scratch','ext4','defaults,nofail,noatime','0','2'])+' # retain'+ending).encode()
    baseline=hashlib.sha256(raw).hexdigest();record=g.noauto_candidate(raw,baseline)
    at=record['insert_offset'];edited=raw[:at]+b',noauto'+raw[at:]
    self.assertEqual(hashlib.sha256(edited).hexdigest(),record['candidate_sha256'])
    self.assertEqual(edited[:at]+edited[at+7:],raw)
    with self.assertRaises(ValueError):g.noauto_candidate(edited,baseline)
    with self.assertRaises(ValueError):g.noauto_candidate(raw+raw,hashlib.sha256(raw+raw).hexdigest())

@unittest.skipUnless(sys.platform=='linux','Linux command regression')
class ActualLinuxTests(unittest.TestCase):
 def test_real_systemctl_root_mount(self):
  actual=g.units({'-.mount'},('Id','Names','LoadState'))
  self.assertEqual(actual['-.mount']['LoadState'],'loaded')
 def test_real_failed_command_is_typed(self):
  with self.assertRaises(g.CommandFailure) as context:g.command(['/usr/bin/python3','-c','raise SystemExit(7)'])
  self.assertEqual(context.exception.returncode,7)
 def test_signature_commands_preserve_synthetic_regular_file(self):
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'synthetic';raw=b'\0'*1048576;path.write_bytes(raw)
   for name in ('blkid','file','wipefs'):
    self.assertIsNotNone(g.executable(name))
    result=g.signature_probe(name,str(path));self.assertNotEqual(result.get('state'),'UNKNOWN')
    self.assertEqual(path.read_bytes(),raw)
 def test_full_current_manager_graph(self):
  graph=g.full_graph();self.assertTrue(graph['closure_complete']);self.assertIn('-.mount',graph['unit_names'])
  self.assertLess(len(json.dumps(graph)),220000)

if __name__=='__main__':unittest.main()
