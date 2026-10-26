"""Native Actions-owned Books connection/schema preflight; never a writer.

Only repository BRIDGE_WORKER_DATABASE_URL, scoped by the workflow to this step.
No Oracle, credential export, fallback, user SQL, private book inputs or grants.
The exact selector fixes the source contract, not the existence of current rights.
"""
from __future__ import annotations
import datetime,hashlib,importlib,ipaddress,json,os,re,select,socket,ssl,stat,sys,time,types
from pathlib import Path
from urllib.parse import parse_qs,unquote_to_bytes,urlsplit
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

CONNECT_ERROR_CLASSES=frozenset({'timeout','dns','tls','auth','driver','config','other'})

def classify_connect_failure(exc,driver):
    """Fixed fields from trusted types only: never inspect exception data/text/chain.

    A category identifies a typed layer, not a proven underlying cause. libpq
    often supplies a generic OperationalError: that deliberately stays other.
    Optional driver classes missing from the qualified module stay unclassified.
    """
    fallback=('other','UNCLASSIFIED_CONNECT_EXCEPTION',None)
    actual=type(exc)
    if type(actual)is not type:
        return fallback
    ancestry=type.__getattribute__(actual,'__mro__')
    def matches(cls):
        return type(cls)is type and any(base is cls for base in ancestry)
    for cls,category,basis in (
        (TimeoutError,'timeout','TYPED_TIMEOUT'),
        (socket.gaierror,'dns','TYPED_DNS_RESOLUTION'),
        (ssl.SSLError,'tls','TYPED_TLS_LAYER'),
        (ImportError,'driver','CLIENT_IMPORT_ERROR'),
        (TypeError,'driver','CLIENT_API_TYPE_ERROR'),
        (ValueError,'config','CLIENT_VALUE_ERROR'),
    ):
        if matches(cls):
            return category,basis,None
    if type(driver)is not types.ModuleType:
        return fallback
    namespace=vars(driver)
    base_error=namespace.get('Error')
    if (type(base_error)is not type or base_error in (Exception,BaseException)
        or not issubclass(base_error,Exception)
        or type.__getattribute__(base_error,'__module__')not in ('psycopg','psycopg.errors')):
        return fallback
    errors=namespace.get('errors')
    error_namespace=vars(errors)if type(errors)is types.ModuleType else {}
    def trusted_error(source,name):
        cls=source.get(name)
        if (type(cls)is not type or cls is base_error
            or type.__getattribute__(cls,'__name__')!=name
            or type.__getattribute__(cls,'__module__')not in ('psycopg','psycopg.errors')
            or not any(base is base_error for base in type.__getattribute__(cls,'__mro__'))):
            return None
        return cls
    rules=(
        (error_namespace,'ConnectionTimeout','timeout','DRIVER_TYPED_TIMEOUT',None),
        (error_namespace,'InvalidPassword','auth','PG_INVALID_PASSWORD_CLASS','28P01'),
        (error_namespace,'InvalidAuthorizationSpecification','auth','PG_AUTHORIZATION_CLASS','28000'),
        (error_namespace,'InvalidParameterValue','config','PG_PARAMETER_CLASS','22023'),
        (namespace,'ProgrammingError','config','DRIVER_PROGRAMMING_ERROR',None),
        (namespace,'InterfaceError','driver','DRIVER_INTERFACE_ERROR',None),
        (namespace,'NotSupportedError','driver','DRIVER_UNSUPPORTED_ERROR',None),
        (namespace,'OperationalError','other','DRIVER_OPERATIONAL_UNCLASSIFIED',None),
    )
    for source,name,category,basis,sqlstate in rules:
        if matches(trusted_error(source,name)):
            return category,basis,sqlstate
    return fallback


def snapshot(driver,env,now=None,before_driver=None):
    """One connection and two fixed catalog SELECTs; no Books payload rows."""
    connection=None;tx=False;entered=False;selects=0;identity=False;schema=False
    cleanup='NOT_CONNECTED';code='BOOKS_RUNTIME_FAILURE';status='CLOSED'
    connect_diag=(None,None,None);connect_call_returned=False
    try:
        execution_identity(env)
        route_attested(env.get('BOOKS_ROUTE_ATTESTATION',''),now or datetime.datetime.now(datetime.timezone.utc))
        need(not any(k.startswith('PG')for k in env),'BOOKS_LIBPQ_ENVIRONMENT_FORBIDDEN')
        raw=env.get('BRIDGE_WORKER_DATABASE_URL','');need(bool(raw),'BOOKS_SOURCE_MISSING')
        raw=strict_dsn(raw)
        if before_driver is not None:before_driver()
        entered=True
        try:
            connection=connect_books_owned(driver,raw)
            connect_call_returned=True
        except BaseException as exc:
            connect_diag=classify_connect_failure(exc,driver)
            raise
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
        'retry_allowed':False,'write_admitted':False,'Books_authority_attested':False,
        'connect_call_returned':connect_call_returned,'connect_error_class':connect_diag[0],
        'connect_error_basis':connect_diag[1],'connect_error_sqlstate':connect_diag[2]}

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

