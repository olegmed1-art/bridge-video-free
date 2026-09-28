"""One fixed terminal generation: observe, then preserve by rename; never replay."""
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import time
from ops import light_native_service_controller as control
from ops import light_native_service_switch as switch
from ops import light_native_pilot_release as release
from ops import oracle_light_active_hold_attest as hold
from ops.native_maintenance_agreement import Agreement

SOURCE = '526d344da98adb9da85ecaabd91c2d183a016bb6'
PACKAGE = '9330ec7c45c1b2024af2652f127159bfce408dde9c54a8c7b2128c374b304e32'
REQUEST = 'da822b23cf92e7ba69c94258692b7c7cadebe84538c4b54a8bdce320c780db46'
DISPATCH = '9289ad56-f0aa-4683-be0c-101ec820d412'
TASK = 'd8595f4c-4c02-43d0-9de7-9f377a7aa0c4'
WORK = '6bda2dca-d0a1-4783-bbb2-d4006ed3e536'
PROVIDER = 'task_e_6aba7c5b6d5c8323ad13a01a7499dc41'
TERMINAL = 'f451b910b7aa7fa471cae3a0e73a07751442f5e27b4d81ad5c0b45e872299086'
EVIDENCE = 'fe0191a8946e251425b75fe40521a1cfb4787382f39e586863be81fe40c6d1c8'
SUPERVISOR = '8b703b9ba6504c61913cd11293b644f8'
SCOPE = dict(version=1, operation='retire_completed_native_pilot_generation', source=SOURCE,
             request_sha256=REQUEST, terminal_sha256=TERMINAL, db_writes=False, provider_submit=False)
RECEIPTS = Path('/var/lib/bridge-light-retirement-da822b23cf92')
SUFFIX = '-completed-' + REQUEST[:12]
require = release.require
sha = control.digest

def identity(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
canonical = control.canonical


def inventory(root):
    """Bounded regular-file inventory. Reject links and unsafe path types before descent."""
    rows = {}
    total = 0
    def walk(path):
        nonlocal total
        require(len(rows) < 512, 'RETIRE_INVENTORY_COUNT')
        st = path.lstat()
        require(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode), 'RETIRE_INVENTORY_TYPE')
        require(not stat.S_ISREG(st.st_mode) or st.st_nlink == 1, 'RETIRE_INVENTORY_LINK')
        name = '.' if path == root else path.relative_to(root).as_posix()
        value = dict(inode=st.st_ino, uid=st.st_uid, gid=st.st_gid,
                     mode=stat.S_IMODE(st.st_mode), kind='directory' if stat.S_ISDIR(st.st_mode) else 'file')
        if stat.S_ISREG(st.st_mode):
            total += st.st_size
            require(total <= 8*1024*1024, 'RETIRE_INVENTORY_SIZE')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as f:
                require(identity(os.fstat(f.fileno())) == identity(st), 'RETIRE_INVENTORY_RACE')
                data = f.read(st.st_size + 1)
                require(len(data) == st.st_size and identity(path.lstat()) == identity(st), 'RETIRE_INVENTORY_RACE')
            value['sha256'] = sha(data)
        rows[name] = value
        if stat.S_ISDIR(st.st_mode):
            for child in sorted(path.iterdir()): walk(child)
    walk(root)
    return rows


def absent(path):
    require(not path.exists() and not path.is_symlink(), 'RETIRE_DESTINATION_EXISTS')


def db_observe(psycopg, parameters, credential, intake):
    with psycopg.connect(**parameters(credential), autocommit=True) as conn:
        conn.read_only = True
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            intake.engine.identity(conn, intake.target())
            rows = {}
            for name, table, key, val in [('native','native_cli_receipt','dispatch_id',DISPATCH),
                    ('task','task','task_id',TASK),('work','project_work_item','work_item_id',WORK),
                    ('outbox','role_dispatch_outbox','dispatch_id',DISPATCH)]:
                rows[name] = intake.one(conn, 'SELECT to_jsonb(t) FROM autopilot.'+table+' t WHERE '+key+'=%s::uuid',(val,))
            rows['config'] = intake.one(conn,'SELECT to_jsonb(t) FROM autopilot.native_cli_config t WHERE singleton')
            rows['role'] = intake.one(conn,"SELECT to_jsonb(t) FROM autopilot.role_registry t WHERE role_id='AUTOPILOT'")
            rows['counts'] = list(conn.execute("""SELECT
                (SELECT count(*) FROM autopilot.task WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')),
                (SELECT count(*) FROM autopilot.native_cli_receipt WHERE state<>'TERMINAL'),
                (SELECT count(*) FROM autopilot.task WHERE goal_json->>'origin_task_id'=%s),
                (SELECT count(*) FROM autopilot.project_work_task WHERE work_item_id=%s::uuid)""",(TASK,WORK)).fetchone())
    return rows


