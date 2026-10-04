"""Disposable PostgreSQL rehearsal. Fixed loopback DB; no configurable DSN.

No production importer or activation CLI is exposed. Official repository
migrations must already have been applied by the isolated CI bootstrap.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from .candidate_package import package, METHOD, SCOPE, MIGRATION_CHECKSUM
from .api_harness import offline_client, request_body, request_path
from .scenarios import CASES, scenarios

DB = "tournament_rehearsal"
ROLE = "tournament_rehearsal_owner"


def scalar(conn, query, params=()):
    return conn.execute(query, params).fetchone()[0]


@contextmanager
def rejects(conn, code):
    try:
        with conn.transaction():
            yield
    except psycopg.Error as exc:
        assert code in str(exc) or exc.sqlstate == code, (code, exc.sqlstate, str(exc))
    else:
        raise AssertionError("Expected SQL refusal: " + code)


def counts(conn):
    return {table: scalar(conn, "SELECT count(*) FROM " + table) for table in (
        "bidding.rule", "public.knowledge_item", "public.knowledge_version",
        "bidding.rule_test", "bidding.rule_test_run", "bidding.ingestion_run",
        "bidding.ingestion_event", "public.canon_activation", "bidding.runtime_activation")}


def event(conn, run, number, action, target=None, details=None):
    conn.execute("""INSERT INTO bidding.ingestion_event
        (ingestion_run_id,event_no,role_key,action_key,target_type,target_id,details)
        VALUES (%s,%s,'isolated_rehearsal',%s,'bidding.rule',%s,%s)""",
        (run, number, action, target, Jsonb(details or {})))


def start_run(conn, school, source, pkg, operation):
    metadata = {"operation":operation,"scope":SCOPE}
    if operation == "import":
        # JSONB changes object order; preserve exact source serialization as text
        # and verify it against every imported JSONB rule before HTTP use.
        metadata["snapshot_text"] = json.dumps([c["source_rule"] for c in pkg["candidates"]],ensure_ascii=False,indent=2)+"\n"
    return scalar(conn, """INSERT INTO bidding.ingestion_run
        (school_id,source_id,source_manifest_key,source_sha256,repository_ref,metadata)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING ingestion_run_id""",
        (school, source, METHOD + ":" + operation, pkg["source_sha256"],
         "test/tournament-teacher-canon-20261004", Jsonb(metadata)))


def finish_run(conn, run):
    conn.execute("UPDATE bidding.ingestion_run SET status='completed',finished_at=clock_timestamp() WHERE ingestion_run_id=%s", (run,))


def import_candidates(conn, school, source, pkg, fail_after=None):
    if pkg != package():
        raise ValueError("CANDIDATE_PACKAGE_MISMATCH")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(20261004, 200)")
        existing = conn.execute("SELECT rule_key,compiled_payload,lifecycle_status FROM bidding.rule WHERE school_id=%s ORDER BY rule_key", (school,)).fetchall()
        if existing:
            expected = sorted((c["rule_key"], c, "candidate") for c in pkg["candidates"])
            if existing != expected:
                raise ValueError("EXISTING_CANDIDATE_BATCH_MISMATCH")
            return "already_imported"
        run = start_run(conn, school, source, pkg, "import")
        for n, candidate in enumerate(pkg["candidates"], 1):
            r = candidate["source_rule"]
            item = scalar(conn, """INSERT INTO public.knowledge_item
                (school_id,stable_key,knowledge_type,title,status) VALUES (%s,%s,'bidding_rule',%s,'candidate')
                RETURNING knowledge_item_id""", (school,r["rule_id"],r["meaning"]))
            version = scalar(conn, """INSERT INTO public.knowledge_version
                (knowledge_item_id,version_no,content,authority_class,review_status,bidding_system_key,
                 agreement_scope,method_version,provenance,status)
                VALUES (%s,1,%s,'research_candidate','unreviewed','SCHOOL_TOURNAMENT_CURRENT_V1',%s,%s,%s,'candidate')
                RETURNING knowledge_version_id""", (item,Jsonb(candidate),Jsonb({"scope_key":SCOPE}),METHOD,
                    Jsonb({"source_sha256":pkg["source_sha256"],"decision_ids":r["decision_ids"],"test_only":True})))
            conn.execute("INSERT INTO public.knowledge_version_source(knowledge_version_id,source_id,source_locator) VALUES (%s,%s,%s)",
                (version,source,Jsonb({"source_url":r["source_url"],"rules_url":r["rules_url"],"sheet_row":r["sheet_row"],"literal_excerpt":r["original_excerpt"]})))
            rule = scalar(conn, """INSERT INTO bidding.rule
                (school_id,knowledge_version_id,rule_key,rule_kind,auction_pattern,hand_constraints,
                 public_context_constraints,action,meaning,forcing_semantics,explanation,
                 condition_schema_version,compiled_payload,lifecycle_status,method_version)
                VALUES (%s,%s,%s,'bid',%s,%s,%s,%s,%s,%s,%s,'tournament-candidate-v1',%s,'candidate',%s)
                RETURNING rule_id""", (school,version,r["rule_id"],Jsonb({"auction":r["auction"]}),
                    Jsonb({"predicate":r["predicate"],"formalization":"pending"}),
                    Jsonb({"scope_key":SCOPE,"test_only":True}),Jsonb({"call":r["call"]}),
                    Jsonb({"text":r["meaning"]}),Jsonb({"status":r["forcing_status"]}),
                    Jsonb({"literal_source":r["original_excerpt"],"teacher_decisions":r["decision_ids"]}),Jsonb(candidate),METHOD))
            event(conn,run,n,"candidate_imported",rule,{"rule_key":r["rule_id"],"runtime_eligible":False})
            if fail_after == n:
                raise RuntimeError("INJECTED_PARTIAL_IMPORT_FAILURE")
        finish_run(conn,run)
    return "imported"


def run_cases_from_database(conn, school, pkg):
    rows = conn.execute("SELECT rule_id,rule_key,compiled_payload FROM bidding.rule WHERE school_id=%s ORDER BY (compiled_payload->>'ordinal')::int",(school,)).fetchall()
    exported = [r[2]["source_rule"] for r in rows]
    assert exported == [c["source_rule"] for c in pkg["candidates"]]
    snapshot_text = scalar(conn,"SELECT metadata->>'snapshot_text' FROM bidding.ingestion_run WHERE school_id=%s AND source_manifest_key=%s",(school,METHOD+":import"))
    assert json.loads(snapshot_text) == exported
    ids = {row[1]:row[0] for row in rows}
    results = []
    # The existing HTTP adapter consumes the DB readback, not the original file.
    import experiments.tournament_teacher.consumer as consumer
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory)/"rules.json"
        snapshot.write_text(snapshot_text,encoding="utf-8")
        with patch.object(consumer,"RULES_PATH",snapshot), offline_client(enabled=True) as client:
            for case in CASES:
                key = "RULE-TOUR-"+case[0]
                for name, request, expected in scenarios(case):
                    response = client.post(request_path(request),json=request_body(request))
                    assert response.status_code == 200
                    answer = response.json()
                    assert answer["status"] == expected, (key,name,answer)
                    kind = ("hidden_information" if name=="hidden_information" else
                            "positive" if name=="positive" else
                            "boundary" if name.startswith("boundary") else "negative")
                    results.append((ids[key],name,kind,request,expected,answer["status"]))
    # Auth and socket sentinels are restored before writing local test evidence.
    with conn.transaction():
        for rule,name,kind,request,expected,actual in results:
            test = scalar(conn,"""INSERT INTO bidding.rule_test
                (school_id,rule_id,test_key,test_type,fixture,expected,method_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING rule_test_id""",
                (school,rule,name,kind,Jsonb(request),Jsonb({"status":expected}),METHOD))
            conn.execute("""INSERT INTO bidding.rule_test_run
                (school_id,rule_test_id,result,result_details,method_version)
                VALUES (%s,%s,'pass',%s,%s)""",(school,test,Jsonb({"expected":expected,"actual":actual,"via":"existing_HTTP_API_DB_readback"}),METHOD))
    return len(results)


def candidate_gates(conn, school, other_school):
    rule,version = conn.execute("SELECT rule_id,knowledge_version_id FROM bidding.rule WHERE school_id=%s LIMIT 1",(school,)).fetchone()
    assert scalar(conn,"SELECT count(*) FROM bidding.rule WHERE school_id=%s AND bidding.rule_passes_activation_gates(rule_id)",(school,)) == 0
    with rejects(conn,"BID_ACTIVATION_GATES_NOT_SATISFIED"):
        conn.execute("""INSERT INTO bidding.runtime_activation(school_id,rule_id,authority_lane,scope_key,status)
            VALUES (%s,%s,'world_external',%s,'active')""",(school,rule,SCOPE))
    with rejects(conn,"BID_RULE_KNOWLEDGE_SCHOOL_MISMATCH"):
        conn.execute("UPDATE bidding.rule SET school_id=%s WHERE rule_id=%s",(other_school,rule))
    with rejects(conn,"BID_APPEND_ONLY"):
        conn.execute("UPDATE bidding.rule_test_run SET result='fail' WHERE school_id=%s",(school,))
    with conn.transaction():
        conn.execute("SET LOCAL ROLE bridge_school_worker")
        assert scalar(conn,"SELECT current_user") == "bridge_school_worker"
        with rejects(conn,"42501"):
            conn.execute("INSERT INTO public.canon_activation(knowledge_version_id,scope_key,valid_from,status) VALUES (%s,%s,now(),'active')",(version,SCOPE))
    assert scalar(conn,"SELECT count(*) FROM bidding.active_school_canon_rule_v WHERE school_id=%s",(school,)) == 0
    assert scalar(conn,"SELECT count(*) FROM public.canon_activation") == 0
    assert scalar(conn,"SELECT count(*) FROM bidding.runtime_activation") == 0


def rollback_candidates(conn, school, source, pkg):
    before = counts(conn)
    with conn.transaction():
        run = start_run(conn,school,source,pkg,"retire_candidates")
        rows = conn.execute("SELECT rule_id,rule_key FROM bidding.rule WHERE school_id=%s ORDER BY rule_key",(school,)).fetchall()
        for n,(rule,key) in enumerate(rows,1):
            conn.execute("UPDATE bidding.rule SET lifecycle_status='retired',updated_at=clock_timestamp() WHERE rule_id=%s",(rule,))
            event(conn,run,n,"candidate_retired",rule,{"rule_key":key,"reason":"rehearsal_rollback"})
        finish_run(conn,run)
    after = counts(conn)
    for table in before:
        assert after[table] == before[table] + (1 if table=="bidding.ingestion_run" else 26 if table=="bidding.ingestion_event" else 0)
    assert scalar(conn,"SELECT count(*) FROM bidding.rule WHERE school_id=%s AND lifecycle_status='retired'",(school,)) == 26
    assert scalar(conn,"SELECT count(*) FROM bidding.active_school_canon_rule_v WHERE school_id=%s",(school,)) == 0


def main():
    pkg = package()
    # Discard all ambient libpq configuration without printing or using values.
    for name in list(os.environ):
        if name.startswith("PG"):
            os.environ.pop(name, None)
    # No environment DSN, password, service file, production identifier or host override.
    with psycopg.connect(host="127.0.0.1",hostaddr="127.0.0.1",port=55432,dbname=DB,user=ROLE,
                         password="",passfile="/dev/null",sslmode="disable",connect_timeout=3,
                         autocommit=True) as conn:
        assert scalar(conn,"SELECT current_database()") == DB
        assert scalar(conn,"SELECT current_user") == ROLE
        assert scalar(conn,"SELECT checksum FROM public.schema_migration WHERE migration_key='0200_bidding_knowledge_v0'") == MIGRATION_CHECKSUM
        assert scalar(conn,"SELECT count(*) FROM bidding.rule") == 0
        assert scalar(conn,"SELECT count(*) FROM bidding.runtime_activation") == 0
        assert scalar(conn,"SELECT count(*) FROM public.canon_activation") == 0
        assert scalar(conn,"SELECT count(*) FROM bidding.video_canon_source_policy") == 0
        school = scalar(conn,"INSERT INTO public.school(stable_name) VALUES ('SYNTHETIC tournament rehearsal') RETURNING school_id")
        other = scalar(conn,"INSERT INTO public.school(stable_name) VALUES ('SYNTHETIC other school') RETURNING school_id")
        source = scalar(conn,"INSERT INTO public.source(school_id,source_type,title,status) VALUES (%s,'synthetic_rehearsal','Public minimal snapshot in disposable test DB','active') RETURNING source_id",(school,))
        before = counts(conn)
        try:
            import_candidates(conn,school,source,pkg,fail_after=10)
        except RuntimeError as exc:
            assert str(exc)=="INJECTED_PARTIAL_IMPORT_FAILURE"
        else:
            raise AssertionError("Fault injection did not run")
        assert counts(conn)==before
        assert import_candidates(conn,school,source,pkg)=="imported"
        committed = counts(conn)
        assert import_candidates(conn,school,source,pkg)=="already_imported"
        assert counts(conn)==committed
        altered = deepcopy(pkg)
        altered["create_activations"] = True
        try:
            import_candidates(conn,school,source,altered)
        except ValueError as exc:
            assert str(exc)=="CANDIDATE_PACKAGE_MISMATCH"
        else:
            raise AssertionError("Altered package accepted")
        cases = run_cases_from_database(conn,school,pkg)
        candidate_gates(conn,school,other)
        rollback_candidates(conn,school,source,pkg)
        from .postgres_control import activation_control
        controls = activation_control(conn,other,source)
        assert scalar(conn,"SELECT count(*) FROM bidding.active_school_canon_rule_v") == 0
        migrations = conn.execute("SELECT migration_key,checksum FROM public.schema_migration ORDER BY migration_key").fetchall()
        print(json.dumps(dict(status="PASS",database="DISPOSABLE_LOOPBACK_ONLY",
            candidate_rules=26,http_cases_from_database=cases,created_candidate_activations=0,
            partial_import_rollback=True,idempotent_import=True,history_preserved=True,
            candidate_gates="DENIED",synthetic_activation_control=controls,
            final_counts=counts(conn),migration_0200_checksum=MIGRATION_CHECKSUM,
            applied_repository_migrations=migrations),indent=2))


if __name__ == "__main__":
    main()
