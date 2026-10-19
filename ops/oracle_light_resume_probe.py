"""Read-only post-terminal probe against the unchanged installed Light runtime."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import urllib.request

from oracle_autopilot import worker
from oracle_autopilot.contract import ROLE_DISPATCH_MAILBOX_PR
from oracle_autopilot.light_runtime_probe import CANARY, FENCE, require, validate_identity
from oracle_autopilot.parallel_work_intake import manifest_sha256
from oracle_autopilot.rollout_verify import validate_receipt

CLAIM_SHA = '8a194b02bee3f4c855dd2771cd12930ff1876b8966810a3356cd7e263ede5e16'
# 0373 adds mailbox locking/capacity predicates without changing the worker RPC
# interfaces. Pin the changed functions AND their capacity dependencies. 0374
# changes only health reporting. Never grant the worker migration-table access.
DB_FUNCTIONS = {
    'autopilot.claim_next_task(text,integer)': (
        CLAIM_SHA, 'v', '{neondb_owner=X/neondb_owner,autopilot_runtime=X/neondb_owner}', True),
    'autopilot.claim_project_work_probe(text,integer)': (
        'f2d09373a9d061472ab3c117d7f3c7dcffdaa3ed63a7294995264522ea8c8099', 'v',
        '{neondb_owner=X/neondb_owner,autopilot_runtime=X/neondb_owner,autopilot_runtime_principal=X/neondb_owner}', True),
    'autopilot.role_dispatch_queue_available(integer)': (
        '0466e071351a5305872e83c7cf2cc30d494aed8e4222b95ceee772f17adbe78a', 's',
        '{neondb_owner=X/neondb_owner}', False),
    'autopilot.enforce_role_dispatch_mailbox_capacity()': (
        'c7c8ba9c6076d792bf8fea31cbc8404c0399e87ac01502b432c330f430a266d6', 'v',
        '{neondb_owner=X/neondb_owner}', False),
}
ACTIVE_VIEW_SHA = '081df5a675d2f8574e5251d7c6da255c2bede0ef316fba22a7c9471721f334d4'


def validate_resume_dsn(dsn):
    # The installed Neon validator checks identity/TLS but permits arbitrary
    # query keys. Our read-only options must not mask startup options that the
    # real worker would use, including a different transaction isolation.
    query = parse_qs(urlsplit(dsn).query, keep_blank_values=True, strict_parsing=True)
    require(set(query) <= {'sslmode', 'channel_binding', 'sslrootcert',
                           'connect_timeout', 'application_name'}
            and all(len(values) == 1 for values in query.values()))


def validate_database_contract(functions, view, trigger, isolation):
    require(isolation == 'read committed')
    require(len(functions) == len(DB_FUNCTIONS)
            and {row['signature'] for row in functions} == set(DB_FUNCTIONS))
    for row in functions:
        digest, volatility, acl, execute = DB_FUNCTIONS[row['signature']]
        require(hashlib.sha256(row['definition'].encode()).hexdigest() == digest
                and row['owner'] == 'neondb_owner' and row['prosecdef'] is True
                and row['provolatile'] == volatility
                and row['proconfig'] == ['search_path=pg_catalog, autopilot']
                and row['acl'] == acl and row['worker_execute'] is execute)
        if row['signature'] == 'autopilot.claim_next_task(text,integer)':
            require(FENCE not in row['definition'])
    require(view and view['owner'] == 'neondb_owner' and view['kind'] == 'v'
            and view['acl'] == '{neondb_owner=arwdDxtm/neondb_owner}'
            and view['options'] is None
            and hashlib.sha256(view['definition'].encode()).hexdigest() == ACTIVE_VIEW_SHA)
    # tgtype5 is AFTER INSERT FOR EACH ROW; a disabled/conditional/BEFORE guard
    # cannot enforce the reviewed capacity semantics.
    require(trigger == dict(name='role_dispatch_mailbox_capacity_guard', enabled='O',
            type=5, function='autopilot.enforce_role_dispatch_mailbox_capacity()', condition=None))


def validate_queue(rows, capacity):
    require(len(rows) == 1)
    row = rows[0]
    require(str(row['task_id']) == CANARY and row['status'] == 'DONE'
            and row['attempts'] == 1 and row['lease_epoch'] == 1
            and row['goal_type'] == 'CHATGPT_ROLE_DISPATCH_V1'
            and row['lease_until'] is None
            and all(row[k] == 0 for k in ('cost_reserved_microusd',
                                         'cost_actual_microusd', 'cost_cap_microusd')))
    require(capacity['active_workers'] == 0 and capacity['probe_reservations'] == 0)


def main():
    stage = 'configuration'
    try:
        require(os.geteuid() != 0 and ROLE_DISPATCH_MAILBOX_PR == 1703)
        require(os.environ.get('AUTOPILOT_ADMISSION_MODE') == 'HOLD')
        require(os.environ.get('AUTOPILOT_DB_BACKEND', 'neon') == 'neon')
        config = worker.load_config()
        validate_resume_dsn(config.dsn)
        require(config.worker_id == 'oracle-autopilot-light-1')
        broker = worker.load_token_broker_config()
        release = json.loads((Path.cwd() / 'ops/autopilot/broker-release.json').read_text())
        require(all(getattr(broker, key) == release[value] for key, value in {
            'url': 'broker_url', 'expected_source_sha': 'broker_source_sha',
            'expected_artifact_sha256': 'broker_artifact_sha256',
            'expected_policy_sha256': 'broker_policy_sha256',
            'expected_provenance_sha256': 'broker_provenance_sha256'}.items()))
        stage = 'database'
        with worker.psycopg.connect(config.dsn, autocommit=True, connect_timeout=10,
                row_factory=worker.dict_row,
                options='-c statement_timeout=5000 -c default_transaction_read_only=on') as conn:
            # Canonical output for regprocedure::text and pg_get_viewdef.
            conn.execute('SET search_path=pg_catalog')
            identity = conn.execute('SELECT current_user AS name, current_database() AS db, '
                'rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin,rolconnlimit '
                'FROM pg_roles WHERE rolname=current_user').fetchone()
            validate_identity(identity)
            acl = conn.execute("""SELECT
                pg_has_role(current_user,'autopilot_runtime_principal','MEMBER') AS runtime_member,
                (pg_has_role(current_user,'neondb_owner','MEMBER') OR
                 pg_has_role(current_user,'autopilot_callback','MEMBER') OR
                 pg_has_role(current_user,'bridge_school_worker','MEMBER')) AS forbidden_member,
                EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND c.relkind IN ('r','p')
                    AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                         OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS direct_access
            """).fetchone()
            require(acl['runtime_member'] and not acl['forbidden_member'] and not acl['direct_access'])
            stage = 'database_contract'
            functions = conn.execute("""SELECT p.oid::regprocedure::text AS signature,
                pg_get_functiondef(p.oid) AS definition,pg_get_userbyid(p.proowner) AS owner,
                p.prosecdef,p.provolatile,p.proconfig,p.proacl::text AS acl,
                has_function_privilege(current_user,p.oid,'EXECUTE') AS worker_execute
                FROM pg_proc p WHERE p.oid=ANY(%s::regprocedure[])""",
                (list(DB_FUNCTIONS),)).fetchall()
            view = conn.execute("""SELECT pg_get_viewdef(c.oid,true) AS definition,
                pg_get_userbyid(c.relowner) AS owner,c.relkind::text AS kind,
                c.relacl::text AS acl,c.reloptions AS options
                FROM pg_class c WHERE c.oid='autopilot.role_dispatch_active'::regclass""").fetchone()
            trigger = conn.execute("""SELECT t.tgname AS name,t.tgenabled::text AS enabled,
                t.tgtype AS type,t.tgfoid::regprocedure::text AS function,
                pg_get_expr(t.tgqual,t.tgrelid) AS condition FROM pg_trigger t
                WHERE t.tgrelid='autopilot.role_dispatch_outbox'::regclass
                  AND t.tgname='role_dispatch_mailbox_capacity_guard' AND NOT t.tgisinternal""").fetchone()
            isolation = conn.execute("SELECT current_setting('default_transaction_isolation') AS value").fetchone()['value']
            validate_database_contract(functions, view, trigger, isolation)
            stage = 'queue'
            rows = conn.execute("SELECT task_id,status,attempts,goal_type,lease_until,lease_epoch,"
                "cost_reserved_microusd,cost_actual_microusd,cost_cap_microusd FROM autopilot.task_status "
                "WHERE task_id=%s OR status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')",
                (CANARY,)).fetchall()
            capacity = conn.execute('SELECT * FROM autopilot.role_worker_capacity_snapshot()').fetchone()
            validate_queue(rows, capacity)
            validate_receipt(conn.execute('SELECT * FROM autopilot.parallel_work_manifest_status(%s)',
                                          (manifest_sha256(),)).fetchone())
        stage = 'broker_health'
        worker._require_approved_broker_release(config=broker,
            opener=urllib.request.build_opener(worker._RejectBrokerRedirects()))
        print(json.dumps({'status': 'PASS', 'stage': 'complete', 'mailbox_pr': 1703,
            'worker_id': config.worker_id, 'database_user': identity['name'],
            'fence_sha256': CLAIM_SHA, 'manifest_sha256': manifest_sha256()}))
    except Exception as exc:
        print(json.dumps({'status': 'FAIL', 'stage': stage, 'error_type': type(exc).__name__}))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
