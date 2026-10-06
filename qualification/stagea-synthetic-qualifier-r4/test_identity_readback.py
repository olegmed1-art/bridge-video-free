"""Synthetic-only regression: all processes and cgroup I/O are mocked."""
import ast,contextlib,copy,hashlib,inspect,io,json,sys,types,unittest
from pathlib import Path
from unittest.mock import patch,Mock
test_root=Path(__file__).resolve().parent
sys.path.insert(0,str(test_root/'source' if (test_root/'source').is_dir() else test_root))
import stagea_control as c
import ci_bootstrap as b
UNIT='bridge-stagea-containment-'+c.ROOT.name.encode().hex()[:12].ljust(12,'0')+'-123-1-0123456789abcdef.service'
class Group:
 def __truediv__(self,other):return self
 def is_dir(self):return True
 def read_text(self):return 'populated 1\n'
 def stat(self):return types.SimpleNamespace(st_ino=7)
def good():
 return {**c.PROPERTIES,**c.NETWORK,'RuntimeMaxUSec':'5min','ReadOnlyPaths':str(c.ROOT),
 'ReadWritePaths':str(c.ROOT/'evidence'),'ActiveState':'active','InvocationID':'a'*32,
 'MainPID':'123','ControlGroup':'/system.slice/'+UNIT,'ActiveEnterTimestampMonotonic':'1'}
