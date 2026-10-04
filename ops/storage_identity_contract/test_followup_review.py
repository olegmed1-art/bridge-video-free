"""Independent offline regressions; real collector functions, synthetic inputs only."""
import hashlib,importlib.util,itertools,json,unittest
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parent/'diagnostic_guest.py'
spec=importlib.util.spec_from_file_location('review_guest',P);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
class ReviewTests(unittest.TestCase):
 def test_option_terminator(self):
  raw='Id=-.mount\nNames=-.mount\nLoadState=loaded\n'
  with patch.object(g,'command',return_value=raw) as c:g.units({'-.mount'})
  args=c.call_args.args[0];self.assertLess(args.index('--'),args.index('-.mount'))
 def test_extra_alias_has_index(self):
  def units(names,*args):return {'a.service':{**dict.fromkeys(g.EDGES,[]),'Id':'a.service','Names':['a.service','alias.service'],'LoadState':'loaded'}}
  with patch.object(g,'SEEDS',('a.service',)),patch.object(g,'command',return_value='a.service loaded active running description'),patch.object(g,'units',side_effect=units):r=g.full_graph()
  self.assertIn('alias.service',r['unit_names']);self.assertTrue(r['closure_complete'])
 def test_all_three_node_graphs(self):
  names=['a.service','b.service','c.service'];pairs=list(itertools.product(range(3),repeat=2))
  for mask in range(512):
   adj={i:{j for bit,(x,j) in enumerate(pairs) if x==i and mask&(1<<bit)} for i in range(3)}
   expected={0}
   while True:
    nxt=expected|set().union(*(adj[i] for i in expected))
    if nxt==expected:break
    expected=nxt
   def units(todo,*args):return {n:{**dict.fromkeys(g.EDGES,[]),'Id':n,'Names':[n],'LoadState':'loaded','Requires':[names[j] for j in adj[names.index(n)]]} for n in todo}
   with patch.object(g,'SEEDS',(names[0],)),patch.object(g,'command',return_value=names[0]+' loaded active running'),patch.object(g,'units',side_effect=units):r=g.full_graph()
   self.assertEqual(set(r['unit_names']),{names[i] for i in expected},mask)
   actual={(r['unit_names'][a],r['unit_names'][b]) for a,k,targets in r['edges'] if r['edge_properties'][k]=='Requires' for b in targets}
   self.assertEqual(actual,{(names[i],names[j]) for i in expected for j in adj[i]},mask)
 def test_unknown_graph_no_false_stopped(self):
  self.assertTrue(all(v.get('state')=='UNKNOWN' for v in g.process_paths({}).values()))
  self.assertTrue(all(v.get('state')=='UNKNOWN' for v in g.identity_comparison({},{}).values()))
  self.assertEqual(g.graph_recheck(None)['state'],'UNKNOWN')
 def test_exact_noauto_whitespace_and_comments(self):
  for nl,sep in itertools.product((b'\n',b'\r\n'),(b' ',b'\t',b'  ')):
   raw=b'# keep private comment'+nl+sep.join([('UUID='+g.UUID).encode(),b'/mnt/bridge-scratch',b'ext4',b'defaults,nofail,noatime',b'0',b'2'])+b' # retain'+nl
   r=g.noauto_candidate(raw,hashlib.sha256(raw).hexdigest());at=r['insert_offset'];candidate=raw[:at]+b',noauto'+raw[at:]
   self.assertEqual(hashlib.sha256(candidate).hexdigest(),r['candidate_sha256']);self.assertEqual(candidate.replace(b',noauto',b'',1),raw);self.assertFalse(r['applied'])
 def test_noauto_drift_duplicates(self):
  raw=('UUID='+g.UUID+' /mnt/bridge-scratch ext4 defaults,nofail,noatime 0 2\n').encode()
  for data,pin in ((raw,'0'*64),(raw+raw,hashlib.sha256(raw+raw).hexdigest()),(raw.replace(b'ext4',b'xfs'),hashlib.sha256(raw.replace(b'ext4',b'xfs')).hexdigest())):
   with self.assertRaises(ValueError):g.noauto_candidate(data,pin)
 def test_signature_readonly_argv_and_redaction(self):
  responses={'blkid':'TYPE=ext4\nLABEL=SECRET_SENTINEL\nUUID=a-b-c\n','file':'data\n','wipefs':'{"signatures":[]}'}
  for name in responses:
   with patch.object(g,'executable',return_value='/usr/bin/'+name),patch.object(g,'command',return_value=responses[name]) as c:r=g.signature_probe(name,'/dev/vdd')
   args=c.call_args.args[0];self.assertIn('--',args);self.assertEqual(args[-1],'/dev/vdd');self.assertNotIn('SECRET_SENTINEL',json.dumps(r));self.assertTrue(r['absence_does_not_prove_blank'])
   if name=='wipefs':self.assertIn('--no-act',args)
   if name=='blkid':self.assertIn('-p',args)
 def test_uv_argv_secret_not_emitted(self):
  raw=json.dumps({'type':'a(sasbttttuii)','data':[['/usr/bin/test',['SECRET_SENTINEL'],False,0,0,0,0,0,0,0]]})
  with patch.object(g,'command',return_value=raw),patch.object(g,'units',return_value={g.UV:{'InvocationID':'a'*32}}):r=g.uv_startup()
  self.assertNotIn('SECRET_SENTINEL',json.dumps(r));self.assertTrue(r['typed_status']['exec_start_pre'][0]['argv_omitted'])
if __name__=='__main__':unittest.main(verbosity=2)
