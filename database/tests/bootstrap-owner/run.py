"""Generic fixtures only. No private DDL, production endpoints or credentials."""
import json,os,queue,subprocess,threading,time
IMAGE='postgres@sha256:9e73daeb439141c2b11eea2463f5f1a3b269fd90d897b41cddb7cb440f21aa5d'
ENV={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8'}
GUARD="""CREATE FUNCTION app.admin_recover() RETURNS text LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,app AS $$
BEGIN
 IF current_setting('fixture.branch_id',true) NOT IN ('fixture_allowed')
 OR current_setting('fixture.branch_id',true) IS NULL
 OR current_user <> 'source_owner' OR current_database() <> 'source'
 OR current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='app.journal'::regclass)) THEN
  RAISE EXCEPTION 'FIXTURE_OWNER_GUARD';
 END IF;
 RETURN current_user;
END $$;"""
def main():
    name='bootstrap-owner-'+str(os.getpid()); checks=[]; b=None
    report={'status':'FAIL','checks':checks,'scope':'GENERIC_FIXTURES_ONLY','network':'none'}
    def docker(*args,data=None,ok=True):
        r=subprocess.run(['docker',*args],input=data,text=True,capture_output=True,env=ENV,timeout=60)
        if ok and r.returncode:raise RuntimeError(r.stderr[:1200])
        return r
    def sql(query,db='postgres',ok=True,tcp=False,user='postgres'):
        return docker('exec','-i',name,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-h','127.0.0.1' if tcp else '/tmp','-U',user,'-d',db,data=query,ok=ok)
    def val(query,db='postgres'):return sql(query,db).stdout.strip()
    def hba(text):
        docker('exec','-i',name,'sh','-c','cat > /tmp/data/pg_hba.conf',data=text)
        assert val('SELECT pg_reload_conf()')=='t'
        assert val('SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL')=='0'
    def open_b():
        p=subprocess.Popen(['docker','exec','-i',name,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-h','/tmp','-U','postgres','-d','target'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=ENV,bufsize=1)
        q=queue.Queue()
        def reader():
            for line in p.stdout:q.put(line.rstrip())
            q.put(None)
        threading.Thread(target=reader,daemon=True).start()
        return p,q
    def bsql(query):
        marker='DONE_'+str(time.monotonic_ns());b[0].stdin.write(query+'\n\\echo '+marker+'\n');b[0].stdin.flush();out=[]
        while True:
            line=b[1].get(timeout=30)
            if line is None:raise RuntimeError('B_EXITED '+str(out))
            if line==marker:return '\n'.join(out)
            out.append(line)
    try:
        docker('run','-d','--pull=never','--name',name,'--network','none','--read-only','--user','postgres','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','512m','--cpus','1','--pids-limit','96','--tmpfs','/tmp:rw,nosuid,size=256m,mode=1777','--entrypoint','/bin/sh',IMAGE,'-c',"initdb -D /tmp/data -U postgres --auth-local=peer --auth-host=trust --locale-provider=builtin --locale=C.UTF-8 >/tmp/init.log 2>&1 && exec postgres -D /tmp/data -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp -c shared_buffers=32MB -c max_connections=10")
        for _ in range(100):
            if docker('exec',name,'pg_isready','-h','/tmp',ok=False).returncode==0:break
            time.sleep(.1)
        assert val("SELECT current_setting('server_version_num')")=='180006'
        ins=json.loads(docker('inspect',name).stdout)[0]
        assert ins['HostConfig']['NetworkMode']=='none' and not ins['HostConfig']['Binds'] and not ins['HostConfig']['PortBindings']
        baseline=docker('exec',name,'cat','/tmp/data/pg_hba.conf').stdout
        sql('CREATE DATABASE target TEMPLATE template0 ALLOW_CONNECTIONS false CONNECTION LIMIT 0;')
        sql('CREATE ROLE runtime_fixture LOGIN NOSUPERUSER; CREATE ROLE source_owner NOLOGIN;')
        assert 'not currently accepting connections' in sql('SELECT 1','target',ok=False).stderr
        checks.append('initial_closed_database_rejects_owner')
        sql('BEGIN; REVOKE ALL ON DATABASE target FROM PUBLIC; ALTER DATABASE target ALLOW_CONNECTIONS true; COMMIT;')
        assert val("SELECT has_database_privilege('runtime_fixture','target','CONNECT')")=='f'
        assert sql('SELECT 1','target',tcp=True).stdout.strip()=='1'
        assert sql('SELECT 1','target',tcp=True,user='runtime_fixture',ok=False).returncode!=0
        checks.append('acl_and_connlimit_block_runtime_but_not_tcp_superuser')
        sql('ALTER DATABASE target ALLOW_CONNECTIONS false;')
        prefix='host target all 0.0.0.0/0 reject\nhost target all ::/0 reject\nlocal target postgres peer\nlocal target all reject\n'
        hba(prefix+baseline)
        for _ in range(50):
            denial=sql('SELECT 1','target',tcp=True,ok=False)
            if 'pg_hba.conf rejects connection' in denial.stderr:break
            time.sleep(.1)
        assert 'pg_hba.conf rejects connection' in denial.stderr
        checks.append('loaded_hba_denies_tcp_superuser_before_open')
        sql('ALTER DATABASE target ALLOW_CONNECTIONS true;')
        b=open_b(); assert bsql("SELECT current_user||':'||current_database();")=='postgres:target'
        assert val("SELECT count(*) FROM pg_stat_activity WHERE datname='target'")=='1'
        sql('ALTER DATABASE target ALLOW_CONNECTIONS false;')
        assert 'not currently accepting connections' in sql('SELECT 1','target',ok=False).stderr
        assert bsql('SELECT 42;')=='42'
        checks.append('existing_peer_session_survives_committed_close_new_sessions_denied')
        # Transaction failure rolls back both shared role and target objects.
        assert bsql("BEGIN; CREATE ROLE rolled_owner NOLOGIN; CREATE SCHEMA rolled; ROLLBACK; SELECT to_regnamespace('rolled') IS NULL;")=='t'
        assert val("SELECT count(*) FROM pg_roles WHERE rolname='rolled_owner'")=='0'
        checks.append('transactional_role_and_schema_rollback')
        bsql("BEGIN; SET LOCAL lock_timeout='3s'; SET LOCAL statement_timeout='30s'; CREATE ROLE fixture_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT; ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC; ALTER DEFAULT PRIVILEGES FOR ROLE fixture_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC; CREATE EXTENSION pgcrypto; CREATE SCHEMA app AUTHORIZATION fixture_owner; CREATE TABLE app.journal(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY); ALTER TABLE app.journal OWNER TO fixture_owner;"+GUARD+" ALTER FUNCTION app.admin_recover() OWNER TO fixture_owner; REVOKE ALL ON SCHEMA public FROM PUBLIC; REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public,app FROM PUBLIC; GRANT USAGE ON SCHEMA public TO fixture_owner; GRANT EXECUTE ON FUNCTION public.digest(text,text),public.digest(bytea,text) TO fixture_owner; COMMIT;")
        assert bsql("SELECT count(*) FROM app.journal;")=='0'
        assert bsql("SELECT is_called FROM app.journal_id_seq;")=='f'
        assert bsql("SELECT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit FROM pg_roles WHERE rolname='fixture_owner';")=='f'
        assert val("SELECT count(*) FROM pg_auth_members WHERE roleid='fixture_owner'::regrole OR member='fixture_owner'::regrole")=='0'
        assert bsql("SELECT has_function_privilege('runtime_fixture','app.admin_recover()','EXECUTE');")=='f'
        assert bsql("SET ROLE runtime_fixture; DO $$ BEGIN PERFORM app.admin_recover(); RAISE EXCEPTION 'RUNTIME_EXECUTE_LEAK'; EXCEPTION WHEN insufficient_privilege THEN NULL; END $$; RESET ROLE; SELECT 'runtime_denied';")=='runtime_denied'
        assert bsql("SET ROLE fixture_owner; SELECT length(public.digest('fixture','sha256')); RESET ROLE;")=='32'
        checks.append('empty_objects_sequences_nologin_owner_digest_and_no_runtime_execute')
        # Actual administrative guard is invoker; database binding must still reject.
        assert bsql("DO $$ BEGIN PERFORM app.admin_recover(); RAISE EXCEPTION 'GUARD_BYPASSED'; EXCEPTION WHEN raise_exception THEN IF SQLERRM <> 'FIXTURE_OWNER_GUARD' THEN RAISE; END IF; END $$; SELECT 'denied';")=='denied'
        assert bsql("SET fixture.branch_id='fixture_allowed'; DO $$ BEGIN PERFORM app.admin_recover(); RAISE EXCEPTION 'GUARD_BYPASSED'; EXCEPTION WHEN raise_exception THEN IF SQLERRM <> 'FIXTURE_OWNER_GUARD' THEN RAISE; END IF; END $$; SELECT 'denied';")=='denied'
        checks.append('mapped_owner_guard_denies_even_with_spoofed_branch_setting')
        # Positive reference proves the synthetic predicate can actually pass.
        sql('CREATE DATABASE source TEMPLATE template0;')
        sql('CREATE SCHEMA app AUTHORIZATION source_owner; CREATE TABLE app.journal(id bigint); ALTER TABLE app.journal OWNER TO source_owner;'+GUARD+'ALTER FUNCTION app.admin_recover() OWNER TO source_owner;',db='source')
        assert val("SET fixture.branch_id='fixture_allowed'; SET ROLE source_owner; SELECT app.admin_recover();",db='source')=='source_owner'
        checks.append('source_owner_database_branch_reference_passes_same_guard')
        assert bsql("CREATE FUNCTION app.definer_identity() RETURNS text LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS 'SELECT current_user::text'; ALTER FUNCTION app.definer_identity() OWNER TO fixture_owner; REVOKE ALL ON FUNCTION app.definer_identity() FROM PUBLIC; SELECT app.definer_identity();")=='fixture_owner'
        checks.append('separate_security_definer_fixture_observes_mapped_owner')
        # Commit response deliberately ignored; surviving B reconciles, no replay.
        bsql('BEGIN; CREATE TABLE app.unknown_commit(id integer); ALTER TABLE app.unknown_commit OWNER TO fixture_owner; COMMIT;')
        assert bsql("SELECT to_regclass('app.unknown_commit') IS NOT NULL;")=='t'
        checks.append('unknown_result_reconciled_in_existing_session_without_replay')
        b[0].stdin.close();assert b[0].wait(timeout=10)==0;b=None
        assert val("SELECT count(*) FROM pg_stat_activity WHERE datname='target'")=='0'
        assert val("SELECT (NOT datallowconn) AND datconnlimit=0 FROM pg_database WHERE datname='target'")=='t'
        hba(baseline)
        assert docker('exec',name,'cat','/tmp/data/pg_hba.conf').stdout==baseline
        assert 'not currently accepting connections' in sql('SELECT 1','target',ok=False).stderr
        checks.append('final_closed_zero_sessions_exact_hba_restore')
        report.update(status='PASS',limitations=['Generic synthetic schema, not private DDL validation','Fixture guard is a renamed predicate model, not the private function','No production access or credentials','No physical host failure injection'])
    finally:
        if b:
            try:b[0].terminate()
            except OSError:pass
        try:
            removal=docker('rm','-f','-v',name,ok=False)
            remaining=docker('ps','-a','--format','{{.Names}}',ok=False)
            report['cleanup']=removal.returncode==0 and remaining.returncode==0 and name not in remaining.stdout.splitlines()
        except Exception:report['cleanup']=False
        if not report['cleanup']:report['status']='FAIL_CLEANUP'
        print(json.dumps(report,sort_keys=True))
        if not report['cleanup']:raise RuntimeError('CLEANUP_NOT_PROVEN')
if __name__=='__main__':main()