CONNECT_ONLY_CODES=frozenset({'BOOKS_CONNECT_ONLY_OK','BOOKS_CONNECT_POLL_FAILED',
 'BOOKS_CONNECT_DEADLINE','BOOKS_CONNECT_ONLY_FAILURE','BOOKS_CLIENT_CLEANUP_UNKNOWN'})

def libpq_failure_diagnostic(buffer,secrets):
    """Process bounded, in-process bytes into hints; no text is ever returned.

    The native getter may already have materialized a larger buffer. Reject it;
    never truncate-and-match. Patterns are hints from untrusted text, not causes.
    """
    fallback=('other','UNRECOGNIZED_LIBPQ_BUFFER','unknown',None,None)
    if type(buffer)is not bytes or len(buffer)>8192 or type(secrets)is not tuple:
        return ('other','LIBPQ_BUFFER_NOT_CLASSIFIED','unknown',None,None)
    if any(type(s)is not bytes for s in secrets):
        return ('other','LIBPQ_BUFFER_NOT_CLASSIFIED','unknown',None,None)
    masked=buffer
    for secret in sorted(set(secrets),key=len,reverse=True):
        if secret:masked=masked.replace(secret,b'\x00')
    masked=masked.lower()
    # The prefix and full phrase matter; isolated words and free-form SQLSTATE
    # strings are never interpreted as proof of a cause.
    prefix=rb'(?:^|\n)(?:connection (?:failed|to server [^\n]{0,512} failed):[ \t]*)?'
    rules=(
      ('auth',None,prefix+rb'(?:fatal|error):[ \t]+password authentication failed for user\b'),
      ('auth',None,prefix+rb'(?:fatal|error):[ \t]+(?:role|user) [^\n]{1,256} does not exist\b'),
      ('tls',None,prefix+rb'(?:ssl error:[ \t]+[^\n]{0,128}certificate verify failed|server does not support ssl, but ssl was required)\b'),
      ('tls',None,prefix+rb'(?:ssl error:[ \t]+[^\n]{0,128}(?:handshake failure|protocol version)|channel binding required, but ssl not in use)\b'),
      ('config','CONFIG_UNSUPPORTED_STARTUP_PARAMETER_HINT',prefix+rb'(?:fatal|error):[ \t]+unsupported startup parameter\b'),
      ('config','CONFIG_UNRECOGNIZED_PARAMETER_HINT',prefix+rb'(?:fatal|error):[ \t]+unrecognized configuration parameter\b'),
      ('config','CONFIG_INVALID_PARAMETER_VALUE_HINT',prefix+rb'(?:fatal|error):[ \t]+invalid value for parameter\b'),
      ('config','CONFIG_ENDPOINT_ID_MISSING_HINT',prefix+rb'(?:fatal|error):[ \t]+endpoint id is not specified\b'),
    )
    hits=[(category,detail,match)for category,detail,pattern in rules
          for match in re.finditer(pattern,masked)]
    categories={category for category,_,_ in hits}
    if len(categories)!=1:
        return fallback if not hits else('other','AMBIGUOUS_LIBPQ_HINT','unknown',None,None)
    category=next(iter(categories));detail=None;parameter=None
    if category=='config':
        details={value for _,value,_ in hits}
        detail=next(iter(details))if len(details)==1 else'CONFIG_MULTIPLE_MATCHING_HINTS'
        # Return only names already hardcoded by this connector, never values or
        # unknown identifiers. These remain text-pattern hints, not authority.
        name=rb'(options|statement_timeout|lock_timeout|idle_in_transaction_session_timeout|default_transaction_read_only|search_path)'
        parameter_pattern=rb'^[ \t]*:?[ \t]*(?:"'+name+rb'"|\''+name+rb'\'|'+name+rb')(?=$|[ \t:\r\n])'
        parameters=[]
        for _,_,match in hits:
            token=re.match(parameter_pattern,masked[match.end():])
            parameters.append(next((g.decode('ascii')for g in token.groups()if g is not None),None)if token else None)
        if len(set(parameters))==1 and 'CONFIG_ENDPOINT_ID_MISSING_HINT'not in details:parameter=parameters[0]
        if len(details)!=1:parameter=None
    return category,'LIBPQ_MESSAGE_PATTERN_HINT','hint',detail,parameter