def validate_db(rows, terminal, original):
    require(rows['counts'] == [0,0,0,1], 'RETIRE_ACTIVE_OR_SUCCESSOR')
    native = rows['native']
    require(native['state']=='TERMINAL' and native['provider_task_id']==PROVIDER
            and native['request']==terminal['request'] and native['terminal']==terminal['result'], 'RETIRE_NATIVE_CHANGED')
    require(terminal['result']['status']=='SUCCEEDED' and terminal['result']['result_code']=='AUDIT_PASSED'
            and terminal['result']['provider_evidence_sha256']==EVIDENCE, 'RETIRE_TERMINAL_RESULT')
    require(rows['task']['status']=='DONE' and rows['work']['state']=='DONE'
            and rows['work']['last_task_id']==TASK and rows['outbox']['status']=='CALLBACK_ACCEPTED'
            and rows['outbox']['delivery_contract_version']==4, 'RETIRE_TASK_NOT_CLOSED')
    require(rows['config']==original['native_config'] and rows['config']['enabled'] is False
            and all(rows['role'].get(k)==v for k,v in original['autopilot_role'].items() if k!='updated_at'), 'RETIRE_CONTROLS_CHANGED')


def service_canary():
    """Read the already-created canary; no submission, private CLI text or auth copying."""
    user = pwd.getpwnam('school-autopilot')
    def identity():
        os.setgroups([]); os.setgid(user.pw_gid); os.setuid(user.pw_uid)
    program = '''import sys,hashlib,json
sys.path.insert(0,sys.argv[1])
from oracle_autopilot import codex_cli_bridge as b
task='task_e_6aba8e6036ec83238d9c7806f8e70590'
s=b.run_cli(['cloud','status',task],timeout=20,profile='light')
d=b.run_cli(['cloud','diff',task,'--attempt','1'],timeout=20,profile='light')
assert s.returncode==0 and s.stdout.startswith('[READY]')
assert d.returncode==0 and not d.stderr and len(d.stdout.encode())==291
assert hashlib.sha256(d.stdout.encode()).hexdigest()=='51c9ed41a9d79674fc5cd95adab55264805ba900816d7291f8a077d6ebc7a2b0'
print('SERVICE_PROFILE_CANARY_DIFF_VERIFIED')
'''
    r = subprocess.run([release.PYTHON,'-I','-B','-c',program,str(control.plan.source_path(SOURCE))],
        cwd='/',env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},preexec_fn=identity,
        capture_output=True,timeout=50)
    require(r.returncode==0 and r.stdout==b'SERVICE_PROFILE_CANARY_DIFF_VERIFIED\n', 'RETIRE_SERVICE_CANARY_UNVERIFIED')


def archive_sources(sources, archives, inventories, guard):
    for src,dst,expected in zip(sources,archives,inventories,strict=True):
        guard(); absent(dst)
        require(inventory(src)==expected and src.parent.stat().st_dev==dst.parent.stat().st_dev,'RETIRE_SOURCE_CHANGED')
        os.rename(src,dst); release.staging.fsync_directory(src.parent)
        absent(src); require(inventory(dst)==expected,'RETIRE_ARCHIVE_READBACK')


def valid_command(value, expected):
    return value.startswith(expected) and value.endswith(' }') and value.count('{')==value.count('}')==1


