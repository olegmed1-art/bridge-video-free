from dataclasses import fields, replace
from decimal import Decimal
import unittest
from ops import ibm_trial_plan as p


class TrialPlanTests(unittest.TestCase):
    def setUp(self):
        self.ready = p.Preconditions(**{f.name: True for f in fields(p.Preconditions)})

    def decision(self, **kw):
        args=dict(preconditions=self.ready, started=True, state='running', elapsed_seconds=60, running_seconds=10)
        args.update(kw)
        return p.decide(**args)

    def test_every_precondition_is_required_before_start(self):
        self.assertEqual('PROPOSE_SINGLE_START',self.decision(started=False,state='stopped'))
        for f in fields(p.Preconditions):
            self.assertEqual('BLOCKED', self.decision(preconditions=replace(self.ready, **{f.name:False}),started=False,state='stopped'))
        self.assertEqual('BLOCKED',self.decision(started=False,state='running'))
        for kw in [{'action_outcome_unknown':True},{'stop_submitted':True},{'work_activity':True},
                   {'observer_healthy':False},{'service_stop_failed':True}]:
            self.assertEqual('BLOCKED',self.decision(started=False,state='stopped',**kw))
        with self.assertRaises(ValueError): self.decision(started=False,state='stopped',preconditions=object())

    def test_exact_actions_never_force_or_other_resources(self):
        for action in ['start','stop']:
            request=p.action_proposal(action)
            self.assertEqual({'type':action,'force':False},request['body'])
            self.assertIn(p.INSTANCE_ID,request['url'])
        for action in ['restart','force_stop','delete','STOP']:
            with self.assertRaises(ValueError): p.action_proposal(action)

    def test_admin_deadline_is_120_seconds_after_running(self):
        self.assertEqual('TRY_IDENTIFIED_GUEST_ROUTE_ONLY',self.decision(elapsed_seconds=200,running_seconds=119))
        self.assertEqual('PROPOSE_ORDINARY_STOP',self.decision(elapsed_seconds=200,running_seconds=120))

    def test_ten_minute_limit_includes_starting(self):
        for state in ['starting','running']:
            self.assertEqual('PROPOSE_ORDINARY_STOP',self.decision(state=state,elapsed_seconds=600,admin_verified=True,six_services_stopped=True))

    def test_activity_stop_failure_or_lost_observer_stop_immediately(self):
        for kw in [{'work_activity':True},{'service_stop_failed':True},{'observer_healthy':False}]:
            self.assertEqual('PROPOSE_ORDINARY_STOP',self.decision(**kw))

    def test_admin_then_temporary_stop_then_read_only(self):
        self.assertEqual('STOP_EXACT_SIX_SERVICES_TEMPORARILY',self.decision(admin_verified=True))
        self.assertEqual('READ_ONLY_GUEST_DIAGNOSTICS',self.decision(admin_verified=True,six_services_stopped=True))
        self.assertEqual(6,len(p.SERVICES))

    def test_uncertain_mutation_and_submitted_stop_not_retried(self):
        for kw in [{'action_outcome_unknown':True},{'stop_submitted':True}]:
            self.assertEqual('READ_RECONCILE_AND_ALERT_OPERATOR',self.decision(elapsed_seconds=601,**kw))
        self.assertEqual('READ_RECONCILE_AND_ALERT_OPERATOR',self.decision(state='stopped',stop_submitted=True))
        self.assertEqual('READ_RECONCILE_AND_ALERT_OPERATOR',self.decision(state='stopped',action_outcome_unknown=True,terminal_stop_reconciled=True))
        self.assertEqual('CONFIRMED_STOPPED',self.decision(state='stopped',stop_submitted=True,terminal_stop_reconciled=True))

    def test_unknown_state_and_missing_running_clock_fail_closed(self):
        self.assertEqual('READ_RECONCILE_AND_ALERT_OPERATOR',self.decision(state='failed'))
        self.assertEqual('PROPOSE_ORDINARY_STOP',self.decision(running_seconds=None))

    def test_cost_threshold_and_no_credit_assumption(self):
        self.assertEqual(Decimal('10'),p.estimate_usd(Decimal('60'),600,Decimal('0')))
        self.assertEqual(Decimal('11'),p.estimate_usd(Decimal('60'),600,Decimal('1')))
        for rate in [Decimal('NaN'),Decimal('Infinity'),Decimal('-1'),1.0]:
            with self.assertRaises(ValueError):p.estimate_usd(rate,600,Decimal('0'))

    def test_bad_clocks_and_flags_refused(self):
        for kw in [{'elapsed_seconds':-1},{'running_seconds':61},{'started':1},{'elapsed_seconds':True}]:
            with self.assertRaises(ValueError):self.decision(**kw)


if __name__ == '__main__':
    unittest.main()
