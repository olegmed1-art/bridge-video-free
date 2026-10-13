"""Offline school controller verifies the unchanged Start-relative Stop envelope."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import signal
import unittest
from unittest import mock
from ops import ibm_trial_executor as e
from ops import ibm_school_worlds_probe as p
from tests.test_ibm_trial_executor import Clock

B=dict(run_id='99999123456',attempt=1,head='a'*40)
ACK='OWNER_APPROVED_IBM_SCHOOL_WORLDS_10MIN_10USD'


class SchoolWorldsControllerTests(unittest.TestCase):
    def run_case(self, *, running=0, io_seconds=0, success=True, fault=None, stop_fault=None,
                 signal_at=None, identity=None, sink_fault=None):
        clock=Clock();events=[];actions=[];budgets=[];bindings=[];started=None
        class Provider:
            def backup(self):return dict(p.IDENTITY) if identity is None else identity
            def state(self):
                clock.sleep(io_seconds)
                if not actions:return 'stopped',True
                if actions[-1][0]=='stop' and not stop_fault:return 'stopped',True
                return ('running',False) if clock.now()-started>=running else ('starting',False)
            def action(self,action):
                nonlocal started
                actions.append((action,clock.now()))
                if action=='start':started=clock.now()
                clock.sleep(io_seconds)
                if signal_at==action:e.signal.getsignal(e.signal.SIGTERM)(e.signal.SIGTERM,None)
                if action=='stop' and stop_fault:raise e.ControlError(stop_fault)
        class Guest:
            kind='school_worlds';binding=B
            def bind_identity(self,proof):
                bindings.append(proof)
                if proof!=p.IDENTITY:raise p.WorldsError('fresh_api_identity_required')
            def run_once(self,*,max_seconds):
                budgets.append(max_seconds)
                if signal_at=='probe':e.signal.getsignal(e.signal.SIGTERM)(e.signal.SIGTERM,None)
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

    def test_school_kind_uses_existing_bounds_single_start_and_stop(self):
        self.assertEqual((265,378,420,600),(e.UNIT_ISOLATION_MIN_SECONDS,e.UNIT_ISOLATION_SECONDS,e.DIAGNOSTIC_STOP_SECONDS,e.WINDOW_SECONDS))
        for running in (0,115,120,240,385,600):
            for io_seconds in (0,10):
                with self.subTest(running=running,io=io_seconds):
                    code,actions,budgets,proofs,events,total=self.run_case(running=running,io_seconds=io_seconds,success=False)
                    self.assertEqual(3,code);self.assertEqual(['start','stop'],[a for a,_ in actions])
                    self.assertLessEqual(actions[1][1]-actions[0][1],420);self.assertLessEqual(total,600)
                    self.assertLessEqual(len(budgets),1);self.assertEqual([p.IDENTITY],proofs)
                    for budget in budgets:self.assertGreaterEqual(budget,265);self.assertLessEqual(budget,378)
                    self.assertEqual('STOPPED_OBSERVED',events[-1]['event'])

    def test_late_running_does_not_reset_deadline(self):
        code,actions,budgets,_,events,_=self.run_case(running=115)
        self.assertEqual(0,code);self.assertEqual([268],budgets)
        row=next(r for r in events if r['event']=='SCHOOL_WORLDS_STARTED')
        self.assertEqual((115,420,180),(row['seconds_since_start'],row['stop_submit_by_seconds'],row['confirmation_reserve_seconds']))
        self.assertFalse(any(r['event'] in {'UNIT_ISOLATION_STARTED','UNIT_ISOLATION_RESULT','GUEST_DIAGNOSTIC_RESULT'} for r in events))
        code,actions,budgets,_,events,_=self.run_case(running=120)
        self.assertEqual(3,code);self.assertEqual([],budgets);self.assertEqual(['start','stop'],[a for a,_ in actions])

    def test_success_requires_exact_true_one_shot(self):
        for success in (True,False,None,1):
            code,actions,budgets,_,events,_=self.run_case(success=success)
            self.assertEqual(0 if success is True else 3,code);self.assertEqual(1,len(budgets))
            self.assertEqual(['start','stop'],[a for a,_ in actions])
            row=next(r for r in events if r['event']=='SCHOOL_WORLDS_RESULT')
            self.assertIs(row['generated'],success is True)

    def test_wrong_identity_blocks_before_start(self):
        for identity in ({},{**p.IDENTITY,'instance_id':'wrong'}):
            code,actions,budgets,_,_,_=self.run_case(identity=identity)
            self.assertIsInstance(code,p.WorldsError);self.assertEqual([],actions);self.assertEqual([],budgets)

    def test_cancellation_exceptions_and_closed_logs_preserve_finally_stop(self):
        for fault in (RuntimeError('PRIVATE_SENTINEL'),KeyboardInterrupt(),SystemExit(2)):
            code,actions,_,_,events,_=self.run_case(fault=fault)
            self.assertEqual(['start','stop'],[a for a,_ in actions]);self.assertEqual('STOPPED_OBSERVED',events[-1]['event'])
            self.assertNotIn('PRIVATE_SENTINEL',json.dumps(events))
        for phase in ('start','probe'):
            code,actions,_,_,events,_=self.run_case(signal_at=phase)
            self.assertEqual(3,code);self.assertEqual(['start','stop'],[a for a,_ in actions])
        for event in ('START_SUBMITTING','SCHOOL_WORLDS_RESULT','TRIAL_CONTAINMENT','ORDINARY_STOP_SUBMITTING'):
            code,actions,_,_,_,_=self.run_case(sink_fault=event)
            self.assertEqual(0,code);self.assertEqual(['start','stop'],[a for a,_ in actions])

    def test_stop_refusal_or_unknown_never_retries_or_succeeds(self):
        for fault in ('http_403','transport_failed'):
            code,actions,_,_,events,total=self.run_case(stop_fault=fault)
            self.assertEqual(4,code);self.assertEqual(['start','stop'],[a for a,_ in actions])
            self.assertEqual('STOP_NOT_CONFIRMED',events[-1]['event']);self.assertLessEqual(total,600)

    def test_separate_cli_ack_and_no_guest_diagnostic(self):
        with mock.patch.object(p,'prepare') as prepare,mock.patch.object(e,'authenticate') as auth,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(3,e.main(['school_worlds','--ack','OWNER_APPROVED_10MIN_10USD']))
            self.assertEqual(3,e.main(['school_worlds','--ack',ACK,'--guest-diagnostic']))
            prepare.assert_not_called();auth.assert_not_called()

    def test_school_kind_requires_maintenance_identity_lane_before_provider_reads(self):
        provider=mock.Mock();guest=mock.Mock(kind='school_worlds')
        with self.assertRaisesRegex(e.ControlError,'school_worlds_scope_invalid'):
            e.trial(provider,guest_probe=guest,maintenance=False)
        provider.backup.assert_not_called();provider.action.assert_not_called()

    def test_cli_prepares_before_iam_and_closes_after_controller(self):
        for options,revision in (([],p.LEGACY_BUNDLE_REVISION),
                                 (['--revision',p.LEGACY_BUNDLE_REVISION],p.LEGACY_BUNDLE_REVISION),
                                 (['--revision',p.CONSTRAINT_KEYS_REVISION],p.CONSTRAINT_KEYS_REVISION)):
            order=[];guest=mock.Mock()
            with self.subTest(options=options), \
                 mock.patch.object(p,'prepare',side_effect=lambda **kw:order.append('prepare') or guest) as prepare, \
                 mock.patch.object(e,'authenticate',side_effect=lambda:order.append('iam') or 'fake'), \
                 mock.patch.object(e,'trial',side_effect=lambda *a,**kw:order.append('trial') or 0) as trial,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0,e.main(['school_worlds','--ack',ACK,*options]))
            self.assertEqual(['prepare','iam','trial'],order);guest.close.assert_called_once()
            prepare.assert_called_once_with(revision=revision)
            self.assertTrue(trial.call_args.kwargs['maintenance'])

    def test_prepare_failure_has_no_iam_and_iam_failure_closes_route(self):
        with mock.patch.object(p,'prepare',side_effect=p.WorldsError('PRIVATE_SENTINEL')), \
             mock.patch.object(e,'authenticate') as auth,contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(4,e.main(['school_worlds','--ack',ACK]))
        auth.assert_not_called();self.assertNotIn('PRIVATE_SENTINEL',output.getvalue())
        guest=mock.Mock()
        with mock.patch.object(p,'prepare',return_value=guest),mock.patch.object(e,'authenticate',side_effect=e.ControlError('http_403')), \
             contextlib.redirect_stdout(io.StringIO()):self.assertEqual(3,e.main(['school_worlds','--ack',ACK]))
        guest.close.assert_called_once()

    def test_independent_stop_never_prepares_school_route(self):
        with mock.patch.object(p,'prepare') as prepare,mock.patch.object(e,'authenticate',return_value='fake'), \
             mock.patch.object(e,'ordinary_stop',return_value=0) as stop,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0,e.main(['stop','--ack','ORDINARY_STOP_EXACT_IBM']))
        prepare.assert_not_called();stop.assert_called_once()

    def test_dispatch_is_main_only_fixed_job_and_pinned_artifact_upload(self):
        workflow=Path('.github/workflows/ibm-school-compute.yml').read_text()
        block=workflow.split('  school-worlds:',1)[1].split('  stop:',1)[0]
        for text in ("inputs.mode == 'school_worlds'",p.PRODUCTION_REF,
                     'ref: ${{ github.sha }}','persist-credentials: false',ACK,
                     'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
                     'ibm-school-worlds-${{ github.run_id }}','retention-days: 7','include-hidden-files: false'):
            self.assertIn(text,block)
        self.assertEqual(1,block.count('ops/ibm_trial_executor.py school_worlds'))
        for command in ('ops/ibm_trial_executor.py school_worlds', 'ops/ibm_school_compute_receipt.py'):
            line=next(line for line in block.splitlines() if command in line)
            self.assertIn('--revision normalized-constraints-v2',line)
        self.assertLess(block.index('ops/ibm_trial_executor.py school_worlds'),block.index('actions/upload-artifact@'))
        self.assertNotIn('continue-on-error',block)
        self.assertIn("inputs.mode == 'stop' && 'ibm-vpc-independent-stop'",workflow)
        self.assertNotIn('isolate_units',workflow)


if __name__=='__main__':unittest.main()