def libpq_failure_hint(buffer,secrets):
    # Compatibility for callers requiring the original three-field contract.
    return libpq_failure_diagnostic(buffer,secrets)[:3]


def connect_only(driver,env,now=None,*,resolve=socket.getaddrinfo,
                 wait=select.select,clock=time.monotonic):
    """One DNS result, one owned native handle, no application SQL or retry."""
    result={'status':'UNKNOWN','code':'BOOKS_CONNECT_ONLY_FAILURE','phase':'PRECHECK',
      'failure_phase':None,'connect_error_class':None,'connect_error_basis':None,
      'connect_error_certainty':None,'connect_error_detail':None,'connect_error_parameter':None,
      'dns_result_selected':False,'native_start_calls':0,
      'connect_poll_succeeded':False,'tls_established':None,'password_auth_used':None,
      'native_finish_attempted':False,'client_cleanup':'NOT_CREATED',
      'server_cleanup':'UNVERIFIED','application_sql_count':0,'retry_allowed':False,
      'write_admitted':False,'Books_authority_attested':False}
    native=None;owned=False;phase='PRECHECK'
    try:
        execution_identity(env)
        route_attested(env.get('BOOKS_ROUTE_ATTESTATION',''),now or datetime.datetime.now(datetime.timezone.utc))
        need(not any(k.startswith('PG')for k in env),'BOOKS_LIBPQ_ENVIRONMENT_FORBIDDEN')
        raw=env.get('BRIDGE_WORKER_DATABASE_URL','');need(bool(raw),'BOOKS_SOURCE_MISSING')
        raw=strict_dsn(raw)
        phase='RESOLVE'
        rows=resolve(HOST,5432,type=socket.SOCK_STREAM,proto=socket.IPPROTO_TCP)
        need(type(rows)is list and 0<len(rows)<=64,'BOOKS_DRIVER_UNQUALIFIED')
        first=rows[0]
        need(type(first)is tuple and len(first)==5 and first[0]in(socket.AF_INET,socket.AF_INET6)
             and first[1]==socket.SOCK_STREAM and first[2]==socket.IPPROTO_TCP
             and type(first[4])is tuple and len(first[4])>=2
             and type(first[4][0])is str and type(first[4][1])is int and first[4][1]==5432,'BOOKS_DRIVER_UNQUALIFIED')
        address=ipaddress.ip_address(first[4][0])
        need(address.is_global and not address.is_multicast
             and address.version==(4 if first[0]==socket.AF_INET else 6),'BOOKS_DRIVER_UNQUALIFIED')
        result['dns_result_selected']=True
        phase='BUILD_PARAMETERS'
        # Preserve the original host for SNI/auth semantics. One hostaddr prevents
        # the high-level connect() loop over multiple resolved addresses.
        # This zero-SQL mode sends no options-based GUC bundle through the pooler.
        # Query modes require their own transaction policy; never reuse this as one.
        info=driver.conninfo.make_conninfo(raw,host=HOST,hostaddr=str(address),port=5432,
             connect_timeout=5,sslmode='require',channel_binding='require',
             gssencmode='disable',options='',
             application_name='books-connect-diag-'+env['BOOKS_REQUEST_ID'][:8])
        need(type(info)is str and len(info)<=32768,'BOOKS_DRIVER_UNQUALIFIED')
        encoded=urlsplit(raw).password.encode('utf-8')
        decoded=unquote_to_bytes(urlsplit(raw).password)
        scrub=(raw.encode('utf-8'),info.encode('utf-8'),encoded,decoded,
               decoded.replace(b'\\',b'\\\\').replace(b"'",b"\\'"))
        deadline=clock()+5.0
        phase='START';result['native_start_calls']=1
        native=driver.pq.PGconn.connect_start(info.encode('utf-8'))
        need(type(native)is driver.pq.PGconn,'BOOKS_DRIVER_UNQUALIFIED');owned=True
        phase='POLL';polls=0
        while True:
            remaining=deadline-clock()
            if remaining<=0:
                result.update(code='BOOKS_CONNECT_DEADLINE',connect_error_class='timeout',
                  connect_error_basis='LOCAL_MONOTONIC_DEADLINE',connect_error_certainty='stage')
                result['failure_phase']=phase;break
            polls+=1;need(polls<=1000,'BOOKS_DRIVER_UNQUALIFIED')
            state=native.connect_poll()
            if clock()>=deadline:
                result.update(code='BOOKS_CONNECT_DEADLINE',connect_error_class='timeout',
                  connect_error_basis='LOCAL_MONOTONIC_DEADLINE',connect_error_certainty='stage')
                result['failure_phase']=phase;break
            if state==driver.pq.PollingStatus.OK:
                need(native.status==driver.pq.ConnStatus.OK,'BOOKS_SESSION_POLICY')
                result['connect_poll_succeeded']=True
                result['tls_established']=native.ssl_in_use is True
                result['password_auth_used']=native.used_password is True
                if not result['tls_established']or not result['password_auth_used']:
                    result.update(code='BOOKS_SESSION_POLICY',connect_error_class='tls'if not result['tls_established']else'auth',
                      connect_error_basis='TLS_NOT_ACTIVE'if not result['tls_established']else'PASSWORD_USE_NOT_OBSERVED',
                      connect_error_certainty='stage',failure_phase=phase)
                    break
                result.update(status='CONNECT_ONLY_CLIENT_CLOSED',code='BOOKS_CONNECT_ONLY_OK')
                break
            if state==driver.pq.PollingStatus.FAILED:
                category,basis,certainty,detail,parameter=libpq_failure_diagnostic(native.error_message,scrub)
                result.update(code='BOOKS_CONNECT_POLL_FAILED',connect_error_class=category,
                              connect_error_basis=basis,connect_error_certainty=certainty,
                              connect_error_detail=detail,connect_error_parameter=parameter)
                result['failure_phase']=phase;break
            need(state in(driver.pq.PollingStatus.READING,driver.pq.PollingStatus.WRITING),'BOOKS_DRIVER_UNQUALIFIED')
            fd=native.socket;need(type(fd)is int and fd>=0,'BOOKS_DRIVER_UNQUALIFIED')
            remaining=deadline-clock()
            if remaining>0:
                read=[fd]if state==driver.pq.PollingStatus.READING else[]
                write=[fd]if state==driver.pq.PollingStatus.WRITING else[]
                wait(read,write,[],min(0.25,remaining))
    except BaseException as exc:
        category,basis,_=classify_connect_failure(exc,driver)
        result.update(connect_error_class=category,connect_error_basis=basis,
                      connect_error_certainty='typed'if category!='other'else'unknown')
        if type(exc)is Closed and len(exc.args)==1 and type(exc.args[0])is str and exc.args[0]in SAFE:
            result['code']=exc.args[0]
        result['failure_phase']=phase
    finally:
        if owned:
            result['native_finish_attempted']=True
            try:
                native.finish()
                closed=native.pgconn_ptr is None
            except BaseException:closed=False
            result['client_cleanup']='FINISHED_POINTER_NONE'if closed else'UNKNOWN'
            if not closed:result.update(status='UNKNOWN',code='BOOKS_CLIENT_CLEANUP_UNKNOWN')
        elif result['native_start_calls']:
            result.update(status='UNKNOWN',code='BOOKS_CLIENT_CLEANUP_UNKNOWN',client_cleanup='UNKNOWN')
        result['phase']='FINISHED'if result['client_cleanup']=='FINISHED_POINTER_NONE'else phase
    need(len(json.dumps(result,separators=(',',':')).encode())<=1024,'BOOKS_RUNTIME_FAILURE')
    return result



