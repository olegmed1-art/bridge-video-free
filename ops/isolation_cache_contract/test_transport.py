import unittest
from prepare_driver import execute_phases

class PhaseTests(unittest.TestCase):
    def scenario(self,fail=None):
        calls=[];events=[]
        values={'settle':{'state':'BOOT_JOBS_SETTLED'},
                'prepare':{'state':'PREPARED_NO_MUTATION','manifest':{'boot':'synthetic'},'baseline_sha':'a'*64},
                'copy':{'state':'OFFHOST_COPY_VERIFIED','receipt':{'verified_sha':'a'*64}},
                'apply':{'state':'DISABLED_VERIFIED'},'canary':{'state':'CPU_CANARY_PASS'},
                'diagnostic':{'state':'DIAGNOSTICS_ONLY'}}
        def remote(phase,cap,request):
            calls.append(phase)
            if phase==fail:return {'state':'UNKNOWN'}
            if phase=='apply':
                self.assertEqual(request['baseline_sha'],'a'*64)
                self.assertEqual(request['copy_receipt'],values['copy']['receipt'])
                self.assertNotIn('queue_receipt',request)
            return values[phase]
        def copy(prepared):
            calls.append('copy');self.assertEqual(prepared,values['prepare'])
            return {'state':'UNKNOWN'} if fail=='copy' else values['copy']
        def reserve():
            calls.append('reserve')
            if fail=='reserve':raise RuntimeError('BUDGET')
        return calls,events,lambda:execute_phases(remote,copy,lambda *x:events.append(x),reserve)
    def test_success_full_sequence(self):
        calls,events,run=self.scenario();run()
        self.assertEqual(calls,['settle','prepare','copy','reserve','apply','canary','diagnostic'])
        self.assertEqual(events[-1][0],'ISOLATION_COMPLETE_REQUEST_PARENT_STOP')
    def test_every_refusal_stops_following_phases(self):
        order=['settle','prepare','copy','reserve','apply','canary','diagnostic']
        for failure in order:
            with self.subTest(failure=failure):
                calls,events,run=self.scenario(failure)
                with self.assertRaises((AssertionError,RuntimeError)):run()
                self.assertEqual(calls,order[:order.index(failure)+1])
                self.assertFalse(any(x[0]=='ISOLATION_COMPLETE_REQUEST_PARENT_STOP' for x in events))
                self.assertNotIn('restore',calls)

if __name__=='__main__':unittest.main()
