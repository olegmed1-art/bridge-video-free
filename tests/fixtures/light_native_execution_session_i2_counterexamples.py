"""Independent I2 source/synthetic-port counterexamples executed 2026-10-19.

This preserves the exact portable script body executed by reviewer task
``/root/review2193_i2_record`` (launch metadata: gpt-5.6-sol, high).  It is not
production, PID1, GitHub Actions, database, provider, host-acceptance, or service
evidence.  The fixture executes the frozen production session definitions while
substituting filesystem, clock, systemctl, /proc, and external legacy ports.

Reviewed source SHA-256:

* ops/light_native_lane_execution.py:
  d2c1553ca229d894db0cad6500c3927f779d26282dae0499c0ee2a952fe20362
* ops/light_native_lane_controller.py:
  4e79d2809d35198129a1aee2597bf4acc2ed482a8fa8e87fa85d38d4dcbd05a0
* ops/light_native_service_unit.py:
  2d1422303a0649445d0b68790f13b61ea9698964d64e0cdc10f4d9172dd5e231
* tests/test_light_native_execution_session.py:
  4217ce52d7379af0c92fc1e108a327701bff4094cd37f7619c68a6a89a96fcab

Observed labels from the completed run were:

* pointer_drift_records ('session-intent.json',)
* pointer_drift_during_first REFUSED LANE_EXEC_SESSION_POINTER
* replacement_sticky True
* coordinated_replacement_after_first REFUSED LANE_EXEC_SESSION_CLOSED
* expiry_ceiling_end_ns 141000000000
* expiry_ceiling_capture REFUSED bounded-at-wall-expiry

The final REFUSED label is emitted after the exact ceiling assertion solely so
the shared outcome printer records that bounded result; it is not a product
refusal code.  Saving this file did not rerun the script.
"""
import sys, importlib.util
from pathlib import Path
from types import SimpleNamespace, ModuleType
from dataclasses import replace
sys.path.insert(0, str(Path.cwd()))
p=Path('tests/test_light_native_execution_session.py')
spec=importlib.util.spec_from_file_location('_session_fixture',p)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

def outcome(name, fn):
 try:
  fn()
 except Exception as exc:
  print(name, 'REFUSED', str(exc))
 else:
  print(name, 'UNEXPECTED_ACCEPT')

def pointer_drift_during_first():
 f=m.Fixture()
 original=f.e._execution_identity
 def drift(*args):
  result=original(*args)
  f.files[f.pointer]=f.e.encoded(dict(request_sha256='f'*64))
  return result
 f.e._execution_identity=drift
 try:
  f.enter()
 finally:
  print('pointer_drift_records', tuple(f.writes))
  assert 'session-intent.json' in f.writes and 'session-first.json' not in f.writes

def coordinated_replacement_after_first():
 f=m.Fixture();s=f.enter()
 f.identity=replace(f.identity,start_ticks=10001,invocation_id='c'*32)
 f.loaded['InvocationID']='c'*32
 try:
  s.assert_current()
 finally:
  print('replacement_sticky', s.failed)

def expiry_ceiling_capture():
 f=m.Fixture()
 class LaneRunBinding:
  deadline=500.0
  def assert_running(self): pass
 binary=Path('/synthetic-python');f.files[binary]=b'binary'
 f.wall=1800000001*m.NANO;f.mono=101*m.NANO
 fake=SimpleNamespace(LaneRunBinding=LaneRunBinding)
 from unittest.mock import patch
 with patch.dict(sys.modules,{'ops.light_native_lane_run_guard':fake}), \
      patch.object(f.e,'Path',return_value=SimpleNamespace(resolve=lambda strict:binary)):
  clock,_=f.e.capture_clock(120,1800000041,LaneRunBinding())
 assert clock['monotonic_end_ns']==141*m.NANO
 print('expiry_ceiling_end_ns',clock['monotonic_end_ns'])
 raise RuntimeError('bounded-at-wall-expiry')

outcome('pointer_drift_during_first', pointer_drift_during_first)
outcome('coordinated_replacement_after_first', coordinated_replacement_after_first)
outcome('expiry_ceiling_capture', expiry_ceiling_capture)
