"""Future one-shot isolated PG/Python native qualification; default pins only.

Execution requires the exact first owner feature-branch push. No production
credentials, remote DB, host mount, registry/cache/artifact upload or retry.
"""
from pathlib import Path
import argparse,hashlib,io,json,os,re,subprocess,sys,tarfile,tempfile,time
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
import r3_driver as r3
BRANCH='refs/heads/codex/teacher-staging-native-once-20261025'
PG='postgres@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c'
PY='python@sha256:e8a38829209aea82774882e5157e1f7c3e8c8393ff53a697b3580896e57f345c'
DOCKER=r3.DOCKER
need=r3.need
def digest(raw):return hashlib.sha256(raw).hexdigest()
def sources():
    m=json.loads((HERE/'MANIFEST.json').read_bytes())
    for row in m['files']:
        p=(ROOT/row['path']).resolve();need(p.is_relative_to(ROOT) and not p.is_symlink(),'SOURCE_PATH')
        raw=p.read_bytes();need(len(raw)==row['bytes'] and digest(raw)==row['sha256'],'SOURCE_PIN:'+row['path'])
    need(m['PG_image']==PG and m['Python_base_image']==PY and m['genuine_checks']==0,'RUNTIME_PINS')
    return m
def guard(m):
    need(os.name=='posix' and os.environ.get('GITHUB_ACTIONS')=='true','CI_ONLY')
    need(os.environ.get('GITHUB_REPOSITORY')=='olegmed1-art/bridge-video-free' and os.environ.get('CI_REPOSITORY_PRIVATE')=='false','PUBLIC_REPOSITORY_ONLY')
    need(os.environ.get('GITHUB_REF')==BRANCH and os.environ.get('GITHUB_EVENT_NAME')=='push','EXACT_INITIAL_BRANCH')
    need(os.environ.get('GITHUB_RUN_ATTEMPT')=='1' and os.environ.get('CI_CREATED')=='true' and os.environ.get('CI_FORCED')=='false'
        and os.environ.get('CI_BEFORE')=='0'*40 and os.environ.get('CI_ACTOR')=='olegmed1-art','NO_RERUN_OR_FORCE')
    need(re.fullmatch('[0-9]+',os.environ.get('GITHUB_RUN_ID','')) is not None,'RUN_ID')
def ledger_path():return Path(os.environ['RUNNER_TEMP']).resolve()/('teacher-stage-'+os.environ['GITHUB_RUN_ID']+'.json')
def write_state(path,s):path.write_text(json.dumps(s)+'\n',encoding='utf8')
def inspect(commands,kind,name):
    args=DOCKER+(['volume','inspect',name] if kind=='volume' else ['image','inspect',name] if kind=='image' else ['inspect',name])
    code,out,err=commands.run(args,check=False)
    if code==0:return json.loads(out)[0]
    # A daemon/permission/parse failure is never interpreted as absence.
    exact=re.escape(name)
    volume_absent=kind=='volume' and (re.search(r'get '+exact+r': no such volume(?:\s|$)',err,re.I) or re.search(r'no such volume: '+exact+r'(?:\s|$)',err,re.I))
    image_absent=kind=='image' and re.search(r'No such image: '+exact+r'(?:\s|$)',err)
    absent=code==1 and ((kind=='container' and r3.is_absent(code,err,name)) or volume_absent or image_absent)
    need(absent,'INSPECT_OUTCOME_UNKNOWN:'+kind);return None
def volume_guard(v,s):
    need(v['Name']==s['volume'] and v['Driver']=='local' and v.get('Labels',{}).get('teacher-stage.run')==s['run_id'],'SOCKET_VOLUME_OWNER')
    need(v.get('Options')==dict(type='tmpfs',device='tmpfs',o='size=4m,nosuid,nodev,noexec,mode=0777'),'SOCKET_VOLUME_NOT_TMPFS')
