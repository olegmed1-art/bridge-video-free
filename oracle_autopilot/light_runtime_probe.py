"""No-claim, no-publication preflight executed under the real Light service UID."""
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request

from . import worker
from .contract import ROLE_DISPATCH_MAILBOX_PR
from .parallel_work_intake import manifest_sha256
from .rollout_verify import validate_receipt

CANARY = 'f05c605f-f664-4ff7-9927-a039f000a929'
FENCE = 'LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1'


def require(condition):
    if not condition:
        raise ValueError('PREFLIGHT_CONTRACT_MISMATCH')


def validate_queue(rows, capacity):
    require(len(rows) == 1)
    row = rows[0]
    require(str(row['task_id']) == CANARY and row['status'] == 'READY' and row['attempts'] == 0
            and row['goal_type'] == 'CHATGPT_ROLE_DISPATCH_V1' and row['lease_until'] is None
            and row['lease_epoch'] == 0 and row['cost_reserved_microusd'] == 0
            and row['cost_actual_microusd'] == 0)
    require(capacity['active_workers'] == 1 and capacity['probe_reservations'] == 0)


def main():
    stage = 'configuration'
    try:
        require(os.geteuid() != 0 and ROLE_DISPATCH_MAILBOX_PR == 1703)
        config = worker.load_config()
        require(config.worker_id == 'oracle-autopilot-light-1')
        require(os.environ.get('AUTOPILOT_DB_BACKEND','neon') == 'neon')
        broker = worker.load_token_broker_config()
        release = json.loads((Path.cwd()/'ops/autopilot/broker-release.json').read_text())
        require(all(getattr(broker,key) == release[value] for key,value in {
            'url':'broker_url','expected_source_sha':'broker_source_sha',
            'expected_artifact_sha256':'broker_artifact_sha256','expected_policy_sha256':'broker_policy_sha256',
            'expected_provenance_sha256':'broker_provenance_sha256'}.items()))
        stage = 'database_identity'
        with worker.psycopg.connect(config.dsn,autocommit=True,connect_timeout=10,
                row_factory=worker.dict_row,options='-c statement_timeout=5000 -c default_transaction_read_only=on') as conn:
            identity = conn.execute("SELECT current_user AS name, current_database() AS db, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
            require(identity['name'] == 'autopilot_light_worker_login' and identity['db'] == 'neondb'
                    and not any(identity[k] for k in ('rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')))
            stage = 'acl_and_fence'
            acl = conn.execute("SELECT has_table_privilege(current_user,'autopilot.task','SELECT,INSERT,UPDATE,DELETE') AS direct_task, has_table_privilege(current_user,'autopilot.role_dispatch_outbox','SELECT,INSERT,UPDATE,DELETE') AS direct_outbox, has_function_privilege(current_user,'autopilot.claim_next_task(text,integer)','EXECUTE') AS can_claim, has_function_privilege(current_user,'autopilot.claim_role_dispatch_outbox_v2(text,integer)','EXECUTE') AS can_publish").fetchone()
            require(not acl['direct_task'] and not acl['direct_outbox'] and acl['can_claim'] and acl['can_publish'])
            definition = conn.execute("SELECT pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure) AS definition").fetchone()['definition']
            require(definition.count(FENCE) == 1 and "p_worker_id = 'oracle-autopilot-light-1'" in definition)
            stage = 'queue'
            rows = conn.execute("SELECT task_id,status,attempts,goal_type,lease_until,lease_epoch,cost_reserved_microusd,cost_actual_microusd FROM autopilot.task_status WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')").fetchall()
            capacity = conn.execute('SELECT * FROM autopilot.role_worker_capacity_snapshot()').fetchone()
            validate_queue(rows,capacity)
            stage = 'manifest'
            receipt = conn.execute('SELECT * FROM autopilot.parallel_work_manifest_status(%s)',(manifest_sha256(),)).fetchone()
            validate_receipt(receipt)
        stage = 'broker_health'
        worker._require_approved_broker_release(config=broker,
            opener=urllib.request.build_opener(worker._RejectBrokerRedirects()))
        print(json.dumps({'status':'PASS','stage':'complete','mailbox_pr':1703,
            'worker_id':config.worker_id,'database_user':identity['name'],
            'fence_sha256':hashlib.sha256(definition.encode()).hexdigest(),'manifest_sha256':manifest_sha256()}))
    except Exception as exc:
        print(json.dumps({'status':'FAIL','stage':stage,'error_type':type(exc).__name__}))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
