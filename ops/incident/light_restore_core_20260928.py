"""One fixed-scope AFTER workflow restoration; no permission session or stage admission.

The caller owns the original exclusive journal locks, an authenticated incident
run, the separate durable receipt, a private CAS store and fresh reconciliation.
Every failure is terminal for this invocation; inspect before any new authority.
"""
import hashlib

SOURCE = '8bbc1d61010ef70c3fca02b5151ac86fce02a144'
SCOPE = 'c0795e9e1533c3baf554a0d785b303180d8bf6771d07e6d98e5f67f0b96ef542'
HEAD = '4da1bcf5d0aef685289dc3c72cbf710f94f9508f41cab1380c87c1f6a29d6dac'
PAIR = '9d427c665d3248b6b8d8ee730e8acf2a5539ae65499576c8075f2e35b59ec99a'
WORKFLOW = 343949665
OUTCOME = 'AFTER'
INITIAL_OPERATION = ('BOUND', 'PREPARED', 'SESSION_BOUND', 'SESSION_INTENT', 'SESSION_RESULT')
INITIAL_PAUSE = ('PLAN', 'DISABLE_INTENT', 'DISABLED')


class RestoreRefused(RuntimeError):
    """Public-safe terminal status; never contains private exception text."""

    def __init__(self, code, phase, effects_possible):
        super().__init__(code)
        self.code, self.phase, self.effects_possible = code, phase, effects_possible


def _require(condition, code):
    if not condition:
        raise RuntimeError(code)


def _identity(value):
    return (type(value) is dict and set(value) == {'run_id', 'attempt', 'job_id'}
            and all(type(n) is int and n > 0 for n in value.values()))


def _initial(packet, operation, pause, guard, snapshot, digest):
    _require(type(packet) is dict and packet.get('stage') == 'restore'
             and packet.get('expected_outcome') == OUTCOME
             and packet.get('accepted_head_digest') == HEAD
             and packet.get('recovery_pair_digest') == PAIR
             and type(packet.get('scope')) is dict and digest(packet['scope']) == SCOPE
             and packet['scope'].get('source') == SOURCE
             and packet['scope'].get('operation') == 'apply'
             and type(packet.get('plan')) is dict
             and packet['plan'].get('source') == SOURCE
             and digest(packet['plan']) == guard.plan_digest
             and len(packet['plan'].get('workflows', [])) == 1
             and packet['plan']['workflows'][0]['id'] == WORKFLOW
             and packet['plan']['workflows'][0]['state'] == 'active'
             and type(packet.get('prior_units')) is list
             and [u.get('stage') for u in packet['prior_units']] == ['prepare', 'execute']
             and all(u.get('source') == SOURCE and u.get('scope_digest') == SCOPE
                     and _identity(u.get('run')) for u in packet['prior_units']),
             'INCIDENT_PACKET')
    _require(getattr(guard, 'receipt_accepted', False) is True
             and _identity(guard.run_identity)
             and all(callable(getattr(guard, name, None)) for name in
                     ('assert_current', 'assert_live', 'assert_reconciled')),
             'INCIDENT_AUTHORITY')
    _require(tuple(r['event'].get('kind') for r in operation.records) == INITIAL_OPERATION
             and tuple(r['event'].get('kind') for r in pause.records) == INITIAL_PAUSE
             and operation.records[0]['event'] == {'kind': 'BOUND', 'scope': packet['scope']}
             and pause.records[0]['event'] == {'kind': 'PLAN', 'digest': guard.plan_digest,
                 'plan': packet['plan'], 'operation_scope_digest': SCOPE}
             and all(r['event'].get('run') in [u['run'] for u in packet['prior_units']]
                     for r in operation.records[1:])
             and operation.records[-1]['event']['outcome'] == OUTCOME,
             'INCIDENT_JOURNALS')
    _require(hashlib.sha256(snapshot.capture_locked(operation, pause)).hexdigest() == PAIR,
             'INCIDENT_PAIR')
    _require(all(guard.run_identity['run_id'] != u['run']['run_id']
                 for u in packet['prior_units']), 'INCIDENT_RUN')


