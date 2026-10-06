"""Synthetic diagnostics only: no root, /run, PID1 or external operations."""
import contextlib,io,json,os,subprocess,sys,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
test_root=Path(__file__).resolve().parent
sys.path.insert(0,str(test_root/'source' if (test_root/'source').is_dir() else test_root))
import ci_bootstrap as boot
def scenario(case):
 return {'case':case,'verdict':'PASS','actual_candidate_supervise_body':True,
  'natural_control_drain':True,'natural_worker_drain':True,'candidate_admin_clients_gone':True,
  'candidate_admin_bound_seconds':62,'seconds_since_control_activation':1.0,
  'outer_launcher_is_harness_owned':True,'external_cleanup_counted_as_candidate_pass':False,
  'production':False,'live_sql':False}
def records():
 return [scenario(case) for case in boot.CASES]+[{'qualification':'PASS_ACTUAL_CANDIDATE_SYNTHETIC',
  'cases':4,'frozen_supervisor_qualified':False,'production_ready':False,'external_containment_is_not_candidate_pass':True}]
def wire(values=None):
 return ('\n'.join(json.dumps(v) for v in (records() if values is None else values))+'\n').encode()
class Diagnostics(unittest.TestCase):
 def invoke(self,fail=None,cleanup_fail=False,raw=None,exitcode=0,write_fail=False,preflight=False):
  output=io.StringIO();capture=Mock();contain=Mock()
  child=types.SimpleNamespace(stdin=Mock(),stdout=Mock(),returncode=exitcode)
  control=types.SimpleNamespace(new_unit=Mock(return_value='synthetic-outer'),
   command=Mock(return_value=['synthetic-only']),gated_code=Mock(return_value='synthetic'),
   identity=Mock(return_value=({'MainPID':'123'},Path('/synthetic/cgroup'),1)))
  profile=types.SimpleNamespace(empty=Mock(return_value=True))
  args=types.SimpleNamespace(scope_approved=not preflight,source_root=Path('/synthetic/source'),
   runtime_pin='0'*64,suite_pin='1'*64,bootstrap_pin='2'*64)
  def capture_body(proc,sink):
   sink.extend(wire() if raw is None else raw)
   if fail=='child_capture':raise boot.OutputRefused('CHILD_CAPTURE_TIMEOUT')
  capture.side_effect=capture_body
  if cleanup_fail:contain.side_effect=RuntimeError('SECRET_CLEANUP_DO_NOT_PRINT')
  original_emit=boot.emit_diagnostic
  def emit(value):
   if write_fail and 'child_diagnostics' in value:raise OSError('SECRET_STDOUT_DO_NOT_PRINT')
   original_emit(value)
  stage=Mock(return_value=Path('/synthetic/stage'));read=Mock(return_value=b'synthetic')
  popen=Mock(return_value=child)
  if fail=='preflight':read.side_effect=RuntimeError('SECRET_PREFLIGHT_DO_NOT_PRINT')
  if fail=='staging':stage.side_effect=RuntimeError('SECRET_STAGING_DO_NOT_PRINT')
  if fail=='outer_launch':popen.side_effect=RuntimeError('SECRET_LAUNCH_DO_NOT_PRINT')
  if fail=='identity':control.identity.side_effect=RuntimeError('SECRET_IDENTITY_DO_NOT_PRINT')
  if fail=='receipt_validation':profile.empty.return_value=False
  old_path=list(sys.path)
  try:
   with contextlib.ExitStack() as stack:
    for context in [patch.object(sys,'platform','linux'),patch.object(os,'getuid',return_value=0,create=True),
     patch.object(os,'getgid',return_value=0,create=True),patch.dict(os.environ,{'GITHUB_ACTIONS':'true'},clear=True),
     patch.object(Path,'read_text',return_value='systemd'),patch.object(Path,'is_file',return_value=True),
     patch.object(boot,'read_checked',read),patch.object(boot,'stage',stage),
     patch.dict(sys.modules,{'stagea_control':control,'stagea_watchdog':types.SimpleNamespace(profile=lambda:profile)}),
     patch.object(boot.subprocess,'Popen',popen),patch.object(boot.select,'select',return_value=([child.stdout],[],[])),
     patch.object(boot.os,'read',return_value=b'GATE\n'),patch.object(boot,'bounded_capture',capture),
     patch.object(boot,'contain',contain),patch.object(boot,'emit_diagnostic',side_effect=emit),
     contextlib.redirect_stdout(output)]:stack.enter_context(context)
    rc=boot.run(args)
  finally:sys.path[:]=old_path
  text=output.getvalue();self.assertNotIn('SECRET_',text)
  values=[json.loads(line) for line in text.splitlines()]
  return rc,values,contain,stage,control
 def test_each_primary_phase_has_fixed_code(self):
  for phase in ('preflight','staging','outer_launch','identity','child_capture','receipt_validation'):
   with self.subTest(phase=phase):
    rc,values,contain,_,_=self.invoke(fail=phase)
    self.assertEqual(rc,78)
    failure=values[-1]['primary_failure'];self.assertEqual(failure['phase'],phase)
    self.assertEqual(failure['error_code'],'CHILD_CAPTURE_TIMEOUT' if phase=='child_capture' else boot.PHASE_CODES[phase])
    self.assertEqual(contain.call_count,0 if phase in ('preflight','staging') else 1)
 def test_success_requires_separate_verified_cleanup(self):
  rc,values,contain,_,control=self.invoke()
  self.assertEqual(rc,0);self.assertEqual(contain.call_count,1)
  final=values[-1];self.assertIsNone(final['primary_failure']);self.assertEqual(final['cleanup_status'],'VERIFIED')
  self.assertEqual(final['child_exit_status'],'EXIT_ZERO')
  self.assertIs(final['root_pid1_executed'],True)
  self.assertEqual(control.identity.call_args.args[1],300);self.assertTrue(control.identity.call_args.kwargs['network'])
  self.assertEqual([v['bootstrap_phase'] for v in values if 'bootstrap_phase' in v],
   ['preflight','staging','outer_launch','identity','child_capture','receipt_validation','containment'])
 def test_primary_and_cleanup_failures_do_not_mask_each_other(self):
  rc,values,contain,_,_=self.invoke(fail='child_capture',cleanup_fail=True)
  self.assertEqual(rc,78);self.assertEqual(contain.call_count,1)
  self.assertEqual(values[-1]['primary_failure'],{'phase':'child_capture','error_code':'CHILD_CAPTURE_TIMEOUT'})
  self.assertEqual(values[-1]['cleanup_status'],'CONTAINMENT_UNPROVEN')
 def test_cleanup_only_failure_never_claims_pass(self):
  rc,values,contain,_,_=self.invoke(cleanup_fail=True)
  self.assertEqual(rc,78);self.assertIsNone(values[-1]['primary_failure'])
  self.assertEqual(values[-1]['cleanup_status'],'CONTAINMENT_UNPROVEN')
  self.assertFalse(values[-1]['harness_containment_verified'])
 def test_partial_child_records_survive_capture_failure(self):
  rc,values,_,_,_=self.invoke(fail='child_capture',raw=wire(records()[:2]))
  diagnostic=next(v for v in values if 'child_diagnostics' in v)
  self.assertEqual(len(diagnostic['child_diagnostics']),2);self.assertEqual(diagnostic['child_diagnostics_status'],'PARTIAL')
  self.assertEqual(rc,78)
 def test_typed_records_emitted_before_final_receipt_validation(self):
  rc,values,_,_,_=self.invoke(fail='receipt_validation')
  diagnostic=next(v for v in values if 'child_diagnostics' in v)
  self.assertEqual(len(diagnostic['child_diagnostics']),5)
  self.assertLess(values.index(diagnostic),next(i for i,v in enumerate(values) if v.get('bootstrap_phase')=='containment'))
  self.assertEqual(rc,78)
 def test_unapproved_preflight_never_stages_or_contains(self):
  rc,values,contain,stage,_=self.invoke(preflight=True)
  self.assertEqual(rc,78);stage.assert_not_called();contain.assert_not_called()
  self.assertEqual(values[-1]['cleanup_status'],'NO_OUTER_LAUNCH_ATTEMPT')
  self.assertFalse(values[-1]['outer_launch_attempted'])
  self.assertIsNone(values[-1]['root_pid1_executed'])
 def test_child_exit_failure_separate_from_cleanup(self):
  rc,values,_,_,_=self.invoke(exitcode=78)
  self.assertEqual(rc,78);self.assertEqual(values[-1]['primary_failure']['error_code'],'CHILD_EXIT_NONZERO')
  self.assertEqual(values[-1]['child_exit_status'],'EXIT_NONZERO')
  self.assertEqual(values[-1]['cleanup_status'],'VERIFIED')
 def test_diagnostic_write_failure_still_contains(self):
  rc,values,contain,_,_=self.invoke(write_fail=True)
  self.assertEqual(rc,78);contain.assert_called_once()
  self.assertEqual(values[-1]['cleanup_status'],'VERIFIED')
 def test_secret_or_unknown_child_field_is_not_emitted(self):
  bad=scenario(boot.CASES[0]);bad['raw_exception']='SECRET_UNKNOWN_DO_NOT_PRINT'
  rc,values,_,_,_=self.invoke(raw=wire([bad]))
  self.assertEqual(rc,78)
  self.assertEqual(next(v for v in values if 'child_diagnostics' in v)['child_diagnostics'],[])
 def test_invalid_types_duplicates_order_and_nonfinite(self):
  cases=[]
  bad=scenario('foreign');cases.append(wire([bad]))
  bad=scenario(boot.CASES[0]);bad['production']=0;cases.append(wire([bad]))
  bad=scenario(boot.CASES[0]);bad['seconds_since_control_activation']=float('nan');cases.append(wire([bad]))
  cases.append(b'{"case":"payload","case":"payload"}\n')
  for raw in cases:
   with self.subTest(raw=raw):
    rows,status=boot.typed_child_diagnostics(raw);self.assertEqual(rows,[]);self.assertEqual(status,'CHILD_RECORD_INVALID')
 def test_record_count_and_line_limit_retain_only_valid_prefix(self):
  rows,status=boot.typed_child_diagnostics(wire()+b'{"raw_exception":"SECRET"}\n')
  self.assertEqual(len(rows),5);self.assertEqual(status,'CHILD_RECORD_LIMIT')
  rows,status=boot.typed_child_diagnostics(wire(records()[:1])+b'x'*4097)
  self.assertEqual(len(rows),1);self.assertEqual(status,'CHILD_RECORD_LIMIT')
 def test_output_cap_retains_prefix_and_never_echoes_tail(self):
  rows,status=boot.typed_child_diagnostics(wire()+b'x'*boot.MAX_CHILD_BYTES)
  self.assertEqual(len(rows),5);self.assertEqual(status,'CHILD_OUTPUT_LIMIT')
  self.assertLess(len(json.dumps(rows)),8192)
 def test_fail_case_record_is_typed_diagnostic_not_pass(self):
  rows,status=boot.typed_child_diagnostics(wire([{'case':'payload','verdict':'FAIL_NOT_QUALIFIED','production':False,'live_sql':False}]))
  self.assertEqual(rows[0]['verdict'],'FAIL_NOT_QUALIFIED');self.assertEqual(status,'PARTIAL')
 def test_pass_summary_cannot_override_fail_case(self):
  data=records();data[0]={'case':'payload','verdict':'FAIL_NOT_QUALIFIED','production':False,'live_sql':False}
  rc,values,_,_,_=self.invoke(raw=wire(data))
  self.assertEqual(rc,78);self.assertEqual(values[-1]['primary_failure']['error_code'],'RECEIPTS_REFUSED')
 def test_bounded_capture_overflow_memory_and_deadline(self):
  child=types.SimpleNamespace(stdin=Mock(),stdout=Mock(),wait=Mock())
  child.stdout.fileno.return_value=1;raw=bytearray()
  with patch.object(boot.time,'monotonic',return_value=0),patch.object(boot.select,'select',return_value=([child.stdout],[],[])),patch.object(boot.os,'read',side_effect=[b'x'*4096]*16+[b'x']):
   with self.assertRaises(boot.OutputRefused) as error:boot.bounded_capture(child,raw)
  self.assertEqual(error.exception.code,'CHILD_OUTPUT_LIMIT');self.assertEqual(len(raw),65537)
  raw=bytearray(wire(records()[:1]))
  with patch.object(boot.time,'monotonic',return_value=0),patch.object(boot.select,'select',return_value=([],[],[])):
   with self.assertRaises(boot.OutputRefused) as error:boot.bounded_capture(child,raw)
  self.assertEqual(error.exception.code,'CHILD_CAPTURE_TIMEOUT');self.assertEqual(len(boot.typed_child_diagnostics(raw)[0]),1)
 def test_bounded_capture_eof_and_same_305s_wait_ceiling(self):
  child=types.SimpleNamespace(stdin=Mock(),stdout=Mock(),wait=Mock());child.stdout.fileno.return_value=1
  raw=bytearray()
  with patch.object(boot.time,'monotonic',return_value=0),patch.object(boot.select,'select',return_value=([child.stdout],[],[])),patch.object(boot.os,'read',side_effect=[b'{}\n',b'']):
   boot.bounded_capture(child,raw)
  self.assertEqual(raw,b'{}\n');child.wait.assert_called_once_with(timeout=305);child.stdin.close.assert_called_once()
if __name__=='__main__':unittest.main()
