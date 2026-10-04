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
from .package import formal_package, position, envelope, cases, check_schema
DB = "tournament_rehearsal"
ROLE = "tournament_rehearsal_owner"

def scalar(conn, query, params=()):
    return conn.execute(query, params).fetchone()[0]


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

            bindings.append(dict(rule_id=rule,knowledge_version_id=version,rule_key=entry["rule_key"],call=r["call"]))
        event(conn,run,1,"candidates_created",bindings)
    return bindings, run


def event(conn,run,number,action,bindings):
    conn.execute("INSERT INTO bidding.ingestion_event(ingestion_run_id,event_no,role_key,action_key,details) VALUES (%s,%s,'pilot_rehearsal',%s,%s)",
        (run,number,action,Jsonb({"bindings":[{k:str(v) for k,v in b.items()} for b in bindings]})))


@contextmanager
def api_client():
    import secrets
    import app as entrypoint
    from fastapi.testclient import TestClient
    token=secrets.token_urlsafe(32)  # Disposable test credential; no production secret.
    @contextmanager
    def connection():
        with local_connect(as_app=True) as conn:
            yield conn
    with patch.dict(os.environ,{"BRIDGE_API_TOKEN":token,"VERCEL_OIDC_TOKEN":""}), \
         patch.object(teacher,"connect",connection), \
         TestClient(entrypoint.app,headers={"Authorization":"Bearer "+token}) as client:
        yield client


def activate(conn,bindings,school,expiry):
    # ONLY a rehearsal: never reuse this synthetic approval for production.
    with conn.transaction():
        for b in bindings:
            ca=scalar(conn,"INSERT INTO public.canon_activation(knowledge_version_id,scope_key,valid_from,valid_to,status,approval_provenance) VALUES (%s,%s,now(),%s,'active',%s) RETURNING canon_activation_id",
                (b["knowledge_version_id"],teacher.SCOPE,expiry,Jsonb({"test_only":True})))
            ra=scalar(conn,"INSERT INTO bidding.runtime_activation(school_id,rule_id,authority_lane,canon_activation_id,scope_key,valid_from,valid_to,status,activation_provenance) VALUES (%s,%s,'school_canon',%s,%s,now(),%s,'active',%s) RETURNING runtime_activation_id",
                (school,b["rule_id"],ca,teacher.SCOPE,expiry,Jsonb({"test_only":True})))
            b.update(canon_activation_id=ca,runtime_activation_id=ra)


TABLE_BUDGET={"public.source":1,"public.knowledge_item":2,"public.knowledge_version":2,
    "public.knowledge_version_source":2,"bidding.rule":2,"bidding.rule_test":8,
    "bidding.rule_test_run":8,"ai.decision_position":1,"public.canon_activation":4,
    "bidding.runtime_activation":4,"bidding.ingestion_run":1,"bidding.ingestion_event":5}


