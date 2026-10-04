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
from .ownership import KEYS, inventory
from .resident_preflight import Refused
from .bounded_controller import BoundedController, PATH
from .launch_contract import digest, ORIGIN

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
        l=launch(school)
        a=FixedAdapter(owner,school,l,lambda:None,lambda:NOW)
        r=FixedAdapter(recovery,school,l,lambda:None,lambda:NOW)
        yield a,r,school,l


def stage(a,name):
    return a.execute(name,permit(a.launch,name))


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
    calls=[NOW,NOW,NOW+timedelta(days=1)]
    a.clock=lambda:calls.pop(0) if calls else NOW+timedelta(days=1)
    with pytest.raises(Refused,match="stage_window"):
        stage(a,"baseline")
    assert r.inspect()["rows"]==0


def test_commit_then_source_receipt_loss_is_uncertain_and_recoverable(fixture):
    a,r,_,_=fixture
    calls=0
    def source():
        nonlocal calls
        calls+=1
        if calls==2:
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
    params=tuple(spec["values"][k] for k in KEYS[table])
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
    def execute(self,name,p):
        return self.adapter.execute(name,p)
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
    clock=lambda:NOW
    obs=Observer(l,clock);api=Api(client,obs,clock)
    return BoundedController(l,api,obs,Channel(a,"normal"),Channel(r,"independent-recovery"),
                             clock=clock,sleep=lambda n:None),obs


def test_real_bounded_controller(fixture):
    with api_client() as client:
        ctrl,obs=controller(fixture,client)
        result=ctrl.run()
        assert result["status"]=="BOUNDED_ACCEPTANCE" and result["normal_rows"]==40
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
    a.conn.execute("INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version) VALUES (%s,%s,'fail','{}',%s)",
        (school,spec["values"]["rule_test_id"],spec["values"]["method_version"]))
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
