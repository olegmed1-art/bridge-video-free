"""Disposable PG18: actual six RPCs in one transaction, no GitHub/Cloud calls.

Run only after the disposable database has all migrations.  This fixture
clears its own disposable queue and patches host/Neon observation solely to
the exact localhost CI owner connection; it cannot name a production DSN.
"""
from contextlib import nullcontext
from types import SimpleNamespace
from dataclasses import asdict
import hashlib
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from database.fixtures.native_cli_commit_rehearsal import connection, require
from database import light_native_pilot_intake as intake
from database import native_cli_permission_engine as engine
from ops import oracle_light_active_hold_attest as hold
from ops import light_native_lane_controller as controller
from ops.native_maintenance_agreement import Agreement, COVERAGE
from oracle_autopilot.light_native_adapter import FALSE_FLAGS


def sample(label):
    head='a'*40
    spec=dict(assignment_schema='SLAVIK_DISPATCH_ASSIGNMENT_V1',
              repository='olegmed1-art/bridge-video-free',target_pr=1150,
              expected_head_sha=head,execution_mode='READ_ONLY',
              exact_head_binding=True,cost_cap_microusd=0,max_repair_attempts=0)
    spec.update(dict.fromkeys(FALSE_FLAGS,False))
    value=dict(version=1,source='c'*40,repository='olegmed1-art/bridge-video-free',
               target_pr=1150,expected_head_sha=head,work_key=label,
               objective='Disposable exact-head audit.',priority=0,
               task_spec_json=spec,branch='codex/disposable-native-pilot')
    raw=intake.encoded(value)
    plan=intake.Plan(raw,hashlib.sha256(raw).hexdigest())
    now=int(time.time())
    def iso(epoch):return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(epoch))
    approval=dict(version=1,owner='olegmed1-art',operation_digest=plan.scope_digest,
                  not_before=iso(now-1),expires_at=iso(now+1200),coverage=COVERAGE,
                  evidence='Disposable PG18 transaction fixture; no production authority.')
    accepted=intake.digest(approval)
    agreement=Agreement(approval,accepted,plan.scope)
    prior=hold.HoldIdentity('autopilot-lite-vnic',123,'d'*32,
            '/opt/bridge-school/school-autopilot-production-light/releases/'+('e'*40),'f'*64)
    baseline=dict(version=1,source='c'*40,package_sha256='b'*64,
                  agreement_sha256=accepted,scope_sha256=plan.scope_digest,
                  prior=asdict(prior),protected={},protected_sha256=intake.digest({}),
                  observed_at=now)
    baseline_raw=intake.encoded(baseline)
    return plan,agreement,prior,baseline_raw,hashlib.sha256(baseline_raw).hexdigest()


