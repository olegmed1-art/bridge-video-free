"""Exact final PR 2199 I2 source/synthetic counterexamples run 2026-10-19.

Independent reviewer task ``/root/review2193_i2_record`` was launched with
metadata gpt-5.6-sol, high.  The body below is the exact Python passed to the
isolated local runtime after the corrected source freeze.  Filesystem, clock,
systemctl, /proc, and legacy ports were synthetic.  The selector check executed
the workflow's embedded selector with synthetic changed-path input.  This is
source and synthetic-port assurance, not production, real PID1, GitHub Actions,
database, provider, host acceptance, restore, or workload evidence.

Reviewed SHA-256 values:

* ops/light_native_lane_execution.py:
  930780e00a43454ae3df41a57d7aad77a708909788aa8c05989d643b0f8668e7
* ops/light_native_lane_controller.py:
  4e79d2809d35198129a1aee2597bf4acc2ed482a8fa8e87fa85d38d4dcbd05a0
* tests/test_light_native_execution_session.py:
  b1e04e5b5c39d68287c322e4b7bbd2d0d17d36025bd750ac8e5a5e2ed23cb6af
* tests/fixtures/light_native_execution_session_systemd.py:
  6e307b473fae28b93919d854239ee0d2070c0461dbd6f648645fef5257feaacd
* .github/workflows/native-host-preflight.yml:
  41616d470ace805be92b4fbd0b5ace65b5f68e092be1148365809e1c91f51a89
* tests/test_native_host_preflight_scope.py:
  292336379dd3e76a7cecd8e507f773525c990ab4b30d6a630a726f3229da76ad

Observed labels were:

* orphan_preserved REFUSED RuntimeError LANE_EXEC_SESSION_REENTRY writes ()
* pending_timeout_no_intent REFUSED RuntimeError LANE_EXEC_SESSION_READINESS writes ()
* pending_input_drift_no_intent REFUSED RuntimeError SYNTHETIC_PROTECTED_READ writes ()
* pending_to_active ACCEPTED waits (0.05,) end_ns 220000000000
* execstart_pid0_no_start_no_intent REFUSED RuntimeError LANE_EXEC_SESSION_UNIT writes ()
* post_intent_identity_drift REFUSED RuntimeError LANE_EXEC_SESSION_INPUT_CHANGED
  writes ('session-intent.json',)
* selector_closure ACCEPTED sources 36 trigger_missing () selector_missing ()

The script itself does not enforce its source pins.  Reproduction requires the
six exact hashes above.  Saving this file did not rerun the script.
"""
import copy, importlib.util, sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,Path(path))
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
 return module
m=load('_final_session_fixture','tests/test_light_native_execution_session.py')

def refused(label,f,prepare=None):
 if prepare: prepare()
 try:f.enter()
 except Exception as exc:
  assert not f.writes, (label,f.writes)
  assert f.directory/'session-intent.json' not in f.files
  print(label,'REFUSED',type(exc).__name__,str(exc),'writes',tuple(f.writes))
 else:raise AssertionError(label+' accepted')

f=m.Fixture(); first=f.directory/'session-first.json';f.files[first]=b'preserve orphan';before=copy.deepcopy(f.files)
refused('orphan_preserved',f)
assert f.files==before

f=m.Fixture();f.loaded.update(ActiveState='activating',SubState='start-post',ActiveEnterTimestampMonotonic='0')
def timeout_sleep(_):f.__setattr__('mono',f.mono+m.NANO//2);f.__setattr__('wall',f.wall+m.NANO//2)
f.clock.sleep=timeout_sleep
refused('pending_timeout_no_intent',f)

f=m.Fixture();pending=copy.deepcopy(f.loaded);pending.update(ActiveState='activating',SubState='start-post',ActiveEnterTimestampMonotonic='0');f.show_rows=[pending]
def drift_sleep(_):
 f.files[f.directory/'supervisor.py']=b'changed while pending';f.mono+=50_000_000;f.wall+=50_000_000
f.clock.sleep=drift_sleep
refused('pending_input_drift_no_intent',f)

f=m.Fixture();pending=copy.deepcopy(f.loaded);pending.update(ActiveState='activating',SubState='start-post',ActiveEnterTimestampMonotonic='0');f.show_rows=[pending];observed=[]
def release_sleep(seconds):
 assert not f.writes and f.directory/'session-intent.json' not in f.files
 observed.append(seconds);f.mono+=50_000_000;f.wall+=50_000_000
f.clock.sleep=release_sleep
session=f.enter();assert f.writes==['session-intent.json','session-first.json'] and len(observed)==1
print('pending_to_active','ACCEPTED','waits',tuple(observed),'end_ns',session.monotonic_end_ns)

f=m.Fixture();f.loaded['ExecStart']=f.loaded['ExecStart'].replace('[Mon 2026-10-19 12:00:00 UTC]','[n/a]').replace('pid=4242','pid=0')
refused('execstart_pid0_no_start_no_intent',f)

f=m.Fixture();changed=copy.deepcopy(f.loaded);changed['RuntimeMaxUSec']='109s';f.show_rows=[f.loaded,f.loaded,f.loaded,changed,changed]
try:f.enter()
except Exception as exc:
 assert f.writes==['session-intent.json'] and f.directory/'session-first.json' not in f.files
 print('post_intent_identity_drift','REFUSED',type(exc).__name__,str(exc),'writes',tuple(f.writes))
else:raise AssertionError('post intent identity drift accepted')

s=load('_final_scope_fixture','tests/test_native_host_preflight_scope.py')
sources=s.loaded_session_sources();patterns=s.trigger_paths(s.workflow_text())
missing_trigger=[name for name in sources if not any(s.fnmatch.fnmatchcase(name,pattern) for pattern in patterns)]
missing_selector=[name for name in sources if not s.select([name])['session']]
assert len(sources)==36 and not missing_trigger and not missing_selector
print('selector_closure','ACCEPTED','sources',len(sources),'trigger_missing',tuple(missing_trigger),'selector_missing',tuple(missing_selector))
