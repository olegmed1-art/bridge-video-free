"""Native Actions-owned Books connection/schema preflight; never a writer.

Only repository BRIDGE_WORKER_DATABASE_URL, scoped by the workflow to this step.
No Oracle, credential export, fallback, user SQL, private book inputs or grants.
The exact selector fixes the source contract, not the existence of current rights.
"""
from __future__ import annotations
import datetime,hashlib,importlib,json,os,re,stat,sys,time
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from uuid import UUID

PROJECT='misty-poetry-18012774'
BRANCH='br-aged-mud-b1i64914'
ENDPOINT='ep-noisy-pine-b1pe30sf'
HOST='ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech'
DATABASE='neondb'
ROLE='bridge_school_worker_principal'
REPOSITORY='olegmed1-art/bridge-video-free'
OPTIONS='-c statement_timeout=2000 -c lock_timeout=500 -c idle_in_transaction_session_timeout=5000 -c default_transaction_read_only=on -c search_path=pg_catalog'
BEGIN='BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY'
TABLES=tuple('public.'+name for name in ('school','source','asset','source_asset',
 'knowledge_item','knowledge_version','knowledge_version_source','changeset','domain_event',
 'outbox_message','books_authority_object','books_authority_state','books_authority_attempt','books_authority_phase'))
IDENTITY_SQL="""SELECT pg_catalog.current_database(), session_user, current_user,
 pg_catalog.current_setting('transaction_read_only'), pg_catalog.current_setting('transaction_isolation'),
 pg_catalog.current_setting('statement_timeout'), pg_catalog.current_setting('lock_timeout'),
 pg_catalog.current_setting('idle_in_transaction_session_timeout'), pg_catalog.current_setting('search_path'),
 pg_catalog.pg_has_role(current_user,%s,'USAGE'),
 (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user),
 (SELECT rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user),
 pg_catalog.has_schema_privilege(current_user,'public','CREATE')"""
SCHEMA_SQL="""SELECT pg_catalog.count(c.oid), pg_catalog.bool_and(
 c.relkind='r' AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
 AND pg_catalog.has_table_privilege(c.oid,'SELECT'))
 FROM pg_catalog.unnest(%s::text[]) n
 LEFT JOIN pg_catalog.pg_class c ON c.oid=pg_catalog.to_regclass(n)"""
SAFE=frozenset({'BOOKS_SOURCE_MISSING','BOOKS_DSN_POLICY','BOOKS_LIBPQ_ENVIRONMENT_FORBIDDEN',
 'BOOKS_ROUTE_UNATTESTED','BOOKS_EXECUTION_IDENTITY','BOOKS_DRIVER_UNQUALIFIED','BOOKS_DRIVER_IMPORT_FAILED',
 'BOOKS_CONNECTION_TARGET','BOOKS_SESSION_POLICY','BOOKS_SCHEMA_UNAVAILABLE','BOOKS_RUNTIME_FAILURE',
 'BOOKS_READONLY_METADATA_OK','BOOKS_ALREADY_ATTEMPTED','BOOKS_CLEANUP_UNKNOWN'})
class Closed(ValueError):pass
def need(ok,code):
    if not ok:raise Closed(code)

def strict_dsn(raw):
    """Same exact worker URI policy as the retained Books source operator."""
    if type(raw)is not str or not raw or len(raw)>8192 or any(c.isspace()for c in raw):
        raise Closed('BOOKS_DSN_POLICY')
    try:
        parsed=urlsplit(raw);query=parse_qs(parsed.query,keep_blank_values=True,strict_parsing=True)
        valid=(parsed.scheme in ('postgresql','postgres')and parsed.username==ROLE and bool(parsed.password)
            and parsed.hostname==HOST and parsed.port in (None,5432)and parsed.path=='/'+DATABASE
            and not parsed.fragment and query=={'sslmode':['require'],'channel_binding':['require']})
    except (ValueError,TypeError):valid=False
    need(valid,'BOOKS_DSN_POLICY');return raw

