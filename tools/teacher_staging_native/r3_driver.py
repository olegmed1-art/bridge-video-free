"""Opt-in disposable PG18 source qualification. Default only checks file pins.

No production DSN, external DB provider, package installation or genuine receipts.
The --execute entrypoint is for one future explicitly authorized public CI job.
"""
from pathlib import Path,PurePosixPath
import argparse,hashlib,io,json,os,re,subprocess,tarfile,tempfile,threading,time

ROOT=Path(__file__).resolve().parents[1]
MANIFEST='database/fixtures/teacher0332_native_ci_manifest.json'
BRANCH='refs/heads/codex/teacher-0332-native-once-r3-20261025'
DOCKER=['docker','--host','unix:///var/run/docker.sock']
NIL="'00000000-0000-0000-0000-000000000032'::uuid"
READY_SQL=("SELECT jsonb_build_object('database',current_database(),"
    "'server_version_num',current_setting('server_version_num')::integer,"
    "'postmaster_pid',split_part(pg_read_file('postmaster.pid',0,64),E'\\n',1)::integer);")

def need(ok,code):
    if not ok:raise RuntimeError(code)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def lit(text):return "'"+text.replace("'","''")+"'"

def check_sources(root=ROOT):
    m=json.loads((root/MANIFEST).read_bytes())
    need(m['SOURCE_ONLY'] is True and m['genuine_Canon_checks']==0,'SOURCE_ONLY_MANIFEST_REQUIRED')
    for row in m['files']:
        p=(root/row['path']).resolve()
        need(p.is_relative_to(root.resolve()) and not p.is_symlink(),'SOURCE_PATH_ESCAPE')
        raw=p.read_bytes()
        need(len(raw)==row['bytes'] and digest(raw)==row['sha256'],'SOURCE_PIN_MISMATCH:'+row['path'])
    need(len(m['files'])==25 and len(m['install_order'])==16,'EXACT_SOURCE_SCOPE_REQUIRED')
    return m

def guard(m):
    need(os.name=='posix' and os.environ.get('GITHUB_ACTIONS')=='true','CI_ONLY')
    need(os.environ.get('GITHUB_REPOSITORY')==m['repository'] and os.environ.get('CI_REPOSITORY_PRIVATE')=='false','PUBLIC_EXPECTED_REPOSITORY_ONLY')
    need(os.environ.get('GITHUB_EVENT_NAME')=='push' and os.environ.get('GITHUB_REF')==BRANCH,'EXACT_FIRST_BRANCH_PUSH_ONLY')
    need(os.environ.get('GITHUB_RUN_ATTEMPT')=='1' and os.environ.get('CI_CREATED')=='true' and os.environ.get('CI_FORCED')=='false','NO_RERUN_OR_FORCE')
    need(os.environ.get('CI_BEFORE')=='0'*40,'FIRST_PUBLICATION_ONLY')
    need(re.fullmatch(r'[0-9a-f]{40}',os.environ.get('GITHUB_SHA','')) is not None,'EXACT_HEAD_REQUIRED')
    need(re.fullmatch(r'[0-9]+',os.environ.get('GITHUB_RUN_ID','')) is not None,'RUN_ID_REQUIRED')
    need(os.environ.get('CI_ACTOR')==m['repository'].split('/')[0],'OWNER_ACTOR_REQUIRED')