READ_SQL_CODES=frozenset({'BOOKS_SQL_FAILURE','BOOKS_SQL_PROTOCOL','BOOKS_SQL_DEADLINE',
 'BOOKS_RESULT_BOUNDS','BOOKS_ROLLBACK_UNKNOWN','BOOKS_SCHEMA_NAMES_OK','BOOKS_CLEANUP_DEADLINE'})
READ_LOCAL_SQL=(
 b"SET LOCAL statement_timeout = '2000ms'",
 b"SET LOCAL lock_timeout = '500ms'",
 b"SET LOCAL idle_in_transaction_session_timeout = '5000ms'",
 b"SET LOCAL default_transaction_read_only = on",
 b"SET LOCAL search_path = pg_catalog",
)
READ_IDENTITY_SQL=b"""SELECT
 (pg_catalog.current_database()=$1::pg_catalog.name) AS c01,
 (session_user=$2::pg_catalog.name) AS c02, (current_user=$3::pg_catalog.name) AS c03,
 (pg_catalog.current_setting('transaction_read_only')='on') AS c04,
 (pg_catalog.current_setting('transaction_isolation')='repeatable read') AS c05,
 (pg_catalog.current_setting('statement_timeout')='2s') AS c06,
 (pg_catalog.current_setting('lock_timeout')='500ms') AS c07,
 (pg_catalog.current_setting('idle_in_transaction_session_timeout')='5s') AS c08,
 (pg_catalog.current_setting('search_path')='pg_catalog') AS c09,
 (pg_catalog.current_setting('default_transaction_read_only')='on') AS c10,
 pg_catalog.pg_has_role(current_user,$4::pg_catalog.name,'USAGE') AS c11,
 NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user) AS c12,
 NOT (SELECT rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user) AS c13,
 NOT pg_catalog.has_schema_privilege(current_user,'public','CREATE') AS c14"""