def text(state):return ('\n'.join(k+'='+v for k,v in state.items())+'\n').encode()
class Readback(unittest.TestCase):
 def call(self,state=None,typed=b'a(sasbttttuii) 0\na(sasbttttuii) 0\n',rc=0,show_rc=0,exc=None,duplicate=False,group=None):
  state=good() if state is None else state
  raw=text(state)+(b'Type=exec\n' if duplicate else b'')
  bus=Mock(return_value=types.SimpleNamespace(returncode=rc,stdout=typed))
  if exc:bus.side_effect=exc
  with patch.object(c,'ctl',return_value=types.SimpleNamespace(returncode=show_rc,stdout=raw)),patch.object(c.subprocess,'run',bus),patch.object(c,'Path',return_value=group or Group()):
   value=c.identity(UNIT,300,network=True)
  return value,bus
 def refused(self,code,**kw):
  with self.assertRaises(c.IdentityRefused) as err:self.call(**kw)
  self.assertEqual(err.exception.code,code)
  self.assertNotIn('SECRET',str(err.exception))
 def test_missing_empty_arrays_require_typed_proof(self):
  state=good();state.pop('ExecStop');state.pop('ExecStopPost')
  (s,g,i),bus=self.call(state)
  self.assertEqual(s['ExecStop'],'');self.assertEqual(s['ExecStopPost'],'');self.assertEqual(i,7)
  args=bus.call_args.args[0]
  self.assertEqual(args[-2:],['ExecStop','ExecStopPost'])
  self.assertIn('get-property',args);self.assertNotIn('set-property',args)
  self.assertEqual(args[5],'/org/freedesktop/systemd1/unit/'+UNIT.replace('-','_2d').replace('.','_2e'))
  self.assertEqual(bus.call_args.kwargs['timeout'],5)
 def test_printed_empty_arrays_still_require_proof(self):
  self.call()
  self.refused('STOP_QUERY',rc=1)
 def test_no_missing_nonstop_property_is_accepted(self):
  for key in c.PROPERTIES:
   if key in ('ExecStop','ExecStopPost'):continue
   state=good();state.pop(key)
   with self.subTest(key=key):self.refused('PROFILE_'+key,state=state)
 def test_stop_handlers_nonempty_text_or_typed_refuse(self):
  for key,code,pos in [('ExecStop','STOP_EXECSTOP_TYPED',0),('ExecStopPost','STOP_EXECSTOPPOST_TYPED',1)]:
   state=good();state[key]='SECRET_NONEMPTY_HANDLER'
   self.refused(code,state=state)
   rows=[b'a(sasbttttuii) 0',b'a(sasbttttuii) 0'];rows[pos]=b'a(sasbttttuii) 1 SECRET'
   self.refused(code,typed=b'\n'.join(rows)+b'\n')
 def test_query_failure_or_exception_does_not_normalize_missing(self):
  state=good();state.pop('ExecStop');state.pop('ExecStopPost')
  for kw in [dict(rc=1),dict(exc=OSError('SECRET_TRANSPORT')),dict(exc=TimeoutError('SECRET_TIMEOUT'))]:
   self.refused('STOP_QUERY',state=state,**kw)
  self.refused('SHOW_QUERY',show_rc=1)
 def test_partial_extra_wrong_type_empty_output_refuse(self):
  for value in [b'',b'a(sasbttttuii) 0\n',b'a(sasbttttuii) 0\n'*3]:
   self.refused('STOP_REPLY_COUNT',typed=value)
  self.refused('STOP_EXECSTOP_TYPED',typed=b'as 0\na(sasbttttuii) 0\n')
  self.refused('STOP_QUERY',typed=b'x'*16384)
 def test_duplicate_show_fields_refuse(self):self.refused('SHOW_FORMAT',duplicate=True)
 def test_closed_predicates_are_independent_and_sensitive_data_hidden(self):
  for key,value,code in [('ReadOnlyPaths','SECRET','READ_ONLY_PATHS'),('ReadWritePaths','SECRET','READ_WRITE_PATHS'),
   ('PrivateNetwork','no','PRIVATE_NETWORK'),('RestrictAddressFamilies','AF_INET','ADDRESS_FAMILIES'),
   ('ActiveState','inactive','ACTIVE_STATE'),('InvocationID','SECRET','INVOCATION_ID'),
   ('MainPID','SECRET','MAIN_PID'),('ControlGroup','SECRET','CONTROL_GROUP'),
   ('ActiveEnterTimestampMonotonic','SECRET','ACTIVATION_TIMESTAMP')]:
   state=good();state[key]=value
   with self.subTest(key=key):self.refused(code,state=state)
  g=Group();g.read_text=lambda:'populated 0';self.refused('CGROUP_POPULATED',group=g)
  g=Group();g.read_text=Mock(side_effect=OSError('SECRET_IO'));self.refused('CGROUP_READ',group=g)
  with self.assertRaises(c.Refused):c.IdentityRefused('SECRET_ARBITRARY_CODE')
 def test_command_and_gate_are_unchanged(self):
  # Frozen AST pins of the independently reviewed original functions.
  # No sibling checkout, execution of baseline code, or runtime transport.
  expected={'command': 'c5ce8c14ff6a8135a2daf7100152b69bbeb7cfaae3b98f7a6fff7a1d3457c013', 'gated_code': 'aad01c42588f0cf4a3aacbdba263dcc975678259736bef72896b020d43dd439b'}
  for name,pin in expected.items():
   node=ast.parse(inspect.getsource(getattr(c,name))).body[0]
   self.assertEqual(hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest(),pin)
 def test_bootstrap_emits_only_closed_condition_and_still_contains(self):
  # Reuse prior synthetic harness, replacing its mocked control object only.
  sys.path.insert(0,str(Path(__file__).resolve().parent))
  import test_bootstrap_diagnostics as prior
  harness=prior.Diagnostics()
  original=prior.types.SimpleNamespace
  def namespace(**kw):
   obj=original(**kw)
   if 'identity' in kw:
    obj.IdentityRefused=c.IdentityRefused;obj.IDENTITY_CODES=c.IDENTITY_CODES
    obj.identity.side_effect=c.IdentityRefused('STOP_EXECSTOP_TYPED')
   return obj
  with patch.object(prior.types,'SimpleNamespace',side_effect=namespace):
   rc,values,contain,stage,control=harness.invoke()
  self.assertEqual(rc,78);contain.assert_called_once()
  self.assertEqual(values[-1]['primary_failure'],{'phase':'identity','error_code':'OUTER_IDENTITY_REFUSED','identity_condition':'STOP_EXECSTOP_TYPED'})
  self.assertEqual(values[-1]['cleanup_status'],'VERIFIED')
  self.assertIsNone(values[-1]['root_pid1_executed'])
if __name__=='__main__':unittest.main()
