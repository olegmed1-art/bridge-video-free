"""Offline controller/dispatch contract. Every cloud/SSH operation is fake."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import signal
import unittest
from unittest import mock
from ops import ibm_trial_executor as e
from ops import ibm_unit_isolation_probe as p
from tests.test_ibm_trial_executor import Clock

B = dict(run_id="99999123456",attempt=1,head="a"*40)

class IsolationControllerTests(unittest.TestCase):
    def run_case(self, *, running=0, io_seconds=0, success=True, fault=None, stop_fault=None,
                 signal_at=None, identity=None, sink_fault=None):
        clock=Clock();events=[];actions=[];budgets=[];bindings=[];started=None
        class Provider:
            def backup(self):return dict(p.IDENTITY) if identity is None else identity
            def state(self):
                clock.sleep(io_seconds)
                if not actions:return "stopped",True
                if actions[-1][0]=="stop" and not stop_fault:return "stopped",True
                return ("running",False) if clock.now()-started >= running else ("starting",False)
            def action(self,action):
                nonlocal started
                actions.append((action,clock.now()))
                if action=="start":started=clock.now()
                clock.sleep(io_seconds)
                if signal_at==action:e.signal.getsignal(e.signal.SIGTERM)(e.signal.SIGTERM,None)
                if action=="stop" and stop_fault:raise e.ControlError(stop_fault)
        class Guest:
            kind="four_unit_isolation";binding=B
            def bind_identity(self,proof):
                bindings.append(proof)
                if proof!=p.IDENTITY:raise p.IsolationError("fresh_api_identity_required")
            def run_once(self,*,max_seconds):
                budgets.append(max_seconds)
                if signal_at=="probe":e.signal.getsignal(e.signal.SIGTERM)(e.signal.SIGTERM,None)
                clock.sleep(max_seconds)
                if fault:raise fault
                return success
        def out(event,**fields):
            events.append(dict(event=event,**fields))
            if event==sink_fault:raise BrokenPipeError('PRIVATE_SENTINEL')
        previous={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM)}
        with mock.patch.object(e,'wall_deadline',return_value=contextlib.nullcontext()):
            try:code=e.trial(Provider(),guest_probe=Guest(),maintenance=True,clock=clock.now,sleep=clock.sleep,out=out)
            except BaseException as exc:code=exc
        self.assertEqual(previous,{sig:signal.getsignal(sig) for sig in previous})
        return code,actions,budgets,bindings,events,clock.now()-(started or 0)

    def test_start_anchored_matrix_and_single_stop(self):
        for running in (0,6,60,180,240,260,265,270,300,385,400,600):
            for io_seconds in (0,5,10):
                with self.subTest(running=running,io=io_seconds):
                    code,actions,budgets,proofs,events,total=self.run_case(running=running,io_seconds=io_seconds,success=False)
                    self.assertEqual(3,code);self.assertEqual(['start','stop'],[a for a,_ in actions])
                    self.assertLessEqual(actions[1][1]-actions[0][1],420)
                    self.assertLessEqual(total,600);self.assertLessEqual(len(budgets),1)
                    self.assertEqual([p.IDENTITY],proofs)
                    for budget in budgets:self.assertGreaterEqual(budget,115);self.assertLessEqual(budget,378)
                    self.assertEqual('STOPPED_OBSERVED',events[-1]['event'])
    def test_late_running_never_resets_budget(self):
        code,actions,budgets,_,events,_=self.run_case(running=240)
        self.assertEqual([143],budgets);self.assertEqual(0,code)
        row=next(r for r in events if r['event']=='UNIT_ISOLATION_STARTED')
        self.assertEqual((240,420,180),(row['seconds_since_start'],row['stop_submit_by_seconds'],row['confirmation_reserve_seconds']))
        self.assertFalse(any(r['event']=='GUEST_DIAGNOSTIC_RESULT' for r in events))
    def test_insufficient_repair_reserve_skips_and_stops(self):
        code,actions,budgets,_,events,_=self.run_case(running=270)
        self.assertEqual(3,code);self.assertEqual([],budgets);self.assertEqual(['start','stop'],[a for a,_ in actions])
    def test_success_requires_isolation_not_just_auth(self):
        for success in (True,False,None,1):
            code,actions,_,_,events,_=self.run_case(success=success)
            self.assertEqual(0 if success is True else 3,code)
            self.assertEqual(['start','stop'],[a for a,_ in actions])
            result=next(r for r in events if r['event']=='UNIT_ISOLATION_RESULT')
            self.assertIs(result['isolated'],success is True)
    def test_identity_absent_or_changed_prevents_start(self):
        for identity in ({},{**p.IDENTITY,'instance_id':'wrong'}):
            code,actions,budgets,_,_,_=self.run_case(identity=identity)
            self.assertIsInstance(code,p.IsolationError);self.assertEqual([],actions);self.assertEqual([],budgets)
    def test_exception_and_interrupt_preserve_stop(self):
        for fault in (RuntimeError('PRIVATE_SENTINEL'),KeyboardInterrupt(),SystemExit(2)):
            code,actions,_,_,events,_=self.run_case(fault=fault)
            self.assertEqual(['start','stop'],[a for a,_ in actions]);self.assertEqual('STOPPED_OBSERVED',events[-1]['event'])
            self.assertNotIn('PRIVATE_SENTINEL',json.dumps(events))
    def test_signal_during_start_or_probe_preserves_stop(self):
        for phase in ('start','probe'):
            code,actions,_,_,events,_=self.run_case(signal_at=phase)
            self.assertEqual(3,code);self.assertEqual(['start','stop'],[a for a,_ in actions]);self.assertEqual('STOPPED_OBSERVED',events[-1]['event'])
    def test_stop_denial_or_uncertainty_is_not_success_or_retried(self):
        for stop_fault in ('http_403','transport_failed'):
            code,actions,_,_,events,total=self.run_case(stop_fault=stop_fault)
            self.assertEqual(4,code);self.assertEqual(['start','stop'],[a for a,_ in actions]);self.assertEqual('STOP_NOT_CONFIRMED',events[-1]['event']);self.assertLessEqual(total,600)
    def test_closed_logs_cannot_skip_stop(self):
        for event in ('START_SUBMITTING','UNIT_ISOLATION_RESULT','TRIAL_CONTAINMENT','ORDINARY_STOP_SUBMITTING'):
            code,actions,_,_,_,_=self.run_case(sink_fault=event)
            self.assertEqual(0,code);self.assertEqual(['start','stop'],[a for a,_ in actions])
    def test_invalid_kind_mode_bool_rejected_before_provider_reads(self):
        client=mock.Mock();guest=mock.Mock(kind='other')
        for kw in ({'maintenance':True,'guest_probe':guest},{'maintenance':1},{'maintenance':True,'guest_probe':None}):
            with self.assertRaises(e.ControlError):e.trial(client,**kw)
        client.backup.assert_not_called();client.action.assert_not_called()
    def test_new_cli_ack_and_flag_are_separate(self):
        with mock.patch.object(p,'prepare') as prepare,mock.patch.object(e,'authenticate') as auth,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(3,e.main(['isolate_units','--ack','OWNER_APPROVED_10MIN_10USD']))
            self.assertEqual(3,e.main(['isolate_units','--ack','OWNER_APPROVED_IBM_FOUR_UNIT_ISOLATION_10MIN_10USD','--guest-diagnostic']))
            prepare.assert_not_called();auth.assert_not_called()
    def test_cli_prepares_before_iam_and_closes_route(self):
        order=[];guest=mock.Mock()
        with mock.patch.object(p,'prepare',side_effect=lambda:order.append('prepare') or guest),mock.patch.object(e,'authenticate',side_effect=lambda:order.append('iam') or 'fake'),mock.patch.object(e,'trial',side_effect=lambda *a,**kw:order.append('trial') or 0) as trial,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0,e.main(['isolate_units','--ack','OWNER_APPROVED_IBM_FOUR_UNIT_ISOLATION_10MIN_10USD']))
        self.assertEqual(['prepare','iam','trial'],order);guest.close.assert_called_once();self.assertTrue(trial.call_args.kwargs['maintenance'])
    def test_prepare_failure_blocks_before_iam(self):
        with mock.patch.object(p,'prepare',side_effect=p.IsolationError('PRIVATE_SENTINEL')),mock.patch.object(e,'authenticate') as auth,contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(4,e.main(['isolate_units','--ack','OWNER_APPROVED_IBM_FOUR_UNIT_ISOLATION_10MIN_10USD']))
        auth.assert_not_called();self.assertNotIn('PRIVATE_SENTINEL',out.getvalue())
    def test_iam_failure_closes_prepared_route(self):
        guest=mock.Mock()
        with mock.patch.object(p,'prepare',return_value=guest),mock.patch.object(e,'authenticate',side_effect=e.ControlError('http_403')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(3,e.main(['isolate_units','--ack','OWNER_APPROVED_IBM_FOUR_UNIT_ISOLATION_10MIN_10USD']))
        guest.close.assert_called_once()
    def test_independent_stop_never_imports_or_prepares_maintenance(self):
        with mock.patch.object(p,'prepare') as prepare,mock.patch.object(e,'authenticate',return_value='fake'),mock.patch.object(e,'ordinary_stop',return_value=0) as stop,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0,e.main(['stop','--ack','ORDINARY_STOP_EXACT_IBM']))
        prepare.assert_not_called();stop.assert_called_once()
    def test_backup_proof_is_returned_only_after_existing_provider_checks(self):
        client=e.Client('fake')
        image=dict(id=e.IMAGE_ID,name=e.IMAGE_NAME,status='available',source_volume={'id':e.VOLUME_ID},encryption='provider_managed')
        instance=dict(id=e.INSTANCE_ID,boot_volume_attachment={'volume':{'id':e.VOLUME_ID}},profile={'name':'bx3dc-8x40'})
        with mock.patch.object(client,'call',side_effect=[image,instance]) as wire:
            self.assertEqual(p.IDENTITY,client.backup());self.assertEqual(2,wire.call_count)
        with mock.patch.object(client,'call',return_value={**image,'status':'failed'}):
            with self.assertRaises(e.ControlError):client.backup()
    def test_workflow_exact_new_mode_and_independent_stop_lane(self):
        workflow=Path('.github/workflows/ibm-vpc-power-probe.yml').read_text()
        self.assertIn('trial_stop, isolate_units]',workflow)
        self.assertIn("inputs.mode == 'trial_stop' && 'ibm-vpc-independent-stop'",workflow)
        block=workflow.split('  unit-isolation:',1)[1].split('  manual-console-trial:',1)[0]
        self.assertIn("inputs.diagnose_ssh == false",block);self.assertIn("inputs.test_oracle == false",block)
        self.assertIn('OWNER_APPROVED_IBM_FOUR_UNIT_ISOLATION_10MIN_10USD',block)
        self.assertNotIn('continue-on-error',block)
    def test_read_only_probe_and_guest_collector_unchanged(self):
        expected={'ops/ibm_ssh_probe_safe.py':'7711357b7929dcf42d8e49739b16f2e4489191644a0bfb711259f57f182e3bee','ops/ibm_trial_guest_probe.py':'78ee98cfed0a6055e15ee8645763324a63722695f8e9c5955691545a0c3960d9'}
        for path,digest in expected.items():self.assertEqual(digest,hashlib.sha256(Path(path).read_bytes()).hexdigest())