class Commands:
    """Bounded streams with a numeric return code on success/failure/timeout."""
    def __init__(self,deadline,env):self.deadline=deadline;self.env=env;self.records=[]
    def run(self,args,*,data=None,seconds=15,check=True):
        remaining=self.deadline-time.monotonic();need(remaining>0,'WALL_DEADLINE')
        timeout=min(seconds,remaining);parts=[bytearray(),bytearray()];totals=[0,0];overflow=threading.Event()
        p=subprocess.Popen(args,stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=self.env)
        def drain(stream,index):
            while True:
                chunk=stream.read(8192)
                if not chunk:break
                totals[index]+=len(chunk)
                parts[index].extend(chunk[:max(0,65536-len(parts[index]))])
                if totals[index]>1048576:overflow.set()
            stream.close()
        threads=[threading.Thread(target=drain,args=(p.stdout,0),daemon=True),threading.Thread(target=drain,args=(p.stderr,1),daemon=True)]
        for t in threads:t.start()
        # Checked SQL/archive inputs may exceed pipe capacity. Confirm the
        # bounded writer has finished, in addition to process/output completion.
        writer=None;input_problem=[];input_complete=threading.Event()
        if data is not None:
            def send():
                try:
                    if p.stdin.write(data)!=len(data):input_problem.append('SHORT_WRITE')
                    p.stdin.close()
                    if not input_problem:input_complete.set()
                except Exception:input_problem.append('WRITE_ERROR')
            writer=threading.Thread(target=send,daemon=True);writer.start()
        started=time.monotonic();reason=None
        while p.poll() is None:
            if overflow.is_set() or time.monotonic()-started>=timeout:
                reason='OUTPUT_LIMIT' if overflow.is_set() else 'TIMEOUT';p.kill();break
            time.sleep(.02)
        code=p.wait(timeout=3)
        for t in threads:t.join(timeout=2)
        if writer:writer.join(timeout=1)
        if overflow.is_set():reason='OUTPUT_LIMIT'
        if any(t.is_alive() for t in threads):reason='STREAM_DRAIN_UNCONFIRMED'
        if reason is None and writer:
            if writer.is_alive():reason='INPUT_STREAM_UNCONFIRMED'
            elif input_problem:reason='INPUT_STREAM_'+input_problem[0]
            elif not input_complete.is_set():reason='INPUT_STREAM_UNCONFIRMED'
        out,err=(bytes(v).decode('utf-8','replace') for v in parts)
        self.records.append(dict(program=Path(args[0]).name,exit_code=code,reason=reason,stdout_bytes=totals[0],stderr_bytes=totals[1],
            stdout_sha256=digest(bytes(parts[0])),stderr_sha256=digest(bytes(parts[1])),
            failure_stdout=out[-4000:] if code or reason else '',failure_stderr=err[-4000:] if code or reason else ''))
        need(reason is None,'COMMAND_FAILED:'+str(code)+':'+str(reason))
        if check:need(code==0,'COMMAND_FAILED:'+str(code)+':'+str(reason))
        return code,out,err

def state_path():
    root=Path(os.environ['RUNNER_TEMP']).resolve();return root/('teacher0332-'+os.environ['GITHUB_RUN_ID']+'.json')

def is_absent(code,err,selector):
    return code==1 and re.search(r'No such (?:object|container):\s*'+re.escape(selector)+r'(?:\s|$)',err) is not None

def actual_container(obj,m,image_id,run_id):
    h=obj['HostConfig'];c=obj['Config']
    need(obj['Image']==image_id and c['Image']==m['image'] and c['Labels'].get('teacher0332.run')==run_id,'ACTUAL_CONTAINER_IDENTITY_MISMATCH')
    need(h['NetworkMode']=='none' and not h.get('Binds') and not h.get('PortBindings') and h['ReadonlyRootfs'] is True,'ACTUAL_CONTAINER_ISOLATION_MISMATCH')
    need((h.get('Init') is None or h.get('Init') is False) and h.get('PidMode','')=='','ACTUAL_PRIVATE_PID1_REQUIRED')
    need((h['NanoCpus'],h['Memory'],h['PidsLimit'])==(1000000000,1073741824,128),'ACTUAL_CONTAINER_LIMITS_MISMATCH')
    expected={'/var/lib/postgresql':'512m','/var/run/postgresql':'4m','/tmp':'64m'}
    need(set(h['Tmpfs'])==set(expected) and all(type(h['Tmpfs'][key]) is str and
        len(h['Tmpfs'][key].split(','))==3 and set(h['Tmpfs'][key].split(','))=={'rw','nosuid','size='+size}
        for key,size in expected.items()),'ACTUAL_TMPFS_MISMATCH')
    need(all(x['Type']=='tmpfs' for x in obj['Mounts']),'ACTUAL_PERSISTENT_MOUNT_FORBIDDEN')
    return dict(image_id=image_id,network='none',readonly_root=True,NanoCpus=h['NanoCpus'],memory=h['Memory'],pids=h['PidsLimit'],tmpfs=h['Tmpfs'])

