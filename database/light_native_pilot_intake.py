"""One accepted shared pilot item, atomically registered and claimed.

This module does not publish to GitHub or authorize the service switch.  The
accepted plan and earlier full HOLD baseline come from independent review.
An uncertain commit is reconciled from the retained snapshot; it is not retried.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import time

from psycopg.pq import TransactionStatus
from oracle_autopilot import codex_cli_bridge as bridge

from database import native_cli_permission_engine as engine
from ops import oracle_light_active_hold_attest as hold
from ops import light_native_service_switch as switch
from ops.native_maintenance_agreement import Agreement
from ops.native_permission_hold_guard import EXPECTED_TARGET


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def encoded(value):
    return engine.encode(value)


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def strict(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit, 'PILOT_INTAKE_SIZE')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'PILOT_INTAKE_DUPLICATE_KEY')
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: require(False, 'PILOT_INTAKE_NONFINITE'))
    require(encoded(value) == raw, 'PILOT_INTAKE_NONCANONICAL')
    return value


def target():
    return engine.Target(**{**EXPECTED_TARGET,
        'neon': engine.NeonBinding(**EXPECTED_TARGET['neon'])})


class Plan:
    KEYS = {'version','source','repository','target_pr','expected_head_sha','work_key','objective',
            'priority','task_spec_json','branch'}

    def __init__(self, raw, accepted_digest):
        require(type(accepted_digest) is str and re.fullmatch('[0-9a-f]{64}',accepted_digest)
                and type(raw) is bytes and hashlib.sha256(raw).hexdigest()==accepted_digest,
                'PILOT_INTAKE_PLAN_NOT_ACCEPTED')
        value = strict(raw, 16384)
        require(type(value) is dict and set(value) == self.KEYS
                and type(value['version']) is int and value['version'] == 1
                and type(value['source']) is str and re.fullmatch('[0-9a-f]{40}',value['source'])
                and value['repository'] == 'olegmed1-art/bridge-video-free'
                and type(value['target_pr']) is int and 1 <= value['target_pr'] <= 1000000
                and type(value['expected_head_sha']) is str
                and re.fullmatch('[0-9a-f]{40}',value['expected_head_sha'])
                and type(value['work_key']) is str
                and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,139}',value['work_key'])
                and type(value['objective']) is str and 1 <= len(value['objective']) <= 1000
                and all(ord(letter) >= 32 for letter in value['objective'])
                and type(value['priority']) is int and value['priority'] in (0,10,20,30)
                and type(value['branch']) is str
                and re.fullmatch(r'(?:codex|autopilot|fix)/[A-Za-z0-9_./-]{1,180}',value['branch'])
                and '..' not in value['branch']
                and not value['branch'].startswith('autopilot/dispatch/'),
                'PILOT_INTAKE_PLAN_SHAPE')
        spec = value['task_spec_json']
        from oracle_autopilot.light_native_adapter import FALSE_FLAGS
        require(type(spec) is dict and len(encoded(spec)) <= 4096
                and spec.get('assignment_schema') == 'SLAVIK_DISPATCH_ASSIGNMENT_V1'
                and spec.get('repository') == value['repository']
                and spec.get('target_pr') == value['target_pr']
                and spec.get('expected_head_sha') == value['expected_head_sha']
                and spec.get('execution_mode') == 'READ_ONLY'
                and spec.get('exact_head_binding') is True
                and all(spec.get(key) is False for key in FALSE_FLAGS)
                and all(type(spec.get(key)) is int and spec[key] == 0
                        for key in ('cost_cap_microusd','max_repair_attempts')),
                'PILOT_INTAKE_SCOPE')
        self.raw,self.digest,self.value = raw,accepted_digest,value
        self.scope = {'version':1,'operation':'native_single_pilot',
                      'source':value['source'],'target':EXPECTED_TARGET,
                      'plan_sha256':accepted_digest}
        self.scope_digest = digest(self.scope)
        self.worker = 'native-single-pilot-' + accepted_digest[:24]


def baseline(plan, agreement, raw, accepted_digest):
    require(type(accepted_digest) is str and re.fullmatch('[0-9a-f]{64}',accepted_digest)
            and type(raw) is bytes and hashlib.sha256(raw).hexdigest()==accepted_digest,
            'PILOT_INTAKE_BASELINE_NOT_ACCEPTED')
    value = strict(raw, 262144)
    require(type(value) is dict and set(value) == {'version','source','package_sha256',
            'agreement_sha256','scope_sha256','prior','protected','protected_sha256',
            'observed_at'} and value['version'] == 1
            and value['source'] == plan.value['source']
            and value['agreement_sha256'] == agreement.accepted
            and value['scope_sha256'] == plan.scope_digest
            and type(value['observed_at']) is int
            and value['observed_at'] <= int(time.time()),
            'PILOT_INTAKE_BASELINE_CHANGED')
    prior = hold.HoldIdentity(**value['prior'])
    require(digest(value['protected']) == value['protected_sha256'],
            'PILOT_INTAKE_PROTECTED_DIGEST')
    require(asdict(hold.attest()) == asdict(prior), 'PILOT_INTAKE_HOLD_CHANGED')
    switch.unchanged_files(value['protected'],prior,value['protected_sha256'])
    return prior,value['protected'],value['protected_sha256']


def one(conn, statement, params=()):
    row = conn.execute(statement, params).fetchone()
    require(row is not None and len(row) == 1 and type(row[0]) is dict,
            'PILOT_INTAKE_RPC_MISSING')
    return row[0]


def prepare(conn, plan, agreement, baseline_raw, accepted_baseline_sha256, snapshot_path,
            *, target_open, observed_head_sha, observed_branch, effect_guard=None):
    """Return a private dispatch receipt; any unexpected selection rolls back.

    snapshot_path must be a new leaf under a private, durable owner directory.
    No retry path is exposed after either a lost commit ACK or retained snapshot.
    """
    require(type(plan) is Plan and type(agreement) is Agreement
            and conn.autocommit is True and conn.read_only is False
            and conn.info.transaction_status == TransactionStatus.IDLE,
            'PILOT_INTAKE_AUTHORITY')
    require(target_open is True and observed_head_sha == plan.value['expected_head_sha']
            and observed_branch == plan.value['branch'],
            'PILOT_INTAKE_HEAD_CHANGED')
    prior,protected,protected_digest = baseline(plan, agreement, baseline_raw,
                                                 accepted_baseline_sha256)
    agreement.assert_held(plan.scope_digest)
    require(isinstance(snapshot_path, Path) and not snapshot_path.exists(),
            'PILOT_INTAKE_SNAPSHOT_EXISTS')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        conn.execute("SET LOCAL statement_timeout='10s'")
        engine.identity(conn, target())
        agreement.assert_held(plan.scope_digest)
        config = one(conn, 'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton FOR UPDATE')
        role = one(conn, "SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT' FOR UPDATE")
        require(config['enabled'] is False and role['enabled'] is True
                and role['execution_scope'] == 'REPOSITORY', 'PILOT_INTAKE_CONFIG_DRIFT')
        counts = conn.execute('''SELECT
          (SELECT count(*) FROM autopilot.task WHERE status IN
             ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')),
          (SELECT count(*) FROM autopilot.native_cli_receipt WHERE state<>'TERMINAL')''').fetchone()
        require(counts == (0,0), 'PILOT_INTAKE_QUEUE_NOT_EMPTY')
        snapshot = {'version':1,'plan_sha256':plan.digest,
                    'baseline_sha256':accepted_baseline_sha256,
                    'target':EXPECTED_TARGET,'native_config':config,'autopilot_role':role}
        snapshot_digest = engine.save_manifest(snapshot_path,snapshot)
        require(engine.load_manifest(snapshot_path,snapshot_digest) == snapshot,
                'PILOT_INTAKE_SNAPSHOT_READBACK')
        agreement.assert_held(plan.scope_digest)
        if effect_guard is not None: effect_guard()
        conn.execute('UPDATE autopilot.native_cli_config SET enabled=true,cutover_at=transaction_timestamp() WHERE singleton')
        conn.execute("UPDATE autopilot.role_registry SET can_repair=false WHERE role_id='AUTOPILOT' AND enabled")
        applied_config = one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c WHERE singleton')
        applied_role = one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r WHERE role_id='AUTOPILOT'")
        require(applied_config['enabled'] is True and applied_role['enabled'] is True
                and applied_role['can_repair'] is False
                and all(applied_config.get(k)==v for k,v in config.items() if k not in ('enabled','cutover_at'))
                and all(applied_role.get(k)==v for k,v in role.items() if k not in ('can_repair','updated_at')),
                'PILOT_INTAKE_CONFIG_APPLY_DRIFT')
        work = one(conn, '''SELECT to_jsonb(x) FROM autopilot.register_universal_work_item(
          %s,'AUTOPILOT','REPOSITORY_AUDIT',%s,%s,%s,%s::jsonb,NULL,'CHATGPT_DIRECTOR','NATIVE_SINGLE_PILOT') x''',
          (plan.value['work_key'],plan.value['objective'],plan.value['target_pr'],
           plan.value['priority'],encoded(plan.value['task_spec_json']).decode()))
        require(work['created'] is True and work['state'] == 'READY', 'PILOT_INTAKE_WORK_NOT_NEW')
        probe = one(conn, 'SELECT to_jsonb(x) FROM autopilot.claim_project_work_probe(%s,60) x',
                    (plan.worker,))
        require(probe['work_item_id'] == work['work_item_id']
                and probe['work_key'] == plan.value['work_key']
                and probe['role'] == 'AUTOPILOT'
                and probe['target_pr'] == plan.value['target_pr'],
                'PILOT_INTAKE_WRONG_WORK')
        materialized = one(conn, '''SELECT to_jsonb(x) FROM autopilot.materialize_project_work_probe(
            %s::uuid,%s,%s,true,%s) x''',
            (work['work_item_id'],plan.worker,probe['lease_epoch'],observed_head_sha))
        require(materialized['created'] is True and materialized['resulting_state'] == 'ACTIVE',
                'PILOT_INTAKE_TASK_NOT_NEW')
        task = one(conn, 'SELECT to_jsonb(x) FROM autopilot.claim_next_task(%s,60) x',
                   (plan.worker,))
        require(task['task_id'] == materialized['task_id']
                and task['goal_type'] == 'CHATGPT_ROLE_DISPATCH_V1'
                and task['cost_cap_microusd'] == task['cost_reserved_microusd'] == 0,
                'PILOT_INTAKE_WRONG_TASK')
        goal=task['goal_json']
        require(type(goal) is dict and set(goal)=={'repository','mailbox_pr','role',
            'target_pr','expected_head_sha','dispatch_epoch','successor_task_key',
            'successor_role','successor_target_pr','successor_expected_head_sha'}
            and goal['repository']==plan.value['repository'] and goal['mailbox_pr']==1637
            and goal['role']=='AUTOPILOT' and goal['target_pr']==plan.value['target_pr']
            and goal['dispatch_epoch']==1
            and goal['expected_head_sha']==observed_head_sha
            and all(goal[k] is None for k in ('successor_task_key','successor_role',
                                             'successor_target_pr','successor_expected_head_sha')),
            'PILOT_INTAKE_GOAL_DRIFT')
        goal_digest=hashlib.sha256(json.dumps(goal,sort_keys=True,separators=(',',':'),
                                              ensure_ascii=False).encode()).hexdigest()
        dispatch = one(conn, 'SELECT to_jsonb(x) FROM autopilot.prepare_role_dispatch(%s::uuid,%s,%s) x',
                       (task['task_id'],plan.worker,task['lease_epoch']))
        require(dispatch['role'] == 'AUTOPILOT' and dispatch['target_pr'] == plan.value['target_pr']
                and dispatch['expected_head_sha'] == observed_head_sha,
                'PILOT_INTAKE_WRONG_DISPATCH')
        publication = one(conn, 'SELECT to_jsonb(x) FROM autopilot.claim_role_dispatch_outbox_v2(%s,300) x',
                          (plan.worker,))
        require(publication['dispatch_id'] == dispatch['dispatch_id']
                and publication['mode'] == 'READ_ONLY'
                and publication['target_pr'] == plan.value['target_pr']
                and publication['expected_head_sha'] == observed_head_sha
                and publication['task_fingerprint'] == dispatch['task_fingerprint'],
                'PILOT_INTAKE_WRONG_PUBLICATION')
        assignment = one(conn,'SELECT to_jsonb(x) FROM autopilot.get_dispatch_assignment(%s::uuid) x',
                         (dispatch['dispatch_id'],))
        require(assignment['task_id'] == task['task_id']
                and assignment['role'] == 'AUTOPILOT'
                and assignment['execution_scope'] == 'REPOSITORY'
                and assignment['can_repair'] is False
                and assignment['task_kind'] == 'REPOSITORY_AUDIT'
                and assignment['task_spec_json'] == plan.value['task_spec_json'],
                'PILOT_INTAKE_ASSIGNMENT_DRIFT')
        require(asdict(hold.service_hold_identity()) == asdict(prior),
                'PILOT_INTAKE_SERVICE_CHANGED')
        switch.unchanged_files(protected,prior,protected_digest)
        agreement.assert_held(plan.scope_digest)
        if effect_guard is not None: effect_guard()
    # This is a DB commit acknowledgement, not GitHub publication or pilot authority.
    return {'plan_sha256':plan.digest,'snapshot_sha256':snapshot_digest,
            'work_item_id':work['work_item_id'],'task_id':task['task_id'],
            'goal_json_sha256':goal_digest,
            'dispatch_id':dispatch['dispatch_id'],'claim_epoch':publication['claim_epoch'],
            'dispatch':publication,'assignment':assignment,
            'applied_config':applied_config,'applied_role':applied_role,
            'published':False,'pilot_authorized':False}


def dispatch_body(dispatch):
    """The exact READ_ONLY broker artifact body from the shared worker."""
    require(type(dispatch) is dict and dispatch.get('mode') == 'READ_ONLY',
            'PILOT_PUBLICATION_MODE')
    return '\n'.join(('AUTOPILOT_DISPATCH_V1',
        'dispatch_id='+str(dispatch['dispatch_id']),
        'dispatch_epoch='+str(dispatch['dispatch_epoch']),
        'role='+str(dispatch['role']),
        'task_fingerprint='+str(dispatch['task_fingerprint']),
        'target_pr='+str(dispatch['target_pr']),
        'mode=READ_ONLY'))


def mark_reviewed_publication(conn, plan, agreement, intake_receipt,
                              discovery_raw, accepted_discovery_sha256, *, effect_guard=None):
    """Mark only an independently accepted real GitHub discovery observation.

    `discovery_raw` is supplied by an authenticated GitHub reader after the
    external broker publishes.  This function cannot create that publication.
    """
    require(type(plan) is Plan and type(agreement) is Agreement
            and conn.autocommit is True and conn.read_only is False
            and conn.info.transaction_status == TransactionStatus.IDLE,
            'PILOT_PUBLICATION_AUTHORITY')
    require(type(accepted_discovery_sha256) is str
            and re.fullmatch('[0-9a-f]{64}',accepted_discovery_sha256)
            and type(discovery_raw) is bytes
            and hashlib.sha256(discovery_raw).hexdigest()==accepted_discovery_sha256,
            'PILOT_PUBLICATION_NOT_ACCEPTED')
    discovery = strict(discovery_raw, 65536)
    dispatch = intake_receipt['dispatch']
    expected_body = dispatch_body(dispatch)
    expected_body_sha = hashlib.sha256(expected_body.encode()).hexdigest()
    require(type(discovery) is dict and set(discovery) == {'repository','number','url',
            'state','draft','head_ref','head_sha','author_login','author_id','author_type',
            'dispatch_id','dispatch_file','dispatch_body','dispatch_body_sha256'}
            and discovery['repository']==plan.value['repository']
            and type(discovery['number']) is int and 1<=discovery['number']<=1000000
            and discovery['url']==f"https://github.com/{plan.value['repository']}/pull/{discovery['number']}"
            and discovery['state']=='open' and discovery['draft'] is True
            and discovery['head_ref']=='autopilot/dispatch/'+dispatch['dispatch_id']
            and type(discovery['head_sha']) is str
            and re.fullmatch('[0-9a-f]{40}',discovery['head_sha'])
            and discovery['author_login']=='bridge-school-oracle-autopilot[bot]'
            and discovery.get('author_id') == 322994314
            and discovery['author_type']=='Bot'
            and discovery['dispatch_id']==dispatch['dispatch_id']
            and discovery['dispatch_file']=='docs/evidence/autopilot/role-dispatch-'+
                dispatch['dispatch_id']+'.md'
            and discovery['dispatch_body']==expected_body
            and discovery['dispatch_body_sha256']==expected_body_sha,
            'PILOT_PUBLICATION_DISCOVERY_DRIFT')
    require(intake_receipt['plan_sha256']==plan.digest
            and intake_receipt['dispatch_id']==dispatch['dispatch_id']
            and intake_receipt['published'] is False
            and intake_receipt['pilot_authorized'] is False,
            'PILOT_PUBLICATION_INTAKE_CHANGED')
    agreement.assert_held(plan.scope_digest)
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        conn.execute("SET LOCAL statement_timeout='5s'")
        engine.identity(conn,target())
        current = one(conn, 'SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o '
                            'WHERE dispatch_id=%s::uuid FOR UPDATE',(dispatch['dispatch_id'],))
        require(current['status']=='CLAIMED'
                and current['claim_owner']==plan.worker
                and current['claim_epoch']==intake_receipt['claim_epoch']
                and current['task_id']==intake_receipt['task_id']
                and current['expected_head_sha']==plan.value['expected_head_sha']
                and current['task_fingerprint']==dispatch['task_fingerprint']
                and current['mode']=='READ_ONLY',
                'PILOT_PUBLICATION_DB_DRIFT')
        agreement.assert_held(plan.scope_digest)
        if effect_guard is not None: effect_guard()
        row = conn.execute('''SELECT autopilot.mark_role_dispatch_published(
            %s::uuid,%s,%s,%s,%s)''',
            (dispatch['dispatch_id'],plan.worker,intake_receipt['claim_epoch'],
             discovery['number'],expected_body_sha)).fetchone()
        require(row == (True,), 'PILOT_PUBLICATION_MARK_FENCED')
        after = one(conn,'SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o '
                         'WHERE dispatch_id=%s::uuid',(dispatch['dispatch_id'],))
        require(after['status']=='PUBLISHED' and after['delivery_contract_version']==3
                and after['github_dispatch_comment_id']==discovery['number']
                and after['dispatch_body_sha256']==expected_body_sha,
                'PILOT_PUBLICATION_READBACK')
        agreement.assert_held(plan.scope_digest)
        if effect_guard is not None: effect_guard()
    return {'dispatch_id':dispatch['dispatch_id'],'github_pull_request':discovery['number'],
            'body_sha256':expected_body_sha,'published':True,'pilot_authorized':False}


def terminal_evidence(raw, accepted_digest, plan, intake_receipt):
    require(type(raw) is bytes and type(accepted_digest) is str
            and re.fullmatch('[0-9a-f]{64}',accepted_digest)
            and hashlib.sha256(raw).hexdigest()==accepted_digest,
            'PILOT_TERMINAL_NOT_ACCEPTED')
    value=strict(raw,65536)
    require(type(value) is dict and type(value.get('request')) is dict,
            'PILOT_TERMINAL_BINDING')
    try:
        bridge.validate_request(value['request'])
    except (KeyError,TypeError,ValueError):
        raise RuntimeError('PILOT_TERMINAL_REQUEST_INVALID') from None
    require(type(value) is dict and set(value)=={'version','plan_sha256','dispatch_id',
            'task_id','provider_task_id','request','result'} and value['version']==1
            and value['plan_sha256']==plan.digest
            and value['dispatch_id']==intake_receipt['dispatch_id']
            and value['task_id']==intake_receipt['task_id']
            and type(value['provider_task_id']) is str
            and re.fullmatch(r'task_[A-Za-z0-9_]{1,120}',value['provider_task_id'])
            and type(value['request']) is dict
            and value['request'].get('dispatch_id')==intake_receipt['dispatch_id']
            and value['request'].get('expected_head_sha')==plan.value['expected_head_sha']
            and value['request'].get('target_pr')==plan.value['target_pr']
            and value['request'].get('mode')=='READ_ONLY'
            and value['request'].get('task_fingerprint')==
                intake_receipt['dispatch']['task_fingerprint']
            and value['request'].get('assignment')==intake_receipt['assignment']
            and value['request'].get('branch')==plan.value['branch'],
            'PILOT_TERMINAL_BINDING')
    result=value['result']
    require(type(result) is dict and set(result)=={'status','result_code','summary',
            'target_head_sha','provider_evidence_sha256'}
            and result['status'] in ('SUCCEEDED','BLOCKED')
            and type(result['result_code']) is str
            and re.fullmatch('[A-Z][A-Z0-9_]{0,63}',result['result_code'])
            and type(result['summary']) is str and 1<=len(result['summary'])<=160
            and all(ord(c)>=32 for c in result['summary'])
            and type(result['target_head_sha']) is str
            and re.fullmatch('[0-9a-f]{40}',result['target_head_sha'])
            and (result['status']=='BLOCKED'
                 or result['target_head_sha']==plan.value['expected_head_sha'])
            and type(result['provider_evidence_sha256']) is str
            and re.fullmatch('[0-9a-f]{64}',result['provider_evidence_sha256']),
            'PILOT_TERMINAL_RESULT')
    return value


def _terminal_rows(conn,plan,intake_receipt,evidence):
    dispatch_id=intake_receipt['dispatch_id']
    receipt=one(conn,'SELECT to_jsonb(n) FROM autopilot.native_cli_receipt n '
                     'WHERE dispatch_id=%s::uuid',(dispatch_id,))
    task=one(conn,'SELECT to_jsonb(t) FROM autopilot.task t WHERE task_id=%s::uuid',
             (intake_receipt['task_id'],))
    work=one(conn,'SELECT to_jsonb(w) FROM autopilot.project_work_item w '
             'WHERE work_item_id=%s::uuid',(intake_receipt['work_item_id'],))
    outbox=one(conn,'SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o '
               'WHERE dispatch_id=%s::uuid',(dispatch_id,))
    mapped=conn.execute('SELECT count(*) FROM autopilot.project_work_task WHERE work_item_id=%s::uuid',
                        (intake_receipt['work_item_id'],)).fetchone()
    successors=conn.execute("SELECT count(*) FROM autopilot.task WHERE goal_json->>'origin_task_id'=%s",
                            (intake_receipt['task_id'],)).fetchone()
    goal_hash=hashlib.sha256(json.dumps(task['goal_json'],sort_keys=True,
        separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    success=evidence['result']['status']=='SUCCEEDED'
    require(receipt['state']=='TERMINAL' and receipt['request']==evidence['request']
            and receipt['provider_task_id']==evidence['provider_task_id']
            and receipt['terminal']==evidence['result']
            and receipt['owner_name']==target().recipient
            and task['task_id']==intake_receipt['task_id']
            and task['status']==('DONE' if success else 'FAILED_CLOSED')
            and goal_hash==intake_receipt['goal_json_sha256']
            and all(task['goal_json'][k] is None for k in ('successor_task_key',
                'successor_role','successor_target_pr','successor_expected_head_sha'))
            and work['work_item_id']==intake_receipt['work_item_id']
            and work['last_task_id']==intake_receipt['task_id']
            and work['state']==('DONE' if success else 'BLOCKED')
            and outbox['task_id']==intake_receipt['task_id']
            and outbox['status']=='CALLBACK_ACCEPTED'
            and outbox['delivery_contract_version']==4
            and mapped==(1,) and successors==(0,),
            'PILOT_TERMINAL_DB_DRIFT')
    return success


def observe_terminal(conn,plan,intake_receipt,terminal_raw,accepted_terminal_sha256):
    """Read-only, exact native terminal and no-successor observation."""
    require(type(plan) is Plan and conn.autocommit is True and conn.read_only is True
            and conn.info.transaction_status==TransactionStatus.IDLE,
            'PILOT_TERMINAL_AUTHORITY')
    evidence=terminal_evidence(terminal_raw,accepted_terminal_sha256,plan,intake_receipt)
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='5s'")
        engine.identity(conn,target())
        success=_terminal_rows(conn,plan,intake_receipt,evidence)
    return {'plan_sha256':plan.digest,'dispatch_id':intake_receipt['dispatch_id'],
            'terminal_sha256':accepted_terminal_sha256,'success':success,
            'controls_restored':False}


def restore_controls_after_terminal(conn,plan,intake_receipt,terminal_raw,
                                    accepted_terminal_sha256,snapshot_path,*,effect_guard=None):
    """Restore only the exact pre-pilot config after a proven terminal.

    No task/receipt/journal is removed. An UNKNOWN or concurrent config edit
    refuses restoration and requires independent reconciliation.
    """
    require(type(plan) is Plan and conn.autocommit is True and conn.read_only is False
            and conn.info.transaction_status==TransactionStatus.IDLE,
            'PILOT_CONTROL_RESTORE_AUTHORITY')
    evidence=terminal_evidence(terminal_raw,accepted_terminal_sha256,plan,intake_receipt)
    before=engine.load_manifest(snapshot_path,intake_receipt['snapshot_sha256'])
    require(type(before) is dict and before['version']==1
            and before['plan_sha256']==plan.digest
            and before['target']==EXPECTED_TARGET,
            'PILOT_CONTROL_SNAPSHOT_CHANGED')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        conn.execute("SET LOCAL statement_timeout='5s'")
        engine.identity(conn,target())
        current_config=one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c '
                                'WHERE singleton FOR UPDATE')
        current_role=one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r "
                              "WHERE role_id='AUTOPILOT' FOR UPDATE")
        require(current_config==intake_receipt['applied_config']
                and current_role==intake_receipt['applied_role'],
                'PILOT_CONTROL_CONCURRENT_CHANGE')
        success=_terminal_rows(conn,plan,intake_receipt,evidence)
        if effect_guard is not None: effect_guard()
        conn.execute('UPDATE autopilot.native_cli_config SET enabled=%s,cutover_at=%s '
                     'WHERE singleton',(before['native_config']['enabled'],
                                        before['native_config']['cutover_at']))
        conn.execute("UPDATE autopilot.role_registry SET can_repair=%s "
                     "WHERE role_id='AUTOPILOT'",(before['autopilot_role']['can_repair'],))
        restored_config=one(conn,'SELECT to_jsonb(c) FROM autopilot.native_cli_config c '
                                 'WHERE singleton')
        restored_role=one(conn,"SELECT to_jsonb(r) FROM autopilot.role_registry r "
                               "WHERE role_id='AUTOPILOT'")
        require(restored_config==before['native_config']
                and all(restored_role.get(k)==v for k,v in before['autopilot_role'].items()
                        if k!='updated_at'), 'PILOT_CONTROL_RESTORE_READBACK')
        if effect_guard is not None: effect_guard()
    return {'plan_sha256':plan.digest,'dispatch_id':intake_receipt['dispatch_id'],
            'terminal_sha256':accepted_terminal_sha256,'success':success,
            'controls_restored':True}
