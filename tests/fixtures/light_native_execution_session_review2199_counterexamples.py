"""Historical PR 2199 review counterexamples from frozen head 80cae076.

The first block below is the exact portable script body executed on 2026-10-19
by independent reviewer task ``/root/review2193_i2_record`` (launch metadata:
gpt-5.6-sol, high) against GitHub head
``80cae0765de71ed6f1cb5d4544d2e0fefd1bb4b2``.  It exercised frozen production
definitions through the synthetic filesystem, clock, systemctl, /proc, and
legacy ports in ``tests/test_light_native_execution_session.py``.  It did not
exercise production, a real PID1, GitHub Actions, a database, a provider, a
host acceptance boundary, or a workload.  Saving this file did not rerun it.
To reproduce those three outputs, first check out the frozen 80ca head and
independently verify the source hashes below.  The verbatim script does not
enforce those pins; running it against the current checkout does not reproduce
the historical review.

Frozen reviewed SHA-256 values for that execution were:

* ops/light_native_lane_execution.py:
  d2c1553ca229d894db0cad6500c3927f779d26282dae0499c0ee2a952fe20362
* ops/light_native_lane_controller.py:
  4e79d2809d35198129a1aee2597bf4acc2ed482a8fa8e87fa85d38d4dcbd05a0
* tests/test_light_native_execution_session.py:
  4217ce52d7379af0c92fc1e108a327701bff4094cd37f7619c68a6a89a96fcab

The observed output was exactly:

* orphan_first FileExistsError  intent_created True first_unchanged True
  writes ('session-intent.json',)
* activating_race RuntimeError LANE_EXEC_SESSION_UNIT intent_created True
  first_created False
* execstart_unavailable ACCEPTED identity_main_pid 4242
  execstart_has_pid0 True

The selector-closure section preserves the historical extraction method and
observed result.  Its Python was reconstructed after the review from the
retained method and outputs; unlike the three counterexamples, it is not
claimed to be a byte-for-byte copy of the command previously run.  That review
read the exact GitHub head through ``gh api``, parsed Python imports with
``ast.walk`` (including function-local imports), and compared the recursive
36-file ``ops`` closure with both ``pull_request.paths`` and the embedded
``session = matches(...)`` list.  It found 18 trigger omissions and 26 session
selector omissions.  The extractor is retained for audit reproducibility and
was not rerun when this file was saved.
"""
import sys, importlib.util
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
p=Path('tests/test_light_native_execution_session.py')
spec=importlib.util.spec_from_file_location('_frozen_session_fixture',p)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

f=m.Fixture()
first=f.directory/'session-first.json'; intent=f.directory/'session-intent.json'
f.files[first]=b'{'
try: f.enter()
except Exception as e: print('orphan_first',type(e).__name__,str(e),'intent_created',intent in f.files,'first_unchanged',f.files[first]==b'{','writes',tuple(f.writes))
else: print('orphan_first UNEXPECTED_ACCEPT')

f=m.Fixture()
f.loaded['ActiveState']='activating';f.loaded['SubState']='start'
try: f.enter()
except Exception as e: print('activating_race',type(e).__name__,str(e),'intent_created',f.directory/'session-intent.json' in f.files,'first_created',f.directory/'session-first.json' in f.files)
else: print('activating_race UNEXPECTED_ACCEPT')

f=m.Fixture()
raw=f.loaded['ExecStart']
raw=raw.replace('start_time=[Mon 2026-10-19 12:00:00 UTC]','start_time=[n/a]').replace('pid=4242','pid=0')
f.loaded['ExecStart']=raw
try:
 s=f.enter()
 print('execstart_unavailable ACCEPTED','identity_main_pid',s.identity['loaded']['MainPID'],'execstart_has_pid0','pid=0' in s.identity['loaded']['ExecStart'])
except Exception as e: print('execstart_unavailable REFUSED',type(e).__name__,str(e))