def wait_for_target_database(commands,cid,report):
    """A real target query on the final PID1 server; never another database.

    The pinned image creates POSTGRES_DB itself. Its temporary bootstrap server
    can accept probes before that database exists and can still stop afterwards.
    Requiring postmaster PID1 also rejects a successful query on that temporary
    child server. No CREATE DATABASE, fallback, grant or credential is issued.
    """
    readiness=dict(status='WAITING_FOR_FINAL_TARGET_POSTMASTER',database='teacher0332',attempts=[])
    report['readiness']=readiness
    for attempt in range(1,31):
        observation=dict(attempt=attempt)
        readiness['attempts'].append(observation)
        previous=len(commands.records)
        try:
            code,out,err=commands.run(DOCKER+['exec','-i',cid,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1',
                '-U','postgres','-d','teacher0332','-f','-'],data=READY_SQL.encode('utf-8'),seconds=3,check=False)
        except BaseException:
            readiness['status']='PROBE_FAILED_OR_UNKNOWN'
            if len(commands.records)>previous:
                last=commands.records[-1]
                observation.update(exit_code=last['exit_code'],reason=last['reason'])
            raise
        observation['exit_code']=code
        if commands.deadline<=time.monotonic():
            readiness['status']='WALL_DEADLINE'
            raise RuntimeError('WALL_DEADLINE')
        if code==0:
            readiness['status']='TARGET_VALIDATION_FAILED'
            try:target=json.loads(out)
            except (ValueError,TypeError) as exc:
                readiness['status']='INVALID_RESPONSE'
                raise RuntimeError('PG18_READINESS_RESPONSE_INVALID') from exc
            need(type(target) is dict and set(target)=={'database','server_version_num','postmaster_pid'},
                'PG18_READINESS_RESPONSE_INVALID')
            need(target['database']=='teacher0332','PG18_READINESS_TARGET_MISMATCH')
            need(type(target['server_version_num']) is int and 180000<=target['server_version_num']<190000,'NATIVE_PG18_REQUIRED')
            need(type(target['postmaster_pid']) is int and target['postmaster_pid']>0,'PG18_READINESS_PID_INVALID')
            observation['postmaster_pid']=target['postmaster_pid']
            if target['postmaster_pid']==1:
                readiness.update(status='PASS_FINAL_TARGET_SQL',server_version_num=target['server_version_num'],postmaster_pid=1)
                observation['state']='FINAL_TARGET_READY'
                return target['server_version_num']
            observation['state']='BOOTSTRAP_CHILD_STILL_RUNNING'
            readiness['status']='WAITING_FOR_FINAL_TARGET_POSTMASTER'
        else:
            transient=code==2 and ('database "teacher0332" does not exist' in err or
                ('connection to server on socket ' in err and
                 ('No such file or directory' in err or 'Connection refused' in err)) or
                'the database system is starting up' in err or 'the database system is shutting down' in err)
            if not transient:
                readiness['status']='UNEXPECTED_PROBE_FAILURE'
                raise RuntimeError('PG18_READINESS_PROBE_FAILED:'+str(code))
            observation['state']='TARGET_NOT_READY'
        remaining=commands.deadline-time.monotonic()
        if remaining<=0:
            readiness['status']='WALL_DEADLINE'
            raise RuntimeError('WALL_DEADLINE')
        time.sleep(min(.5,remaining))
    readiness['status']='NOT_READY_WITHIN_BOUNDED_PROBES'
    raise RuntimeError('PG18_FINAL_TARGET_NOT_READY')

