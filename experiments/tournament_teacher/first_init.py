"""First-binding rehearsal; CLI connects only to the fixed disposable DB.

initialize_candidates requires an already verified source identity. It does not
create sources, authorize approvals, activate anything or expose a production DSN.
"""
from contextlib import contextmanager
import json
import os
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from bridge_school_api import tournament_teacher as teacher
from .formal_package import formal_package
from .postgres_rehearsal import scalar,rejects,DB,ROLE


def local_connect(*, as_app=False):
    for name in list(os.environ):
        if name.startswith("PG"):
            os.environ.pop(name,None)
    conn = psycopg.connect(host="127.0.0.1",hostaddr="127.0.0.1",port=55432,
        dbname=DB,user=ROLE,password="",passfile="/dev/null",sslmode="disable",autocommit=True)
    if as_app:
        conn.execute("SET ROLE bridge_school_app_principal")
        conn.autocommit = False
        conn.row_factory = dict_row
    return conn


def initialize_candidates(conn, school, source):
    """One atomic two-candidate import. No approval, activation or positions."""
    pkg = formal_package()
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(20261004, 201)")
        source_row = conn.execute("SELECT s.canonical_locator,s.status,sc.status FROM public.source s JOIN public.school sc USING(school_id) WHERE s.source_id=%s AND s.school_id=%s FOR SHARE OF s,sc",(source,school)).fetchone()
        expected_url = pkg["rules"][0]["payload"]["source_rule"]["source_url"]
        if source_row != (expected_url,"active","active"):
            raise ValueError("VERIFIED_SOURCE_BINDING_REQUIRED")
        keys = [r["rule_key"] for r in pkg["rules"]]
        if scalar(conn,"SELECT count(*) FROM bidding.rule WHERE school_id=%s AND rule_key=ANY(%s)",(school,keys)):
            raise ValueError("FIRST_IMPORT_REQUIRES_ABSENT_RULE_KEYS")
        if scalar(conn,"SELECT count(*) FROM public.knowledge_item WHERE school_id=%s AND stable_key=ANY(%s)",(school,keys)):
            raise ValueError("FIRST_IMPORT_REQUIRES_ABSENT_KNOWLEDGE_KEYS")
        run = scalar(conn,"""INSERT INTO bidding.ingestion_run(school_id,source_id,source_manifest_key,source_sha256,metadata)
            VALUES (%s,%s,%s,%s,%s) RETURNING ingestion_run_id""",(school,source,teacher.VERSION,
                teacher.digest(pkg),Jsonb({"scope":teacher.SCOPE,"operation":"candidate_import_only","package":pkg})))
        bindings = []
        for n,entry in enumerate(pkg["rules"],1):
            payload=entry["payload"]
            r=payload["source_rule"]
            item=scalar(conn,"INSERT INTO public.knowledge_item(school_id,stable_key,knowledge_type,title,status) VALUES (%s,%s,'bidding_rule',%s,'candidate') RETURNING knowledge_item_id",(school,entry["rule_key"],r["meaning"]))
            version=scalar(conn,"""INSERT INTO public.knowledge_version(knowledge_item_id,version_no,content,authority_class,review_status,bidding_system_key,agreement_scope,method_version,provenance,status)
                VALUES (%s,1,%s,'research_candidate','unreviewed',%s,%s,%s,%s,'candidate') RETURNING knowledge_version_id""",(item,Jsonb(payload),teacher.PROFILE,Jsonb({"scope_key":teacher.SCOPE}),teacher.VERSION,
                    Jsonb({"payload_sha256":entry["payload_sha256"],"decision_ids":r["decision_ids"]})))
            conn.execute("INSERT INTO public.knowledge_version_source(knowledge_version_id,source_id,source_locator) VALUES (%s,%s,%s)",(version,source,Jsonb({"sheet_row":r["sheet_row"],"rules_url":r["rules_url"],"original_excerpt":r["original_excerpt"],"teacher_excerpt":r["teacher_excerpt"]})))
            c=payload["catalog"]
            columns=list(c)
            values=[Jsonb(c[k]) if isinstance(c[k],dict) else c[k] for k in columns]
            # Column names come only from the fixed compiler, never request data.
            sql="INSERT INTO bidding.rule(school_id,knowledge_version_id,rule_key,compiled_payload,lifecycle_status,"+",".join(columns)+") VALUES (%s,%s,%s,%s,'candidate',"+",".join(["%s"]*len(columns))+") RETURNING rule_id"
            rule=scalar(conn,sql,(school,version,entry["rule_key"],Jsonb(payload),*values))
            conn.execute("INSERT INTO bidding.ingestion_event(ingestion_run_id,event_no,role_key,action_key,target_type,target_id,details) VALUES (%s,%s,'first_import','candidate_created','bidding.rule',%s,%s)",(run,n,rule,Jsonb({"rule_key":entry["rule_key"],"payload_sha256":entry["payload_sha256"]})))
            bindings.append(dict(rule_id=rule,knowledge_version_id=version,rule_key=entry["rule_key"],call=r["call"]))
        conn.execute("UPDATE bidding.ingestion_run SET status='completed',finished_at=clock_timestamp() WHERE ingestion_run_id=%s",(run,))
    return bindings