def route_attested(raw,now):
    try:
        need(type(raw)is str and len(raw)<=4096,'BOOKS_ROUTE_UNATTESTED')
        def pairs(items):
            value={}
            for k,v in items:
                need(k not in value,'BOOKS_ROUTE_UNATTESTED');value[k]=v
            return value
        value=json.loads(raw,object_pairs_hook=pairs)
        expected={'project_id':PROJECT,'branch_id':BRANCH,'endpoint_id':ENDPOINT,
                  'pooled_host':HOST,'source':'AUTHORIZED_NEON_API'}
        need(type(value)is dict and set(value)==set(expected)|{'observed_utc'},'BOOKS_ROUTE_UNATTESTED')
        need(all(type(value[k])is str and value[k]==v for k,v in expected.items()),'BOOKS_ROUTE_UNATTESTED')
        stamp=value['observed_utc'];need(type(stamp)is str and len(stamp)<=40,'BOOKS_ROUTE_UNATTESTED')
        then=datetime.datetime.fromisoformat(stamp.replace('Z','+00:00'))
        need(then.tzinfo is not None and 0<=(now-then).total_seconds()<=120,'BOOKS_ROUTE_UNATTESTED')
    except Closed:raise
    except Exception:raise Closed('BOOKS_ROUTE_UNATTESTED')from None

def execution_identity(env):
    approved=env.get('BOOKS_APPROVED_COMMIT','');actual=env.get('GITHUB_SHA','')
    request=env.get('BOOKS_REQUEST_ID','')
    try:canonical=str(UUID(request))==request
    except (ValueError,TypeError,AttributeError):canonical=False
    need(env.get('GITHUB_REPOSITORY')==REPOSITORY and env.get('GITHUB_REF')=='refs/heads/main'
         and env.get('GITHUB_EVENT_NAME')=='workflow_dispatch'and env.get('GITHUB_RUN_ATTEMPT')=='1'
         and type(actual)is str and re.fullmatch('[0-9a-f]{40}',actual)is not None
         and approved==actual and canonical,'BOOKS_EXECUTION_IDENTITY')

def connect_books_owned(driver,raw):
    """Concrete Books provider, no source discovery or role substitution."""
    return driver.connect(raw,connect_timeout=5,autocommit=True,options=OPTIONS,
        application_name='books-actions-readonly')

def snapshot(driver,env,now=None,before_driver=None):
    """One connection and two fixed catalog SELECTs; no Books payload rows."""
    connection=None;tx=False;entered=False;selects=0;identity=False;schema=False
    cleanup='NOT_CONNECTED';code='BOOKS_RUNTIME_FAILURE';status='CLOSED'
    try:
        execution_identity(env)
        route_attested(env.get('BOOKS_ROUTE_ATTESTATION',''),now or datetime.datetime.now(datetime.timezone.utc))
        need(not any(k.startswith('PG')for k in env),'BOOKS_LIBPQ_ENVIRONMENT_FORBIDDEN')
        raw=env.get('BRIDGE_WORKER_DATABASE_URL','');need(bool(raw),'BOOKS_SOURCE_MISSING')
        raw=strict_dsn(raw)
        if before_driver is not None:before_driver()
        entered=True
        connection=connect_books_owned(driver,raw)
        need(connection is not None,'BOOKS_DRIVER_UNQUALIFIED')
        info=connection.info
        need((info.host,info.dbname,info.user,info.port)==(HOST,DATABASE,ROLE,5432),'BOOKS_CONNECTION_TARGET')
        need(info.transaction_status==0 and connection.autocommit is True,'BOOKS_SESSION_POLICY')
        tx=True
        with connection.cursor()as cur:
            cur.execute(BEGIN)
            cur.execute(IDENTITY_SQL,('bridge_school_worker',));selects+=1
            rows=cur.fetchmany(2)
        need(rows==[(DATABASE,ROLE,ROLE,'on','repeatable read','2s','500ms','5s','pg_catalog',True,False,False,False)],'BOOKS_SESSION_POLICY')
        identity=True
        with connection.cursor()as cur:
            cur.execute(SCHEMA_SQL,(list(TABLES),));selects+=1
            rows=cur.fetchmany(2)
        need(rows==[(14,True)],'BOOKS_SCHEMA_UNAVAILABLE');schema=True
        code='BOOKS_READONLY_METADATA_OK';status='READONLY_METADATA_COMPLETE_NOT_AUTHORITY'
    except BaseException as exc:
        if type(exc)is Closed and len(exc.args)==1 and type(exc.args[0])is str and exc.args[0]in SAFE:code=exc.args[0]
        else:code='BOOKS_RUNTIME_FAILURE'
        if entered and connection is None:status='UNKNOWN';cleanup='OUTCOME_UNKNOWN'
    finally:
        if connection is not None:
            rollback=True;closed=True
            if tx:
                try:
                    with connection.cursor()as cur:cur.execute('ROLLBACK')
                except BaseException:rollback=False
            try:
                connection.close()
                closed=connection.closed is True
            except BaseException:closed=False
            if not rollback or not closed:status='UNKNOWN';cleanup='OUTCOME_UNKNOWN';code='BOOKS_CLEANUP_UNKNOWN'
            else:cleanup='ROLLBACK_AND_CLOSE_CONFIRMED'if tx else'CLOSE_CONFIRMED_NO_TRANSACTION'
    return {'status':status,'code':code,'driver_entered':entered,'select_count':selects,
        'identity_verified':identity,'schema_verified':schema,'cleanup':cleanup,
        'retry_allowed':False,'write_admitted':False,'Books_authority_attested':False}