def cleanup(m,state,commands):
    if not state.exists():return {'status':'NO_CONTAINER_CREATED'}
    s=json.loads(state.read_bytes());cid=s.get('container_id');name='teacher0332-'+os.environ['GITHUB_RUN_ID']
    need(s['run_id']==os.environ['GITHUB_RUN_ID'] and s['image']==m['image'],'CLEANUP_INTENT_MISMATCH')
    need(cid is None or re.fullmatch(r'[0-9a-f]{64}',cid) is not None,'CLEANUP_CID_REQUIRED')
    code,out,err=commands.run(DOCKER+['inspect',cid or name],check=False)
    if code!=0:
        need(is_absent(code,err,cid or name),'CLEANUP_INSPECT_UNKNOWN')
        return {'status':'PASS_CONTAINER_AND_TMPFS_REMOVED' if s.get('cleanup')=='PASS_CONTAINER_AND_TMPFS_REMOVED' else 'NO_OWN_CONTAINER_PRESENT'}
    obj=json.loads(out)[0]
    need(obj['Config']['Image']==m['image'] and obj['Config']['Labels'].get('teacher0332.run')==os.environ['GITHUB_RUN_ID'],
        'CLEANUP_OWNERSHIP_MISMATCH')
    need(obj['HostConfig']['NetworkMode']=='none' and not obj['HostConfig'].get('Binds'),'CLEANUP_TARGET_SCOPE_MISMATCH')
    need(all(x['Type']=='tmpfs' for x in obj['Mounts']),'UNEXPECTED_PERSISTENT_MOUNT')
    cid=obj['Id'];need(re.fullmatch(r'[0-9a-f]{64}',cid) is not None,'CLEANUP_ACTUAL_ID_REQUIRED')
    commands.run(DOCKER+['rm','--force','--volumes',cid],seconds=12)
    code,_,err=commands.run(DOCKER+['inspect',cid],check=False)
    need(is_absent(code,err,cid),'CLEANUP_ABSENCE_NOT_CONFIRMED')
    s['cleanup']='PASS_CONTAINER_AND_TMPFS_REMOVED';state.write_text(json.dumps(s)+'\n',encoding='utf-8')
    return {'status':s['cleanup'],'container_id':cid,'persistent_volumes':0}

def source_archive(m,root=ROOT):
    """Bounded, deterministic regular files only; never extract on the host."""
    need(len(m['files'])==25,'EXACT_SOURCE_SCOPE_REQUIRED')
    stream=io.BytesIO();expected={}
    with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        for row in m['files']:
            name=row['path'];parts=PurePosixPath(name).parts
            need(re.fullmatch(r'database/(?:migrations|rollback)/[A-Za-z0-9_./-]+\.sql',name) is not None and
                all(part not in ('.','..') for part in name.split('/')) and '/tmp/teacher0332-source/'+name not in expected,'ARCHIVE_PATH_INVALID')
            path=(root/name).resolve()
            need(path.is_relative_to(root.resolve()) and not (root/name).is_symlink(),'SOURCE_PATH_ESCAPE')
            need(type(row['bytes']) is int and 0<row['bytes']<=1048576,'SOURCE_SIZE_LIMIT')
            with path.open('rb') as source:raw=source.read(row['bytes']+1)
            need(len(raw)==row['bytes'] and digest(raw)==row['sha256'],'SOURCE_PIN_MISMATCH:'+name)
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644
            info.uid=info.gid=info.mtime=0
            archive.addfile(info,io.BytesIO(raw));expected['/tmp/teacher0332-source/'+name]=row['sha256']
    raw=stream.getvalue();need(len(raw)<=1048576,'SOURCE_ARCHIVE_LIMIT')
    return raw,expected

def transfer_sources(commands,cid,m,sql,report):
    raw,expected=source_archive(m,ROOT)
    # Process writes only to existing writable tmpfs; Docker daemon cp refuses
    # read-only rootfs even when its destination is covered by that tmpfs.
    command=('umask 022; test ! -e /tmp/teacher0332-source; mkdir /tmp/teacher0332-source; '
        'exec tar --extract --file=- --directory=/tmp/teacher0332-source '
        '--no-same-owner --no-same-permissions --keep-old-files')
    report['source_transfer']=dict(status='STARTED_NOT_VERIFIED',files=len(expected),archive_bytes=len(raw),archive_sha256=digest(raw))
    commands.run(DOCKER+['exec','-i',cid,'sh','-ec',command],data=raw,seconds=20)
    values=','.join('('+lit(path)+')' for path in expected)
    _,out,_=sql("SELECT jsonb_object_agg(path,encode(public.digest(pg_read_binary_file(path),'sha256'),'hex')) FROM (VALUES "+values+") AS checked(path);")
    try:actual=json.loads(out)
    except (ValueError,TypeError) as exc:raise RuntimeError('SOURCE_READBACK_INVALID') from exc
    need(type(actual) is dict and actual==expected,'SOURCE_READBACK_MISMATCH')
    report['source_transfer']['status']='PASS_ALL_25_SERVER_FILE_HASHES'