# Exact fourteen public names, supplied as fixed typed parameters; no relation contents.
READ_SCHEMA_SQL=('SELECT '+', '.join('pg_catalog.to_regclass($'+str(i)+'::text) IS NOT NULL' for i in range(1,15))).encode('ascii')
READ_PREFIX=(BEGIN.encode('ascii'),)+READ_LOCAL_SQL+(READ_IDENTITY_SQL,READ_SCHEMA_SQL)
READ_ROLLBACK=b'ROLLBACK'
READ_ID_PARAMS=(DATABASE.encode('ascii'),ROLE.encode('ascii'),ROLE.encode('ascii'),b'bridge_school_worker')
READ_TABLE_PARAMS=tuple(name.encode('ascii')for name in TABLES)


def _books_exchange(native,driver,command,state,deadline,wait,clock):
    """Only this fixed nine-statement protocol; no cursor/implicit cancellation."""
    need(state['protocol_ready']is True,'BOOKS_SQL_PROTOCOL')
    need(native.status==driver.pq.ConnStatus.OK and native.nonblocking==1,'BOOKS_SQL_PROTOCOL')
    need(clock()<deadline,'BOOKS_SQL_DEADLINE')
    if command==READ_ROLLBACK:
        need(state['tx_may_exist']and not state['rollback_attempted'],'BOOKS_SQL_PROTOCOL')
        state['rollback_attempted']=True
    else:
        index=state['sql_statement_attempts']
        need(not state['rollback_attempted']and index<len(READ_PREFIX)and command==READ_PREFIX[index],'BOOKS_SQL_PROTOCOL')
    need(state['sql_statement_attempts']<9,'BOOKS_SQL_PROTOCOL')
    params=READ_ID_PARAMS if command==READ_IDENTITY_SQL else READ_TABLE_PARAMS if command==READ_SCHEMA_SQL else()
    types_=(25,25,25,25)if command==READ_IDENTITY_SQL else(25,)*14 if command==READ_SCHEMA_SQL else()
    if command in(READ_IDENTITY_SQL,READ_SCHEMA_SQL):
        need(state['select_count']<2,'BOOKS_SQL_PROTOCOL');state['select_count']+=1
    if command==READ_PREFIX[0]:state['tx_may_exist']=True
    state['sql_statement_attempts']+=1
    state['application_sql_count']+=1
    state['protocol_ready']=False
    results=[];spins=0
    def tick():
        nonlocal spins
        spins+=1;need(spins<=2000 and clock()<deadline,'BOOKS_SQL_DEADLINE')
    def ready(read,write):
        tick();fd=native.socket;need(type(fd)is int and fd>=0,'BOOKS_SQL_PROTOCOL')
        value=wait([fd]if read else[],[fd]if write else[],[],min(0.1,max(0.0,deadline-clock())))
        tick();return value
    try:
        native.send_query_params(command,list(params),param_types=list(types_),param_formats=[0]*len(params),result_format=1)
        tick()
        while True:
            flushed=native.flush();tick();need(flushed in(0,1),'BOOKS_SQL_PROTOCOL')
            if flushed==0:break
            readable,_,_=ready(True,True)
            if readable:native.consume_input();tick()
        while True:
            tick();native.consume_input();tick()
            busy=native.is_busy();tick();need(busy in(0,1),'BOOKS_SQL_PROTOCOL')
            if busy:
                ready(True,False);continue
            value=native.get_result()
            if value is None:
                state['protocol_ready']=True;tick();break
            if type(value)is not driver.pq.PGresult:
                raise Closed('BOOKS_SQL_PROTOCOL')
            results.append(value)
            tick();need(len(results)<=2,'BOOKS_SQL_PROTOCOL')
            # COPY cannot be drained with this closed SELECT/control protocol.
            need(value.status not in(driver.pq.ExecStatus.COPY_IN,driver.pq.ExecStatus.COPY_OUT,driver.pq.ExecStatus.COPY_BOTH),'BOOKS_SQL_PROTOCOL')
        need(len(results)==1,'BOOKS_SQL_PROTOCOL');result=results[0]
        if command not in(READ_IDENTITY_SQL,READ_SCHEMA_SQL):
            need(result.status==driver.pq.ExecStatus.COMMAND_OK and result.nfields==0 and result.ntuples==0,'BOOKS_SQL_FAILURE')
            tag=b'BEGIN'if command==READ_PREFIX[0]else b'ROLLBACK'if command==READ_ROLLBACK else b'SET'
            need(result.command_status==tag,'BOOKS_SQL_PROTOCOL')
            tick()
            return None
        expected=(16,)*14  # identity and fixed relation-resolution booleans only
        need(result.status==driver.pq.ExecStatus.TUPLES_OK,'BOOKS_SQL_FAILURE')
        need(result.command_status==b'SELECT 1'and result.ntuples==1 and result.nfields==len(expected),'BOOKS_RESULT_BOUNDS')
        decoded=[];byte_count=0
        for i,oid in enumerate(expected):
            need(result.ftype(i)==oid and result.fformat(i)==1,'BOOKS_RESULT_BOUNDS')
            raw=result.get_value(0,i);tick()
            need(type(raw)is bytes and len(raw)==(1 if oid==16 else 8),'BOOKS_RESULT_BOUNDS')
            byte_count+=len(raw)
            if oid==16:
                need(raw in(b'\x00',b'\x01'),'BOOKS_RESULT_BOUNDS');decoded.append(raw==b'\x01')
            else:
                value=int.from_bytes(raw,'big',signed=True)
                need(0<=value<=len(TABLES),'BOOKS_RESULT_BOUNDS');decoded.append(value)
        need(state['result_payload_bytes']+byte_count<=64,'BOOKS_RESULT_BOUNDS')
        state['result_payload_bytes']+=byte_count;state['result_rows']+=1
        need(state['result_rows']<=2,'BOOKS_RESULT_BOUNDS')
        return tuple(decoded)
    finally:
        cleared=True
        for value in results:
            try:
                value.clear()
                if value.pgresult_ptr is not None:cleared=False
            except BaseException:cleared=False
        need(cleared,'BOOKS_RESULT_BOUNDS')
        tick()


