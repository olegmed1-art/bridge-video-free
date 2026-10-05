"""Public-code-only ACK regression in a disposable, network-none PostgreSQL.

Uses the actual public ACK function, with the public 0345/0362 ACK substitutions,
on deliberately minimal synthetic tables. Not a full private-schema rehearsal.
"""
import concurrent.futures
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[3]
IMAGE='postgres@sha256:9e73daeb439141c2b11eea2463f5f1a3b269fd90d897b41cddb7cb440f21aa5d'
ENV={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8'}
SIGNATURE='autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)'
MIGRATION=ROOT/'database/migrations/0401_autopilot_codex_ack_fingerprint_not_null.sql'

def public_ack():
    original=(ROOT/'database/migrations/0332_autopilot_codex_event_cycle.sql').read_text()
    m=re.search(r'CREATE OR REPLACE FUNCTION autopilot\.accept_role_dispatch_codex_ack\([\s\S]*?\nEND \$\$;',original)
    assert m,'PUBLIC_ACK_NOT_FOUND'
    source=m.group()
    # Exact ACK-only substitutions from public migrations 0345 and 0362.
    old='outbox.target_pr,outbox.github_dispatch_comment_id::integer'
    assert source.count(old)==1
    source=source.replace(old,'outbox.target_pr,outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer')
    start='BEGIN\n    IF p_signature_verified'
    assert source.count(start)==1
    return source.replace(start,'BEGIN\n    -- TARGET_PR_CODEX_CONTEXT_V1: target is primary; retain legacy routes.\n    IF p_signature_verified')

SCHEMA='''
CREATE SCHEMA autopilot;
CREATE TABLE public.schema_migration(migration_key text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now(), checksum text);
INSERT INTO public.schema_migration(migration_key) VALUES('0362_autopilot_target_pr_codex_context');
CREATE TABLE autopilot.task(task_id uuid PRIMARY KEY,status text NOT NULL);
CREATE TABLE autopilot.role_dispatch_outbox(
 dispatch_id uuid PRIMARY KEY,task_id uuid REFERENCES autopilot.task,status text NOT NULL,
 delivery_contract_version integer,repository text,dispatch_epoch bigint,role text,target_pr integer,
 expected_head_sha text,task_fingerprint text,mode text,github_dispatch_comment_id bigint,mailbox_pr integer,
 published_at timestamptz,delivery_deadline_at timestamptz,sent_at timestamptz,delivered_at timestamptz,
 callback_deadline_at timestamptz,codex_command_pr integer,codex_command_comment_id bigint,
 codex_ack_reaction_id bigint,codex_ack_at timestamptz,updated_at timestamptz,last_error_code text);
CREATE TABLE autopilot.role_dispatch_codex_delivery_proof(
 delivery_id text PRIMARY KEY,payload_fingerprint text NOT NULL CHECK(payload_fingerprint ~ '^[0-9a-f]{64}$'),
 dispatch_id uuid NOT NULL UNIQUE REFERENCES autopilot.role_dispatch_outbox,command_pr integer,
 command_comment_id bigint,command_actor_login text,command_actor_id bigint,command_author_association text,
 command_app_slug text,command_app_id bigint,ack_reaction_id bigint,ack_actor_login text,ack_actor_id bigint,
 proof_body jsonb NOT NULL);
CREATE ROLE fixture_owner NOLOGIN;
CREATE ROLE fixture_callback NOLOGIN;
ALTER SCHEMA autopilot OWNER TO fixture_owner;
ALTER TABLE autopilot.task OWNER TO fixture_owner;
ALTER TABLE autopilot.role_dispatch_outbox OWNER TO fixture_owner;
ALTER TABLE autopilot.role_dispatch_codex_delivery_proof OWNER TO fixture_owner;
GRANT USAGE ON SCHEMA autopilot TO fixture_callback;
'''

def lit(v):return 'NULL' if v is None else "'"+str(v).replace("'","''")+"'"

def main():
    name='ack-fingerprint-'+str(os.getpid())
    checks=[];report={'status':'FAIL','scope':'PUBLIC_ACTUAL_ACK_MINIMAL_SYNTHETIC_TABLES','checks':checks,'network':'none'}
    def docker(*args,data=None,ok=True):
        r=subprocess.run(['docker',*args],input=data,text=True,capture_output=True,env=ENV,timeout=90)
        if ok and r.returncode:raise RuntimeError(r.stderr[:1800])
        return r
    def sql(query,ok=True,variables=None):
        args=[]
        for key,val in (variables or {}).items():args.extend(['-v',key+'='+str(val)])
        return docker('exec','-i',name,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-v','VERBOSITY=verbose','-h','/tmp','-U','postgres','-d','postgres',*args,data=query,ok=ok)
    def value(query):return sql(query).stdout.strip()
    def fingerprint():
        return ':'.join(value("SELECT encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,'' ORDER BY to_jsonb(t)::text),''),'UTF8')),'hex') FROM autopilot."+table+' t') for table in ('task','role_dispatch_outbox','role_dispatch_codex_delivery_proof'))
    def call(delivery,fp,body):
        return 'SELECT accepted::text||\':\'||duplicate::text||\':\'||resulting_state FROM '+SIGNATURE.split('(')[0]+'('+','.join([lit(delivery),lit(fp),'true',lit('olegmed1-art/bridge-video-free'),'100',lit('olegmed1-art'),'315099490',lit('OWNER'),lit('chatgpt-codex-connector'),'1144995',lit('chatgpt-codex-connector[bot]'),'199175422',lit(json.dumps(body,sort_keys=True))+'::jsonb'])+');'
    def seed():
        tid,did=str(uuid.uuid4()),str(uuid.uuid4())
        now=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        stamp=now.strftime('%Y-%m-%dT%H:%M:%SZ')
        value("INSERT INTO autopilot.task VALUES("+lit(tid)+",'WAITING_EXTERNAL'); INSERT INTO autopilot.role_dispatch_outbox(dispatch_id,task_id,status,delivery_contract_version,repository,dispatch_epoch,role,target_pr,expected_head_sha,task_fingerprint,mode,github_dispatch_comment_id,mailbox_pr,published_at,delivery_deadline_at) VALUES("+','.join([lit(did),lit(tid),"'PUBLISHED'",'3',lit('olegmed1-art/bridge-video-free'),'1',"'SYNTHETIC'",'100',lit('a'*40),lit('b'*64),"'READ_ONLY'",'101','102',lit(stamp),"now()+interval '1 hour'"])+')')
        return {'dispatch_id':did,'dispatch_epoch':1,'role':'SYNTHETIC','target_pr':100,'dispatch_pr':101,'command_pr':100,'expected_head_sha':'a'*40,'task_fingerprint':'b'*64,'mode':'READ_ONLY','command_comment_id':200,'ack_reaction_id':201,'command_created_at':stamp,'ack_created_at':stamp}
    def reject(query,marker):
        before=fingerprint();r=sql(query,ok=False)
        assert r.returncode and 'P0001' in r.stderr and marker in r.stderr,(marker,r.stderr)
        assert fingerprint()==before,'REJECT_MUTATED_STATE'
    try:
        docker('run','-d','--pull=never','--name',name,'--network','none','--read-only','--user','postgres','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','512m','--cpus','1','--pids-limit','96','--tmpfs','/tmp:rw,nosuid,size=256m,mode=1777','--entrypoint','/bin/sh',IMAGE,'-c',"initdb -D /tmp/data -U postgres --auth-local=trust --auth-host=reject --no-locale >/tmp/init.log 2>&1 && exec postgres -D /tmp/data -c listen_addresses='' -c unix_socket_directories=/tmp -c shared_buffers=32MB -c max_connections=10")
        for _ in range(50):
            if docker('exec',name,'pg_isready','-h','/tmp',ok=False).returncode==0:break
            time.sleep(.2)
        assert value("SELECT current_setting('server_version_num')")=='180006'
        ins=json.loads(docker('inspect',name).stdout)[0]
        assert ins['HostConfig']['NetworkMode']=='none' and not ins['HostConfig']['Binds'] and not ins['HostConfig']['PortBindings']
        sql(SCHEMA+public_ack()+f'\nALTER FUNCTION {SIGNATURE} OWNER TO fixture_owner; REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC; GRANT EXECUTE ON FUNCTION {SIGNATURE} TO fixture_callback;')
        body=seed();delivery='synthetic-before';valid='c'*64
        assert value('SET ROLE fixture_callback;'+call(delivery,valid,body))=='true:false:SENT'
        assert value(call(delivery,None,body))=='false:true:SENT'
        checks.append('unpatched_null_duplicate_reproduced')
        invariant_query=f"SELECT proowner::regrole::text||':'||proacl::text||':'||prosecdef::text||':'||proconfig::text||':'||pg_get_function_result(oid) FROM pg_proc WHERE oid='{SIGNATURE}'::regprocedure"
        before_invariant=value(invariant_query)
        sql(MIGRATION.read_text())
        assert value(invariant_query)==before_invariant
        checks.append('migration_preserves_identity_owner_acl_security_definer_search_path_return_type')
        assert value(call(delivery,valid,body))=='false:true:SENT'
        for fp in (None,'','g'*64,'a'*63,'A'*64):reject(call(delivery,fp,body),'AUTOPILOT_CODEX_ACK_BODY_INVALID')
        reject(call(delivery,'d'*64,body),'AUTOPILOT_CODEX_ACK_CONFLICT')
        checks.append('existing_receipt_valid_replay_null_empty_malformed_conflict')
        fresh=seed()
        for fp in (None,''):reject(call('synthetic-fresh',fp,fresh),'AUTOPILOT_CODEX_ACK_BODY_INVALID')
        assert value('SET ROLE fixture_callback;'+call('synthetic-fresh',valid,fresh))=='true:false:SENT'
        checks.append('new_receipt_null_empty_rejected_normal_ack_preserved')
        # The first committed result is deliberately not used as authority to send.
        unknown=seed();sql(call('synthetic-unknown',valid,unknown))
        count=value('SELECT count(*) FROM autopilot.role_dispatch_codex_delivery_proof')
        assert value(call('synthetic-unknown',valid,unknown))=='false:true:SENT'
        reject(call('synthetic-unknown',None,unknown),'AUTOPILOT_CODEX_ACK_BODY_INVALID')
        assert value('SELECT count(*) FROM autopilot.role_dispatch_codex_delivery_proof')==count
        checks.append('unknown_committed_response_retry_does_not_add_receipt_no_provider_send')
        def race(delivery,body,fps):
            barrier=threading.Barrier(len(fps))
            def invoke(fp):
                barrier.wait()
                return sql('BEGIN;'+call(delivery,fp,body)+'SELECT pg_sleep(0.5); COMMIT;',ok=False)
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(fps)) as pool:return list(pool.map(invoke,fps))
        outcomes=race('synthetic-race',seed(),[valid,valid])
        assert all(r.returncode==0 for r in outcomes)
        assert sorted(r.stdout.strip() for r in outcomes)==['false:true:SENT','true:false:SENT']
        checks.append('concurrent_same_delivery_exactly_one_accept_one_duplicate')
        outcomes=race('synthetic-null-race',seed(),[valid,None])
        assert sum(r.returncode==0 and 'true:false:SENT' in r.stdout for r in outcomes)==1
        assert sum('AUTOPILOT_CODEX_ACK_BODY_INVALID' in r.stderr for r in outcomes)==1
        checks.append('concurrent_valid_and_null_only_valid_accepted')
        outcomes=race('synthetic-conflict-race',seed(),[valid,'d'*64])
        assert sum(r.returncode==0 and 'true:false:SENT' in r.stdout for r in outcomes)==1
        assert sum('AUTOPILOT_CODEX_ACK_CONFLICT' in r.stderr for r in outcomes)==1
        checks.append('concurrent_conflicting_fingerprints_exactly_one_accept')
        before=value("SELECT pg_get_functiondef('"+SIGNATURE+"'::regprocedure)")
        r=sql(MIGRATION.read_text(),ok=False)
        assert r.returncode and 'AUTOPILOT_ACK_FINGERPRINT_SOURCE_DRIFT' in r.stderr
        assert value("SELECT pg_get_functiondef('"+SIGNATURE+"'::regprocedure)")==before
        checks.append('migration_reapply_fails_closed_without_mutation')

        # Exercise the exact guarded reconciliation script in the isolated fixture.
        reconcile=(ROOT/'database/scripts/reconcile_0401_checksum.sql').read_text()
        key='0401_autopilot_codex_ack_fingerprint_not_null'
        stamp=value("SELECT applied_at::text FROM public.schema_migration WHERE migration_key='"+key+"'")
        params={
            'expected_database':'postgres','expected_branch':'fixture-only',
            'expected_owner':'fixture_owner',
            'expected_oid':value("SELECT '"+SIGNATURE+"'::regprocedure::oid"),
            'expected_definition_sha256':value("SELECT encode(sha256(convert_to(pg_get_functiondef('"+SIGNATURE+"'::regprocedure),'UTF8')),'hex')"),
            'expected_acl_sha256':value("SELECT encode(sha256(convert_to(coalesce(proacl::text,'<NULL>'),'UTF8')),'hex') FROM pg_proc WHERE oid='"+SIGNATURE+"'::regprocedure"),
            'expected_applied_at':stamp,'action':'repair'}
        reconcile="SET neon.branch_id='fixture-only'; SET ROLE fixture_owner;\n"+reconcile
        sql("GRANT SELECT, UPDATE ON public.schema_migration TO fixture_owner;")
        catalog_before=value(invariant_query)+value("SELECT pg_get_functiondef('"+SIGNATURE+"'::regprocedure)")
        state_before=fingerprint()
        def registry_snapshot():
            return value("SELECT string_agg(to_jsonb(r)::text,'' ORDER BY migration_key) FROM public.schema_migration r")
        def denied(changes,marker):
            before=registry_snapshot()
            before_catalog=value(invariant_query)+value("SELECT pg_get_functiondef('"+SIGNATURE+"'::regprocedure)")
            bad=dict(params);bad.update(changes)
            result=sql(reconcile,ok=False,variables=bad)
            assert result.returncode and marker in result.stderr,(marker,result.stderr)
            assert registry_snapshot()==before
            assert fingerprint()==state_before
            assert value(invariant_query)+value("SELECT pg_get_functiondef('"+SIGNATURE+"'::regprocedure)")==before_catalog
        for changes in ({'expected_database':'wrong'},{'expected_branch':'wrong'},{'action':'unknown'}):
            denied(changes,'ACK_CHECKSUM_TARGET_DRIFT')
        for changes in ({'expected_oid':'1'},{'expected_definition_sha256':'0'*64},{'expected_acl_sha256':'0'*64}):
            denied(changes,'ACK_CHECKSUM_CATALOG_DRIFT')
        denied({'expected_applied_at':'2000-01-01T00:00:00Z'},'ACK_CHECKSUM_REGISTRY_DRIFT')

        sql("CREATE RULE fixture_registry_side_effect AS ON UPDATE TO public.schema_migration DO ALSO UPDATE public.schema_migration SET checksum=repeat('e',64) WHERE migration_key='0362_autopilot_target_pr_codex_context';")
        denied({},'ACK_CHECKSUM_UNREVIEWED_REGISTRY')
        sql("DROP RULE fixture_registry_side_effect ON public.schema_migration;")
        sql("CREATE TABLE public.fixture_registry_child () INHERITS(public.schema_migration);")
        denied({},'ACK_CHECKSUM_UNREVIEWED_REGISTRY')
        sql("DROP TABLE public.fixture_registry_child;")
        sql("ALTER TABLE public.schema_migration ENABLE ROW LEVEL SECURITY;")
        denied({},'ACK_CHECKSUM_UNREVIEWED_REGISTRY')
        sql("ALTER TABLE public.schema_migration DISABLE ROW LEVEL SECURITY;")
        sql("CREATE FUNCTION public.fixture_registry_trigger() RETURNS trigger LANGUAGE plpgsql AS $ BEGIN RETURN NEW; END $; CREATE TRIGGER fixture_registry_trigger BEFORE UPDATE ON public.schema_migration FOR EACH ROW EXECUTE FUNCTION public.fixture_registry_trigger();")
        denied({},'ACK_CHECKSUM_UNREVIEWED_TRIGGER')
        sql("DROP TRIGGER fixture_registry_trigger ON public.schema_migration; DROP FUNCTION public.fixture_registry_trigger();")
        sql('ALTER FUNCTION '+SIGNATURE+' OWNER TO postgres;')
        denied({},'ACK_CHECKSUM_CATALOG_DRIFT')
        sql('ALTER FUNCTION '+SIGNATURE+' OWNER TO fixture_owner;')
        sql('ALTER FUNCTION '+SIGNATURE+' SET search_path TO pg_catalog;')
        denied({},'ACK_CHECKSUM_CATALOG_DRIFT')
        sql('ALTER FUNCTION '+SIGNATURE+' SET search_path TO pg_catalog, autopilot;')
        sql('ALTER FUNCTION '+SIGNATURE+' STRICT;')
        denied({},'ACK_CHECKSUM_CATALOG_DRIFT')
        sql('ALTER FUNCTION '+SIGNATURE+' CALLED ON NULL INPUT;')
        sql('ALTER FUNCTION '+SIGNATURE+' SECURITY INVOKER;')
        denied({},'ACK_CHECKSUM_CATALOG_DRIFT')
        sql('ALTER FUNCTION '+SIGNATURE+' SECURITY DEFINER;')
        sql('REVOKE EXECUTE ON FUNCTION '+SIGNATURE+' FROM fixture_callback;')
        denied({},'ACK_CHECKSUM_CATALOG_DRIFT')
        sql('GRANT EXECUTE ON FUNCTION '+SIGNATURE+' TO fixture_callback;')
        assert value(invariant_query)+value("SELECT pg_get_functiondef('"+SIGNATURE+"'::regprocedure)")==catalog_before
        checks.append('actual_rule_rls_trigger_owner_search_path_strictness_security_acl_drifts_rejected')

        checks.append('reconciliation_target_oid_definition_acl_timestamp_negative_controls')
        sql(reconcile,variables=params)
        expected=hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
        assert value("SELECT checksum FROM public.schema_migration WHERE migration_key='"+key+"'")==expected
        assert value("SELECT applied_at::text FROM public.schema_migration WHERE migration_key='"+key+"'")==stamp
        denied({},'ACK_CHECKSUM_REGISTRY_DRIFT')
        params['action']='rollback'
        sql(reconcile,variables=params)
        assert value("SELECT checksum IS NULL FROM public.schema_migration WHERE migration_key='"+key+"'")=='t'
        denied({},'ACK_CHECKSUM_REGISTRY_DRIFT')
        sql("UPDATE public.schema_migration SET checksum=repeat('0',64) WHERE migration_key='"+key+"'")
        denied({},'ACK_CHECKSUM_REGISTRY_DRIFT')
        params['action']='repair'
        denied({},'ACK_CHECKSUM_REGISTRY_DRIFT')
        assert fingerprint()==state_before
        assert value(invariant_query)+value("SELECT pg_get_functiondef('"+SIGNATURE+"'::regprocedure)")==catalog_before
        checks.append('checksum_repair_and_inverse_preserve_timestamp_catalog_and_delivery_state')

        report.update(status='PASS',public_ack_sha256=hashlib.sha256(public_ack().encode()).hexdigest(),migration_sha256=hashlib.sha256(MIGRATION.read_bytes()).hexdigest(),limitations=['Minimal synthetic tables, not private DDL','Unknown response is discarded after commit, not a real broken network','No webhook cryptographic validation','No live database access'])
    finally:
        try:
            cleanup=docker('rm','-f','-v',name,ok=False)
            remaining=docker('ps','-a','--format','{{.Names}}',ok=False)
            report['cleanup']=cleanup.returncode==0 and remaining.returncode==0 and name not in remaining.stdout.splitlines()
        except Exception:
            report['cleanup']=False
        if not report['cleanup']:report['status']='FAIL_CLEANUP'
        print(json.dumps(report,sort_keys=True))
        if not report['cleanup']:raise RuntimeError('OWNED_CONTAINER_CLEANUP_NOT_PROVEN')
if __name__=='__main__':main()