def restore(packet, operation, pause, store, guard):
    """Publish the consumed suffix once, then enable the one owned workflow.

    `guard.assert_live()` must authenticate the incident run, current main,
    fresh exclusive window and supervisor. `assert_reconciled()` additionally
    verifies the exact AFTER database, HOLD, prior hosts and backend/workflow
    drain. The host must own a create-only, off-VM backed receipt first.
    """
    from ops import native_maintenance_checkpoint as cp
    from ops import native_maintenance_snapshot as snapshot
    from ops.native_maintenance_workflow_pause import WorkflowPause, digest
    from ops.native_maintenance_workflow_api import WorkflowAPI

    phase, effects_possible = 'validate', False
    try:
        _initial(packet, operation, pause, guard, snapshot, digest)
        scope, plan_digest = SCOPE, guard.plan_digest

        def current():
            guard.assert_current()

        def live():
            current()
            guard.assert_live()
            current()

        def reconciled():
            current()
            guard.assert_reconciled(scope, OUTCOME)
            current()

        class Release:
            calls = 0

            def assert_reconciled(self, requested):
                _require(requested == plan_digest, 'INCIDENT_RELEASE_SCOPE')
                self.calls += 1
                # The first read precedes intent; after intent the Dispatch
                # guard performs full reconciliation before the PUT. The final
                # release check runs after the observed enable.
                live() if self.calls <= 2 else reconciled()

        class Coordination:
            def assert_scope(self, requested):
                _require(requested == plan_digest, 'INCIDENT_COORDINATION_SCOPE')
                current()

        checkpoint = None

        class Reconciler:
            def assert_reconciled(self, observed_scope, observed_outcome):
                _require(observed_scope == scope and observed_outcome == OUTCOME,
                         'INCIDENT_RECONCILIATION_SCOPE')
                reconciled()

        class Dispatch:
            calls = 0

            def assert_dispatch(self, requested, action, workflow_id):
                nonlocal phase, effects_possible
                _require(requested == plan_digest and action == 'enable'
                         and workflow_id == WORKFLOW, 'INCIDENT_EFFECT_SCOPE')
                self.calls += 1
                _require(self.calls <= 2, 'INCIDENT_DISPATCH_REUSED')
                phase = 'enable_intent_checkpoint'
                if self.calls == 1:
                    reconciled()
                checkpoint.sync(scope, operation, pause)
                # WorkflowAPI invokes this twice around its fresh main-source
                # GET. The second full check follows the final checkpoint and
                # immediately precedes its one PUT.
                if self.calls == 2:
                    reconciled()
                else:
                    live()
                # The GitHub PUT may take effect even if its response is lost.
                if self.calls == 2:
                    effects_possible = True
                    phase = 'enable_possible'

        phase = 'workflow_preflight'
        reconciled()
        api = WorkflowAPI(guard.token, packet['plan'], plan_digest,
                          mutation_guard=Dispatch(), read_api=guard.api)
        _require(api.get_workflow(WORKFLOW) == pause.records[-1]['event']['observed'],
                 'INCIDENT_WORKFLOW_DRIFT')

        phase = 'consumed_suffix'
        effects_possible = True  # OCI CAS may have committed despite an unknown ACK.
        head = cp.publish_reconciled_session_suffix(store, scope, operation, pause,
            accepted_head_digest=HEAD, accepted_pair_digest=PAIR,
            observed_outcome=OUTCOME, reconciler=Reconciler())
        checkpoint = cp.JournalCheckpoint(store, accepted_head_digest=head)
        checkpoint.accept_resume(scope, operation, pause)
        live()

        phase = 'restore_intent'
        live()
        operation.append({'kind': 'RESTORE_INTENT', 'run': guard.run_identity, 'outcome': OUTCOME})
        checkpoint.sync(scope, operation, pause)
        live()

        phase = 'workflow_restore'
        workflow = WorkflowPause(packet['plan'], plan_digest, api, pause, Coordination(),
                                 operation_scope_digest=scope)
        workflow.restore(Release())
        phase = 'enabled_checkpoint'
        live()
        checkpoint.sync(scope, operation, pause)
        reconciled()
        _require(workflow.states[WORKFLOW]['phase'] == 'restored'
                 and api.get_workflow(WORKFLOW) == workflow.states[WORKFLOW]['observed'],
                 'INCIDENT_ENABLE_CONFIRMATION')

        phase = 'restored_checkpoint'
        live()
        operation.append({'kind': 'RESTORED', 'run': guard.run_identity, 'outcome': OUTCOME})
        head = checkpoint.sync(scope, operation, pause)
        reconciled()
        local = snapshot.capture_locked(operation, pause)
        _require(cp.accepted_latest(store, scope, head) == local
                 and operation.records[-1]['event'] ==
                 {'kind': 'RESTORED', 'run': guard.run_identity, 'outcome': OUTCOME},
                 'INCIDENT_FINAL_PAIR')
        return {'scope_digest': scope, 'head_digest': head,
                'pair_digest': cp.sha(local), 'outcome': OUTCOME,
                'restored_workflow_ids': [WORKFLOW]}
    except BaseException as exc:
        # Allow-list only fixed local codes. Never print a traceback, API body,
        # DB error, private journal, token or exception message.
        value = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else None
        code = value if value in {
            'INCIDENT_PACKET', 'INCIDENT_AUTHORITY', 'INCIDENT_JOURNALS',
            'INCIDENT_PAIR', 'INCIDENT_RUN', 'INCIDENT_RELEASE_SCOPE',
            'INCIDENT_COORDINATION_SCOPE', 'INCIDENT_EFFECT_SCOPE',
            'INCIDENT_RECONCILIATION_SCOPE', 'INCIDENT_ENABLE_CONFIRMATION',
            'INCIDENT_FINAL_PAIR', 'INCIDENT_WORKFLOW_DRIFT'} else 'INCIDENT_REFUSED'
        raise RestoreRefused(code, phase, effects_possible) from None