def readonly_snapshot(driver,env,now=None,*,resolve=socket.getaddrinfo,
                 wait=select.select,clock=time.monotonic):
    """One owned native handle, fixed read-only metadata SQL, no payload/retry."""
    result={'status':'UNKNOWN','code':'BOOKS_CONNECT_ONLY_FAILURE','phase':'PRECHECK',
      'failure_phase':None,'connect_error_class':None,'connect_error_basis':None,
      'connect_error_certainty':None,'connect_error_detail':None,'connect_error_parameter':None,
      'dns_result_selected':False,'native_start_calls':0,
      'connect_poll_succeeded':False,'tls_established':None,'password_auth_used':None,
      'native_finish_attempted':False,'client_cleanup':'NOT_CREATED',
      'server_cleanup':'UNVERIFIED','application_sql_count':0,'retry_allowed':False,
      'write_admitted':False,'Books_authority_attested':False}
    result.update(sql_statement_attempts=0,select_count=0,tx_may_exist=False,tx_started=False,
      protocol_ready=False,rollback_attempted=False,rollback_verified=False,
      identity_verified=False,schema_verified=False,result_rows=0,result_payload_bytes=0,
      books_payload_queries=0,cleanup_within_budget=None,
      schema_relation_count=None,schema_policy_all=None,schema_transaction_intrans=None,
      schema_unresolved_names=None,schema_resolution_complete=False)
    started=clock();work_deadline=started+25.0;cleanup_deadline=started+28.0
    native=None;owned=False;phase='PRECHECK'
    try:
        execution_identity(env)
        route_attested(env.get('BOOKS_ROUTE_ATTESTATION',''),now or datetime.datetime.now(datetime.timezone.utc))
        need(not any(k.startswith('PG')for k in env),'BOOKS_LIBPQ_ENVIRONMENT_FORBIDDEN')
        raw=env.get('BRIDGE_WORKER_DATABASE_URL','');need(bool(raw),'BOOKS_SOURCE_MISSING')
        raw=strict_dsn(raw)
        phase='RESOLVE'
        rows=resolve(HOST,5432,type=socket.SOCK_STREAM,proto=socket.IPPROTO_TCP)
        need(type(rows)is list and 0<len(rows)<=64,'BOOKS_DRIVER_UNQUALIFIED')
        first=rows[0]
        need(type(first)is tuple and len(first)==5 and first[0]in(socket.AF_INET,socket.AF_INET6)
             and first[1]==socket.SOCK_STREAM and first[2]==socket.IPPROTO_TCP
             and type(first[4])is tuple and len(first[4])>=2
             and type(first[4][0])is str and type(first[4][1])is int and first[4][1]==5432,'BOOKS_DRIVER_UNQUALIFIED')
        address=ipaddress.ip_address(first[4][0])
        need(address.is_global and not address.is_multicast
             and address.version==(4 if first[0]==socket.AF_INET else 6),'BOOKS_DRIVER_UNQUALIFIED')
        result['dns_result_selected']=True
        phase='BUILD_PARAMETERS'
        # Preserve the original host for SNI/auth semantics. One hostaddr prevents
        # the high-level connect() loop over multiple resolved addresses.
        # No startup GUC bundle: this mode establishes and verifies local policy
        # inside the explicit read-only transaction before the schema SELECT.
        info=driver.conninfo.make_conninfo(raw,host=HOST,hostaddr=str(address),port=5432,
             connect_timeout=5,sslmode='require',channel_binding='require',
             gssencmode='disable',options='',
             application_name='books-connect-diag-'+env['BOOKS_REQUEST_ID'][:8])
        need(type(info)is str and len(info)<=32768,'BOOKS_DRIVER_UNQUALIFIED')
        encoded=urlsplit(raw).password.encode('utf-8')
        decoded=unquote_to_bytes(urlsplit(raw).password)
        scrub=(raw.encode('utf-8'),info.encode('utf-8'),encoded,decoded,
               decoded.replace(b'\\',b'\\\\').replace(b"'",b"\\'"))
        deadline=min(clock()+5.0,work_deadline)
        need(clock()<deadline,'BOOKS_SQL_DEADLINE')
        phase='START';result['native_start_calls']=1
        native=driver.pq.PGconn.connect_start(info.encode('utf-8'))
        need(type(native)is driver.pq.PGconn,'BOOKS_DRIVER_UNQUALIFIED');owned=True
        phase='POLL';polls=0
        while True:
            remaining=deadline-clock()
            if remaining<=0:
                result.update(code='BOOKS_CONNECT_DEADLINE',connect_error_class='timeout',
                  connect_error_basis='LOCAL_MONOTONIC_DEADLINE',connect_error_certainty='stage')
                result['failure_phase']=phase;break
            polls+=1;need(polls<=1000,'BOOKS_DRIVER_UNQUALIFIED')
            state=native.connect_poll()
            if clock()>=deadline:
                result.update(code='BOOKS_CONNECT_DEADLINE',connect_error_class='timeout',
                  connect_error_basis='LOCAL_MONOTONIC_DEADLINE',connect_error_certainty='stage')
                result['failure_phase']=phase;break
            if state==driver.pq.PollingStatus.OK:
                need(native.status==driver.pq.ConnStatus.OK,'BOOKS_SESSION_POLICY')
                result['connect_poll_succeeded']=True
                result['tls_established']=native.ssl_in_use is True
                result['password_auth_used']=native.used_password is True
                if not result['tls_established']or not result['password_auth_used']:
                    result.update(code='BOOKS_SESSION_POLICY',connect_error_class='tls'if not result['tls_established']else'auth',
                      connect_error_basis='TLS_NOT_ACTIVE'if not result['tls_established']else'PASSWORD_USE_NOT_OBSERVED',
                      connect_error_certainty='stage',failure_phase=phase)
                    break
                need(native.transaction_status==0,'BOOKS_SESSION_POLICY')
                native.nonblocking=1;need(native.nonblocking==1,'BOOKS_SQL_PROTOCOL')
                result['protocol_ready']=True
                phase='BEGIN'
                _books_exchange(native,driver,READ_PREFIX[0],result,min(clock()+2.0,work_deadline),wait,clock)
                need(native.transaction_status==2,'BOOKS_SESSION_POLICY');result['tx_started']=True
                phase='LOCAL_POLICY'
                for statement in READ_LOCAL_SQL:
                    _books_exchange(native,driver,statement,result,min(clock()+2.0,work_deadline),wait,clock)
                    need(native.transaction_status==2,'BOOKS_SESSION_POLICY')
                phase='IDENTITY'
                row=_books_exchange(native,driver,READ_IDENTITY_SQL,result,min(clock()+2.0,work_deadline),wait,clock)
                need(row==(True,)*14 and native.transaction_status==2,'BOOKS_SESSION_POLICY')
                result['identity_verified']=True
                phase='SCHEMA'
                row=_books_exchange(native,driver,READ_SCHEMA_SQL,result,min(clock()+2.0,work_deadline),wait,clock)
                # These are fixed-name resolution facts, never a schema/authority admission.
                need(type(row)is tuple and len(row)==14 and all(type(v)is bool for v in row),'BOOKS_RESULT_BOUNDS')
                result.update(schema_relation_count=sum(row),
                              schema_unresolved_names=[name for name,present in zip(TABLES,row)if not present])
                result['schema_transaction_intrans']=native.transaction_status==2
                need(result['schema_transaction_intrans'],'BOOKS_SESSION_POLICY')
                result['schema_resolution_complete']=True
                result.update(status='READONLY_NAMES_COMPLETE_NOT_AUTHORITY',code='BOOKS_SCHEMA_NAMES_OK')
                break
            if state==driver.pq.PollingStatus.FAILED:
                category,basis,certainty,detail,parameter=libpq_failure_diagnostic(native.error_message,scrub)
                result.update(code='BOOKS_CONNECT_POLL_FAILED',connect_error_class=category,
                              connect_error_basis=basis,connect_error_certainty=certainty,
                              connect_error_detail=detail,connect_error_parameter=parameter)
                result['failure_phase']=phase;break
            need(state in(driver.pq.PollingStatus.READING,driver.pq.PollingStatus.WRITING),'BOOKS_DRIVER_UNQUALIFIED')
            fd=native.socket;need(type(fd)is int and fd>=0,'BOOKS_DRIVER_UNQUALIFIED')
            remaining=deadline-clock()
            if remaining>0:
                read=[fd]if state==driver.pq.PollingStatus.READING else[]
                write=[fd]if state==driver.pq.PollingStatus.WRITING else[]
                wait(read,write,[],min(0.25,remaining))
    except BaseException as exc:
        if phase in('PRECHECK','RESOLVE','BUILD_PARAMETERS','START','POLL'):
            category,basis,_=classify_connect_failure(exc,driver)
            result.update(connect_error_class=category,connect_error_basis=basis,
                          connect_error_certainty='typed'if category!='other'else'unknown')
        result['code']=exc.args[0]if type(exc)is Closed and len(exc.args)==1 and type(exc.args[0])is str and exc.args[0]in(SAFE|READ_SQL_CODES)else'BOOKS_SQL_FAILURE'
        result['failure_phase']=phase
    finally:
        if owned:
            if result['tx_may_exist']:
                phase='ROLLBACK'
                try:
                    need(result['protocol_ready']is True and native.status==driver.pq.ConnStatus.OK
                         and native.transaction_status in(0,2,3),'BOOKS_ROLLBACK_UNKNOWN')
                    _books_exchange(native,driver,READ_ROLLBACK,result,min(clock()+2.0,cleanup_deadline),wait,clock)
                    need(native.transaction_status==0,'BOOKS_ROLLBACK_UNKNOWN')
                    result['rollback_verified']=True
                except BaseException:
                    result.update(status='UNKNOWN',code='BOOKS_ROLLBACK_UNKNOWN')
            result['native_finish_attempted']=True
            try:
                native.finish()
                closed=native.pgconn_ptr is None
            except BaseException:closed=False
            result['client_cleanup']=('ROLLBACK_AND_FINISH_CONFIRMED'if result['rollback_verified']else'FINISHED_ROLLBACK_UNVERIFIED'if result['tx_may_exist']else'FINISHED_POINTER_NONE')if closed else'UNKNOWN'
            if not closed:result.update(status='UNKNOWN',code='BOOKS_CLIENT_CLEANUP_UNKNOWN')
            result['cleanup_within_budget']=clock()<=cleanup_deadline
            if not result['cleanup_within_budget']:result.update(status='UNKNOWN',code='BOOKS_CLEANUP_DEADLINE')
        elif result['native_start_calls']:
            result.update(status='UNKNOWN',code='BOOKS_CLIENT_CLEANUP_UNKNOWN',client_cleanup='UNKNOWN')
        result['phase']='FINISHED'if result['client_cleanup']in('FINISHED_POINTER_NONE','ROLLBACK_AND_FINISH_CONFIRMED','FINISHED_ROLLBACK_UNVERIFIED')else phase
    need(len(json.dumps(result,separators=(',',':')).encode())<=2048,'BOOKS_RUNTIME_FAILURE')
    return result

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
        result=readonly_snapshot(driver,os.environ)
        exclusive(folder/'result.json',result)
    except BaseException as exc:
        code=exc.args[0]if type(exc)is Closed and len(exc.args)==1 and type(exc.args[0])is str and exc.args[0]in(SAFE|READ_SQL_CODES)else'BOOKS_RUNTIME_FAILURE'
        result={'status':'UNKNOWN'if folder is not None else'CLOSED','code':code,'retry_allowed':False,'write_admitted':False}
    finally:
        raw=(json.dumps(result,separators=(',',':'))+'\n').encode()
        os.write(output,raw if len(raw)<=4096 else b'{"status":"UNKNOWN","retry_allowed":false}\n');os.close(output)
    return 0 if result.get('status')=='READONLY_NAMES_COMPLETE_NOT_AUTHORITY'else 1
if __name__=='__main__':raise SystemExit(main())