def pbn(shape):
    return ".".join("23456789TJQKA"[:n] or "-" for n in shape)


def cases(call):
    good=(3,1,4,5) if call=="3H" else (1,3,4,5)
    opposite=(1,3,4,5) if call=="3H" else (3,1,4,5)
    yield "positive","positive",good,{},"SUPPORTED"
    yield "minor_orientation","positive",(*good[:2],5,4),{},"SUPPORTED"
    yield "wrong_singleton","negative",opposite,{},"CONTRADICTED"
    yield "singleton_boundary","boundary",(2,2,4,5),{},"CONTRADICTED"
    yield "short_minor","boundary",(*good[:2],3,6),{},"CONTRADICTED"
    yield "hidden_context","hidden_information",good,{"dummy_pbn":"AKQ.JT9.876.5432"},"ABSTAIN"
    yield "wrong_turn","negative",good,{"seat":"E"},"ABSTAIN"
    yield "interference","interference",good,{"auction_json":["1NT","X"]},"ABSTAIN"
    yield "missing_hand","negative",good,{"hand_pbn":None},"ABSTAIN"


def position(school,shape,changes=None):
    return dict(school_id=school,school_status="active",input_status="COMPLETE",decision_type="BIDDING",system_us=teacher.PROFILE,
        dealer="N",seat="S",auction_json=["1NT","PASS"],hand_pbn=pbn(shape),cards_played_json=[],dummy_pbn=None,**{}) | (changes or {})


def envelope(call):
    return dict(teacher_key=teacher.KEY,teacher_version=teacher.VERSION,teacher_system=teacher.PROFILE,
        canon_request=dict(task="assess_call",scope_key=teacher.SCOPE,version=teacher.VERSION,proposed_call=call))


@contextmanager
def real_sql_client():
    import app as entrypoint
    from bridge_school_api.main import require_api_token
    from fastapi.testclient import TestClient
    @contextmanager
    def app_connection():
        conn=local_connect(as_app=True)
        try:
            yield conn
        finally:
            conn.close()
    with patch.dict(os.environ,{"VERCEL_OIDC_TOKEN":"","BRIDGE_API_TOKEN":""}), \
         patch.dict(entrypoint.app.dependency_overrides,{require_api_token:lambda:None}), \
         patch.object(teacher,"connect",app_connection), TestClient(entrypoint.app) as client:
        yield client


