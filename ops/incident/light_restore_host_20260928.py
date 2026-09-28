"""One fixed incident recovery on verified historical libraries, no DB mutations."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import types

PHASE = 'input'
EFFECTS_POSSIBLE = False


@contextmanager
def journals(packet, manifest, auth, probe):
    from ops import native_maintenance_store as storage
    from ops import native_maintenance_snapshot as snapshot
    from ops.native_maintenance_stage_inspect import private_read
    from ops.native_maintenance_workflow_pause import Journal, encoded
    root = storage.PARENT / storage.NAME
    storage.trusted_parent(root.parent)
    info = storage.private_directory(root)
    storage.persistent_mount(root)
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = None
    try:
        auth.require((os.fstat(parent).st_dev,os.fstat(parent).st_ino)==(info.st_dev,info.st_ino),'INCIDENT_STORE')
        lock = storage.open_file(parent,'lock',os.O_RDWR)
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        auth.require(private_read(root/'VERSION',256)==storage.VERSION,'INCIDENT_STORE')
        base = root/auth.SCOPE
        before = storage.private_directory(base)
        auth.require(set(os.listdir(base))=={'operation','pause','units','manifest.json'},'INCIDENT_STORE')
        auth.require(private_read(base/'manifest.json',4*1024*1024)==manifest
                     and auth.sha(manifest)==probe.MANIFEST,'INCIDENT_MANIFEST')
        unit_info = storage.private_directory(base/'units')
        expected = {str(r['run']['run_id'])+'-'+str(r['run']['attempt'])+'.json':r for r in packet['prior_units']}
        auth.require(len(expected)==len(packet['prior_units'])==2 and set(os.listdir(base/'units'))==set(expected),
                     'INCIDENT_PRIORS')
        for name,row in expected.items():
            auth.require(private_read(base/'units'/name,4096)==encoded(row),'INCIDENT_PRIORS')
        with Journal(base/'operation',create_lock=False) as op, Journal(base/'pause',create_lock=False) as pause:
            probe.recorded_hosts(op,packet['scope'],packet['prior_units'])
            auth.require(auth.sha(snapshot.capture_locked(op,pause))==auth.PAIR,'INCIDENT_PAIR')
            yield op,pause
        for path,old in ((root,info),(base,before),(base/'units',unit_info)):
            now=storage.private_directory(path)
            auth.require((now.st_dev,now.st_ino)==(old.st_dev,old.st_ino),'INCIDENT_STORE')
    finally:
        if lock is not None: os.close(lock)
        os.close(parent)


def claim(receipt, auth):
    """Separate fixed incident claim; no replacement of the original claim/unit."""
    from ops import native_maintenance_store as storage
    from ops.native_maintenance_stage_request import ROOT, validate_namespace
    validate_namespace()
    path=ROOT/'claims'
    old=storage.private_directory(path)
    directory=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    raw=auth.encoded(receipt)
    try:
        auth.require((os.fstat(directory).st_dev,os.fstat(directory).st_ino)==(old.st_dev,old.st_ino),'INCIDENT_CLAIM')
        fd=storage.open_file(directory,auth.CLAIM+'.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.fsync(directory)
        fd=storage.open_file(directory,auth.CLAIM+'.json',os.O_RDONLY)
        with os.fdopen(fd,'rb') as stream:
            auth.require(stream.read(len(raw)+1)==raw,'INCIDENT_CLAIM')
    finally:
        os.close(directory)
    return raw


def execute(value, execution, run_id, attempt, workflow_digest, binding, auth, probe, core, life):
    global PHASE,EFFECTS_POSSIBLE
    import base64
    auth.require(type(value) is dict and set(value)=={'source','driver','request','manifest','authorization','token','credential'},
                 'INCIDENT_INPUT')
    PHASE='source'
    payload=base64.b64decode(value['source'],validate=True)
    with probe.source_runtime(payload) as root:
        from ops.native_maintenance_owner_host import loaded_runtime
        from ops.native_maintenance_run_guard import PersistentAPI
        from ops.native_maintenance_stage_request import AcceptedRequest,read_request
        with loaded_runtime(base64.b64decode(value['driver'],validate=True)) as (driver,_), PersistentAPI(value['token']) as api:
            from ops.native_maintenance_stage_inspect import inspection_packet
            from ops import native_maintenance_checkpoint_transport as rpc
            from ops import native_maintenance_coordination as coordination
            from ops import oracle_light_active_hold_attest as hold
            from ops.native_maintenance_owner_attest import parameters
            from ops.native_maintenance_workflow_pause import digest
            from database import native_cli_permission_engine as engine
            raw=base64.b64decode(value['request'],validate=True)
            auth.require(read_request(probe.REQUEST)==raw,'INCIDENT_REQUEST')
            request=AcceptedRequest(raw,probe.REQUEST,auth.SOURCE)
            packet=inspection_packet(request,probe.FAILED_RUN)
            auth.require(packet['stage']=='restore' and packet['expected_outcome']=='AFTER'
                         and digest(packet['scope'])==auth.SCOPE,'INCIDENT_REQUEST')
            manifest=base64.b64decode(value['manifest'],validate=True)
            authority_raw=value['authorization'].encode()
            authority=auth.Authority(authority_raw,execution,run_id,attempt,api,workflow_digest,host=True)
            authority.assert_live()
            supervisor=life.Supervisor(execution,types.SimpleNamespace(run_id=run_id,attempt=attempt))
            supervisor.assert_exclusive()
            accepted_hold=hold.HoldIdentity(**packet['scope']['hold'])
            target=engine.Target(**{**packet['scope']['target'], 'neon':engine.NeonBinding(**packet['scope']['target']['neon'])})
            kwargs=parameters(value['credential'])
            def connect_ro(): return probe.readonly_connection(driver.connect,kwargs)
            priors=[r['supervisor'] for r in packet['prior_units']]
            class Guard:
                receipt_accepted=False
                token=value['token']
                plan_digest=digest(packet['plan'])
                run_identity=authority.run_identity
                def assert_current(self):
                    authority.assert_current();supervisor.assert_alive()
                    auth.require(read_request(probe.REQUEST)==raw,'INCIDENT_REQUEST')
                def assert_live(self):
                    self.assert_current();authority.assert_live();supervisor.assert_exclusive()
                def assert_reconciled(self,scope,outcome):
                    auth.require(scope==auth.SCOPE and outcome=='AFTER','INCIDENT_RECONCILE_SCOPE')
                    self.assert_live()
                    coordination.WorkflowDrain(api,packet['plan'],digest(packet['plan'])).assert_drained()
                    coordination.PriorSupervisors(priors,digest(priors)).assert_drained()
                    auth.require(hold.attest()==accepted_hold,'INCIDENT_HOLD')
                    coordination.OwnedConnections(connect_ro,target,approved_hold=accepted_hold).assert_drained()
                    with connect_ro() as conn,conn.transaction():
                        auth.require(conn.execute('SHOW transaction_read_only').fetchone()==('on',),'INCIDENT_READ_ONLY')
                        conn.execute("SET LOCAL statement_timeout='5s'")
                        conn.execute("SET LOCAL lock_timeout='1s'")
                        state=engine.snapshot(conn,target);engine.dormant(state)
                        auth.require(engine.digest(state)==probe.AFTER,'INCIDENT_AFTER')
                    self.assert_current()
                    probe.source_origins(root)
            guard=Guard();guard.api=api
            channel=life.channel(0,1,binding)
            PHASE='admission'
            guard.assert_live()
            channel.send(dict(kind='INCIDENT_READY',binding=binding,authorization_digest=authority.accepted))
            auth.require(channel.receive()==dict(kind='INCIDENT_START',binding=binding,authorization_digest=authority.accepted),
                         'INCIDENT_ADMISSION')
            guard.assert_live()
            PHASE='locked_preflight'
            with journals(packet,manifest,auth,probe) as (operation,pause):
                guard.assert_reconciled(auth.SCOPE,'AFTER')
                PHASE='claim'
                receipt=dict(version=1,kind='LIGHT_INCIDENT_RESTORE_RECEIPT',authorization=json.loads(authority_raw),
                    authorization_digest=authority.accepted,execution=execution,historical_source=auth.SOURCE,
                    scope=auth.SCOPE,historical_request=probe.REQUEST,run=authority.run_identity,supervisor=supervisor.record)
                EFFECTS_POSSIBLE=True
                receipt_raw=claim(receipt,auth)
                channel.send(dict(kind='INCIDENT_RECEIPT',binding=binding,data=rpc.pack(receipt_raw)))
                auth.require(channel.receive()==dict(kind='INCIDENT_RECEIPT_ACK',binding=binding,digest=auth.sha(receipt_raw)),
                             'INCIDENT_RECEIPT_ACK_UNKNOWN')
                guard.receipt_accepted=True
                PHASE='restore'
                store=rpc.ProxyStore(channel,auth.SCOPE)
                result=core.restore(packet,operation,pause,store,guard)
                PHASE='verify'
                guard.assert_reconciled(auth.SCOPE,'AFTER')
                channel.send(dict(kind='INCIDENT_COMPLETE',binding=binding,authorization_digest=authority.accepted,
                                  receipt_digest=auth.sha(receipt_raw),result=result,supervisor=supervisor.record))
            PHASE='complete'