def container_guard(v,s,kind,image_id):
    h=v['HostConfig'];c=v['Config'];cfg=s['limits'][kind]
    need(v['Image']==image_id and c['Labels'].get('teacher-stage.run')==s['run_id'] and c['Labels'].get('teacher-stage.kind')==kind,'CONTAINER_OWNER_IMAGE')
    need(h['NetworkMode']=='none' and h['ReadonlyRootfs'] is True and not h.get('Binds') and not h.get('PortBindings'),'CONTAINER_ISOLATION')
    need((h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'])==(cfg['cpu'],cfg['memory'],cfg['memory'],cfg['pids']),'AGGREGATE_RESOURCE_SPLIT')
    need(h.get('Privileged') is False and not h.get('CapAdd') and h.get('PidMode','')=='' and not h.get('Init'),'NO_ADDED_PRIVILEGE_OR_INIT')
    volumes=[x for x in v['Mounts'] if x['Type']=='volume'];need(len(volumes)==1 and volumes[0]['Name']==s['volume']
        and volumes[0]['Destination']=='/var/run/postgresql' and volumes[0]['RW'] is True,'EXACT_SOCKET_MOUNT')
    need(all(x['Type'] in ('tmpfs','volume') for x in v['Mounts']),'NO_BIND_OR_PERSISTENT_DATA')
    expected={'/tmp':'rw,nosuid,size=32m'}
    if kind=='pg':expected['/var/lib/postgresql']='rw,nosuid,size=512m'
    need(h.get('Tmpfs')==expected,'EXACT_TMPFS_SPLIT')
    if kind=='py':need(c.get('User')=='999:999','PY_SOCKET_UID_GID')
def cleanup(commands,path):
    if not path.exists():return dict(status='NO_OWN_RESOURCE_INTENT')
    s=json.loads(path.read_bytes());need(s['run_id']==os.environ['GITHUB_RUN_ID'],'CLEANUP_RUN_MISMATCH')
    errors=[];absent={}
    for kind in ('py','pg'):
        try:
            name=s[kind+'_name'];obj=inspect(commands,'container',name)
            if obj:
                image=s.get(kind+'_image_id');need(image is not None,'CLEANUP_IMAGE_OUTCOME_UNKNOWN')
                container_guard(obj,s,kind,image)
                commands.run(DOCKER+['rm','--force','--volumes',obj['Id']],seconds=10)
                need(inspect(commands,'container',name) is None,'CONTAINER_ABSENCE_UNKNOWN')
            absent[kind]=True
        except BaseException as exc:absent[kind]=False;errors.append(dict(resource=kind,code=str(exc)[:300]))
    if all(absent.values()):
        try:
            vol=inspect(commands,'volume',s['volume'])
            if vol:
                volume_guard(vol,s);commands.run(DOCKER+['volume','rm',s['volume']],seconds=5)
                need(inspect(commands,'volume',s['volume']) is None,'VOLUME_ABSENCE_UNKNOWN')
        except BaseException as exc:errors.append(dict(resource='volume',code=str(exc)[:300]))
    else:errors.append(dict(resource='volume',code='CONTAINER_ABSENCE_UNCONFIRMED_NO_VOLUME_DELETE'))
    if absent.get('py'):
        try:
            image=inspect(commands,'image',s['derived_tag'])
            if image:
                need(image.get('Config',{}).get('Labels',{}).get('teacher-stage.run')==s['run_id'],'DERIVED_IMAGE_OWNER')
                if s.get('py_image_id'):need(image['Id']==s['py_image_id'],'DERIVED_IMAGE_CHANGED')
                commands.run(DOCKER+['image','rm',image['Id']],seconds=8)
                need(inspect(commands,'image',s['derived_tag']) is None,'DERIVED_IMAGE_ABSENCE_UNKNOWN')
        except BaseException as exc:errors.append(dict(resource='image',code=str(exc)[:300]))
    s['cleanup']='FAIL_OR_UNKNOWN' if errors else 'PASS_OWN_CONTAINERS_SOCKET_TMPFS_AND_IMAGE_REMOVED';write_state(path,s)
    return dict(status=s['cleanup'],persistent_data_volumes=0 if not errors else 'UNKNOWN',errors=errors)
def archive_sql(m):
    b=io.BytesIO();expected={}
    with tarfile.open(fileobj=b,mode='w',format=tarfile.USTAR_FORMAT) as t:
        for name in m['SQL_files']:
            row=next(r for r in m['files'] if r['path']==name);raw=(ROOT/name).read_bytes()
            need(re.fullmatch(r'database/(migrations|rollback|proposals)/[A-Za-z0-9_./-]+\.sql',name) is not None
                and not any(p in ('.','..') for p in name.split('/')) and 0<len(raw)<=1048576,'EXACT_SQL_ARCHIVE_PATH')
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;info.uid=info.gid=info.mtime=0
            t.addfile(info,io.BytesIO(raw));expected['/tmp/teacher0332-source/'+name]=row['sha256']
    raw=b.getvalue();need(len(raw)<=1048576,'R3_SQL_ARCHIVE_CAP');return raw,expected
def execute(m):
    guard(m);state=ledger_path();need(not state.exists(),'PRIOR_OUTCOME_REQUIRES_READBACK_NO_RETRY')
    run=os.environ['GITHUB_RUN_ID'];name='teacher-stage-'+run
    s=dict(run_id=run,volume=name+'-socket',pg_name=name+'-pg',py_name=name+'-py',derived_tag=name+':once',
        limits=dict(pg=dict(cpu=750000000,memory=805306368,pids=96),py=dict(cpu=250000000,memory=268435456,pids=32)))
    report=dict(status='FAIL_OR_UNKNOWN',SOURCE_ONLY=True,genuine_checks=0,source_commit=os.environ.get('GITHUB_SHA'),commands=[])
    with tempfile.TemporaryDirectory(prefix='teacher-stage-',dir=Path(os.environ['RUNNER_TEMP']).resolve()) as tmp:
        home=Path(tmp);(home/'docker').mkdir()
        env=dict(PATH=os.environ['PATH'],HOME=str(home),DOCKER_CONFIG=str(home/'docker'),LC_ALL='C.UTF-8',DOCKER_BUILDKIT='1')
        commands=r3.Commands(time.monotonic()+291,env) # 9s reserved for inherited kill/drain bounds within300s.
        try:
            _,head,_=commands.run(['git','-C',str(ROOT),'rev-parse','HEAD']);need(head.strip()==os.environ.get('GITHUB_SHA'),'HEAD')
            _,parent,_=commands.run(['git','-C',str(ROOT),'rev-parse','HEAD^']);need(parent.strip()==m['base_commit'],'PARENT')
            _,changed,_=commands.run(['git','-C',str(ROOT),'diff','--name-only','HEAD^','HEAD']);need(set(changed.splitlines())==set(m['publication_files']),'UNREVIEWED_DELTA')
            # Immutable manifests/binaries only; combined pull budget90seconds.
            pull_end=time.monotonic()+90
            for image in (PG,PY):commands.run(DOCKER+['pull',image],seconds=max(.1,pull_end-time.monotonic()))
            for kind,image in (('pg',PG),('python_base',PY)):
                _,raw,_=commands.run(DOCKER+['image','inspect',image]);obj=json.loads(raw)[0]
                need(obj['Architecture']=='amd64' and obj['Os']=='linux','IMAGE_PLATFORM')
                expected=m['retained_PG_config_id'] if kind=='pg' else m['retained_Python_config_id']
                need(obj['Id']==expected,'IMAGE_CONFIG_PIN:'+kind)
                s[kind+'_image_id']=obj['Id']
            # Ownership ledger exists before build/volume/container mutations.
            write_state(state,s)
            commands.run(DOCKER+['build','--no-cache','--pull=false','--label','teacher-stage.run='+run,'--tag',s['derived_tag'],str(HERE)],seconds=90)
            image=inspect(commands,'image',s['derived_tag']);need(image is not None,'BUILD_ACK_UNKNOWN');s['py_image_id']=image['Id'];write_state(state,s)
            commands.run(DOCKER+['volume','create','--driver','local','--label','teacher-stage.run='+run,
                '--opt','type=tmpfs','--opt','device=tmpfs','--opt','o=size=4m,nosuid,nodev,noexec,mode=0777',s['volume']],seconds=5)
            volume_guard(inspect(commands,'volume',s['volume']),s)
            socket=['--mount','type=volume,source='+s['volume']+',target=/var/run/postgresql,volume-nocopy']
            common=['--network','none','--read-only','--init=false']+socket
            commands.run(DOCKER+['create','--name',s['pg_name'],'--label','teacher-stage.run='+run,'--label','teacher-stage.kind=pg']+common+[
                '--cpus','0.75','--memory','768m','--memory-swap','768m','--pids-limit','96','--tmpfs','/var/lib/postgresql:rw,nosuid,size=512m',
                '--tmpfs','/tmp:rw,nosuid,size=32m','-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_DB=teacher0332',PG,
                'postgres','-c','max_connections=8','-c','max_wal_senders=0','-c','shared_buffers=32MB','-c','statement_timeout=3000','-c','lock_timeout=1000'])
            pg=inspect(commands,'container',s['pg_name']);need(pg is not None,'PG_CREATE_UNKNOWN');container_guard(pg,s,'pg',s['pg_image_id'])
            commands.run(DOCKER+['start',s['pg_name']]);r3.wait_for_target_database(commands,s['pg_name'],report)
            def sql(text):return commands.run(DOCKER+['exec','-i',s['pg_name'],'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-U','postgres','-d','teacher0332','-f','-'],data=text.encode())
            sql('CREATE EXTENSION pgcrypto;CREATE EXTENSION btree_gist;')
            raw,expected=archive_sql(m)
            commands.run(DOCKER+['exec','-i',s['pg_name'],'sh','-ec','umask 022; test ! -e /tmp/teacher0332-source; mkdir /tmp/teacher0332-source; exec tar -xf - -C /tmp/teacher0332-source --no-same-owner --no-same-permissions --keep-old-files'],data=raw,seconds=20)
            values=','.join('('+r3.lit(p)+')' for p in expected)
            _,out,_=sql("SELECT jsonb_object_agg(path,encode(public.digest(pg_read_binary_file(path),'sha256'),'hex')) FROM (VALUES "+values+") AS pinned(path)")
            need(json.loads(out)==expected,'SERVER_SQL_FILE_HASHES')
            for path in m['install_order']:
                commands.run(DOCKER+['exec',s['pg_name'],'psql','-X','-q','-v','ON_ERROR_STOP=1','-U','postgres','-d','teacher0332','-f','/tmp/teacher0332-source/'+path],seconds=20)
            sql((HERE/'seed.sql').read_text('utf8'))
            _,target,_=sql("SELECT json_build_object('database_oid',(SELECT oid::text FROM pg_database WHERE datname=current_database()),'system_identifier',(pg_control_system()).system_identifier::text)")
            target=json.loads(target);report['native_target']=target
            commands.run(DOCKER+['create','--name',s['py_name'],'--label','teacher-stage.run='+run,'--label','teacher-stage.kind=py']+common+[
                '--cpus','0.25','--memory','256m','--memory-swap','256m','--pids-limit','32','--tmpfs','/tmp:rw,nosuid,size=32m',
                '-e','GITHUB_ACTIONS=true','-e','QUALIFICATION_RUN='+run,'-e','GITHUB_SHA='+os.environ['GITHUB_SHA'],
                '-e','QUALIFICATION_TARGET_JSON='+json.dumps(target),s['derived_tag'],'--execute'])
            py=inspect(commands,'container',s['py_name']);need(py is not None,'PY_CREATE_UNKNOWN');container_guard(py,s,'py',s['py_image_id'])
            code,out,err=commands.run(DOCKER+['start','--attach',s['py_name']],seconds=max(.1,commands.deadline-time.monotonic()),check=False)
            need(code==0,'NATIVE_CLIENT_EXIT:'+str(code));native=json.loads(out)
            need(native['status']=='PASS_NATIVE_CASES_N01_N23' and len(native['cases'])==23 and native['genuine_checks']==0,'NATIVE_CASES_INCOMPLETE')
            report['native']=native;report['status']='PASS_NATIVE_SYNTHETIC_CHAIN_N01_N23'
        except BaseException as exc:report.update(error_type=type(exc).__name__,error_code=str(exc)[:700])
        finally:
            commands.deadline=time.monotonic()+21 # 9s kill/drain reserve within cleanup30s.
            try:report['cleanup']=cleanup(commands,state)
            except BaseException as exc:report['cleanup']=dict(status='FAIL_OR_UNKNOWN',error_type=type(exc).__name__,code=str(exc)[:700]);report['status']='FAIL_OR_UNKNOWN'
            if report['status']=='PASS_NATIVE_SYNTHETIC_CHAIN_N01_N23' and report['cleanup']['status']=='PASS_OWN_CONTAINERS_SOCKET_TMPFS_AND_IMAGE_REMOVED':
                report['native']['cases'].append(dict(id='N24',status='PASS'));report['status']='PASS_NATIVE_SYNTHETIC_CHAIN_CLEANUP_ONLY'
            report['commands']=commands.records;print(json.dumps(report,ensure_ascii=True,separators=(',',':')))
    return 0 if report['status']=='PASS_NATIVE_SYNTHETIC_CHAIN_CLEANUP_ONLY' else 1
def main():
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group();g.add_argument('--execute',action='store_true');g.add_argument('--cleanup-only',action='store_true');a=p.parse_args()
    m=sources()
    if a.cleanup_only:
        guard(m);r=cleanup(r3.Commands(time.monotonic()+21,dict(PATH=os.environ['PATH'],LC_ALL='C.UTF-8')),ledger_path());print(json.dumps(r));return 0 if r['status'] in ('NO_OWN_RESOURCE_INTENT','PASS_OWN_CONTAINERS_SOCKET_TMPFS_AND_IMAGE_REMOVED') else 1
    if not a.execute:print(json.dumps(dict(status='SOURCE_ONLY_PINS_NO_RUNTIME',files=len(m['files']),genuine_checks=0)));return 0
    return execute(m)
if __name__=='__main__':raise SystemExit(main())