def execute(m):
    guard(m);base=Path(os.environ['RUNNER_TEMP']).resolve();state=state_path()
    need(not state.exists(),'PRIOR_OUTCOME_REQUIRES_READBACK_NO_RETRY')
    report=dict(status='FAIL_OR_UNKNOWN',SOURCE_ONLY=True,genuine_Canon_checks=0,production_provider_qualified=False,
        source_commit=os.environ['GITHUB_SHA'],manifest_sha256=digest((ROOT/MANIFEST).read_bytes()),commands=[])
    with tempfile.TemporaryDirectory(prefix='teacher0332-',dir=base) as temp:
        home=Path(temp);(home/'docker').mkdir()
        env=dict(PATH=os.environ['PATH'],HOME=str(home),DOCKER_CONFIG=str(home/'docker'),LC_ALL='C.UTF-8')
        commands=Commands(time.monotonic()+m['budgets']['driver_wall_seconds'],env)
        try:
            _,head,_=commands.run(['git','-C',str(ROOT),'rev-parse','HEAD']);need(head.strip()==os.environ['GITHUB_SHA'],'HEAD_MISMATCH')
            _,parent,_=commands.run(['git','-C',str(ROOT),'rev-parse','HEAD^']);need(parent.strip()==m['base_commit'],'BASE_MOVED')
            _,changed,_=commands.run(['git','-C',str(ROOT),'diff','--name-only','HEAD^','HEAD'])
            need(set(changed.splitlines())==set(m['publication_files']),'UNREVIEWED_PUBLICATION_DELTA')
            commands.run(DOCKER+['pull',m['image']],seconds=m['budgets']['pull_seconds'])
            _,image,_=commands.run(DOCKER+['image','inspect',m['image']]);im=json.loads(image)[0]
            need(im['Architecture']=='amd64' and im['Os']=='linux','EXPECTED_LINUX_AMD64_IMAGE')
            report['image']=dict(reference=m['image'],actual_image_id=im['Id'],architecture=im['Architecture'],repo_digests=im['RepoDigests'])
            state.write_text(json.dumps(dict(container_id=None,run_id=os.environ['GITHUB_RUN_ID'],image=m['image'],intent='CREATE_ONCE'))+'\n',encoding='utf-8')
            _,out,_=commands.run(DOCKER+['create','--name','teacher0332-'+os.environ['GITHUB_RUN_ID'],
                '--label','teacher0332.run='+os.environ['GITHUB_RUN_ID'],'--network','none','--read-only','--init=false',
                '--cpus','1','--memory','1g','--pids-limit','128',
                '--tmpfs','/var/lib/postgresql:rw,nosuid,size=512m','--tmpfs','/var/run/postgresql:rw,nosuid,size=4m',
                '--tmpfs','/tmp:rw,nosuid,size=64m','-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_DB=teacher0332',
                m['image'],'postgres','-c','max_connections=8','-c','max_wal_senders=0','-c','shared_buffers=32MB',
                '-c','statement_timeout=3000','-c','lock_timeout=1000'])
            cid=out.strip();need(re.fullmatch(r'[0-9a-f]{64}',cid) is not None,'CREATE_ACK_UNKNOWN')
            state.write_text(json.dumps(dict(container_id=cid,run_id=os.environ['GITHUB_RUN_ID'],image=m['image']))+'\n',encoding='utf-8')
            _,native_meta,_=commands.run(DOCKER+['inspect',cid])
            report['actual_container']=actual_container(json.loads(native_meta)[0],m,im['Id'],os.environ['GITHUB_RUN_ID'])
            commands.run(DOCKER+['start',cid])
            report['server_version_num']=wait_for_target_database(commands,cid,report)
            def sql(text,*,user='postgres',check=True):
                return commands.run(DOCKER+['exec','-i',cid,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1',
                    '-U',user,'-d','teacher0332','-f','-'],data=text.encode('utf-8'),check=check)
            sql('CREATE EXTENSION pgcrypto; CREATE EXTENSION btree_gist;')
            transfer_sources(commands,cid,m,sql,report)
            for path in m['install_order']:
                commands.run(DOCKER+['exec',cid,'psql','-X','-q','-v','ON_ERROR_STOP=1','-U','postgres','-d','teacher0332',
                    '-f','/tmp/teacher0332-source/'+path],seconds=20)
            report['native_install']='PASS_EXACT_SOURCE_DDL_ONLY'
            def census():
                _,out,_=sql("SELECT jsonb_build_object('schools',(SELECT count(*) FROM school),'sources',(SELECT count(*) FROM source),'candidates',(SELECT count(*) FROM analysis_candidate),'bundles',"
                    "(SELECT count(*) FROM bidding.video_canon_ai_verification_bundle),'checks',(SELECT count(*) FROM bidding.video_canon_ai_verification),"
                    "'KI',(SELECT count(*) FROM knowledge_item),'KV',(SELECT count(*) FROM knowledge_version),'activation',(SELECT count(*) FROM canon_activation),"
                    "'bindings',(SELECT count(*) FROM bidding.video_definition_store_binding));")
                result=json.loads(out);need(result==dict(schools=1,sources=0,candidates=0,bundles=0,checks=0,KI=0,KV=0,activation=0,bindings=0),
                    'UNEXPECTED_BASELINE_OR_CANON_ROWS');return result
            report['census_before']=census()
            need(sql("SELECT enabled::text||':'||generation::text FROM bidding.video_definition_store_control;")[1].strip()=='false:1','NOT_DEFAULT_DISABLED')
            vectors=[None,False,True,0,-1,9007199254740991,-9007199254740991,[],{},
                {'z':['SOURCE_ONLY',None,True],'a':'\u0411\u0440\u0438\u0434\u0436\n\t\\"'}, {'\u00e9':1,'a':2,'\U0001f600':3}]
            for value in vectors:
                raw=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
                for domain in ('DEFINITION_TEST_STATE_PROPOSAL_0_1','DEFINITION_SOURCE_ONLY_NATIVE_VECTOR'):
                    _,out,_=sql('SELECT jsonb_build_object(\'canonical\',bidding.video_definition_canonical_int('+lit(raw)+
                        '::jsonb),\'digest\',bidding.video_definition_digest('+lit(domain)+','+lit(raw)+'::jsonb));')
                    got=json.loads(out);need(got['canonical']==raw and got['digest']==digest(domain.encode()+b'\0'+raw.encode()),'NATIVE_CODEC_GOLDEN_MISMATCH')
            report['codec_vectors']=len(vectors)*2
            cases=[]
            def refuse(name,statement,message,*,state_code='23514',before='',readonly=False):
                body='BEGIN READ '+('ONLY' if readonly else 'WRITE')+"; SET LOCAL statement_timeout='3000ms'; SET LOCAL lock_timeout='1000ms';"+before
                body+="DO $probe$ BEGIN BEGIN EXECUTE "+lit(statement)+"; RAISE EXCEPTION 'EXPECTED_REFUSAL' USING ERRCODE='PT001'; "
                body+="EXCEPTION WHEN OTHERS THEN IF SQLSTATE<>"+lit(state_code)+" OR position("+lit(message)+" in SQLERRM)=0 THEN RAISE; END IF; END; END $probe$; ROLLBACK;"
                sql(body);cases.append(dict(case=name,status='PASS_REFUSED_NATIVE_SQL'))
            calls={
                'register':'SELECT bidding.register_video_definition_bundle('+NIL+','+NIL+",'{}','"+'0'*64+"')",
                'record':'SELECT bidding.record_video_definition_verification('+NIL+','+NIL+",'SOURCE_ONLY','ERROR','SOURCE_ONLY','I0','"+'0'*64+"',NULL)",
                'read':'SELECT bidding.read_current_video_definition_bundle('+NIL+','+NIL+')'}
            for name,text in calls.items():refuse('disabled_'+name,text,'DEFINITION_STORE_DISABLED')
            refuse('context_missing','SELECT bidding.read_video_definition_test_context('+NIL+')','DEFINITION_BINDING_REQUIRED')
            refuse('readonly_metadata','SELECT bidding.video_definition_metadata_fence()','DEFINITION_RW_ROOT_REQUIRED',readonly=True)
            refuse('unbounded_statement','SELECT bidding.video_definition_bounded_root()','DEFINITION_BOUNDED_TIMEOUTS_REQUIRED',before="SET LOCAL statement_timeout='0';")
            refuse('unbounded_lock','SELECT bidding.video_definition_bounded_root()','DEFINITION_BOUNDED_TIMEOUTS_REQUIRED',before="SET LOCAL lock_timeout='0';")
            refuse('enabled_missing_scope',calls['read'],'DEFINITION_NATIVE_SCOPE_BINDING_REQUIRED',before='UPDATE bidding.video_definition_store_control SET enabled=true,generation=generation+1;')
            need(sql("SELECT enabled::text||':'||generation::text FROM bidding.video_definition_store_control;")[1].strip()=='false:1','TOP_LEVEL_ROLLBACK_NOT_EXACT')
            refuse('control_delete','DELETE FROM bidding.video_definition_store_control','DEFINITION_CONTROL_IMMUTABLE_HISTORY',state_code='55000')
            refuse('control_wrong_generation','UPDATE bidding.video_definition_store_control SET enabled=true','DEFINITION_CONTROL_IMMUTABLE_HISTORY',state_code='55000')
            refuse('duplicate_keys',"SELECT bidding.video_definition_unique_json('{\"SOURCE_ONLY\":1,\"SOURCE_ONLY\":2}'::json)",'DEFINITION_DUPLICATE_JSON_KEY')
            refuse('float',"SELECT bidding.video_definition_canonical_int('1.5'::jsonb)",'DEFINITION_INTEGER_JSON_ONLY')
            refuse('integer_overflow',"SELECT bidding.video_definition_canonical_int('9007199254740992'::jsonb)",'DEFINITION_INTEGER_JSON_ONLY')
            sql('CREATE ROLE source_only_outsider LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;')
            for name,text in [('outsider_RPC',calls['read']),('outsider_DML','UPDATE bidding.video_definition_store_control SET enabled=true,generation=generation+1')]:
                code,_,err=sql("\\set VERBOSITY verbose\n"+text+';',user='source_only_outsider',check=False)
                need(code!=0 and '42501' in err,'WRONG_ROLE_NOT_PERMISSION_DENIED');cases.append(dict(case=name,status='PASS_REFUSED_NATIVE_SQL'))
            report['negative_cases']=cases;report['census_after']=census()
            commands.run(DOCKER+['exec',cid,'psql','-X','-q','-v','ON_ERROR_STOP=1','-U','postgres','-d','teacher0332',
                '-f','/tmp/teacher0332-source/database/rollback/0332_workflow_definition_bundle_store_successor.sql'],seconds=20)
            for function,expected in m['source_function_body_pins'].items():
                _,out,_=sql("SELECT encode(public.digest(convert_to(replace(prosrc,E'\\r\\n',E'\\n'),'UTF8'),'sha256'),'hex') FROM pg_proc WHERE oid="+lit('bidding.'+function+'()')+'::regprocedure;')
                need(out.strip()==expected,'ROLLBACK_BIDDING_BODY_MISMATCH')
            report['rollback']='PASS_EXACT_LEGACY_BODIES_RETAINED_OBJECTS';report['census_after_rollback']=census()
            report['status']='PASS_NATIVE_DDL_CODEC_NEGATIVE_ONLY'
        except BaseException as exc:
            report['error_type']=type(exc).__name__;report['error_code']=str(exc)[:500]
        finally:
            # Cleanup has its own bounded budget, also on an expired test deadline.
            commands.deadline=time.monotonic()+30
            try:report['cleanup']=cleanup(m,state,commands)
            except BaseException as exc:report['cleanup']=dict(status='FAIL_OR_UNKNOWN',error_type=type(exc).__name__,error_code=str(exc)[:500]);report['status']='FAIL_OR_UNKNOWN'
            report['commands']=commands.records
            print(json.dumps(report,ensure_ascii=True,separators=(',',':')))
    return 0 if report['status']=='PASS_NATIVE_DDL_CODEC_NEGATIVE_ONLY' and report['cleanup']['status']=='PASS_CONTAINER_AND_TMPFS_REMOVED' else 1

def main():
    parser=argparse.ArgumentParser();g=parser.add_mutually_exclusive_group();g.add_argument('--execute',action='store_true');g.add_argument('--cleanup-only',action='store_true');args=parser.parse_args()
    manifest=check_sources()
    if args.cleanup_only:
        guard(manifest)
        env=dict(PATH=os.environ['PATH'],LC_ALL='C.UTF-8')
        result=cleanup(manifest,state_path(),Commands(time.monotonic()+30,env))
        print(json.dumps(dict(cleanup=result,genuine_Canon_checks=0)));return 0
    if not args.execute:
        print(json.dumps(dict(status='SOURCE_PINS_ONLY_NO_RUNTIME',files=len(manifest['files']),SQL_executed=0,genuine_Canon_checks=0)));return 0
    return execute(manifest)
if __name__=='__main__':raise SystemExit(main())
