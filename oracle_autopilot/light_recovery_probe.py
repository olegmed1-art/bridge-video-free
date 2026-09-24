"""Read-only compatibility probe for the stopped, failed-closed Light canary."""
import hashlib
import json
import os
from pathlib import Path
import urllib.request

from . import worker
from .contract import ROLE_DISPATCH_MAILBOX_PR
from .light_runtime_probe import CANARY, FENCE, require, validate_identity
from .parallel_work_intake import manifest_sha256
from .rollout_verify import validate_receipt

RETIRED_FENCE_SHA = '44722551c605e775fefde91ad82f15fd08d713ad8903f65265435ead5567c36e'


def validate_queue(rows, capacity):
    require(len(rows) == 1)
    row = rows[0]
    require(str(row['task_id']) == CANARY and row['status'] == 'FAILED_CLOSED'
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
            definition = conn.execute("SELECT pg_get_functiondef("
                "'autopilot.claim_next_task(text,integer)'::regprocedure) AS definition").fetchone()['definition']
            require(FENCE not in definition and hashlib.sha256(definition.encode()).hexdigest() == RETIRED_FENCE_SHA)
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
            'fence_sha256': RETIRED_FENCE_SHA, 'manifest_sha256': manifest_sha256()}))
    except Exception as exc:
        print(json.dumps({'status': 'FAIL', 'stage': stage, 'error_type': type(exc).__name__}))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