# Reconstructed audit extractor; not part of the verbatim executed block above.
def historical_selector_closure():
 import ast, base64, fnmatch, json, re, subprocess, textwrap
 repository='olegmed1-art/bridge-video-free'
 revision='80cae0765de71ed6f1cb5d4544d2e0fefd1bb4b2'
 workflow='.github/workflows/native-host-preflight.yml'
 def source(name):
  raw=subprocess.check_output(['gh','api',f'repos/{repository}/contents/{name}?ref={revision}'],timeout=20)
  row=json.loads(raw)
  assert row['encoding']=='base64' and row['sha']
  return base64.b64decode(row['content'])
 workflow_bytes=source(workflow)
 workflow_text=workflow_bytes.decode('utf-8')
 trigger_block=re.search(r'(?m)^  pull_request:\n    paths:\n((?:      - .+\n)+)',workflow_text)
 assert trigger_block is not None
 trigger_patterns=[ast.literal_eval(line[8:]) for line in trigger_block[1].splitlines()]
 script_match=re.search(r"(?ms)^        run: \|\n          /usr/bin/python3 -I -S -B - <<'PY'\n(.*?)^          PY\n",workflow_text)
 assert script_match is not None
 selector_tree=ast.parse(textwrap.dedent(script_match[1]))
 session_assign=[node for node in selector_tree.body if isinstance(node,ast.Assign)
                 and any(isinstance(target,ast.Name) and target.id=='session' for target in node.targets)]
 assert len(session_assign)==1
 session_call=session_assign[0].value
 assert isinstance(session_call,ast.Call) and isinstance(session_call.func,ast.Name) and session_call.func.id=='matches'
 pattern_node=session_call.args[0]
 assert isinstance(pattern_node,ast.List)
 session_patterns=[]
 for node in pattern_node.elts:
  if isinstance(node,ast.Constant) and type(node.value) is str:
   session_patterns.append(node.value)
  elif isinstance(node,ast.Name) and node.id=='workflow':
   session_patterns.append(workflow)
  else:
   raise AssertionError('Literal session selector or workflow name required')
 test_tree=ast.parse(source('tests/test_light_native_execution_session.py'))
 actual_calls=[node for node in ast.walk(test_tree) if isinstance(node,ast.Call)
               and isinstance(node.func,ast.Name) and node.func.id=='actual']
 assert actual_calls and all(node.args and isinstance(node.args[0],ast.Constant)
                             and type(node.args[0].value) is str for node in actual_calls)
 pending=[node.args[0].value for node in actual_calls]
 closure=set()
 while pending:
  name=pending.pop()
  if name in closure: continue
  closure.add(name)
  assert len(closure)<=128
  for node in ast.walk(ast.parse(source(name))):
   if isinstance(node,ast.Import):
    paths=[alias.name.replace('.','/')+'.py' for alias in node.names if alias.name.startswith('ops.')]
   elif isinstance(node,ast.ImportFrom) and node.module=='ops':
    paths=['ops/'+alias.name+'.py' for alias in node.names]
   elif isinstance(node,ast.ImportFrom) and node.module and node.module.startswith('ops.'):
    paths=[node.module.replace('.','/')+'.py']
   else: paths=[]
   pending.extend(paths)
 missing_trigger=sorted(name for name in closure if not any(fnmatch.fnmatchcase(name,pattern) for pattern in trigger_patterns))
 missing_session=sorted(name for name in closure if not any(fnmatch.fnmatchcase(name,pattern) for pattern in session_patterns))
 expected_trigger=[
  'ops/light_native_bounded.py','ops/light_native_lane_feed.py',
  'ops/light_native_lane_owner.py','ops/light_native_pilot_owner.py',
  'ops/light_native_service_controller.py','ops/native_maintenance_budgets.py',
  'ops/native_maintenance_coordination.py','ops/native_maintenance_driver.py',
  'ops/native_maintenance_executor.py','ops/native_maintenance_lifetime.py',
  'ops/native_maintenance_owner_attest.py','ops/native_maintenance_owner_host.py',
  'ops/native_maintenance_run_guard.py','ops/native_maintenance_store.py',
  'ops/native_maintenance_store_runner.py','ops/native_maintenance_supervisor.py',
  'ops/native_maintenance_workflow_api.py','ops/oracle_autopilot_source_preflight.py']
 assert len(closure)==36
 assert missing_trigger==expected_trigger
 assert len(missing_session)==26
 print('selector_closure',len(closure),'trigger_omissions',tuple(missing_trigger),
       'session_omissions',tuple(missing_session))


if __name__ == '__main__':
 historical_selector_closure()
