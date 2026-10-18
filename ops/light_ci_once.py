"""One deterministic CI task; no scheduler, native lane or provider submission.

Imports are stdlib-only until the already installed runtime is explicitly loaded.
Unknown claim/completion outcomes are never converted into retry/fail_task.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import uuid

REPOSITORY = 'olegmed1-art/bridge-video-free'
RELEASE = '5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c'
INSTALLED = Path('/opt/bridge-school/school-autopilot-production-light/releases') / RELEASE
PYTHON = '/opt/bridge-school/school-autopilot/.venv/bin/python'
HOST = 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
ROLE = 'autopilot_light_worker_login'
CLAIM_SHA = '8a194b02bee3f4c855dd2771cd12930ff1876b8966810a3356cd7e263ede5e16'
TARGET = dict(project_id='misty-poetry-18012774', branch_id='br-aged-mud-b1i64914',
              compute_id='ep-noisy-pine-b1pe30sf', database_name='neondb', role_name='neondb_owner')
MIGRATIONS = {'0373_autopilot_active_dispatch_capacity': 'abf12d7bfaa345a318df8d3c4894f7c096edc4894ca473ac63a98e8865fe7ddd',
              '0374_autopilot_queue_health_semantics': 'c63aa4999adc2e870457a47f95b949018196ef33f71a314b8443f4cf43fb5724'}
KIND = 'GITHUB_CI_READ_ONLY_V1'
EVIDENCE = 'GITHUB_CI_READ_ONLY_SNAPSHOT'
ROOT = Path('/var/lib/bridge-light-ci-once')
ACTIVE = ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')
SERVER_TAGS = {'neon.project_id': (TARGET['project_id'], 'postmaster'),
               'neon.branch_id': (TARGET['branch_id'], 'postmaster'),
               'neon.endpoint_id': (TARGET['compute_id'], 'superuser')}
SERVER_TAG_QUERY = """SELECT name,setting,context,source,reset_val,pending_restart
 FROM pg_catalog.pg_settings WHERE name IN ('neon.project_id','neon.branch_id','neon.endpoint_id')"""
ENV_FILE = '/etc/school-autopilot-production-light.env'


class Blocked(RuntimeError):
    """Only fixed codes, never SQL, credentials or provider exception text."""


def need(ok, code):
    if not ok:
        raise Blocked(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE_JSON_FIELD')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def utc():
    return dt.datetime.now(dt.timezone.utc)


def timestamp(value):
    need(isinstance(value, str), 'TIME_FORMAT')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(parsed.utcoffset() == dt.timedelta(0), 'TIME_ZONE')
    return parsed


def validate_plan(p, *, live=False, now=None):
    need(type(p) is dict and set(p) == {'version','source_main','task_key','goal','target',
         'unit','not_before','expires_at','max_seconds','cost_cap_microusd'}, 'PLAN_FIELDS')
    need(type(p['version']) is int and p['version'] == 1 and p['target'] == TARGET, 'PLAN_TARGET')
    need(isinstance(p['source_main'], str) and re.fullmatch('[0-9a-f]{40}', p['source_main']), 'SOURCE_PIN')
    need(isinstance(p['task_key'], str) and re.fullmatch('light-ci-audit-[A-Za-z0-9._:-]{1,150}', p['task_key']), 'TASK_KEY')
    g = p['goal']
    need(type(g) is dict and set(g) == {'repository','pr_number','expected_head_sha','require_draft'}, 'GOAL_FIELDS')
    need(g['repository'] == REPOSITORY and type(g['pr_number']) is int and 1 <= g['pr_number'] <= 1000000
         and g['require_draft'] is True and isinstance(g['expected_head_sha'], str)
         and re.fullmatch('[0-9a-f]{40}', g['expected_head_sha']), 'GOAL_SCOPE')
    need(p['unit'] == 'school-autopilot-light-ci-' + sha(p['task_key'].encode())[:16] + '.service', 'UNIT_NAME')
    need(type(p['max_seconds']) is int and p['max_seconds'] == 180
         and type(p['cost_cap_microusd']) is int and p['cost_cap_microusd'] == 0, 'PLAN_BUDGET')
    start, end = timestamp(p['not_before']), timestamp(p['expires_at'])
    need(180 <= (end-start).total_seconds() <= 1800, 'PLAN_WINDOW')
    if live:
        now = now or utc()
        need(start <= now and (end-now).total_seconds() >= 180, 'PLAN_EXPIRED_OR_TOO_SHORT')
    return p


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def sql_json(value):
    return quote(canonical(value).decode()) + '::jsonb'


def db_guards():
    return """IF current_database()<>'neondb' OR current_user<>'neondb_owner' THEN
 RAISE EXCEPTION 'CI_ONCE_OWNER_TARGET'; END IF;
 IF (SELECT count(*) FROM pg_catalog.pg_settings WHERE
 (name,setting,context) IN (('neon.project_id','misty-poetry-18012774','postmaster'),
 ('neon.branch_id','br-aged-mud-b1i64914','postmaster'),
 ('neon.endpoint_id','ep-noisy-pine-b1pe30sf','superuser'))
 AND source='configuration file' AND reset_val=setting AND NOT pending_restart)<>3
 THEN RAISE EXCEPTION 'CI_ONCE_OWNER_SERVER_BINDING'; END IF;
 IF (SELECT enabled FROM autopilot.native_cli_config WHERE singleton) IS DISTINCT FROM false
 OR EXISTS(SELECT FROM autopilot.role_dispatch_outbox WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED'))
 OR (SELECT count(*) FROM autopilot.role_dispatch_outbox)<>506
 THEN RAISE EXCEPTION 'CI_ONCE_NATIVE_OR_OUTBOX_ACTIVE'; END IF;
 IF (SELECT jsonb_agg(jsonb_build_array(target_pr,state,hold_reason) ORDER BY target_pr)
 FROM autopilot.project_work_item WHERE target_pr IN (1847,1993)) IS DISTINCT FROM
 '[[1847,"BLOCKED",null],[1993,"PAUSED","OWNER_HOLD"]]'::jsonb
 THEN RAISE EXCEPTION 'CI_ONCE_PROTECTED_WORK_DRIFT'; END IF;
 IF (SELECT encode(sha256(convert_to(pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure),'UTF8')),'hex'))<>""" + quote(CLAIM_SHA) + """ THEN RAISE EXCEPTION 'CI_ONCE_ADMISSION_DRIFT'; END IF;
 IF (SELECT jsonb_object_agg(migration_key,checksum) FROM public.schema_migration
 WHERE migration_key IN ('0373_autopilot_active_dispatch_capacity','0374_autopilot_queue_health_semantics'))
 IS DISTINCT FROM """ + sql_json(MIGRATIONS) + """ THEN RAISE EXCEPTION 'CI_ONCE_MIGRATIONS'; END IF;"""


def owner_snapshot(key):
    # Compare all previous autopilot tasks/controls before and after this one
    # UUID. Book-pilot relations and unrelated client transactions are absent.
    sources = {
      'other_tasks': 'SELECT * FROM autopilot.task WHERE task_key<>'+key,
      'outbox': 'SELECT * FROM autopilot.role_dispatch_outbox',
      'native_receipts': 'SELECT * FROM autopilot.native_cli_receipt',
      'callback_receipts': 'SELECT * FROM autopilot.role_dispatch_callback_receipt',
      'terminal_receipts': 'SELECT * FROM autopilot.role_dispatch_codex_terminal_receipt',
      'delivery_proofs': 'SELECT * FROM autopilot.role_dispatch_codex_delivery_proof',
      'send_intents': 'SELECT * FROM autopilot.codex_command_send_intent',
      'publication_permits': 'SELECT * FROM autopilot.codex_publication_permit',
      'protected_work': 'SELECT * FROM autopilot.project_work_item WHERE target_pr IN (1847,1993)',
      'mailbox_registry': 'SELECT * FROM autopilot.role_dispatch_mailbox_registry',
      'native_config': 'SELECT * FROM autopilot.native_cli_config'}
    parts = []
    for name, source in sources.items():
        digest = "encode(sha256(convert_to(COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text COLLATE \"C\"),'[]'::jsonb)::text,'UTF8')),'hex')"
        parts += [quote(name),"(SELECT jsonb_build_object('count',count(*),'sha256',"+digest+") FROM ("+source+") r)"]
    return 'SELECT jsonb_build_object('+','.join(parts)+') AS unchanged_snapshot'


def owner_requests(plan):
    """Compile fixed owner requests. Never connects, applies DDL or grants."""
    p = validate_plan(plan)
    key, goal, binding = quote(p['task_key']), sql_json(p['goal']), sha(canonical(p))
    guard = db_guards()
    pre = 'DO $ci$ BEGIN ' + guard + """
 IF NOT has_function_privilege(current_user,
 'autopilot.create_shadow_task(text,text,jsonb,integer,bigint,text,text)','EXECUTE')
 OR NOT has_column_privilege(current_user,'autopilot.task','max_attempts','UPDATE')
 THEN RAISE EXCEPTION 'CI_ONCE_EXISTING_AUTHORITY_REQUIRED'; END IF;
 END $ci$"""
    register = """DO $ci$ DECLARE r record; n integer; BEGIN """ + guard + f"""
 PERFORM pg_advisory_xact_lock(hashtextextended('light-ci-once:'||{key},7301));
 IF EXISTS(SELECT FROM autopilot.task WHERE task_key={key}) THEN
 RAISE EXCEPTION 'CI_ONCE_KEY_ALREADY_EXISTS_INSPECT_ONLY'; END IF;
 IF EXISTS(SELECT FROM autopilot.task WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING'))
 THEN RAISE EXCEPTION 'CI_ONCE_COMPETING_TASK'; END IF;
 SELECT * INTO r FROM autopilot.create_shadow_task({key},{quote(KIND)},{goal},20,0,'DIRECTOR','CHATGPT');
 IF r.created IS DISTINCT FROM true OR r.status<>'READY' THEN RAISE EXCEPTION 'CI_ONCE_NOT_FRESH'; END IF;
 UPDATE autopilot.task SET max_attempts=1 WHERE task_id=r.task_id AND task_key={key}
 AND goal_type={quote(KIND)} AND goal_json={goal} AND status='READY' AND attempts=0
 AND lease_epoch=0 AND cost_cap_microusd=0 AND cost_reserved_microusd=0 AND cost_actual_microusd=0;
 GET DIAGNOSTICS n=ROW_COUNT;
 IF n<>1 THEN RAISE EXCEPTION 'CI_ONCE_REGISTRATION_CONTRACT'; END IF;
 END $ci$"""
    receipt = f"""SELECT jsonb_build_object('version',1,'plan_sha256',{quote(binding)},
 'task_id',task_id,'task_key',task_key,'goal',goal_json,'status',status,'attempts',attempts,
 'lease_epoch',lease_epoch,'max_attempts',max_attempts,'cost_cap_microusd',cost_cap_microusd,
 'observed_at',clock_timestamp(),'source','CI_ONCE_OWNER_REGISTRATION_V1') AS registration
 FROM autopilot.task WHERE task_key={key}"""
    timeout = ["SET LOCAL statement_timeout='5s'", "SET LOCAL lock_timeout='2s'",
               "SET LOCAL idle_in_transaction_session_timeout='5s'", "SET LOCAL transaction_timeout='10s'"]
    freshness = f"""DO $ci$ BEGIN IF clock_timestamp()<{quote(p['not_before'])}::timestamptz
 OR clock_timestamp()>{quote(p['expires_at'])}::timestamptz-interval '180 seconds'
 THEN RAISE EXCEPTION 'CI_ONCE_WINDOW'; END IF; END $ci$"""
    acceptance = f"""SELECT jsonb_build_object(
 'task',(SELECT to_jsonb(t) FROM autopilot.task t WHERE task_key={key}),
 'source_cardinality',(SELECT count(*) FROM autopilot.task WHERE task_key={key}),
 'evidence',(SELECT COALESCE(jsonb_agg(to_jsonb(e)),'[]'::jsonb) FROM autopilot.evidence e JOIN autopilot.task t USING(task_id) WHERE t.task_key={key}),
 'steps',(SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM autopilot.step_attempt s JOIN autopilot.task t USING(task_id) WHERE t.task_key={key}),
 'done_events',(SELECT count(*) FROM autopilot.task_event e JOIN autopilot.task t USING(task_id) WHERE t.task_key={key} AND e.event_type='TASK_DONE'),
 'active_tasks',(SELECT count(*) FROM autopilot.task WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')),
 'native_enabled',(SELECT enabled FROM autopilot.native_cli_config WHERE singleton),
 'outbox_active',(SELECT count(*) FROM autopilot.role_dispatch_active),
 'outbox_archive',(SELECT count(*) FROM autopilot.role_dispatch_archive)) AS acceptance"""
    return {'owner-preflight': {**TARGET, 'sql_statements': ['SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'] + timeout + [pre,owner_snapshot(key)]},
            'register': {**TARGET, 'sql_statements': timeout + [freshness, pre, register, receipt]},
            'inspect': {**TARGET, 'sql_statements': ['SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'] + timeout + [pre,owner_snapshot(key),acceptance]}}


def accept_owner(plan, task_id, before, after, result):
    """Pure acceptance checker for separately retained owner read-only results."""
    validate_plan(plan)
    need(before == after and set(before) == {'other_tasks','outbox','native_receipts','callback_receipts',
         'terminal_receipts','delivery_proofs','send_intents','publication_permits','protected_work',
         'mailbox_registry','native_config'} and before['outbox']['count'] == 506, 'OWNER_UNRELATED_DRIFT')
    need(result['source_cardinality'] == 1 and result['active_tasks'] == result['outbox_active'] == 0
         and result['outbox_archive'] == 506 and result['native_enabled'] is False, 'OWNER_SCOPE')
    task = result['task']
    summary = task['safe_summary_json']
    done_status(task,task_id,plan,summary)
    need(task['goal_json'] == plan['goal'] and task['created_by'] == 'DIRECTOR' and task['source'] == 'CHATGPT'
         and summary['task_id'] == task_id and summary['task_kind'] == KIND
         and summary['repository'] == REPOSITORY and summary['pr_number'] == plan['goal']['pr_number']
         and summary['head_sha'] == plan['goal']['expected_head_sha'] and summary['model_calls'] == 0
         and summary['cost_actual_microusd'] == 0 and summary['http_method'] == 'GET'
         and summary['production_mutation'] is False and summary['runtime'] == 'ORACLE_RESIDENT', 'OWNER_TASK')
    need(len(result['steps']) == len(result['evidence']) == result['done_events'] == 1, 'OWNER_TERMINAL_CARDINALITY')
    step, evidence = result['steps'][0], result['evidence'][0]
    need(step['task_id'] == task_id and step['status'] == 'COMPLETED'
         and step['attempt_no'] == step['lease_epoch'] == 1 and step['step_key'] == 'github.ci.snapshot'
         and step['capability_name'] == 'github.ci.snapshot' and step['executor_type'] == 'ORACLE_RESIDENT'
         and step['result_summary_json'] == summary
         and evidence['task_id'] == task_id and evidence['step_attempt_id'] == step['step_attempt_id']
         and evidence['evidence_class'] == EVIDENCE and evidence['provider'] == 'ORACLE_RESIDENT'
         and evidence['retained'] is True and evidence['metadata_json'] == summary
         and evidence['content_sha256'] == sha(canonical(summary)), 'OWNER_RETAINED_EVIDENCE')
    return {'status':'OWNER_TERMINAL_ACCEPTANCE_PASS','task_id':task_id,'content_sha256':sha(canonical(summary)),
            'fresh_github_and_host_exit_i2_still_required':True}


def registration(value, plan):
    fields = {'version','plan_sha256','task_id','task_key','goal','status','attempts','lease_epoch',
              'max_attempts','cost_cap_microusd','observed_at','source'}
    need(type(value) is dict and set(value) == fields, 'REGISTRATION_FIELDS')
    need(value['version'] == 1 and value['plan_sha256'] == sha(canonical(plan))
         and value['task_key'] == plan['task_key'] and value['goal'] == plan['goal']
         and value['status'] == 'READY' and value['attempts'] == value['lease_epoch'] == 0
         and value['max_attempts'] == 1 and value['cost_cap_microusd'] == 0
         and value['source'] == 'CI_ONCE_OWNER_REGISTRATION_V1', 'REGISTRATION_BINDING')
    need(str(uuid.UUID(value['task_id'])) == value['task_id'], 'REGISTRATION_UUID')
    need(timestamp(plan['not_before']) <= timestamp(value['observed_at']) < timestamp(plan['expires_at']), 'REGISTRATION_TIME')
    return value['task_id']


class Journal:
    """Create-only records. A crash/partial write latches the key permanently."""
    def __init__(self, directory):
        self.directory = Path(directory)

    def write(self, name, value):
        need(re.fullmatch('[a-z-]+\\.json', name), 'RECORD_NAME')
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self.directory / name, flags, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == 'posix':
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def validate_claim(row, plan, task_id, worker):
    need(row is not None and str(row['task_id']) == task_id and row['goal_type'] == KIND
         and row['goal_json'] == plan['goal'] and row['current_step_key'] == 'github.ci.snapshot'
         and row['step_cursor'] == 0 and row['attempts'] == row['max_attempts'] == row['lease_epoch'] == 1
         and row['cost_cap_microusd'] == row['cost_reserved_microusd'] == 0, 'WRONG_CLAIM_ROLLBACK')
    task = worker.claimed_task_from_row(row)
    worker.validate_task_contract(task)
    return task


def claim_exact(connect, worker, config, plan, task_id):
    """The ONLY claim call. Validation precedes transaction context COMMIT."""
    with connect() as conn:
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='2s'")
            row = conn.execute('SELECT * FROM autopilot.claim_next_task(%s,%s)',
                               (config.worker_id, config.lease_seconds)).fetchone()
            task = validate_claim(row, plan, task_id, worker)
        # Any commit/connection error propagates as unknown; caller never retries.
    return task


def done_status(row, task_id, plan, summary):
    need(row is not None and str(row['task_id']) == task_id and row['task_key'] == plan['task_key']
         and row['goal_type'] == KIND and row['status'] == 'DONE' and row['attempts'] == row['lease_epoch'] == 1
         and row['max_attempts'] == 1 and row['cost_cap_microusd'] == row['cost_reserved_microusd'] == row['cost_actual_microusd'] == 0
         and row['safe_summary_json'] == summary, 'TERMINAL_NOT_CONFIRMED')


def run_one(plan, task_id, runtime, journal):
    validate_plan(plan, live=True)
    binding = {'plan_sha256': sha(canonical(plan)), 'task_id': task_id}
    journal.write('claim-intent.json', binding)
    try:
        task = runtime.claim(plan, task_id)
    except Exception:
        journal.write('claim-unknown.json', binding)
        raise Blocked('CLAIM_NOT_CONFIRMED_NO_REPLAY') from None
    journal.write('claim-confirmed.json', binding)
    with runtime.lease(task):
        try:
            # Split installed execute_task at its existing fetch/complete boundary.
            # A GET error is known pre-completion; _complete errors are ambiguous.
            summary = runtime.fetch(plan['goal'])
        except Exception:
            journal.write('fetch-failed.json', binding)
            try:
                runtime.fail_known(task)
            except Exception:
                journal.write('failure-unknown.json', binding)
            raise Blocked('READ_FAILED_NO_RETRY') from None
        summary = {**summary, 'task_id': task_id, 'task_kind': KIND, 'runtime': 'ORACLE_RESIDENT'}
        need(summary.get('model_calls') == 0 and summary.get('cost_actual_microusd') == 0
             and summary.get('production_mutation') is False and summary.get('http_method') == 'GET'
             and summary.get('head_sha') == plan['goal']['expected_head_sha'], 'FETCH_SCOPE')
        journal.write('completion-intent.json', {**binding, 'content_sha256': sha(canonical(summary))})
        try:
            runtime.complete(task, summary)
            row = runtime.status(task_id)
            done_status(row, task_id, plan, summary)
        except Exception:
            journal.write('completion-unknown.json', binding)
            raise Blocked('COMPLETION_NOT_CONFIRMED_INSPECT_ONLY') from None
    result = {**binding, 'status': 'DONE_RUNTIME_READBACK', 'content_sha256': sha(canonical(summary)),
              'independent_owner_acceptance_required': True, 'model_cost_microusd': 0}
    journal.write('terminal.json', result)
    return result


class Runtime:
    def __init__(self, worker):
        self.worker = worker
        self.config = worker.load_config()
        need(self.config.worker_id == 'oracle-autopilot-light-1'
             and self.config.lease_seconds == 60 and self.config.heartbeat_seconds == 15, 'WORKER_PROFILE')
        # The installed connector also serves heartbeat/completion. Bind its
        # config to the installed OS trust store, without credential fallback.
        from dataclasses import replace
        dsn = worker.psycopg.conninfo.make_conninfo(self.config.dsn,sslmode='verify-full',
                  sslrootcert='/etc/ssl/certs/ca-certificates.crt',channel_binding='require',
                  gssencmode='disable',
                  options='-c statement_timeout=5000 -c lock_timeout=2000')
        self.config = replace(self.config,dsn=dsn)

    def connect(self):
        return self.worker.psycopg.connect(self.config.dsn, autocommit=True, connect_timeout=5,
                   row_factory=self.worker.dict_row, application_name='school-light-ci-once')

    def probe(self, *, require_empty=False):
        with self.connect() as conn:
            need(conn.info.host == HOST and conn.info.port == 5432, 'RUNTIME_ENDPOINT')
            with conn.transaction():
                conn.execute('SET TRANSACTION READ ONLY')
                conn.execute("SET LOCAL statement_timeout='5s'")
                row = conn.execute("SELECT current_user AS role,current_database() AS db,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
                need(row and row['role'] == ROLE and row['db'] == 'neondb'
                     and not any(row[k] for k in ('rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')), 'RUNTIME_IDENTITY')
                server_binding(conn.execute(SERVER_TAG_QUERY).fetchall())
                authority = conn.execute("""SELECT pg_has_role(current_user,'autopilot_runtime_principal','MEMBER') AS member,
                  (pg_has_role(current_user,'neondb_owner','MEMBER') OR pg_has_role(current_user,'autopilot_callback','MEMBER')
                   OR pg_has_role(current_user,'bridge_school_worker','MEMBER')) AS forbidden,
                  EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND c.relkind IN ('r','p')
                   AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS direct_access""").fetchone()
                need(authority and authority['member'] and not authority['forbidden'] and not authority['direct_access'], 'RUNTIME_AUTHORITY_DRIFT')
                if require_empty:
                    count = conn.execute("SELECT count(*) AS n FROM autopilot.task_status WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')").fetchone()
                    need(count['n']==0,'PREFLIGHT_QUEUE_NOT_EMPTY')
                digest = conn.execute("SELECT encode(sha256(convert_to(pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure),'UTF8')),'hex') AS hash").fetchone()
                need(digest and digest['hash'] == CLAIM_SHA, 'RUNTIME_CLAIM_DRIFT')
                for signature in ('claim_next_task(text,integer)','heartbeat_task(uuid,text,bigint,integer)',
                                  'complete_task(uuid,text,bigint,text,text,jsonb)','fail_task(uuid,text,bigint,text,boolean)'):
                    need(conn.execute('SELECT has_function_privilege(current_user,%s,\'EXECUTE\') AS ok',
                                      ('autopilot.' + signature,)).fetchone()['ok'], 'EXISTING_RUNTIME_RPC_REQUIRED')

    def claim(self, plan, task_id):
        self.probe()
        return claim_exact(self.connect, self.worker, self.config, plan, task_id)

    def lease(self, task):
        return self.worker.keep_lease_alive(self.config, task)

    def fetch(self, goal):
        return self.worker.fetch_github_ci_snapshot(goal)

    def complete(self, task, summary):
        self.worker._complete(self.config, task, evidence_class=EVIDENCE, summary=summary)

    def status(self, task_id):
        with self.connect() as conn, conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            return conn.execute('SELECT * FROM autopilot.task_status WHERE task_id=%s::uuid', (task_id,)).fetchone()

    def fail_known(self, task):
        # Fail RPC is fenced by owner/epoch; no retry/requeue is requested.
        self.worker.fail_task(self.config, task, error_code='LIGHT_CI_ONCE_READ_FAILED', retryable=False)


def read_root(path, *, limit=1048576):
    path = Path(path)
    for parent in path.parents:
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'UNTRUSTED_PARENT')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022 and info.st_size <= limit, 'UNTRUSTED_FILE')
        raw = stream.read(limit + 1)
        need(len(raw) <= limit, 'FILE_LIMIT')
        return raw


def server_binding(tags):
    need(len(tags) == 3 and {t['name'] for t in tags} == set(SERVER_TAGS), 'SERVER_BINDING_TAGS')
    need(all((t['setting'],t['context']) == SERVER_TAGS[t['name']] for t in tags), 'SERVER_BINDING_VALUES')
    need(all(t['source'] == 'configuration file' and t['reset_val'] == t['setting']
             and t['pending_restart'] is False for t in tags), 'SERVER_BINDING_PROVENANCE')


def duration(value):
    parts = re.findall(r'([0-9]+)(min|s)', value)
    if ''.join(n+u for n,u in parts) == value and parts:
        return sum(int(n) * (60 if u == 'min' else 1) for n,u in parts)
    need(value == '0', 'SYSTEMD_DURATION')
    return 0


def unit_properties(plan, job, *, probe):
    # 165+5(start)+5(stop) <=180, Type=exec (RuntimeMaxSec has no effect on oneshot).
    return {'Type':'exec', 'User':'school-autopilot', 'Group':'school-autopilot',
            'WorkingDirectory':str(INSTALLED), 'Restart':'no', 'RuntimeMaxUSec':'25s' if probe else '2min 45s',
            'TimeoutStartUSec':'5s','TimeoutStopUSec':'5s','KillMode':'control-group','SendSIGKILL':'yes',
            'NoNewPrivileges':'yes','PrivateTmp':'yes','PrivateDevices':'yes','ProtectSystem':'strict',
            'ProtectHome':'yes','ProtectKernelTunables':'yes','ProtectKernelModules':'yes',
            'ProtectControlGroups':'yes','RestrictSUIDSGID':'yes','ReadWritePaths':str(job),
            'CapabilityBoundingSet':'','RestrictNamespaces':'yes','LockPersonality':'yes',
            'RestrictRealtime':'yes','MemoryMax':'268435456','TasksMax':'16',
            'CPUQuotaPerSecUSec':'250ms','UMask':'0077','StandardOutput':'null','StandardError':'null',
            'PassEnvironment':'','UnsetEnvironment':''}


def attest_unit(plan, job, *, probe):
    unit = plan['unit'].replace('.service','-probe.service') if probe else plan['unit']
    wanted = unit_properties(plan, job, probe=probe)
    extra = {'MainPID','InvocationID','ActiveState','SubState','ControlGroup','Environment'}
    out = subprocess.run(['/usr/bin/systemctl','show','--all',unit] + ['--property='+x for x in [*wanted,*sorted(extra)]],
                         env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},capture_output=True,text=True,check=True,timeout=5).stdout
    actual = {}
    for line in out.splitlines():
        key, sep, value = line.partition('=')
        need(sep and key not in actual, 'UNIT_PROPERTIES_FORMAT')
        actual[key] = value
    need(set(actual) == set(wanted) | extra, 'UNIT_PROPERTIES_MISSING')
    for key, value in wanted.items():
        if key.endswith('USec') and key != 'CPUQuotaPerSecUSec':
            need(duration(actual[key].replace(' ','')) == duration(value.replace(' ','')), 'UNIT_DEADLINE')
        else:
            need(actual[key] == value, 'UNIT_SANDBOX')
    # EnvironmentFiles may be omitted by systemctl on systemd255. Read the
    # typed D-Bus array; do not infer an empty or approved array from omission.
    unit_path = ''.join(c if c.isascii() and c.isalnum() else '_'+format(ord(c),'02x') for c in unit)
    files = subprocess.run(['/usr/bin/busctl','get-property','org.freedesktop.systemd1',
             '/org/freedesktop/systemd1/unit/'+unit_path,'org.freedesktop.systemd1.Service','EnvironmentFiles'],
             env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},capture_output=True,text=True,check=True,timeout=5).stdout.strip()
    need(files == 'a(sb) 1 "'+ENV_FILE+'" false', 'UNIT_ENVIRONMENT_FILES')
    need(sorted(shlex.split(actual['Environment'])) == sorted([
         'AUTOPILOT_ADMISSION_MODE='+('HOLD' if probe else 'ACTIVE'),'PYTHONDONTWRITEBYTECODE=1']), 'UNIT_ENVIRONMENT')
    need(actual['MainPID'] == str(os.getpid()) and re.fullmatch('[0-9a-f]{32}',actual['InvocationID'])
         and os.environ.get('INVOCATION_ID') == actual['InvocationID']
         and actual['ActiveState'] == 'active' and actual['SubState'] == 'running'
         and actual['ControlGroup'] == '/system.slice/'+unit, 'UNIT_PROCESS_IDENTITY')
    return {'invocation_id':actual['InvocationID'],'control_group':actual['ControlGroup'],'unit':unit}


def restrict_environment():
    # EnvironmentFile is not copied to unit metadata. Discard every unrelated
    # token/provider/proxy/import variable before importing installed runtime.
    keep = {'AUTOPILOT_DATABASE_URL','AUTOPILOT_RUNTIME_MODE','AUTOPILOT_WORKER_ID',
            'AUTOPILOT_LEASE_SECONDS','AUTOPILOT_HEARTBEAT_SECONDS','AUTOPILOT_RECOVERY_POLL_SECONDS',
            'AUTOPILOT_ADMISSION_MODE'}
    values = {k:v for k,v in os.environ.items() if k in keep}
    need(not any(os.environ.get(k) for k in ('PYTHONPATH','PYTHONHOME')), 'ENVIRONMENT_OVERRIDE')
    need(os.environ.get('AUTOPILOT_DB_BACKEND','neon') == 'neon', 'ENVIRONMENT_BACKEND')
    os.environ.clear()
    os.environ.update(values)
    os.environ.update(PATH='/usr/bin:/bin',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1',
                      AUTOPILOT_DB_BACKEND='neon',AUTOPILOT_EXPECTED_DB_USER=ROLE)


def load_installed(manifest):
    need(manifest['revision'] == RELEASE and manifest['sha256'] == '3f7736a4fa7a6796eb9fa7be10e1d01e6cd04cb803d426991c942a91f7eb1c07', 'RUNTIME_BUNDLE_PIN')
    # Same canonical original augmented-bundle formula; data never executes here.
    original = json.dumps({k:manifest[k] for k in ('revision','files')},sort_keys=True,separators=(',',':')).encode()
    need(sha(original) == manifest['sha256'], 'RUNTIME_BUNDLE_DIGEST')
    for name, expected in manifest['files'].items():
        need(re.fullmatch(r'(oracle_autopilot|autopilot_phase3b)/[a-z0-9_]+\.py|ops/autopilot/(broker-release\.json|broker-hold\.env|previous-hold\.conf)',name), 'RUNTIME_PATH')
        need(read_root(INSTALLED/name) == expected.encode(), 'INSTALLED_SOURCE_DRIFT')
    need(os.environ.get('AUTOPILOT_ADMISSION_MODE') in {'HOLD','ACTIVE'} and
         not any(os.environ.get(x) for x in ('PYTHONPATH','PYTHONHOME')), 'ENVIRONMENT_OVERRIDE')
    sys.path.insert(0,str(INSTALLED))
    from oracle_autopilot import worker
    need(Path(worker.__file__).resolve() == INSTALLED/'oracle_autopilot/worker.py', 'IMPORT_SOURCE')
    return worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['probe','run'])
    parser.add_argument('--control',type=Path,required=True)
    parser.add_argument('--control-sha256',required=True)
    args = parser.parse_args()
    raw = read_root(args.control)
    need(sha(raw) == args.control_sha256, 'CONTROL_HASH')
    control = strict_json(raw)
    need(set(control) == {'plan','runtime','registration'}, 'CONTROL_FIELDS')
    plan = validate_plan(control['plan'],live=True)
    need(os.name == 'posix' and os.geteuid() != 0, 'RESTRICTED_POSIX_RUNTIME_REQUIRED')
    job = ROOT/'jobs'/sha(plan['task_key'].encode())
    info = job.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, 'JOURNAL_DIRECTORY')
    identity = attest_unit(plan,job,probe=args.operation == 'probe')
    journal = Journal(job)
    journal.write('probe-unit.json' if args.operation == 'probe' else 'run-unit.json', identity)
    restrict_environment()
    worker = load_installed(control['runtime'])
    runtime = Runtime(worker)
    runtime.probe(require_empty=args.operation=='probe')
    if args.operation == 'probe':
        # Prove the actual inherited capability/target/venv and public GET before READY.
        snapshot = runtime.fetch(plan['goal'])
        journal.write('probe-pass.json',{'plan_sha256':sha(canonical(plan)),**identity,
                                            'observed_at':utc().isoformat(),'public_head':snapshot['head_sha']})
        print(json.dumps({'status':'SANDBOX_READONLY_PROBE_PASS'}))
        return
    need(os.environ.get('AUTOPILOT_ADMISSION_MODE') == 'ACTIVE', 'FINITE_ACTIVE_ADMISSION_REQUIRED')
    task_id = registration(control['registration'],plan)
    print(json.dumps(run_one(plan,task_id,runtime,journal)))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        code = str(exc) if isinstance(exc,Blocked) else 'UNCLASSIFIED_INSPECT_ONLY'
        print(json.dumps({'status':'NOT_ACCEPTED','code':code}))
        raise SystemExit(2) from None
