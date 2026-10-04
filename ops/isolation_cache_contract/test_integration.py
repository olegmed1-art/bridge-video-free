"""Actual Engine, supervisor Gate, and phase scheduler; all I/O synthetic."""
import unittest
import supervisor as s
import test_cache
from test_watcher import NONCE
import test_transport

class IntegratedTests(unittest.TestCase):
 def case(self,change=None,missing=0,latency=.1):
  e,r,trace,state,events=test_cache.CacheTests().case(0,change,missing,latency)
  gate=s.Gate(NONCE,False,e.clock.wall(),e.clock.mono())
  original=r.send;actions=[]
  def send(packet):
   if packet['type'] in ('BIND','RUNNING'):
    result=gate.receive(packet,e.clock.wall(),e.clock.mono())
    if packet['type']=='RUNNING':
     self.assertEqual(result,'START')
     self.assertEqual([x[0] for x in trace[-2:]],['run','jobs'])
     self.assertLess(e.clock.wall()-packet['t0'],120)
     actions.append('guest_stage')
     calls,ev,execute=test_transport.PhaseTests().scenario();execute();actions.extend(calls)
   original(packet)
  r.send=send
  return e,gate,actions,state
 def test_final_refresh_before_guest_stage_and_all_service_phases(self):
  e,g,a,state=self.case();self.assertTrue(e.execute())
  self.assertEqual(a,['guest_stage','settle','prepare','copy','reserve','apply','canary','diagnostic'])
  self.assertEqual(g.starts,1)
  self.assertEqual(g.tick(g.bound['t0']+270,g.mono_t0+270),'STOP')
 def test_final_refusals_perform_zero_guest_writes(self):
  for why in ('cancel','rerun','sha','ambiguous','timeout','stale','job_id','job_terminal','boot'):
   e,g,a,_=self.case(why);self.assertFalse(e.execute(),why)
   self.assertEqual(a,[],why);self.assertEqual(g.starts,0,why)
 def test_checkpoint_four_second_calls_consume_shared_admission(self):
  e,g,a,state=self.case(missing=3,latency=4)
  self.assertTrue(e.execute());self.assertEqual(state['job_calls'],5)
  self.assertLess(e.clock.wall()-g.bound['t0'],120)
  self.assertEqual(e.budget.wall_end,g.bound['t0']+270)
 def test_late_final_checkpoint_cannot_extend_admission(self):
  e,g,a,state=self.case(missing=3,latency=4)
  probe=e.probe
  def late(budget):
   if state['probes']==0:e.clock.sleep(70)
   return probe(budget)
  e.probe=late
  self.assertFalse(e.execute());self.assertEqual(a,[]);self.assertEqual(g.starts,0)

if __name__=='__main__':unittest.main()