def reconcile(package_raw, payload_raw, accepted, wheels, credential, token, run_guard, unused):
    require(sha(payload_raw)==accepted, 'RETIRE_PAYLOAD')
    p = control.strict_json(payload_raw,262144)
    require(p.get('action') in ('observe','retire'), 'RETIRE_ACTION')
    require(set(p)==({'action'} if p['action']=='observe' else {'action','observation_sha256','agreement','accepted_agreement_sha256'}), 'RETIRE_FIELDS')
    run_guard.assert_running()
    package = control.verified_package(package_raw,SOURCE,PACKAGE)
    prior = control.stage_observation(SOURCE,package)
    require(asdict(hold.attest())==asdict(prior), 'RETIRE_HOLD_CHANGED')
    req, old_prior, protected, pdigest, directory = control.ledger(REQUEST)
    require(control.restored_receipt(req,old_prior,protected,pdigest,directory) is not None, 'RETIRE_RESTORE_MISSING')
    root = control.plan.ROOT
    raw = control.read(root/'intake'/'recovered-terminal.json')
    require(sha(raw)==TERMINAL, 'RETIRE_TERMINAL_DIGEST')
    terminal = control.strict_json(raw,262144)
    require(sha(control.read(root/'recovered-cloud-evidence.json'))==EVIDENCE, 'RETIRE_EVIDENCE_DIGEST')
    completed = control.strict_json(control.read(root/'log-recovery-completed.json'),4096)
    require(completed['task_done'] is True and completed['controls_restored'] is True and completed['native_transport_verified'] is False and completed['terminal_sha256']==TERMINAL, 'RETIRE_RECOVERY_INCOMPLETE')
    sources = [root,switch.CONTROL,control.CLAIM]
    archives = [x.with_name(x.name+SUFFIX) for x in sources]
    for path in archives: absent(path)
    absent(RECEIPTS)
    require(hold.read(switch.CONTROL/'admission',0o640,16)==b'HOLD\n', 'RETIRE_ADMISSION')
    switch.unit_absent(control.plan.PILOT_UNIT)
    switch.no_processes(control.plan.PILOT_UNIT)
    fields = ['LoadState','ActiveState','SubState','MainPID','ControlPID','InvocationID','Restart','ExecStart','ExecStopPost']
    supervisor = switch.show(control.plan.SUPERVISOR_UNIT,fields)
    require(supervisor['LoadState']=='loaded' and supervisor['ActiveState']==supervisor['SubState']=='failed'
        and supervisor['MainPID']==supervisor['ControlPID']=='0' and supervisor['InvocationID']==SUPERVISOR
        and supervisor['Restart']=='no', 'RETIRE_SUPERVISOR_CHANGED')
    script = str(directory/'supervisor.py')
    for key,action in [('ExecStart','run'),('ExecStopPost','restore')]:
        expected = '{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I -S -B '+script+' '+action+' '+REQUEST+' ; ignore_errors=no ;'
        require(valid_command(supervisor[key],expected), 'RETIRE_SUPERVISOR_COMMAND')
    cgroup = Path('/sys/fs/cgroup/system.slice')/control.plan.SUPERVISOR_UNIT
    require(not cgroup.exists() or dict(line.split() for line in (cgroup/'cgroup.events').read_text().splitlines()).get('populated')=='0', 'RETIRE_SUPERVISOR_PROCESS')
    lock_fd = os.open(control.CLAIM/'pilot.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(lock_fd,'rb') as lock:
        require(stat.S_ISREG(os.fstat(lock.fileno()).st_mode), 'RETIRE_LOCK_TYPE')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with __import__('ops.light_native_pilot_owner',fromlist=['loaded_runtime']).loaded_runtime(wheels) as (psycopg,_):
            from database import light_native_pilot_intake as intake
            from ops.native_maintenance_owner_attest import parameters
            original = intake.engine.load_manifest(root/'intake'/'before.json','3911c8aeee1264331055ccfe66865fa77551be32d887f10a4b5867c0de75ee78')
            rows = db_observe(psycopg,parameters,credential,intake)
            validate_db(rows,terminal,original)
            inventories = [inventory(x) for x in sources]
            journal = control.plan.LIGHT/'runtime/codex-dispatch'
            journal_inventory = inventory(journal)
            observation = dict(version=1,source=SOURCE,request_sha256=REQUEST,db_sha256=sha(canonical(rows)),
                hold=asdict(prior),supervisor=supervisor,inventories=inventories,journal=journal_inventory)
            observed = sha(canonical(observation))
            service_canary()
            if p['action']=='observe':
                return dict(audit='LIGHT_COMPLETED_GENERATION_OBSERVED',observation_sha256=observed,
                    service_profile_canary_diff_verified=True,task_done=True,queue_empty=True,db_writes=False)
            require(p['observation_sha256']==observed, 'RETIRE_OBSERVATION_CHANGED')
            agreement = Agreement(p['agreement'],p['accepted_agreement_sha256'],SCOPE)
            def guard():
                run_guard.assert_running(); agreement.assert_held(sha(canonical(SCOPE)))
                release.staging.require_current_main(SOURCE)
                require(asdict(hold.attest())==asdict(prior), 'RETIRE_HOLD_CHANGED')
                require(db_observe(psycopg,parameters,credential,intake)==rows,'RETIRE_DB_CHANGED')
                switch.unit_absent(control.plan.PILOT_UNIT); switch.no_processes(control.plan.PILOT_UNIT)
                require(inventory(journal)==journal_inventory,'RETIRE_JOURNAL_CHANGED')
            guard()
            control.new_directory(RECEIPTS,0,0,0o700)
            control.retained(RECEIPTS/'observation.json',canonical(observation))
            control.retained(RECEIPTS/'intent.json',payload_raw)
            archive_sources(sources,archives,inventories,guard)
            guard()
            require(switch.show(control.plan.SUPERVISOR_UNIT,fields)==supervisor,'RETIRE_SUPERVISOR_CHANGED')
            switch.command('/usr/bin/systemctl','reset-failed',control.plan.SUPERVISOR_UNIT)
            for _ in range(50):
                state=switch.show(control.plan.SUPERVISOR_UNIT,['LoadState','ActiveState','MainPID'])
                if state=={'LoadState':'not-found','ActiveState':'inactive','MainPID':'0'}: break
                time.sleep(.1)
            switch.unit_absent(control.plan.SUPERVISOR_UNIT)
            for dst,expected in zip(archives,inventories): require(inventory(dst)==expected,'RETIRE_ARCHIVE_FINAL')
            guard()
            result=dict(audit='LIGHT_COMPLETED_GENERATION_ARCHIVED',observation_sha256=observed,
                archives=[str(x) for x in archives],service_profile_canary_diff_verified=True,db_writes=False,pilot_submitted=False)
            control.retained(RECEIPTS/'completed.json',canonical(result))
            return result