def main(result_code='AUDIT_PASSED'):
    with connection() as conn:
        conn.read_only=False
        require(conn.execute('SELECT current_database(),session_user,current_user').fetchone()
                == ('bridge_school_ci','postgres','postgres'), 'DISPOSABLE_IDENTITY_REQUIRED')
        controller.verify_terminal_policy(conn)
        # Earlier CI fixtures intentionally persist queue rows.  Only this
        # disposable database can be cleared, and only under the fixed guard.
        conn.execute('TRUNCATE autopilot.project_work_item,autopilot.task CASCADE')
        conn.execute('UPDATE autopilot.project_planner_state SET enabled=true')
        conn.execute("UPDATE autopilot.role_registry SET enabled=true,can_repair=true WHERE role_id='AUTOPILOT'")
        conn.execute('UPDATE autopilot.native_cli_config SET enabled=false')
        plan,agreement,prior,baseline_raw,baseline_digest=sample('native-intake-pg18')
        def ci_identity(actual,_):
            require(actual is conn and actual.execute(
                'SELECT current_database(),session_user,current_user').fetchone()
                == ('bridge_school_ci','postgres','postgres'), 'DISPOSABLE_IDENTITY_REQUIRED')
        # Disposable superuser runs both the owner step and runtime RPCs in
        # this isolated fixture. Production target().recipient remains the
        # distinct Light login and is never patched in source.
        disposable=engine.Target('bridge_school_ci','postgres','postgres','postgres')
        with patch.object(intake.engine,'identity',ci_identity), \
             patch.object(intake,'target',lambda:disposable), \
             patch.object(intake.hold,'attest',lambda:prior), \
             patch.object(intake.hold,'service_hold_identity',
                          lambda:hold.ServiceHoldIdentity(**asdict(prior))), \
             patch.object(intake.switch,'unchanged_files',lambda *a:None):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'before.json'
                result=intake.prepare(conn,plan,agreement,baseline_raw,baseline_digest,path,
                    target_open=True,observed_head_sha=plan.value['expected_head_sha'],
                    observed_branch=plan.value['branch'])
                require(path.is_file() and result['published'] is False
                        and result['pilot_authorized'] is False,'DISPOSABLE_INTAKE_RECEIPT')
                row=conn.execute('''SELECT w.state,t.status,o.status,o.mode,r.can_repair,c.enabled
                    FROM autopilot.project_work_item w
                    JOIN autopilot.project_work_task m USING(work_item_id)
                    JOIN autopilot.task t USING(task_id)
                    JOIN autopilot.role_dispatch_outbox o USING(task_id)
                    JOIN autopilot.role_registry r ON r.role_id=o.role
                    CROSS JOIN autopilot.native_cli_config c
                    WHERE o.dispatch_id=%s::uuid''',(result['dispatch_id'],)).fetchone()
                require(row==('ACTIVE','WAITING_EXTERNAL','CLAIMED','READ_ONLY',False,True),
                        'DISPOSABLE_INTAKE_DB_STATE')
                conn.read_only=True
                controller.committed_intake(conn,plan,result)
                conn.read_only=False
                snapshots=[]
                refreshed=intake.refresh_publication_claim(conn,plan,agreement,result,
                    effect_guard=lambda:None,
                    durable_before=lambda row:snapshots.append(('before',row)),
                    durable_after=lambda row:snapshots.append(('after',row)))
                require([name for name,_ in snapshots]==['before','after'], 'DISPOSABLE_REFRESH_RECORDS')
                require(conn.execute('SELECT claim_epoch=1 AND attempts=1 AND claim_until<=updated_at+interval \'300 seconds\' '
                    'AND claim_until>clock_timestamp()+interval \'60 seconds\' FROM autopilot.role_dispatch_outbox WHERE dispatch_id=%s::uuid',
                    (result['dispatch_id'],)).fetchone()==(True,), 'DISPOSABLE_REFRESH_CAP')
                conn.read_only=True
                intake.assert_publication_claim(conn,plan,agreement,result,refreshed)
                intake.assert_publication_claim(conn,plan,agreement,result,refreshed)
                conn.read_only=False
                # Force expiry only in this guarded disposable database. The
                # owner helper must refuse it without reclaim or epoch change.
                conn.execute('UPDATE autopilot.role_dispatch_outbox SET claim_until=clock_timestamp()-interval \'1 second\' '
                    'WHERE dispatch_id=%s::uuid',(result['dispatch_id'],))
                expired=intake.publication_claim_rows(conn,plan,result)
                try:
                    intake.refresh_publication_claim(conn,plan,agreement,result,effect_guard=lambda:None,
                        durable_before=lambda row:None,
                        durable_after=lambda row:require(False,'EXPIRED_REFRESH_WROTE_AFTER'))
                except RuntimeError as exc:
                    require(str(exc)=='PILOT_CLAIM_REFRESH_EXPIRED','DISPOSABLE_REFRESH_WRONG_REFUSAL')
                else:
                    require(False,'DISPOSABLE_EXPIRED_CLAIM_RENEWED')
                require(intake.publication_claim_rows(conn,plan,result)==expired,'DISPOSABLE_EXPIRED_REFRESH_MUTATED')
                # Restore only this fixture's artificial expiry so the existing
                # native terminal/control-restore rehearsal can proceed.
                conn.execute('UPDATE autopilot.role_dispatch_outbox SET claim_until=%s::timestamptz '
                    'WHERE dispatch_id=%s::uuid',(refreshed['outbox']['claim_until'],result['dispatch_id']))
                print('LIGHT_NATIVE_PUBLICATION_REFRESH_REAL_PG18_PASS')
                body=intake.dispatch_body(result['dispatch'])
                body_sha=hashlib.sha256(body.encode()).hexdigest()
                marked=conn.execute('''SELECT autopilot.mark_role_dispatch_published(
                    %s::uuid,%s,%s,%s,%s)''',(result['dispatch_id'],plan.worker,
                    result['claim_epoch'],9900366,body_sha)).fetchone()
                require(marked==(True,), 'DISPOSABLE_PUBLICATION_MARK')
                reserve=conn.execute('SELECT autopilot.native_cli_reserve(%s::uuid,%s::jsonb,%s)',
                    (result['dispatch_id'],intake.encoded(result['assignment']).decode(),
                     plan.value['branch'])).fetchone()[0]
                request=reserve['request']
                require(conn.execute('SELECT autopilot.native_cli_begin(%s::jsonb)',
                    (intake.encoded(request).decode(),)).fetchone()==(True,),
                    'DISPOSABLE_NATIVE_BEGIN')
                provider_task='task_e_disposable_intake_pg18'
                conn.execute('SELECT autopilot.native_cli_ack(%s::jsonb,%s,%s)',
                    (intake.encoded(request).decode(),provider_task,'b'*64))
                terminal={'status':'SUCCEEDED','result_code':result_code,
                          'summary':'Disposable intake finished.',
                          'target_head_sha':plan.value['expected_head_sha'],
                          'provider_evidence_sha256':'d'*64}
                conn.execute('SELECT autopilot.native_cli_finish(%s::jsonb,%s,%s::jsonb)',
                    (intake.encoded(request).decode(),provider_task,
                     intake.encoded(terminal).decode()))
                print('DISPOSABLE_TERMINAL_STATES',conn.execute(
                    '''SELECT n.state,n.owner_name,t.status,w.state,o.status,o.delivery_contract_version
                       FROM autopilot.native_cli_receipt n
                       JOIN autopilot.role_dispatch_outbox o USING(dispatch_id)
                       JOIN autopilot.task t USING(task_id)
                       JOIN autopilot.project_work_task m USING(task_id)
                       JOIN autopilot.project_work_item w USING(work_item_id)
                       WHERE n.dispatch_id=%s::uuid''',(result['dispatch_id'],)).fetchone())
                envelope={'version':1,'plan_sha256':plan.digest,
                    'dispatch_id':result['dispatch_id'],'task_id':result['task_id'],
                    'provider_task_id':provider_task,'request':request,'result':terminal}
                terminal_raw=intake.encoded(envelope)
                terminal_sha=hashlib.sha256(terminal_raw).hexdigest()
                conn.read_only=True
                observed=intake.observe_terminal(conn,plan,result,terminal_raw,terminal_sha)
                require(observed['success'] is True and observed['controls_restored'] is False,
                        'DISPOSABLE_TERMINAL_READBACK')
                conn.read_only=False
                restored=intake.restore_controls_after_terminal(conn,plan,result,
                    terminal_raw,terminal_sha,path)
                require(restored['controls_restored'] is True
                        and conn.execute('SELECT enabled FROM autopilot.native_cli_config').fetchone()==(False,)
                        and conn.execute("SELECT can_repair FROM autopilot.role_registry WHERE role_id='AUTOPILOT'").fetchone()==(True,),
                        'DISPOSABLE_CONTROL_RESTORE')
                # Actual DB reconciliation after the restore already committed.
                # No repeat restore SQL is permitted after a lost acknowledgment.
                driver=SimpleNamespace(connect=lambda **kwargs:nullcontext(conn))
                guard=SimpleNamespace(assert_running=lambda:None)
                with patch.object(intake,'restore_controls_after_terminal',
                                  side_effect=AssertionError('restore SQL repeated')):
                    reconciled=controller.reconcile_controls(driver,{},plan,result,terminal_raw,Path(directory),guard)
                    require(reconciled['controls_restored'] is True,'DISPOSABLE_RESTORE_RECONCILE')
            # Containment deliberately preserves the pending graph, with no
            # provider execution, publication command, retry or synthetic result.
            conn.read_only=False
            conn.execute('TRUNCATE autopilot.project_work_item,autopilot.task CASCADE')
            plan,agreement,prior,baseline_raw,baseline_digest=sample('native-contain-pg18')
            with tempfile.TemporaryDirectory() as directory:
                directory=Path(directory)
                result=intake.prepare(conn,plan,agreement,baseline_raw,baseline_digest,directory/'before.json',
                    target_open=True,observed_head_sha=plan.value['expected_head_sha'],
                    observed_branch=plan.value['branch'],
                    durable_receipt=lambda row:(directory/'intake.json').write_bytes(intake.encoded(row)))
                with patch.object(controller,'read',lambda path,*a:path.read_bytes()), \
                     patch.object(controller,'remember',lambda path,raw:path.write_bytes(raw)), \
                     patch.object(controller.execution,'stopped',lambda:None), \
                     patch.object(controller.install,'verify_running',lambda *a:None), \
                     patch.object(controller.pwd,'getpwnam',lambda *a:None), \
                     patch.object(controller.install,'LEDGER',directory/'unused-ledger'), \
                     patch.object(controller.install,'CONTROL',directory/'unused-control'):
                    contained=controller.contain(driver,{},plan,directory,{'version':1},guard)
                    require(contained['state']=='CONTAINED_UNRESOLVED'
                            and contained['queue_retry_authorized'] is False,'DISPOSABLE_CONTAINMENT')
                    require(controller.contain(driver,{},plan,directory,{'version':1},guard)==contained,
                            'DISPOSABLE_CONTAIN_RECONCILE')
                require(conn.execute('SELECT enabled FROM autopilot.native_cli_config').fetchone()==(False,)
                        and conn.execute("SELECT can_repair FROM autopilot.role_registry WHERE role_id='AUTOPILOT'").fetchone()==(False,)
                        and conn.execute('SELECT status FROM autopilot.task WHERE task_id=%s::uuid',
                                         (result['task_id'],)).fetchone()==('WAITING_EXTERNAL',),
                        'DISPOSABLE_CONTAIN_PRESERVES_PENDING_GRAPH')
        print('LIGHT_NATIVE_PILOT_INTAKE_REAL_PG18_PASS')


if __name__=='__main__':
    main()
    main('AUDIT_FINDINGS_REPORTED')
