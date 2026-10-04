"""REAL PostgreSQL tests. ONLY fixed loopback disposable database; no live env."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch
import psycopg
from psycopg.types.json import Jsonb
import pytest

from tools.tournament_pilot.rehearsal import DB, ROLE, TABLE_BUDGET, scalar, api_client
from tools.tournament_pilot.package import envelope
from .test_fixed_adapter import launch, permit, NOW
from .fixed_adapter import FixedAdapter, CommitUncertain, RecoveryUnproven
from .ownership import KEYS, inventory, declared_values
from .resident_preflight import Refused
from .bounded_controller import BoundedController, PATH
from .launch_contract import digest, ORIGIN

def record_evidence(case, value):
    from pathlib import Path
    path=Path("canon-cutoff-watchdog-evidence.json")
    records=json.loads(path.read_text()) if path.exists() else []
    records.append({"case":case,"evidence_class":"real_loopback_postgresql18_not_live",**value})
    path.write_text(json.dumps(records,sort_keys=True,indent=2))


def connect(user=ROLE):
    return psycopg.connect(host="127.0.0.1",hostaddr="127.0.0.1",port=55432,
        dbname=DB,user=user,password="",passfile="/dev/null",sslmode="disable",autocommit=True)


@pytest.fixture
def fixture():
    # Test-only superuser cleanup/fault injection. Fixed localhost, fixed DB,
    # never included in the owner adapter or operational command.
    with connect("postgres") as admin:
        assert admin.info.host=="127.0.0.1" and admin.info.port==55432
        assert admin.execute("SELECT current_database(),current_user").fetchone()==(DB,"postgres")
        admin.execute("TRUNCATE "+",".join(TABLE_BUDGET)+" CASCADE")
    with connect() as owner, connect() as recovery:
        school=scalar(owner,"INSERT INTO public.school(stable_name) VALUES (%s) RETURNING school_id",
                      ("synthetic-adapter-"+str(uuid4()),))
        clock=lambda:datetime.now(timezone.utc)
        l=launch(school,now=clock())
        a=FixedAdapter(owner,school,l,lambda:None,clock)
        r=FixedAdapter(recovery,school,l,lambda:None,clock)
        yield a,r,school,l


def stage(a,name):
    return a.execute(name,permit(a.launch,name,a.clock()))


def full(a):
    result={}
    for name in ("baseline","initial","revoke","reactivate"):
        result[name]=stage(a,name)
    return result


def test_real_lifecycle_http_and_independent_expired_revoke(fixture):
    a,r,school,l=fixture
    with api_client() as client:
        assert client.post(PATH,json=envelope("3H")).status_code==404
        stage(a,"baseline")
        assert client.post(PATH,json=envelope("3H")).json()["status"]=="ABSTAIN"
        first=stage(a,"initial")
        answer=client.post(PATH,json=envelope("3H")).json()
        assert answer["status"]=="SUPPORTED" and answer["point_threshold"] is None
        assert answer["source_bindings"][0]["decision_ids"]==["TDEC-20261003-002"]
        assert client.post(PATH,json=envelope("3S")).json()["status"]=="CONTRADICTED"
        stage(a,"revoke")
        assert client.post(PATH,json=envelope("3H")).json()["status"]=="ABSTAIN"
        final=stage(a,"reactivate")
        assert final["rows"]==40 and final["original_expiry"]==first["original_expiry"]
        assert client.post(PATH,json=envelope("3H")).json()["status"]=="SUPPORTED"
        r.clock=lambda:NOW+timedelta(days=2) # recovery ignores NORMAL controller clock
        stopped=r.recover()
        assert stopped["rows"]==42 and stopped["active"]==0
        assert r.recover()["rows"]==42
        assert client.post(PATH,json=envelope("3H")).json()["status"]=="ABSTAIN"
    assert scalar(a.conn,"SELECT count(*) FROM ai.teacher_output")==0
    assert scalar(a.conn,"SELECT count(*) FROM ai.search_run")==0
    assert scalar(a.conn,"SELECT count(*) FROM ai.final_decision")==0


def test_noop_only_when_all_ids_absent(fixture):
    a,r,_,_=fixture
    assert r.recover()["no_op"] is True
    assert r.inspect()["rows"]==0


@pytest.mark.parametrize("name",["initial","revoke","reactivate"])
def test_out_of_order_never_writes(fixture,name):
    a,r,_,_=fixture
    with pytest.raises(Refused,match="out_of_order"):
        stage(a,name)
    assert a.inspect()["rows"]==0


def test_serialized_duplicate_stage(fixture):
    a,r,_,_=fixture
    barrier=Barrier(2)
    def attempt(adapter):
        barrier.wait()
        try:
            stage(adapter,"baseline")
            return "committed"
        except Refused:
            return "refused"
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(attempt,[a,r]))
    assert sorted(results)==["committed","refused"]
    assert a.inspect()["rows"]==2
    assert r.recover()["rows"]==4


def test_window_rechecked_after_lock(fixture):
    a,r,_,_=fixture
    base=a.clock()
    class ExpireAfterLock(ConnectionProxy):
        def execute(self,sql,*args,**kwargs):
            result=self.conn.execute(sql,*args,**kwargs)
            if "pg_advisory_xact_lock(20261004,201)" in sql:
                a.clock=lambda:base+timedelta(days=1)
            return result
    a.conn=ExpireAfterLock(a.conn)
    with pytest.raises(Refused,match="stage_window"):
        stage(a,"baseline")
    assert r.inspect()["rows"]==0


def test_commit_then_source_receipt_loss_is_uncertain_and_recoverable(fixture):
    a,r,_,_=fixture
    calls=0
    def source():
        nonlocal calls
        calls+=1
        if calls==3:
            raise Refused("source_after_missing")
    a.source_check=source
    with pytest.raises(CommitUncertain):
        stage(a,"baseline")
    assert r.inspect()["state"]=="baseline"
    assert r.recover()["rows"]==4


def test_connection_loss_before_or_after_dispatch_does_not_claim_rollback(fixture):
    a,r,_,_=fixture
    stage(a,"baseline")
    a.conn.close()
    with pytest.raises(Refused):
        stage(a,"initial")
    assert r.recover()["rows"]==4


def test_recovery_failure_is_unproven(fixture):
    a,r,_,_=fixture
    stage(a,"baseline");stage(a,"initial")
    r.source_check=lambda:(_ for _ in ()).throw(Refused("channel_lost"))
    with pytest.raises(RecoveryUnproven):
        r.recover()
    assert a.inspect()["active"]==4


def test_natural_key_foreign_id_refuses_before_write(fixture):
    a,r,school,l=fixture
    spec=next(s for s in a.compiled["declared_rows"] if s["table"]=="public.source")
    a.conn.execute("INSERT INTO public.source(school_id,source_type,title,canonical_locator) VALUES (%s,'document','foreign',%s)",
                   (school,spec["values"]["canonical_locator"]))
    with pytest.raises(Refused,match="natural_key_collision"):
        stage(a,"baseline")
    with pytest.raises(RecoveryUnproven):
        r.recover()
    assert scalar(a.conn,"SELECT count(*) FROM public.source")==1


FIELDS={"public.source":"title","ai.decision_position":"hand_pbn",
    "public.knowledge_item":"title","public.knowledge_version":"provenance",
    "public.knowledge_version_source":"source_locator","bidding.rule":"compiled_payload",
    "bidding.rule_test":"fixture","bidding.rule_test_run":"result_details",
    "bidding.ingestion_run":"metadata","bidding.ingestion_event":"details",
    "public.canon_activation":"approval_provenance","bidding.runtime_activation":"activation_provenance"}


@pytest.mark.parametrize("index",range(42))
def test_every_deterministic_id_foreign_content_refuses_without_updates(fixture,index):
    a,r,_,_=fixture
    full(a);r.recover()
    spec=a.compiled["declared_rows"][index];table=spec["table"];field=FIELDS[table]
    where=" AND ".join(k+"=%s" for k in KEYS[table])
    params=tuple(declared_values(spec)[k] for k in KEYS[table])
    value="foreign" if field in ("title","hand_pbn") else Jsonb({"foreign":True})
    # Fixture superuser injects corruption, including append-only tables.
    # Operational adapter has no DISABLE TRIGGER/ALTER/cleanup path.
    with connect("postgres") as admin:
        with admin.transaction():
            admin.execute("ALTER TABLE "+table+" DISABLE TRIGGER ALL")
            admin.execute("UPDATE "+table+" SET "+field+"=%s WHERE "+where,(value,*params))
            admin.execute("ALTER TABLE "+table+" ENABLE TRIGGER ALL")
    before=a.conn.execute("SELECT pg_catalog.to_jsonb(t) FROM "+table+" t WHERE "+where,params).fetchone()[0]
    with pytest.raises(Refused):
        stage(a,"baseline")
    with pytest.raises(RecoveryUnproven):
        r.recover()
    after=a.conn.execute("SELECT pg_catalog.to_jsonb(t) FROM "+table+" t WHERE "+where,params).fetchone()[0]
    assert after==before # foreign row never overwritten/revoked by emergency


def test_partial_state_refuses_even_emergency(fixture):
    a,r,_,_=fixture
    stage(a,"baseline")
    with connect("postgres") as admin:
        with admin.transaction():
            admin.execute("ALTER TABLE ai.decision_position DISABLE TRIGGER ALL")
            admin.execute("DELETE FROM ai.decision_position")
            admin.execute("ALTER TABLE ai.decision_position ENABLE TRIGGER ALL")
    with pytest.raises(Refused,match="partial"):
        a.inspect()
    with pytest.raises(RecoveryUnproven):
        r.recover()


class Channel:
    def __init__(self,adapter,name):
        self.adapter=adapter;self.channel_id=name;self.claimed=False
    def claim(self,launch):
        assert not self.claimed
        assert self.adapter.inspect()["state"]=="absent"
        self.claimed=True # fixture-only; live requires durable authoritative claim
        return dict(status="DURABLE_CLAIM",contract_hash=launch.fingerprint,intent=launch.intent,
            validation_build_id=launch.validation_build_id,controller_run_id=launch.controller_run_id,attempt=1)
    def execute(self,name,p):
        return self.adapter.execute(name,p)
    def inspect(self,launch):
        return self.adapter.inspect()
    def recover(self,launch):
        return self.adapter.recover()


class Observer:
    evidence_class="synthetic_receipt_ledger_real_postgresql18_not_vercel"
    def __init__(self,l,clock):
        self.l=l;self.clock=clock;self.ledger={};self.fail_path=None;self.ready=True
    def deployment(self,l):
        return dict(deployment_id=l.deployment_id,sha=l.deployment_sha,origin=ORIGIN,state="READY")
    def request(self,l,receipt):
        if self.fail_path==receipt["path"]:
            return []
        return self.ledger.get(receipt["request_id"],[])
    def reverify(self,l,receipt):
        return bool(self.request(l,receipt))
    def recovery(self,l,channel):
        return dict(channel_id=channel,program_hash=l.module_hash,runtime_sha=l.runtime_sha,
            contract_hash=l.fingerprint,status="RECOVERY_READY_READ_ONLY",independent=self.ready,
            attempt=1,event="workflow_dispatch",ref="refs/heads/main",actor="olegmed1-art",
            source_transport="authenticated_github",owned_revoke_privileges=True,
            watchdog_status="WATCHDOG_ARMED",watchdog_cutoff=l.public()["stage_until"],
            unconditional_revoke=True,watchdog_run_id=789,watchdog_original_sha256="b"*64,
            run_id=456,original_record_sha256="a"*64,observed_at=self.clock().isoformat().replace("+00:00","Z"))


class Api:
    def __init__(self,client,observer,clock):
        self.client=client;self.observer=observer;self.clock=clock;self.count=0
    def behavior(self):
        from .teacher_behavior import inspect_teacher_connection
        with connect("bridge_school_app_principal") as app:
            return inspect_teacher_connection(app,database=DB)
    def school_matches(self,data):
        return data=={"school":"fixture"}
    def request(self,path,body):
        self.count+=1
        # Health/overview are transport fixtures. Teacher requests below execute
        # REAL existing HTTP auth -> app-role SQL -> reader gates -> assessment.
        if path=="/healthz":
            status,data=200,{"status":"ok"}
        elif path=="/v1/overview":
            status,data=200,{"school":"fixture"}
        else:
            response=self.client.post(path,json=body);status,data=response.status_code,response.json()
        at=self.clock().isoformat().replace("+00:00","Z")
        rid="iad1-1791158400000-"+format(self.count,"012x")
        receipt=dict(request_id=rid,path=path,status_code=status,at=at)
        self.observer.ledger[rid]=[dict(deployment_id=self.observer.l.deployment_id,
            **receipt,invocation_id="invocation"+str(self.count),original_record_sha256=digest(receipt))]
        return dict(status=status,data=data,receipt=receipt)


def controller(fixture,client):
    a,r,_,l=fixture
    clock=lambda:datetime.now(timezone.utc)
    obs=Observer(l,clock);api=Api(client,obs,clock)
    return BoundedController(l,api,obs,Channel(a,"normal"),Channel(r,"independent-recovery"),
                             clock=clock,sleep=lambda n:None),obs


def test_real_bounded_controller(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        result=ctrl.run()
        assert result["status"]=="BOUNDED_ACCEPTANCE" and result["normal_rows"]==40
        assert result["pilot_24h_admission"] is False
        assert result["unconditional_revoke_at"]==ctrl.launch.public()["stage_until"]
        assert result["requests"]==9 and len(result["receipts"])==9
        assert len({r["request_id"] for r in result["receipts"]})==9
        with pytest.raises(Refused,match="single_controller"):
            ctrl.run()
        assert ctrl.recovery.recover(ctrl.launch)["rows"]==42


def test_no_recovery_readiness_means_no_writes(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client);obs.ready=False
        with pytest.raises(Refused,match="before_writes"):
            ctrl.run()
        assert fixture[0].inspect()["rows"]==0


def test_missing_correlation_after_initial_invokes_independent_recovery(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        original=ctrl.normal.execute
        def lose(name,p):
            result=original(name,p)
            if name=="initial":obs.fail_path=PATH
            return result
        ctrl.normal.execute=lose
        with pytest.raises(Refused,match="owned_recovery_confirmed"):
            ctrl.run()
        assert fixture[0].inspect()["rows"]==36
        assert fixture[0].inspect()["active"]==0


def test_controller_lost_recovery_cannot_claim_success(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        original=ctrl.normal.execute
        def lose(name,p):
            result=original(name,p)
            if name=="initial":
                obs.fail_path=PATH
                ctrl.recovery.adapter.source_check=lambda:(_ for _ in ()).throw(Refused("lost"))
            return result
        ctrl.normal.execute=lose
        with pytest.raises(RecoveryUnproven):
            ctrl.run()
        assert fixture[0].inspect()["active"]==4


def test_extra_test_run_refuses_before_revoke(fixture):
    a,r,school,l=fixture
    stage(a,"baseline");stage(a,"initial")
    spec=next(s for s in a.compiled["declared_rows"] if s["table"]=="bidding.rule_test_run")
    with connect("postgres") as admin:
        with admin.transaction():
            admin.execute("ALTER TABLE bidding.rule_test_run DISABLE TRIGGER ALL")
            admin.execute("INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version) VALUES (%s,%s,'fail','{}',%s)",
                (school,spec["values"]["rule_test_id"],spec["values"]["method_version"]))
            admin.execute("ALTER TABLE bidding.rule_test_run ENABLE TRIGGER ALL")
    with pytest.raises(Refused,match="foreign_related_row"):
        stage(a,"revoke")
    with pytest.raises(RecoveryUnproven):
        r.recover()
    assert scalar(a.conn,"SELECT count(*) FROM bidding.runtime_activation WHERE status='active'")==2


def test_final_correlation_failure_recovers(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        obs.reverify=lambda l,r:False
        with pytest.raises(Refused,match="owned_recovery_confirmed"):
            ctrl.run()
        assert fixture[0].inspect()["rows"]==42
        assert fixture[0].inspect()["active"]==0


def test_forged_stage_receipt_recovers(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        original=ctrl.normal.execute
        def forge(name,p):
            return original(name,p)|{"plan_hash":"0"*64}
        ctrl.normal.execute=forge
        with pytest.raises(Refused,match="owned_recovery_confirmed"):
            ctrl.run()
        assert fixture[0].inspect()["rows"]==4


def test_compiler_inventory_diagnostic(fixture):
    # Raw fixture transaction preserves underlying SQL diagnostics for tests;
    # production owner CLI still emits only fixed sanitized categories.
    a,r,_,_=fixture
    with a.conn.transaction(force_rollback=True):
        a._begin()
        a._write_path()
        for name in ("baseline","initial"):
            for sql in a.compiled[name]:
                a.conn.execute(sql)
        assert inventory(a.conn,a.compiled,a.school)["rows"]==34


def test_final_owned_readback_drift_recovers(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        original=ctrl.normal.inspect
        ctrl.normal.inspect=lambda l:original(l)|{"owned_outputs":{"teacher_output":1,"search_run":0,"final_decision":0}}
        with pytest.raises(Refused,match="owned_recovery_confirmed"):
            ctrl.run()
        assert fixture[0].inspect()["active"]==0


def test_wall_supervisor_interrupts_blocked_preflight_without_writes(fixture):
    import time
    from .launch_contract import Launch
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        now=datetime.now(timezone.utc)
        l=Launch.parse(ctrl.launch.public()|{
            "open_at":(now-timedelta(seconds=1)).isoformat().replace("+00:00","Z"),
            "admission_until":(now+timedelta(seconds=.3)).isoformat().replace("+00:00","Z"),
            "stage_until":(now+timedelta(seconds=.3)).isoformat().replace("+00:00","Z")})
        ctrl.launch=l;obs.l=l
        clock=lambda:datetime.now(timezone.utc)
        ctrl.clock=clock;obs.clock=clock;ctrl.api.clock=clock
        fixture[0].launch=l;fixture[1].launch=l
        reached=[]
        def blocked():
            reached.append(True)
            time.sleep(2)
            raise AssertionError("Supervisor did not interrupt")
        ctrl.api.behavior=blocked
        began=time.monotonic()
        with pytest.raises(Refused,match="before_writes"):
            ctrl.run()
        assert reached and time.monotonic()-began < 1.5
        assert fixture[0].inspect()["rows"]==0


def test_missing_durable_claim_stops_before_writes(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        ctrl.normal.claim=lambda l:None
        with pytest.raises(Refused,match="durable_single_build_claim"):
            ctrl.run()
        assert fixture[0].inspect()["rows"]==0


def test_untrusted_public_create_refuses_without_write(fixture):
    a,r,_,_=fixture
    with connect("postgres") as admin:
        admin.execute("GRANT CREATE ON SCHEMA public TO bridge_school_app_principal")
    try:
        with pytest.raises(Refused,match="trusted_trigger_schema"):
            stage(a,"baseline")
        assert r.inspect()["rows"]==0
    finally:
        with connect("postgres") as admin:
            admin.execute("REVOKE CREATE ON SCHEMA public FROM bridge_school_app_principal")


def test_temp_schema_shadow_refuses_without_write(fixture):
    a,r,_,_=fixture
    a.conn.execute("CREATE TEMP TABLE knowledge_version(x integer)")
    with pytest.raises(Refused,match="trusted_trigger_schema"):
        stage(a,"baseline")
    assert r.inspect()["rows"]==0


class ConnectionProxy:
    def __init__(self,conn):
        self.conn=conn
    def __getattr__(self,name):
        return getattr(self.conn,name)


def test_real_transport_loss_after_commit_is_uncertain_and_independently_recovered(fixture):
    a,r,_,_=fixture
    real=a.conn
    class AfterCommit(ConnectionProxy):
        @contextmanager
        def transaction(self,**kwargs):
            with self.conn.transaction(**kwargs):
                yield
            if not kwargs.get("force_rollback"):
                self.conn.close()
                raise psycopg.OperationalError("fixture_transport_lost_after_commit")
    a.conn=AfterCommit(real)
    with pytest.raises(CommitUncertain):
        stage(a,"baseline")
    assert r.inspect()["rows"]==2 # committed, despite absent adapter receipt
    assert r.recover()["rows"]==4


def test_real_transport_loss_inside_write_aborts_server_transaction(fixture):
    a,r,_,_=fixture
    class DuringWrite(ConnectionProxy):
        def execute(self,sql,*args,**kwargs):
            result=self.conn.execute(sql,*args,**kwargs)
            if sql.startswith("INSERT INTO public.source("):
                self.conn.close()
                raise psycopg.OperationalError("fixture_transport_lost_before_commit")
            return result
    a.conn=DuringWrite(a.conn)
    with pytest.raises(CommitUncertain):
        stage(a,"baseline")
    assert r.inspect()["rows"]==0
    assert r.recover()["no_op"] is True


def test_committed_stage_receipt_publication_failure_recovers_without_replay(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        original=ctrl.normal.execute
        def missing(name,p):
            original(name,p)
            raise OSError("fixture_receipt_publication_failed")
        ctrl.normal.execute=missing
        with pytest.raises(Refused,match="owned_recovery_confirmed"):
            ctrl.run()
        assert fixture[0].inspect()["rows"]==4
        assert fixture[0].inspect()["active"]==0


def test_inspection_waiting_for_lock_sees_committed_recovery(fixture):
    from threading import Event
    a,r,_,_=fixture
    full(a)
    waiting=Event()
    class ObserveLock(ConnectionProxy):
        def execute(self,sql,*args,**kwargs):
            if "pg_advisory_xact_lock(20261004,201)" in sql:
                waiting.set() # after catalog_path SELECT, before lock wait
            return self.conn.execute(sql,*args,**kwargs)
    r.conn=ObserveLock(r.conn)
    pool=ThreadPoolExecutor(1)
    try:
        with a.conn.transaction():
            a._begin()
            future=pool.submit(r.inspect)
            assert waiting.wait(1)
            a._write_path()
            for sql in a.compiled["emergency"]:
                a.conn.execute(sql)
        observed=future.result(timeout=2)
        assert (observed["state"],observed["rows"],observed["active"])==("emergency",42,0)
    finally:
        pool.shutdown(wait=True)


def real_window(fixture, seconds):
    from .launch_contract import Launch
    a,r,_,l=fixture
    now=datetime.now(timezone.utc)
    changed=Launch.parse(l.public()|{
        "open_at":(now-timedelta(seconds=1)).isoformat().replace("+00:00","Z"),
        "admission_until":(now+timedelta(seconds=seconds)).isoformat().replace("+00:00","Z"),
        "stage_until":(now+timedelta(seconds=seconds)).isoformat().replace("+00:00","Z")})
    a.launch=r.launch=changed
    a.clock=r.clock=lambda:datetime.now(timezone.utc)
    return changed


@pytest.mark.parametrize("point",["write","post_inventory"])
def test_real_delayed_sql_cannot_commit_after_cutoff(fixture,point):
    a,r,_,_=fixture
    real_window(fixture,.9)
    from . import fixed_adapter as module
    original=module.inventory
    if point=="write":
        class DelayAfterWrites(ConnectionProxy):
            def execute(self,sql,*args,**kwargs):
                result=self.conn.execute(sql,*args,**kwargs)
                if sql.startswith("INSERT INTO ai.decision_position("):
                    self.conn.execute("SELECT pg_catalog.pg_sleep(2)")
                return result
        a.conn=DelayAfterWrites(a.conn)
    else:
        def delayed(conn,compiled,school,**kwargs):
            result=original(conn,compiled,school,**kwargs)
            if conn is a.conn and result["state"]=="baseline":
                conn.execute("SELECT pg_catalog.pg_sleep(2)")
            return result
        module.inventory=delayed
    try:
        with pytest.raises(CommitUncertain):
            a.execute("baseline",permit(a.launch,"baseline",a.clock()))
    finally:
        module.inventory=original
    assert r.inspect()["rows"]==0
    assert r.recover()["no_op"] is True
    record_evidence("delayed_"+point,{"status":"PASS","committed_rows":0,"completion":"UNCERTAIN_THEN_ZERO_READBACK"})


def test_precommit_clock_check_rolls_back_after_slow_inventory(fixture):
    a,r,_,_=fixture
    from . import fixed_adapter as module
    original=module.inventory
    def drift(conn,compiled,school,**kwargs):
        result=original(conn,compiled,school,**kwargs)
        if conn is a.conn and result["state"]=="baseline":
            # Transaction still open, all writes visible here but uncommitted.
            a.clock=lambda:NOW+timedelta(days=1)
        return result
    with patch.object(module,"inventory",drift):
        with pytest.raises(Refused,match="stage_window"):
            stage(a,"baseline")
    assert r.inspect()["rows"]==0


def test_remote_commit_completed_after_cutoff_is_uncertain(fixture):
    a,r,_,_=fixture
    real=a.conn
    class LateAck(ConnectionProxy):
        @contextmanager
        def transaction(self,**kwargs):
            with self.conn.transaction(**kwargs):
                yield
            if not kwargs.get("force_rollback"):
                a.clock=lambda:NOW+timedelta(days=1)
    a.conn=LateAck(real)
    with pytest.raises(CommitUncertain):
        stage(a,"baseline")
    assert r.inspect()["rows"]==2
    assert r.recover()["rows"]==4


def test_separate_watchdog_survives_controller_process_death(fixture,tmp_path):
    import subprocess,sys,time
    a,r,school,l=fixture
    l=real_window(fixture,6)
    contract=tmp_path/"public-contract.json";contract.write_text(json.dumps(l.public()))
    armed=tmp_path/"armed.json";active=tmp_path/"active.json";receipt=tmp_path/"revoke.json"
    def command(role,ready):
        return [sys.executable,"-m","tools.canon_auth.watchdog_fixture",
            "--role",role,"--contract",str(contract),"--school",str(school),
            "--ready",str(ready),"--receipt",str(receipt)]
    watchdog=subprocess.Popen(command("watchdog",armed),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    controller=None
    def wait_file(path,seconds=3):
        end=time.monotonic()+seconds
        while not path.exists() and time.monotonic()<end:
            time.sleep(.03)
        assert path.exists()
    try:
        wait_file(armed)
        assert json.loads(armed.read_text())["status"]=="WATCHDOG_ARMED"
        controller=subprocess.Popen(command("controller",active),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        assert controller.pid != watchdog.pid
        wait_file(active)
        assert r.inspect()["active"]==4
        controller.kill();controller.wait(timeout=2)
        assert watchdog.poll() is None # independent OS process remains armed
        watchdog.wait(timeout=8)
        assert watchdog.returncode==0
        result=json.loads(receipt.read_text())
        assert result["status"]=="WATCHDOG_REVOKE_CONFIRMED"
        assert result["receipt"]["active"]==0 and result["receipt"]["rows"]==36
        assert r.inspect()["state"]=="emergency"
        record_evidence("independent_watchdog_controller_death",{"status":"PASS",
            "controller_pid":controller.pid,"watchdog_pid":watchdog.pid,
            "controller_killed":True,"watchdog_survived":True,"result":result})
    finally:
        for process in (controller,watchdog):
            if process is not None and process.poll() is None:
                process.kill();process.wait(timeout=2)


def test_precommit_source_failure_proves_rollback(fixture):
    a,r,_,_=fixture
    calls=0
    def source():
        nonlocal calls
        calls+=1
        if calls==2:
            raise Refused("precommit_source_missing")
    a.source_check=source
    with pytest.raises(Refused,match="precommit_source_missing"):
        stage(a,"baseline")
    assert r.inspect()["rows"]==0


def test_server_budget_bounds_behind_client_skew_with_delayed_sql(fixture):
    import time
    a,r,_,_=fixture
    l=real_window(fixture,1.1)
    a.clock=lambda:datetime.now(timezone.utc)-timedelta(seconds=.5)
    captured=[]
    class SkewDelay(ConnectionProxy):
        def execute(self,sql,*args,**kwargs):
            result=self.conn.execute(sql,*args,**kwargs)
            if sql.startswith("INSERT INTO ai.decision_position("):
                captured.append(self.conn.execute("SELECT pg_catalog.current_setting('transaction_timeout')").fetchone()[0])
                self.conn.execute("SELECT pg_catalog.pg_sleep(2)")
            return result
    a.conn=SkewDelay(a.conn)
    begin=time.monotonic()
    with pytest.raises(CommitUncertain):
        a.execute("baseline",permit(l,"baseline",a.clock()))
    assert captured and int(captured[0].removesuffix("ms")) <= 1000
    assert time.monotonic()-begin < 1.6
    assert r.inspect()["rows"]==0
    assert r.recover()["no_op"] is True


def test_client_ahead_cannot_admit_before_server_open(fixture):
    from .launch_contract import Launch
    a,r,_,l=fixture
    now=datetime.now(timezone.utc)
    l=Launch.parse(l.public()|{
        "open_at":(now+timedelta(seconds=2)).isoformat().replace("+00:00","Z"),
        "admission_until":(now+timedelta(seconds=4)).isoformat().replace("+00:00","Z"),
        "stage_until":(now+timedelta(seconds=10)).isoformat().replace("+00:00","Z")})
    a.launch=l;a.clock=lambda:now+timedelta(seconds=3)
    with pytest.raises(Refused,match="server_stage_deadline"):
        a.execute("baseline",permit(l,"baseline",a.clock()))
    assert r.inspect()["rows"]==0


@pytest.mark.parametrize("fault",["delayed_configuration","inherited_positive_timer"])
def test_deadline_arming_latency_and_inherited_timer_cannot_extend_cutoff(fixture,fault):
    a,r,_,_=fixture
    l=real_window(fixture,1.2)
    if fault=="inherited_positive_timer":
        a.conn.execute("SET transaction_timeout='60s'")
    captured=[]
    class ArmDelay(ConnectionProxy):
        def execute(self,sql,*args,**kwargs):
            if fault=="delayed_configuration" and sql.lstrip().startswith("WITH budget AS"):
                self.conn.execute("SELECT pg_catalog.pg_sleep(.7)")
            result=self.conn.execute(sql,*args,**kwargs)
            if sql.startswith("INSERT INTO ai.decision_position("):
                captured.append(self.conn.execute("SHOW transaction_timeout").fetchone()[0])
                self.conn.execute("SELECT pg_catalog.pg_sleep(3)")
            return result
    a.conn=ArmDelay(a.conn)
    with pytest.raises(CommitUncertain):
        a.execute("baseline",permit(l,"baseline",a.clock()))
    assert captured
    if fault=="delayed_configuration":
        assert int(captured[0].removesuffix("ms")) <= 500
    assert r.inspect()["rows"]==0
    assert r.recover()["no_op"] is True


def test_before_fix_delayed_real_sql_committed_after_cutoff_counterfactual(fixture):
    import subprocess,types
    a,r,school,l=fixture
    l=real_window(fixture,1.1)
    text=subprocess.check_output(["git","show",
        "40ee13d2826a7cd697b9dd0d89962cd542cb887c:tools/canon_auth/fixed_adapter.py"],text=True)
    previous=types.ModuleType(__package__+".before_remote_cutoff_fix")
    previous.__package__=__package__
    exec(compile(text,"immutable-before-cutoff-fix","exec"),previous.__dict__)
    class Delayed(ConnectionProxy):
        def execute(self,sql,*args,**kwargs):
            result=self.conn.execute(sql,*args,**kwargs)
            if sql.startswith("INSERT INTO ai.decision_position("):
                self.conn.execute("SELECT pg_catalog.pg_sleep(1.5)")
            return result
    old=previous.FixedAdapter(Delayed(a.conn),school,l,lambda:None,a.clock)
    receipt=old.execute("baseline",permit(l,"baseline",a.clock()))
    # Counterfactual runs ONLY the public old code in the anonymous disposable
    # loopback fixture. It demonstrates the exact bug, then independently cleans
    # owned bindings; no old code is exposed as an operational entrypoint.
    assert receipt["status"]=="STAGE_COMMITTED" and a.clock() >= l.stage_until
    assert r.inspect()["rows"]==2
    assert r.recover()["rows"]==4
    record_evidence("before_fix_counterfactual_40ee",{"status":"BUG_REPRODUCED",
        "late_commit":True,"committed_rows":2,"independent_revoke_rows":4})


@pytest.mark.parametrize("call", ["3H", "3S"])
@pytest.mark.parametrize("kind", ["sibling_relation", "changed_relation"])
def test_full_source_relation_primary_key_refuses_before_revoke(fixture,call,kind):
    a,r,school,l=fixture
    stage(a,"baseline");stage(a,"initial")
    binding=next(b for b in a.compiled["ids"]["bindings"] if b["call"]==call)
    spec=next(s for s in a.compiled["declared_rows"]
        if s["table"]=="public.knowledge_version_source"
        and s["values"]["knowledge_version_id"]==binding["knowledge_version_id"])
    values=declared_values(spec)
    with connect("postgres") as admin:
        with admin.transaction():
            admin.execute("ALTER TABLE public.knowledge_version_source DISABLE TRIGGER ALL")
            if kind=="sibling_relation":
                admin.execute("INSERT INTO public.knowledge_version_source("
                    "knowledge_version_id,source_id,relation_type,source_locator) "
                    "SELECT knowledge_version_id,source_id,'supports',source_locator "
                    "FROM public.knowledge_version_source WHERE knowledge_version_id=%s AND source_id=%s "
                    "AND relation_type='derived_from'",
                    (values["knowledge_version_id"],values["source_id"]))
            else:
                admin.execute("UPDATE public.knowledge_version_source SET relation_type='supports' "
                    "WHERE knowledge_version_id=%s AND source_id=%s AND relation_type='derived_from'",
                    (values["knowledge_version_id"],values["source_id"]))
            admin.execute("ALTER TABLE public.knowledge_version_source ENABLE TRIGGER ALL")
    before=a.conn.execute("SELECT pg_catalog.to_jsonb(t) FROM public.knowledge_version_source t "
        "WHERE knowledge_version_id=%s AND source_id=%s ORDER BY relation_type",
        (values["knowledge_version_id"],values["source_id"])).fetchall()
    events=scalar(a.conn,"SELECT count(*) FROM bidding.ingestion_event")
    activations=a.conn.execute("SELECT runtime_activation_id,status FROM bidding.runtime_activation "
                              "ORDER BY runtime_activation_id").fetchall()
    with pytest.raises(Refused):
        stage(a,"revoke")
    with pytest.raises(RecoveryUnproven):
        r.recover()
    assert a.conn.execute("SELECT pg_catalog.to_jsonb(t) FROM public.knowledge_version_source t "
        "WHERE knowledge_version_id=%s AND source_id=%s ORDER BY relation_type",
        (values["knowledge_version_id"],values["source_id"])).fetchall()==before
    assert scalar(a.conn,"SELECT count(*) FROM bidding.ingestion_event")==events
    assert a.conn.execute("SELECT runtime_activation_id,status FROM bidding.runtime_activation "
                          "ORDER BY runtime_activation_id").fetchall()==activations


def test_before_fix_source_relation_counterfactual_real_inventory(fixture):
    import subprocess,types
    a,r,school,l=fixture
    stage(a,"baseline");stage(a,"initial")
    spec=next(s for s in a.compiled["declared_rows"] if s["table"]=="public.knowledge_version_source")
    with connect("postgres") as admin:
        admin.execute("INSERT INTO public.knowledge_version_source("
            "knowledge_version_id,source_id,relation_type,source_locator) "
            "SELECT knowledge_version_id,source_id,'supports',source_locator "
            "FROM public.knowledge_version_source WHERE knowledge_version_id=%s AND source_id=%s",
            (spec["values"]["knowledge_version_id"],spec["values"]["source_id"]))
    source=subprocess.check_output(["git","show",
        "12fd5acb1fd1448e958b421ecb89031380654758:tools/canon_auth/ownership.py"],text=True)
    old=types.ModuleType("tools.canon_auth.before_source_pk_fix")
    old.__package__="tools.canon_auth"
    exec(compile(source,"immutable_12fd_ownership","exec"),old.__dict__)
    with a.conn.transaction(force_rollback=True):
        previous=old.inventory(a.conn,a.compiled,school)
        assert previous["state"]=="initial" and previous["rows"]==34 and previous["active"]==4
    with a.conn.transaction(force_rollback=True):
        with pytest.raises(Refused,match="foreign_related_row"):
            inventory(a.conn,a.compiled,school)
    assert scalar(a.conn,"SELECT count(*) FROM public.knowledge_version_source")==3
    record_evidence("before_after_full_source_relation_primary_key",
        {"before_sha":"12fd5acb1fd1448e958b421ecb89031380654758",
         "before_accepted_rows":34,"actual_source_link_rows":3,
         "after":"REFUSED_FOREIGN_RELATED_ROW","activation_changes":0})
