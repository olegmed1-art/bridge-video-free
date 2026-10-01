import contextlib
import io
import json
import time
from pathlib import Path
import unittest
from unittest import mock
import urllib.error
from ops import ibm_trial_executor as e
from tests.test_ibm_vpc_oracle_probe import token


class Clock:
    def __init__(self): self.t=0
    def now(self): return self.t
    def sleep(self,n): self.t+=n


class Fake:
    def __init__(self,states,unknown=None):
        self.states=iter(states); self.last=('stopped',True); self.actions=[]; self.unknown=unknown; self.backups=0
    def state(self):
        self.last=next(self.states,self.last)
        if isinstance(self.last,Exception): raise self.last
        return self.last
    def backup(self): self.backups+=1
    def action(self,action):
        self.actions.append(action)
        if action==self.unknown: raise e.ControlError('transport_failed')


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        p=mock.patch('socket.socket.connect',side_effect=AssertionError('LIVE_NETWORK_FORBIDDEN'))
        p.start();self.addCleanup(p.stop)
        self.clock=Clock();self.events=[]
    def out(self,event,**kw): self.events.append({'event':event,**kw})
    def stop(self,client,**kw):return e.ordinary_stop(client,clock=self.clock.now,sleep=self.clock.sleep,out=self.out,**kw)
    def trial(self,client,mode="trial"):
        with mock.patch.object(e,'LIVE_START_ENABLED',True):
            return e.trial(client,mode=mode,clock=self.clock.now,sleep=self.clock.sleep,out=self.out)

    def test_live_start_locked_before_credentials_and_at_transport(self):
        lock=mock.patch.object(e,'LIVE_START_ENABLED',False)
        lock.start();self.addCleanup(lock.stop)
        with mock.patch.object(e,'authenticate') as auth, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(3,e.main(['trial','--ack','OWNER_APPROVED_10MIN_10USD']))
            auth.assert_not_called()
        with mock.patch.object(e,'wire') as wire:
            with self.assertRaisesRegex(e.ControlError,'live_start_locked'):e.Client('secret').action('start')
            wire.assert_not_called()

    def test_stop_from_starting_and_wait_for_observed_stopped(self):
        c=Fake([('starting',False),('running',False),('stopping',False),('stopped',True)])
        self.assertEqual(0,self.stop(c,start_uncertain=True))
        self.assertEqual(['stop'],c.actions)
        self.assertEqual('STOPPED_OBSERVED',self.events[-1]['event'])
        self.assertEqual('NOT_EXPOSED_BY_API',self.events[-1]['action_queue'])

    def test_enabled_cli_requires_ack_and_routes_to_mocked_trial(self):
        with mock.patch.object(e,'authenticate',return_value='fake-token') as auth, mock.patch.object(e,'trial',return_value=0) as trial, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(3,e.main(['trial','--ack','WRONG']))
            auth.assert_not_called();trial.assert_not_called()
            self.assertEqual(0,e.main(['trial','--ack','OWNER_APPROVED_10MIN_10USD']))
            auth.assert_called_once();trial.assert_called_once()

    def test_unknown_stop_not_repeated(self):
        c=Fake([('running',False),('running',False),('stopped',True)],unknown='stop')
        self.assertEqual(0,self.stop(c))
        self.assertEqual(['stop'],c.actions)
        self.assertTrue(self.events[-1]['stop_post_unknown'])

    def test_stopped_after_unknown_start_not_declared_terminal(self):
        c=Fake([('stopped',True)])
        self.assertEqual(4,self.stop(c,start_uncertain=True))
        self.assertEqual([],c.actions)
        self.assertTrue(any(x['event']=='STOPPED_BUT_START_OUTCOME_UNRESOLVED' for x in self.events))
        self.assertEqual(e.STOP_POLL_SECONDS,self.clock.t)

    def test_nonstartable_stopped_waits_for_queued_start_then_stops(self):
        c=Fake([('stopped',False),('starting',False),('stopping',False),('stopped',True)])
        self.assertEqual(0,self.stop(c,start_uncertain=True))
        self.assertEqual(['stop'],c.actions)

    def test_queue_unobservable_remains_explicit_blocker(self):
        c=Fake([('stopped',False)])
        self.assertEqual(4,self.stop(c,start_uncertain=True))
        self.assertEqual([],c.actions)
        self.assertEqual(e.STOP_POLL_SECONDS,self.clock.t)

    def test_stop_read_403_is_not_hidden(self):
        c=Fake([e.ControlError('http_403')])
        self.assertEqual(4,self.stop(c))
        self.assertEqual('http_403',self.events[-1]['reason'])
        self.assertEqual([],c.actions)

    def test_stop_post_403_remains_failure_even_if_external_stop_succeeds(self):
        class C(Fake):
            def action(self,action):self.actions.append(action);raise e.ControlError('http_403')
        c=C([('running',False),('stopped',True)])
        self.assertEqual(4,self.stop(c))
        self.assertEqual(['stop'],c.actions)
        self.assertTrue(any(x.get('event')=='ORDINARY_STOP_REFUSED' and x.get('reason')=='http_403' for x in self.events))

    def test_trial_stops_without_admin_signal_before_120_seconds(self):
        class C(Fake):
            def state(self):
                if not self.actions:return ('stopped',True)
                return ('stopped',True) if self.actions[-1]=='stop' else ('running',False)
        c=C([])
        self.assertEqual(0,self.trial(c));self.assertEqual(['start','stop'],c.actions)
        self.assertLessEqual(self.clock.t,120)

    def test_trial_stuck_starting_requests_stop_before_600(self):
        class C(Fake):
            def state(self):
                if not self.actions:return ('stopped',True)
                return ('stopped',True) if self.actions[-1]=='stop' else ('starting',False)
        c=C([])
        self.assertEqual(0,self.trial(c));self.assertEqual(['start','stop'],c.actions)
        self.assertLessEqual(self.clock.t,600)

    def test_manual_console_has_absolute_600_bound_without_short_rdc_cutoff(self):
        clock=self.clock
        class C(Fake):
            def state(self):
                clock.sleep(e.HTTP_SECONDS)
                if not self.actions:return ('stopped',True)
                if self.actions[-1]=='stop':return ('stopped',True)
                return ('running',False) if clock.now()-self.start_time>=400 else ('starting',False)
            def action(self,action):
                self.actions.append(action)
                if action=='start':self.start_time=clock.now()
                else:self.stop_time=clock.now()
                clock.sleep(e.HTTP_SECONDS)
        c=C([]);self.assertEqual(0,self.trial(c,'manual_console_trial'))
        self.assertEqual(['start','stop'],c.actions)
        self.assertGreater(c.stop_time-c.start_time,120)
        self.assertGreater(c.stop_time-c.start_time,540)
        self.assertLessEqual(c.stop_time-c.start_time,600)
        self.assertTrue(any(x['event']=='RUNNING_MANUAL_CONSOLE_WINDOW' for x in self.events))
        self.assertFalse(any(x['event']=='RUNNING_GUEST_WINDOW' for x in self.events))

    def test_manual_console_immediate_running_uses_full_absolute_window(self):
        class C(Fake):
            def state(self):
                if not self.actions:return ('stopped',True)
                return ('stopped',True) if self.actions[-1]=='stop' else ('running',False)
        c=C([])
        self.assertEqual(0,self.trial(c,'manual_console_trial'))
        self.assertEqual(['start','stop'],c.actions)
        self.assertGreaterEqual(self.clock.t,565)
        self.assertLessEqual(self.clock.t,600)

    def test_manual_console_routes_only_with_distinct_ack(self):
        with mock.patch.object(e,'authenticate',return_value='fake') as auth, mock.patch.object(e,'trial',return_value=0) as trial, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(3,e.main(['manual_console_trial','--ack','OWNER_APPROVED_10MIN_10USD']))
            auth.assert_not_called()
            self.assertEqual(0,e.main(['manual_console_trial','--ack','OWNER_APPROVED_MANUAL_CONSOLE_10MIN_10USD']))
            self.assertEqual('manual_console_trial',trial.call_args.kwargs['mode'])

    def test_manual_console_unknown_start_is_not_repeated(self):
        c=Fake([('stopped',True),('starting',False),('stopped',True)],unknown='start')
        self.assertEqual(0,self.trial(c,'manual_console_trial'))
        self.assertEqual(['start','stop'],c.actions)

    def test_manual_console_remains_lockable_before_auth(self):
        with mock.patch.object(e,'LIVE_START_ENABLED',False), mock.patch.object(e,'authenticate') as auth, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(3,e.main(['manual_console_trial','--ack','OWNER_APPROVED_MANUAL_CONSOLE_10MIN_10USD']))
            auth.assert_not_called()

    def test_slow_get_and_post_reserve_stop_submission_time(self):
        clock=self.clock
        class C(Fake):
            def state(self):
                clock.sleep(e.HTTP_SECONDS)
                if not self.actions:return ('stopped',True)
                return ('stopped',True) if self.actions[-1]=='stop' else ('starting',False)
            def action(self,action):
                self.actions.append(action)
                if action=='start':self.start_time=clock.now()
                else:self.stop_time=clock.now()
                clock.sleep(e.HTTP_SECONDS)
        c=C([]);self.assertEqual(0,self.trial(c))
        self.assertLessEqual(c.stop_time-c.start_time,e.WINDOW_SECONDS)

    def test_unknown_start_is_never_repeated_and_active_state_contained(self):
        c=Fake([('stopped',True),('starting',False),('stopped',True)],unknown='start')
        self.assertEqual(0,self.trial(c));self.assertEqual(['start','stop'],c.actions)
        self.assertTrue(any(x.get('start_outcome_unknown') is True for x in self.events))

    def test_accepted_start_without_observed_transition_is_unresolved(self):
        c=Fake([('stopped',True)])
        self.assertEqual(4,self.trial(c));self.assertEqual(['start'],c.actions)
        self.assertFalse(any(x['event']=='STOPPED_OBSERVED' for x in self.events))

    def test_delayed_start_after_initial_stopped_reads_is_contained(self):
        c=Fake([('stopped',True)]*10+[('starting',False),('stopped',True)])
        self.assertEqual(0,self.stop(c,start_uncertain=True));self.assertEqual(['stop'],c.actions)

    def test_explicit_external_stop_does_not_restart(self):
        c=Fake([('stopped',True),('running',False),('stopping',False),('stopped',True)])
        self.assertEqual(0,self.trial(c));self.assertEqual(['start'],c.actions)

    def test_bad_preflight_no_mutation(self):
        c=Fake([('stopped',False)])
        with self.assertRaises(e.ControlError):self.trial(c)
        self.assertEqual([],c.actions)

    def test_actual_client_binds_backup_boot_and_quoted_profile(self):
        image={'id':e.IMAGE_ID,'name':e.IMAGE_NAME,'status':'available','source_volume':{'id':e.VOLUME_ID},'encryption':'none'}
        instance={'id':e.INSTANCE_ID,'boot_volume_attachment':{'volume':{'id':e.VOLUME_ID}},'profile':{'name':'bx3dc-8x40'}}
        with mock.patch.object(e,'wire',side_effect=[image,instance]):e.Client('TOKEN').backup()
        for bad in [{**instance,'profile':{'name':'other'}},{**instance,'boot_volume_attachment':{}},{**instance,'id':'other'}]:
            with mock.patch.object(e,'wire',side_effect=[image,bad]):
                with self.assertRaisesRegex(e.ControlError,'boot_or_quoted_profile_mismatch'):e.Client('TOKEN').backup()

    def test_transport_exact_target_no_force_and_no_lists(self):
        c=e.Client('TOKEN_SENTINEL')
        with mock.patch.object(e,'wire',return_value={'type':'stop'}) as wire:
            c.action('stop'); req=wire.call_args.args[0]
            self.assertEqual(e.ORIGIN+e.ACTION_PATH+e.QUERY,req.full_url)
            self.assertEqual({'type':'stop','force':False},json.loads(req.data))
        for method,path,body in [('POST',e.ACTION_PATH,{'type':'stop','force':True}),
            ('POST',e.ACTION_PATH,{'type':'restart','force':False}),('GET',e.ACTION_PATH,None),
            ('GET','/v1/instances',None),('POST','/v1/images',{})]:
            with mock.patch.object(e,'wire') as wire:
                with self.assertRaises(e.ControlError):c.call(method,path,body)
                wire.assert_not_called()

    def test_start_ttl_checked_at_post_boundary_but_short_token_can_stop(self):
        with mock.patch.object(e.time,'time',return_value=10000):
            for remaining in [61,899]:
                value=token(exp=10000+remaining)
                with mock.patch.object(e,'wire',return_value={'access_token':value}):
                    with mock.patch.dict(e.os.environ,{'IBM_CLOUD_API_KEY':'fake'}):
                        self.assertEqual(value,e.authenticate())
                with mock.patch.object(e,'wire') as wire:
                    with self.assertRaisesRegex(e.ControlError,'start_token_lifetime_insufficient'):
                        e.Client(value).action('start')
                    wire.assert_not_called()
                with mock.patch.object(e,'wire',return_value={'type':'stop'}) as wire:
                    e.Client(value).action('stop');wire.assert_called_once()
            with mock.patch.object(e,'wire',return_value={'type':'start'}) as wire:
                e.Client(token(exp=10900)).action('start')
                self.assertEqual({'type':'start','force':False},json.loads(wire.call_args.args[0].data))

    def test_http_errors_and_redirects_do_not_leak_secret_bodies(self):
        for code in [403,404,500]:
            op=mock.Mock();op.open.side_effect=urllib.error.HTTPError('SECRET',code,'SECRET',{},io.BytesIO(b'SECRET'))
            with mock.patch.object(e.urllib.request,'build_opener',return_value=op), mock.patch.object(e,'wall_deadline',return_value=contextlib.nullcontext()):
                with self.assertRaisesRegex(e.ControlError,'^http_'+str(code)+'$'):e.Client('secret').action('stop')
        with self.assertRaisesRegex(e.ControlError,'redirect_refused'):
            e.NoRedirect().redirect_request(None,None,302,'SECRET',{},'https://evil.invalid')

    def test_wall_deadline_interrupts_and_restores_signal(self):
        with mock.patch.object(e.signal,'setitimer',create=True) as timer, \
             mock.patch.object(e.signal,'getitimer',return_value=(0,0),create=True), \
             mock.patch.object(e.signal,'ITIMER_REAL',0,create=True), \
             mock.patch.object(e.signal,'SIGALRM',14,create=True), \
             mock.patch.object(e.signal,'getsignal',return_value='previous'), \
             mock.patch.object(e.signal,'signal') as handler:
            with self.assertRaisesRegex(e.ControlError,'wall_deadline_expired'):
                with e.wall_deadline(10):handler.call_args.args[1](14,None)
            self.assertEqual([mock.call(0,10),mock.call(0,0)],timer.call_args_list)
            self.assertEqual(mock.call(14,'previous'),handler.call_args)

    def test_real_wire_wraps_entire_body_operation_in_deadline(self):
        with mock.patch.object(e,'wall_deadline') as deadline, mock.patch.object(e,'_wire',return_value={}) as inner:
            self.assertEqual({},e.wire('request',timeout=7))
            deadline.assert_called_once_with(7);inner.assert_called_once_with('request',timeout=7)
            deadline.return_value.__exit__.assert_called_once()

    @unittest.skipUnless(hasattr(e.signal,'setitimer'),'POSIX wall-timer smoke check runs in Ubuntu contract job')
    def test_posix_wall_timer_really_interrupts_blocking_wait(self):
        with self.assertRaisesRegex(e.ControlError,'wall_deadline_expired'):
            with e.wall_deadline(0.01):time.sleep(0.2)

    def test_workflow_stop_independent_and_start_dispatch_scoped(self):
        s=Path('.github/workflows/ibm-vpc-power-probe.yml').read_text()
        self.assertIn("inputs.mode == 'trial_stop' && 'ibm-vpc-independent-stop'",s)
        self.assertNotIn("false && github.event_name == 'workflow_dispatch'",s)
        self.assertTrue(e.LIVE_START_ENABLED)
        start=s.split('  trial-start:')[1].split('  manual-console-trial:')[0]
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.mode == 'trial_start' && inputs.test_oracle == false && github.ref == 'refs/heads/review/ibm-trial-control-20261001'",start)
        manual=s.split('  manual-console-trial:')[1].split('  trial-stop:')[0]
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.mode == 'manual_console_trial' && inputs.test_oracle == false && github.ref == 'refs/heads/review/ibm-trial-control-20261001'",manual)
        self.assertIn('manual_console_trial --ack OWNER_APPROVED_MANUAL_CONSOLE_10MIN_10USD',manual)
        self.assertIn('needs: contract',manual)
        self.assertNotIn('ORACLE_SSH_PRIVATE_KEY',manual)
        stop=s.split('  trial-stop:')[1].split('  oracle-probe:')[0]
        self.assertNotIn('needs: trial-start',stop)
        self.assertNotIn('ORACLE_SSH_PRIVATE_KEY',stop)
        self.assertEqual(2,s.count("(inputs.mode == 'status' || inputs.mode == '')"))


if __name__=='__main__':unittest.main()
