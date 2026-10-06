"""Definition-only StageA: one connection, client guard, definitions, rollback.
No connection/credential discovery, application rows, classifier invocation or StageB.
"""
from dataclasses import dataclass
import hashlib
import json
import re
import time

SQL_SHA256 = "a80ffb474c78dec2a01573426e952d2050cfebd1fa994942cfe775df74ad8209"
RELATIONS = ("project_work_item","paused_work_reconcile_receipt","project_planner_state",
             "role_dispatch_outbox","task","native_cli_receipt","native_cli_config",
             "role_registry","project_work_task")
FUNCTIONS = ("autopilot.role_blocker_requires_owner(text)","autopilot.enforce_enabled_role()",
             "autopilot.role_is_enabled(text)","autopilot.set_project_work_dependency_state()",
             "autopilot.release_project_work_dependents()")
MAX_OUTPUT = 262144

class Refused(RuntimeError):
    pass

def require(ok):
    if not ok:
        raise Refused("STAGE_A_REFUSED")

@dataclass(frozen=True)
class Binding:
    host: str
    database: str
    branch: str
    owner: str
    need_dependency_state_definition: bool = False
    need_dependency_release_definition: bool = False

def catalog_query(raw):
    require(type(raw) is bytes and hashlib.sha256(raw).hexdigest() == SQL_SHA256)
    found = re.findall(rb"catalog_sql constant text := \$catalog_query\$(.*?)\$catalog_query\$;", raw, re.S)
    require(len(found) == 1)
    return found[0].decode("utf-8")

def client_guard(conn, pq, binding):
    require(type(binding) is Binding and all(type(v) is str and 0 < len(v) <= 512
            for v in (binding.host,binding.database,binding.branch,binding.owner)))
    require(type(binding.need_dependency_state_definition) is bool
            and type(binding.need_dependency_release_definition) is bool)
    info = conn.info
    parameters = info.get_parameters()
    require(not conn.closed and conn.autocommit is True
            and info.status == pq.ConnStatus.OK
            and info.transaction_status == pq.TransactionStatus.IDLE
            and info.pipeline_status == pq.PipelineStatus.OFF
            and info.host == binding.host and info.port == 5432
            and info.dbname == binding.database and info.user == binding.owner
            and conn.pgconn.ssl_in_use
            and parameters.get("sslmode") == "verify-full"
            and parameters.get("channel_binding") == "require")

RELATION_QUERY = """
SELECT jsonb_build_object(
 'name',c.relname,'owner',pg_get_userbyid(c.relowner),'kind',c.relkind,
 'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,'acl',c.relacl::text,
 'options',c.reloptions,
 'columns',(SELECT jsonb_agg(jsonb_build_object('number',a.attnum,'name',a.attname,
 'type',a.atttypid::regtype::text,'not_null',a.attnotnull,'identity',a.attidentity,
 'generated',a.attgenerated,'default',pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
 FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
 WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
 'constraints',(SELECT jsonb_agg(jsonb_build_object('name',k.conname,'kind',k.contype,
 'validated',k.convalidated,'deferrable',k.condeferrable,'deferred',k.condeferred,
 'definition',pg_get_constraintdef(k.oid,true)) ORDER BY k.conname)
 FROM pg_constraint k WHERE k.conrelid=c.oid))
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='autopilot' AND c.relname=ANY(%s) ORDER BY c.relname
"""
TRIGGER_QUERY = """
SELECT jsonb_build_object('relation',t.tgrelid::regclass::text,'name',t.tgname,
 'enabled',t.tgenabled,'internal',t.tgisinternal,'definition',pg_get_triggerdef(t.oid,true),
 'function',p.oid::regprocedure::text,'owner',pg_get_userbyid(p.proowner),
 'security_definer',p.prosecdef,'config',p.proconfig,'acl',p.proacl::text,
 'function_sha256',encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex'))
FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
WHERE t.tgrelid IN ('autopilot.project_work_item'::regclass,
 'autopilot.paused_work_reconcile_receipt'::regclass)
ORDER BY t.tgrelid::regclass::text,t.tgname
"""
FUNCTION_QUERY = """
SELECT jsonb_build_object('name',p.oid::regprocedure::text,
 'owner',pg_get_userbyid(p.proowner),'volatility',p.provolatile,'security_definer',p.prosecdef,
 'config',p.proconfig,'acl',p.proacl::text,
 'sha256',encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex'),
 'definition',CASE WHEN %s THEN pg_get_functiondef(p.oid) ELSE NULL END)
FROM pg_proc p WHERE p.oid=to_regprocedure(%s)
"""

def collect(conn, pq, binding, sql_bytes, *, seconds=45):
    """Caller owns conn exclusively. Always directly closes it; never commits."""
    started = False
    validated = False
    result = None
    deadline = None
    def query(statement, values=None):
        require(time.monotonic() < deadline)
        cursor = conn.execute(statement, values)
        rows = cursor.fetchall() if cursor.description else []
        require(time.monotonic() < deadline)
        return rows
    try:
        require(type(seconds) is int and 1 <= seconds <= 45)
        deadline = time.monotonic() + seconds
        catalog = catalog_query(sql_bytes)  # Validate source before ANY SQL.
        client_guard(conn,pq,binding)        # IDLE before BEGIN on this connection.
        validated = True
        started = True
        query("BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY")
        query("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
        query("SET LOCAL lock_timeout = '2s'")
        query("SET LOCAL statement_timeout = '5s'")
        query("SET LOCAL search_path = pg_catalog, pg_temp")
        require(query("SELECT current_setting('transaction_read_only'), "
                      "current_setting('transaction_isolation')") == [('on','read committed')])
        identity = query("SELECT current_database()=%s, current_setting('neon.branch_id',true)=%s, "
                         "current_user::text=%s, session_user::text=%s",
                         (binding.database,binding.branch,binding.owner,binding.owner))
        require(identity == [(True,True,True,True)])
        before = query(catalog)
        require(len(before)==1 and type(before[0][0]) is str
                and re.fullmatch(r'[0-9a-f]{64}',before[0][0]))
        relations = [r[0] for r in query(RELATION_QUERY,(list(RELATIONS),))]
        require(len(relations) == 9 and {r['name'] for r in relations} == set(RELATIONS))
        triggers = [r[0] for r in query(TRIGGER_QUERY)]
        functions = []
        for index,name in enumerate(FUNCTIONS):
            body = index < 3 or (index == 3 and binding.need_dependency_state_definition) or (
                    index == 4 and binding.need_dependency_release_definition)
            rows = query(FUNCTION_QUERY,(body,name))
            require(len(rows) == 1)
            functions.append(rows[0][0])
        # A point-in-time drift guard, not exclusion of concurrent DDL.
        require(query(catalog) == before)
        result = dict(stage='A',catalog_sha256=before[0][0],relations=relations,
                      triggers=triggers,functions=functions,production_mutations=False,
                      stage_b_allowed=False,ddl_window_proven=False)
        require(len(json.dumps(result,ensure_ascii=True).encode()) <= MAX_OUTPUT)
    except BaseException:
        result = None
    finally:
        try:
            if started:
                conn.execute("ROLLBACK")
                require(conn.info.transaction_status == pq.TransactionStatus.IDLE)
        except BaseException:
            result = None
        finally:
            if validated:
                try:
                    conn.close()
                    require(conn.closed)
                except BaseException:
                    result = None
    if result is not None and time.monotonic() >= deadline:
        result = None
    if result is None:
        raise Refused("STAGE_A_REFUSED") from None
    return result