def rehearsal():
    check_schema()
    with local_connect() as conn:
        assert scalar(conn,"SELECT current_database()")==DB
        school=scalar(conn,"INSERT INTO public.school(stable_name) VALUES (%s) RETURNING school_id",("synthetic-pilot-"+str(uuid4()),))
        baseline={t:scalar(conn,"SELECT count(*) FROM "+t) for t in TABLE_BUDGET}
        outputs=scalar(conn,"SELECT count(*) FROM ai.teacher_output")
        with conn.transaction():
            source=scalar(conn,"INSERT INTO public.source(school_id,source_type,title,canonical_locator) VALUES (%s,'document','SRC-0096 synthetic rehearsal',%s) RETURNING source_id",
                (school,formal_package()["rules"][0]["payload"]["source_rule"]["source_url"]))
            bindings,run=initialize_candidates(conn,school,source)
        try:
            initialize_candidates(conn,school,source)
        except ValueError as exc:
            assert str(exc)=="FIRST_IMPORT_REQUIRES_ABSENT_RULE_KEYS"
        else:
            raise AssertionError("Duplicate import accepted")
        from psycopg.rows import dict_row
        evaluations=[]
        # All 18 semantic cases retained in 8 SQL gate suites, not 18 positions.
        for b in bindings:
            with conn.cursor(row_factory=dict_row) as cur:
                row=cur.execute("SELECT * FROM bidding.rule WHERE rule_id=%s",(b["rule_id"],)).fetchone()
            row["scope_key"]=teacher.SCOPE
            row["runtime_activation_id"]=None
            for kind in ("positive","negative","boundary","hidden_information"):
                fixtures=[]; observed=[]
                for name,case_kind,shape,change,expected in cases(b["call"]):
                    if kind!=case_kind:
                        continue
                    pos=position(school,shape,change)
                    result=teacher.evaluate(pos,b["call"],[row])
                    assert result["status"]==expected,(name,result)
                    fixtures.append({"case":name,"position":{k:str(v) if isinstance(v,type(school)) else v for k,v in pos.items()},"expected":expected})
                    observed.append({"case":name,"observed":result["status"],"expected":expected})
                test=scalar(conn,"INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING rule_test_id",
                    (school,b["rule_id"],kind,kind,Jsonb({"cases":fixtures}),Jsonb({"all_cases_match":True}),teacher.VERSION))
                conn.execute("INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version) VALUES (%s,%s,'pass',%s,%s)",
                    (school,test,Jsonb({"evidence_class":"synthetic_candidate_evaluator","cases":observed}),teacher.VERSION))
                evaluations.extend(observed)
        assert len(evaluations)==18
        assert not any(scalar(conn,"SELECT bidding.rule_passes_activation_gates(%s)",(b["rule_id"],)) for b in bindings)
        for b in bindings:
            conn.execute("UPDATE public.knowledge_version SET authority_class='school_canon',review_status='reviewed' WHERE knowledge_version_id=%s",(b["knowledge_version_id"],))
            conn.execute("UPDATE bidding.rule SET lifecycle_status='validated' WHERE rule_id=%s",(b["rule_id"],))
            assert scalar(conn,"SELECT bidding.rule_passes_activation_gates(%s)",(b["rule_id"],))
        event(conn,run,2,"synthetic_review_only",bindings)
        pid=scalar(conn,"INSERT INTO ai.decision_position(school_id,source_id,stable_key,decision_type,seat,dealer,hand_pbn,auction_json,cards_played_json,system_us,input_status) VALUES (%s,%s,%s,'BIDDING','S','N',%s,%s,'[]',%s,'COMPLETE') RETURNING position_id",
            (school,source,teacher.CANARY_KEY,teacher.CANARY_HAND,Jsonb(["1NT","PASS"]),teacher.PROFILE))
        path=f"/v1/ai/positions/{pid}/teacher-evidence"
        def assess(client,call="3H"):
            response=client.post(path,json=envelope(call))
            assert response.status_code==200,response.text
            return response.json()
        with api_client() as client:
            assert client.post(path,json=envelope("3H"),headers={"Authorization":""}).status_code==401
            assert client.post(path,json=envelope("3H"),headers={"Authorization":"Bearer wrong"}).status_code==403
            assert assess(client)["status"]=="ABSTAIN"
            expiry=scalar(conn,"SELECT now()+interval '24 hours'")
            activate(conn,bindings,school,expiry)
            event(conn,run,3,"first_activation",bindings)
            assert assess(client)["status"]=="SUPPORTED"
            assert assess(client,"3S")["status"]=="CONTRADICTED"
            # Same valid stored position, but not the approved canary -> refusal.
            conn.execute("UPDATE ai.decision_position SET stable_key='other-valid-position' WHERE position_id=%s",(pid,))
            assert assess(client)["reason"]=="PILOT_POSITION_ONLY"
            conn.execute("UPDATE ai.decision_position SET stable_key=%s,hand_pbn='2.234.2345.23456' WHERE position_id=%s",(teacher.CANARY_KEY,pid))
            assert assess(client)["reason"]=="PILOT_POSITION_ONLY"
            conn.execute("UPDATE ai.decision_position SET hand_pbn=%s WHERE position_id=%s",(teacher.CANARY_HAND,pid))
            original=scalar(conn,"SELECT source_locator FROM public.knowledge_version_source WHERE knowledge_version_id=%s",(bindings[0]["knowledge_version_id"],))
            conn.execute("UPDATE public.knowledge_version_source SET source_locator='[]' WHERE knowledge_version_id=%s",(bindings[0]["knowledge_version_id"],))
            assert assess(client)["reason"]=="SOURCE_BINDING_MISMATCH"
            conn.execute("UPDATE public.knowledge_version_source SET source_locator=%s WHERE knowledge_version_id=%s",(Jsonb(original),bindings[0]["knowledge_version_id"]))
            # Actual HTTP -> application-principal SQL -> gates, with loopback UI.
            from .screen import create_server
            from threading import Thread
            from urllib.request import urlopen,Request
            import re
            with create_server(port=0,sender=lambda call:assess(client,call)) as screen:
                thread=Thread(target=screen.serve_forever,daemon=True);thread.start()
                origin=f"http://127.0.0.1:{screen.server_port}"
                html=urlopen(origin).read().decode()
                nonce=re.search(r'nonce="([^"]+)"',html).group(1)
                def click():
                    request=Request(origin+"/assess",data=b'{"call":"3H"}',headers={"Origin":origin,"Content-Type":"application/json","X-Pilot-Nonce":nonce})
                    return json.load(urlopen(request))
                assert click()["status"]=="SUPPORTED"
                with conn.transaction():
                    for b in bindings:
                        conn.execute("UPDATE bidding.runtime_activation SET status='revoked',valid_to=clock_timestamp() WHERE runtime_activation_id=%s",(b["runtime_activation_id"],))
                        conn.execute("UPDATE public.canon_activation SET status='revoked',valid_to=clock_timestamp() WHERE canon_activation_id=%s",(b["canon_activation_id"],))
                    event(conn,run,4,"owned_bindings_revoked",bindings)
                assert click()["status"]=="ABSTAIN"
                activate(conn,bindings,school,expiry)
                event(conn,run,5,"reactivated_same_expiry",bindings)
                assert click()["status"]=="SUPPORTED"
                screen.shutdown();thread.join(timeout=5)
        conn.execute("UPDATE bidding.ingestion_run SET status='completed',finished_at=clock_timestamp() WHERE ingestion_run_id=%s",(run,))
        actual={t:scalar(conn,"SELECT count(*) FROM "+t)-baseline[t] for t in TABLE_BUDGET}
        assert actual==TABLE_BUDGET,(actual,TABLE_BUDGET)
        assert sum(actual.values())==40
        assert scalar(conn,"SELECT count(*) FROM ai.teacher_output")==outputs
        assert scalar(conn,"SELECT count(*) FROM ai.search_run WHERE position_id=%s",(pid,))==0
        assert scalar(conn,"SELECT count(*) FROM ai.final_decision WHERE position_id=%s",(pid,))==0
        return dict(status="PASS",row_budget=actual,total_pilot_rows=40,extra_synthetic_school_fixture=1,semantic_cases=evaluations,
            authenticated_api=True,other_position="ABSTAIN",screen_lifecycle=["SUPPORTED","ABSTAIN","SUPPORTED"],
            auth_missing=401,auth_wrong=403,teacher_output_writes=0,search_runs=0,final_decisions=0,
            ttl_hours=24,reactivation_extends_expiry=False)


if __name__=="__main__":
    print(json.dumps(rehearsal(),ensure_ascii=False,indent=2))