def rehearsal():
    with local_connect() as conn:
        assert scalar(conn,"SELECT current_database()")==DB
        school=scalar(conn,"INSERT INTO public.school(stable_name) VALUES ('SYNTHETIC first formal teacher scope') RETURNING school_id")
        source=scalar(conn,"INSERT INTO public.source(school_id,source_type,title,canonical_locator,status) VALUES (%s,'document','Synthetic identity for public source excerpt',%s,'active') RETURNING source_id",(school,formal_package()["rules"][0]["payload"]["source_rule"]["source_url"]))
        baseline_outputs=scalar(conn,"SELECT count(*) FROM ai.teacher_output")
        bindings=initialize_candidates(conn,school,source)
        try:
            initialize_candidates(conn,school,source)
        except ValueError as exc:
            assert str(exc)=="FIRST_IMPORT_REQUIRES_ABSENT_RULE_KEYS"
        else:
            raise AssertionError("Duplicate first import accepted")
        ids=[b["rule_id"] for b in bindings]
        assert scalar(conn,"SELECT count(*) FROM bidding.runtime_activation WHERE rule_id=ANY(%s)",(ids,))==0
        assert scalar(conn,"SELECT count(*) FROM bidding.rule WHERE rule_id=ANY(%s) AND bidding.rule_passes_activation_gates(rule_id)",(ids,))==0
        http_cases=[]
        for b in bindings:
            with conn.cursor(row_factory=dict_row) as cur:
                row=cur.execute("SELECT * FROM bidding.rule WHERE rule_id=%s",(b["rule_id"],)).fetchone()
            row.update(scope_key=teacher.SCOPE,runtime_activation_id=None)
            for name,kind,shape,changes,expected in cases(b["call"]):
                pos=position(school,shape,changes)
                actual=teacher.evaluate(pos,b["call"],[row])
                assert actual["status"]==expected,(name,actual)
                fixture={k:(str(v) if k=="school_id" else v) for k,v in pos.items()}
                test=scalar(conn,"INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING rule_test_id",(school,b["rule_id"],name,kind,Jsonb(fixture),Jsonb({"status":expected}),teacher.VERSION))
                conn.execute("INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version) VALUES (%s,%s,'pass',%s,%s)",(school,test,Jsonb({"observed":actual["status"],"expected":expected,"validation":"candidate evaluator; not active runtime"}),teacher.VERSION))
                posid=scalar(conn,"""INSERT INTO ai.decision_position(school_id,stable_key,decision_type,seat,dealer,hand_pbn,auction_json,cards_played_json,dummy_pbn,system_us,input_status)
                    VALUES (%s,%s,'BIDDING',%s,%s,%s,%s,%s,%s,%s,'COMPLETE') RETURNING position_id""",(school,b["call"]+":"+name,pos["seat"],pos["dealer"],pos["hand_pbn"],Jsonb(pos["auction_json"]),Jsonb(pos["cards_played_json"]),pos["dummy_pbn"],teacher.PROFILE))
                http_cases.append((posid,b["call"],expected,name))
        first=http_cases[0]
        path=lambda pid:f"/v1/ai/positions/{pid}/teacher-evidence"
        with real_sql_client() as client:
            before=client.post(path(first[0]),json=envelope(first[1]))
            assert before.status_code==200 and before.json()["status"]=="ABSTAIN",before.text
        # Passing tests alone never confer approval.
        assert not any(scalar(conn,"SELECT bidding.rule_passes_activation_gates(%s)",(r,)) for r in ids)
        with conn.transaction():
            for b in bindings:
                conn.execute("UPDATE public.knowledge_version SET authority_class='school_canon',review_status='reviewed' WHERE knowledge_version_id=%s",(b["knowledge_version_id"],))
                conn.execute("UPDATE bidding.rule SET lifecycle_status='validated' WHERE rule_id=%s",(b["rule_id"],))
                assert scalar(conn,"SELECT bidding.rule_passes_activation_gates(%s)",(b["rule_id"],)) is True
        with real_sql_client() as client:
            eligible_not_active=client.post(path(first[0]),json=envelope(first[1]))
            assert eligible_not_active.json()["status"]=="ABSTAIN"
        # Rehearsal approval ONLY; production approval is a separate action.
        with conn.transaction():
            for b in bindings:
                ca=scalar(conn,"INSERT INTO public.canon_activation(knowledge_version_id,scope_key,valid_from,status,approval_provenance) VALUES (%s,%s,now(),'active',%s) RETURNING canon_activation_id",(b["knowledge_version_id"],teacher.SCOPE,Jsonb({"test_only":True,"scope":"shape_meaning_only","decision":"TDEC-20261003-002"})))
                ra=scalar(conn,"INSERT INTO bidding.runtime_activation(school_id,rule_id,authority_lane,canon_activation_id,scope_key,status,activation_provenance) VALUES (%s,%s,'school_canon',%s,%s,'active',%s) RETURNING runtime_activation_id",(school,b["rule_id"],ca,teacher.SCOPE,Jsonb({"test_only":True,"version":teacher.VERSION})))
                b.update(canon_activation_id=ca,runtime_activation_id=ra)
        answers=[]
        with real_sql_client() as client:
            for pid,call,expected,name in http_cases:
                response=client.post(path(pid),json=envelope(call))
                assert response.status_code==200 and response.json()["status"]==expected,(name,response.text)
                answer=response.json()
                assert answer["action"] is None and answer["persisted"] is False
                answers.append({"case":call+":"+name,"status":answer["status"],"reason":answer["reason"]})
            unsupported=client.post(path(first[0]),json=envelope("3NT"))
            assert unsupported.json()["status"]=="ABSTAIN"
            stale=envelope(first[1]); stale["canon_request"]["version"]="stale"
            assert client.post(path(first[0]),json=stale).status_code==409
            injected=envelope(first[1]); injected["canon_request"]["partner_hand"]=["SA"]
            assert client.post(path(first[0]),json=injected).status_code==422
            wrong_scope=envelope(first[1]); wrong_scope["canon_request"]["scope_key"]="default"
            assert client.post(path(first[0]),json=wrong_scope).status_code==409
            assert client.post(path(uuid4()),json=envelope(first[1])).status_code==404
            sample=client.post(path(first[0]),json=envelope(first[1])).json()
            # A valid rule digest alone must not disguise a changed source binding.
            conn.execute("UPDATE public.source SET canonical_locator='https://example.invalid/mismatched-source' WHERE source_id=%s",(source,))
            mismatch=client.post(path(first[0]),json=envelope(first[1])).json()
            assert mismatch["status"]=="ABSTAIN" and mismatch["reason"]=="SOURCE_BINDING_MISMATCH"
            conn.execute("UPDATE public.source SET canonical_locator=%s WHERE source_id=%s",(formal_package()["rules"][0]["payload"]["source_rule"]["source_url"],source))
            assert client.post(path(first[0]),json=envelope(first[1])).json()["status"]=="SUPPORTED"
            original_locator=scalar(conn,"SELECT source_locator FROM public.knowledge_version_source WHERE knowledge_version_id=%s AND source_id=%s",(bindings[0]["knowledge_version_id"],source))
            conn.execute("UPDATE public.knowledge_version_source SET source_locator='[]'::jsonb WHERE knowledge_version_id=%s AND source_id=%s",(bindings[0]["knowledge_version_id"],source))
            malformed=client.post(path(first[0]),json=envelope(first[1]))
            assert malformed.status_code==200 and malformed.json()["reason"]=="SOURCE_BINDING_MISMATCH"
            conn.execute("UPDATE public.knowledge_version_source SET source_locator=%s WHERE knowledge_version_id=%s AND source_id=%s",(Jsonb(original_locator),bindings[0]["knowledge_version_id"],source))
        # Rollback owns only these four activation rows, preserving definitions/tests.
        with conn.transaction():
            rollback_run=scalar(conn,"INSERT INTO bidding.ingestion_run(school_id,source_id,source_manifest_key,source_sha256,metadata) VALUES (%s,%s,%s,%s,%s) RETURNING ingestion_run_id",(school,source,teacher.VERSION+":rollback",teacher.digest(formal_package()),Jsonb({"test_only":True,"operation":"revoke_owned_bindings"})))
            for n,b in enumerate(bindings,1):
                conn.execute("UPDATE bidding.runtime_activation SET status='revoked',valid_to=clock_timestamp() WHERE runtime_activation_id=%s",(b["runtime_activation_id"],))
                conn.execute("UPDATE public.canon_activation SET status='revoked',valid_to=clock_timestamp() WHERE canon_activation_id=%s",(b["canon_activation_id"],))
                conn.execute("INSERT INTO bidding.ingestion_event(ingestion_run_id,event_no,role_key,action_key,target_type,target_id,details) VALUES (%s,%s,'rehearsal_only','binding_revoked','bidding.rule',%s,%s)",(rollback_run,n,b["rule_id"],Jsonb({"scope_key":teacher.SCOPE,"runtime_activation_id":str(b["runtime_activation_id"]),"canon_activation_id":str(b["canon_activation_id"])})))
            conn.execute("UPDATE bidding.ingestion_run SET status='completed',finished_at=clock_timestamp() WHERE ingestion_run_id=%s",(rollback_run,))
        with real_sql_client() as client:
            after=client.post(path(first[0]),json=envelope(first[1])).json()
            assert after["status"]=="ABSTAIN" and after["source_bindings"]==[]
        assert scalar(conn,"SELECT count(*) FROM ai.teacher_output")==baseline_outputs
        assert scalar(conn,"SELECT count(*) FROM bidding.rule WHERE rule_id=ANY(%s)",(ids,))==2
        assert scalar(conn,"SELECT count(*) FROM bidding.rule_test_run tr JOIN bidding.rule_test t USING(rule_test_id) WHERE t.rule_id=ANY(%s)",(ids,))==len(http_cases)
        assert scalar(conn,"SELECT count(*) FROM bidding.runtime_activation WHERE rule_id=ANY(%s) AND status='revoked'",(ids,))==2
        for source_binding in sample["source_bindings"]:
            for key in ("rule_id","knowledge_version_id","runtime_activation_id","database_source_id"):
                source_binding[key]="<disposable UUID bound by SQL>"
        sample["position_id"]="<stored position UUID>"
        return dict(status="PASS",scope=teacher.SCOPE,package_sha256=teacher.digest(formal_package()),
            candidate_before=before.json()["status"],eligible_without_activation=eligible_not_active.json()["status"],
            active_http_cases=answers,sample_request=envelope(first[1]),sample_response=sample,
            after_revocation=after["status"],retained_rule_count=2,retained_test_runs=len(http_cases),
            retained_revoked_activation_rows=4,teacher_output_writes=0)


if __name__=="__main__":
    print(json.dumps(rehearsal(),ensure_ascii=False,indent=2))
