"""Network-isolated PostgreSQL 18.6 synthetic protocol proof; NOT production DDL proof.

Two disposable official-image containers. No host mounts, remote DSNs, repository
imports, secrets, cloud clients, artifacts, caches or installed packages.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

IMAGE='postgres@sha256:9e73daeb439141c2b11eea2463f5f1a3b269fd90d897b41cddb7cb440f21aa5d'
VERSION='18.6'
HERE=Path(__file__).resolve().parent
ENV={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8'}

def main():
    started=time.monotonic()
    deadline=started+900
    checks=[]; names=[]
    report=dict(status='FAIL',scope='GENERIC_SYNTHETIC_ONLY',production_schema_tested=False,
                network='none',image=IMAGE,checks=checks)
    def docker(*args,data=None,ok=True,seconds=60):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise RuntimeError('TIME_BUDGET')
        p=subprocess.run(['docker',*args],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         env=ENV,timeout=min(remaining,seconds))
        if ok and p.returncode:raise RuntimeError('DOCKER_COMMAND_FAILED')
        return p
    def sql(name,query,ok=True):
        return docker('exec','-i',name,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1',
            '-v','VERBOSITY=verbose','-h','/tmp','-U','postgres','-d','postgres',
            data=query.encode('utf-8'),ok=ok)
    def value(name,query):return sql(name,query).stdout.decode().strip()
    def expect(name,query,expected,label):
        if value(name,query)!=expected:raise RuntimeError('ASSERT:'+label)
        checks.append(label)
    def denied(name,query,label,state='42501',marker=None):
        p=sql(name,query,ok=False)
        err=p.stderr.decode('utf-8',errors='replace')
        if p.returncode==0 or not re.search(r'\b'+state+r'\b',err) or (marker and marker not in err):
            raise RuntimeError('WRONG_DENIAL:'+label)
        checks.append(label)
    def manifest(name):
        result={}
        for table in ('task','outbox','receipt','event'):
            q="SELECT count(*)::text||':'||encode(sha256(convert_to(coalesce(string_agg(encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex'),'' ORDER BY encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex')),''),'UTF8')),'hex') FROM sim."+table+' t;'
            result[table]=value(name,q)
        for seq in ('event_id_seq','unused_seq'):
            result[seq]=value(name,"SELECT last_value::text||':'||is_called::text FROM sim."+seq)
        result['relation_acl']=value(name,"SELECT string_agg(c.relname||':'||coalesce(c.relacl::text,'NULL')||':'||pg_get_userbyid(c.relowner),',' ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='sim'")
        result['function_acl']=value(name,"SELECT string_agg(p.proname||':'||coalesce(p.proacl::text,'NULL')||':'||pg_get_userbyid(p.proowner)||':'||coalesce(p.proconfig::text,'NULL')||':'||p.prosecdef::text,',' ORDER BY p.proname) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='sim'")
        result['schema_acl']=value(name,"SELECT nspacl::text||':'||pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='sim'")
        result['default_acl']=value(name,"SELECT coalesce(string_agg(pg_get_userbyid(defaclrole)||':'||defaclnamespace::regnamespace::text||':'||defaclobjtype||':'||defaclacl::text,',' ORDER BY defaclobjtype),'') FROM pg_default_acl")
        result['roles']=value(name,"SELECT string_agg(rolname||':'||rolsuper::text||':'||rolcreatedb::text||':'||rolcreaterole::text||':'||rolreplication::text||':'||rolbypassrls::text||':'||rolcanlogin::text,',' ORDER BY rolname) FROM pg_roles WHERE rolname LIKE 'rep_%'")
        result['memberships']=value(name,"SELECT coalesce(string_agg(r.rolname||':'||m.rolname||':'||a.admin_option::text,',' ORDER BY r.rolname,m.rolname),'') FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member WHERE r.rolname LIKE 'rep_%' OR m.rolname LIKE 'rep_%'")
        result['constraints']=value(name,"SELECT string_agg(c.conname||':'||pg_get_constraintdef(c.oid)||':'||c.convalidated::text,',' ORDER BY c.conname) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='sim'")
        result['triggers']=value(name,"SELECT string_agg(pg_get_triggerdef(t.oid)||':'||t.tgenabled,',' ORDER BY t.tgname) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='sim' AND NOT t.tgisinternal")
        return result
    try:
        report['image_id']=docker('image','inspect','--format','{{.Id}}',IMAGE).stdout.decode().strip()
        for suffix in ('source','target'):
            name='pg18-synthetic-'+str(os.getpid())+'-'+suffix
            names.append(name) # Register before launch so uncertain start is cleaned up.
            docker('run','-d','--pull=never','--name',name,'--network','none','--read-only',
                '--user','postgres','--cap-drop','ALL','--security-opt','no-new-privileges',
                '--memory','512m','--cpus','1','--pids-limit','128',
                '--tmpfs','/tmp:rw,nosuid,size=384m,mode=1777',
                '--entrypoint','/bin/sh',IMAGE,'-c',
                "initdb -D /tmp/data -U postgres --auth-local=trust --auth-host=reject --encoding=UTF8 --no-locale >/tmp/init.log 2>&1 && exec postgres -D /tmp/data -c listen_addresses='' -c unix_socket_directories=/tmp -c shared_buffers=32MB -c max_connections=10")
            ready=False
            for _ in range(60):
                if docker('exec',name,'pg_isready','-h','/tmp','-U','postgres',ok=False).returncode==0:
                    ready=True;break
                time.sleep(0.5)
            if not ready:raise RuntimeError('POSTGRES_NOT_READY')
            inspected=json.loads(docker('inspect',name).stdout)[0]
            h=inspected['HostConfig']
            if h['NetworkMode']!='none' or h['Binds'] or h['PortBindings'] or not h['ReadonlyRootfs']:
                raise RuntimeError('ISOLATION_CONTRACT')
            versions={tool:docker('exec',name,tool,'--version').stdout.decode().strip()
                      for tool in ('postgres','psql','pg_dump','pg_restore','initdb','pg_ctl')}
            if not all(re.search(r'\b18\.6\b',v) for v in versions.values()):raise RuntimeError('VERSION_MISMATCH')
            if value(name,'SHOW server_version_num;')!='180006':raise RuntimeError('SERVER_VERSION')
            report.setdefault('versions',{})[suffix]=versions
            sql(name,(HERE/'roles.sql').read_text(encoding='utf-8'))
        source,target=names
        sql(source,(HERE/'fixture.sql').read_text(encoding='utf-8'))
        archive=docker('exec',source,'pg_dump','-h','/tmp','-U','postgres','-d','postgres','-Fc').stdout
        if not archive.startswith(b'PGDMP'):raise RuntimeError('DUMP_FORMAT')
        report['archive_sha256']=hashlib.sha256(archive).hexdigest()
        docker('exec','-i',target,'pg_restore','-h','/tmp','-U','postgres','-d','postgres',
               '--exit-on-error','--single-transaction',data=archive)
        if manifest(source)!=manifest(target):raise RuntimeError('RESTORE_MANIFEST')
        checks.append('restore_counts_checksums_owners_acl_roles_sequences_constraints_triggers')
        expect(target,"SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname='rep_worker';",'f','least_privilege')
        denied(target,"SET ROLE rep_worker; INSERT INTO sim.task VALUES(2,'bad','READY');",'worker_direct_write_denied')
        denied(target,"SET ROLE rep_health; SELECT sim.claim('fixture-dispatch-1');",'health_execute_denied')
        expect(target,"SET ROLE rep_worker; SELECT sim.claim('fixture-dispatch-1');",'HOLD','unknown_dispatch_not_replayed')
        barrier=threading.Barrier(2)
        def claim(_):
            barrier.wait(timeout=10)
            return value(target,"SET ROLE rep_worker; SELECT sim.claim('fixture-dispatch-2');")
        with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(claim,range(2)))
        if sorted(claims)!=['HOLD','SEND_ONCE']:raise RuntimeError('CONCURRENT_CLAIM')
        checks.append('concurrent_claim_only_one_send_intent')
        expect(target,"SET ROLE rep_worker; SELECT sim.claim('fixture-dispatch-2');",'HOLD','retry_after_committed_intent_held')
        expect(target,"SET ROLE rep_callback; SELECT sim.accept_receipt('fixture-dispatch-1','synthetic-hash','OK');",'OK','late_receipt')
        expect(target,"SET ROLE rep_callback; SELECT sim.accept_receipt('fixture-dispatch-1','synthetic-hash','OK');",'OK','duplicate_receipt_idempotent')
        denied(target,"SET ROLE rep_callback; SELECT sim.accept_receipt('fixture-dispatch-1','different','OK');",'wrong_identity_rejected','P0001','RECEIPT_IDENTITY')
        denied(target,"SET ROLE rep_callback; SELECT sim.accept_receipt('fixture-dispatch-1','synthetic-hash','DIFFERENT');",'conflicting_result_rejected','P0001','RECEIPT_CONFLICT')
        denied(target,"SET ROLE rep_callback; SELECT sim.accept_receipt('fixture-dispatch-1','synthetic-hash',NULL);",'null_receipt_rejected','P0001','RECEIPT_NULL')
        denied(target,'DELETE FROM sim.receipt;','receipt_immutable','P0001','IMMUTABLE_RECEIPT')
        expect(target,"INSERT INTO sim.event(task_id,payload) VALUES(1,'{}') RETURNING id;",'101','sequence_nextval_no_collision')
        expect(target,"SELECT nextval('sim.unused_seq');",'50','uncalled_sequence_preserved')
        denied(target,"INSERT INTO sim.outbox VALUES(99,999,'orphan','x','PENDING');",'foreign_key_enforced','23503')
        if manifest(source)==manifest(target):raise RuntimeError('NEW_WRITES_UNDETECTED')
        checks.append('new_writes_detected')
        for name in names:
            sql(name,'REVOKE EXECUTE ON FUNCTION sim.claim(text) FROM rep_worker; REVOKE EXECUTE ON FUNCTION sim.accept_receipt(text,text,text) FROM rep_callback;')
            denied(name,"SET ROLE rep_worker; SELECT sim.claim('fixture-dispatch-1');",'worker_fenced_'+('source' if name==source else 'target'))
            denied(name,"SET ROLE rep_callback; SELECT sim.accept_receipt('fixture-dispatch-1','synthetic-hash','OK');",'callback_fenced_'+('source' if name==source else 'target'))
        # This is known synthetic delta replay, NOT a production reverse-migration implementation.
        sql(source,"SELECT sim.accept_receipt('fixture-dispatch-1','synthetic-hash','OK'); SELECT sim.claim('fixture-dispatch-2'); INSERT INTO sim.event(id,task_id,payload) VALUES(101,1,'{}'); SELECT setval('sim.event_id_seq',101,true); SELECT setval('sim.unused_seq',50,true);")
        if manifest(source)!=manifest(target):raise RuntimeError('ROLLBACK_MANIFEST')
        checks.append('rollback_known_delta_counts_checksums_acl_sequences_equal')
        report['status']='PASS_SYNTHETIC_ONLY'
    except Exception as exc:
        report['error_type']=type(exc).__name__
        if isinstance(exc,RuntimeError):report['gate']=str(exc)
    finally:
        # Independent cleanup budget; remove only names owned by this process.
        report['cleanup']=True
        for name in names:
            try:
                r=subprocess.run(['docker','rm','-f','-v',name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV,timeout=20)
                check=subprocess.run(['docker','ps','-aq','--filter','name=^/'+name+'$'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV,timeout=10)
                if r.returncode or check.returncode or check.stdout.strip():report['cleanup']=False
            except Exception:report['cleanup']=False
        if not report['cleanup']:report['status']='FAIL_CLEANUP'
    report['seconds']=round(time.monotonic()-started,3)
    print(json.dumps(report,sort_keys=True))
    return 0 if report['status']=='PASS_SYNTHETIC_ONLY' else 1

if __name__=='__main__':raise SystemExit(main())