def driver_from_target(path):
    """Approved official hash-locked install target; no site/.pth loading."""
    root=Path(path)
    need(root.is_absolute()and root.is_dir()and not root.is_symlink(),'BOOKS_DRIVER_UNQUALIFIED')
    need(not any(k in sys.modules for k in ('psycopg','psycopg_binary','typing_extensions')),'BOOKS_DRIVER_UNQUALIFIED')
    sys.path.insert(0,str(root));os.environ['PSYCOPG_IMPL']='binary'
    try:
        driver=importlib.import_module('psycopg')
        need(Path(driver.__file__).resolve().is_relative_to(root.resolve())and driver.__version__=='3.3.4'
             and driver.pq.__impl__=='binary','BOOKS_DRIVER_UNQUALIFIED')
        return driver
    except Closed:raise
    except BaseException:raise Closed('BOOKS_DRIVER_IMPORT_FAILED')from None

def exclusive(path,value):
    raw=(json.dumps(value,separators=(',',':'))+'\n').encode();need(len(raw)<=4096,'BOOKS_RUNTIME_FAILURE')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb',closefd=False)as out:out.write(raw);out.flush();os.fsync(fd)
    finally:os.close(fd)

def main():
    # Mute native/provider stdout/stderr before it can access the selected input.
    output=os.dup(1);null=os.open(os.devnull,os.O_RDWR)
    os.dup2(null,1);os.dup2(null,2);os.close(null)
    result={'status':'CLOSED','code':'BOOKS_RUNTIME_FAILURE','retry_allowed':False,'write_admitted':False}
    folder=None
    try:
        need(os.name=='posix'and sys.version_info[:2]==(3,12)and sys.flags.isolated and sys.flags.no_site,'BOOKS_EXECUTION_IDENTITY')
        execution_identity(os.environ)
        route_attested(os.environ.get('BOOKS_ROUTE_ATTESTATION',''),datetime.datetime.now(datetime.timezone.utc))
        root=Path(os.environ.get('RUNNER_TEMP',''));need(root.is_absolute()and root.is_dir()and not root.is_symlink(),'BOOKS_EXECUTION_IDENTITY')
        folder=root/('books-readonly-'+os.environ['BOOKS_REQUEST_ID'])
        try:folder.mkdir(mode=0o700)
        except FileExistsError:raise Closed('BOOKS_ALREADY_ATTEMPTED')from None
        exclusive(folder/'intent.json',{'state':'INTENT_OUTCOME_UNKNOWN','retry_allowed':False})
        # No application module import, dotenv, alternate URI or untrusted input code.
        driver=driver_from_target(str(root/'books-driver'))
        result=snapshot(driver,os.environ)
        exclusive(folder/'result.json',result)
    except BaseException as exc:
        code=exc.args[0]if type(exc)is Closed and len(exc.args)==1 and type(exc.args[0])is str and exc.args[0]in SAFE else'BOOKS_RUNTIME_FAILURE'
        result={'status':'UNKNOWN'if folder is not None else'CLOSED','code':code,'retry_allowed':False,'write_admitted':False}
    finally:
        raw=(json.dumps(result,separators=(',',':'))+'\n').encode()
        os.write(output,raw if len(raw)<=4096 else b'{"status":"UNKNOWN","retry_allowed":false}\n');os.close(output)
    return 0 if result.get('status')=='READONLY_METADATA_COMPLETE_NOT_AUTHORITY'else 1
if __name__=='__main__':raise SystemExit(main())
