"""Prepare locally, or supervise one explicitly accepted root-admin pilot.

No command enables/restarts the persistent Light or any workflow. No secrets are
returned, written to the packet, placed in argv or substituted into shell code.
"""
import argparse
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

# -I excludes the script directory; add only the accepted root-owned package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops import light_ci_once as ci

SOURCE_FILES = ('ops/__init__.py','ops/light_ci_once.py','ops/light_ci_once_admin.py',
                'ops/oracle_light_active_hold_attest.py','ops/light_ci_handoff.py')


def write_new(path, value, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_NOFOLLOW',0), mode)
    with os.fdopen(fd,'wb') as f:
        f.write(ci.canonical(value)+b'\n')
        f.flush()
        os.fsync(f.fileno())
    if os.name == 'posix':
        fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def original_runtime(repo, manifest_path=None):
    # Reuse ONLY the established local bundle builder. Its historical activation
    # and old probe are never invoked or changed. No lazy network fetch is allowed.
    if manifest_path is not None:
        runtime = ci.strict_json(Path(manifest_path).read_bytes())
        ci.need(set(runtime)=={'revision','files','sha256'} and runtime['revision']==ci.RELEASE
                and runtime['sha256']=='3f7736a4fa7a6796eb9fa7be10e1d01e6cd04cb803d426991c942a91f7eb1c07', 'ORIGINAL_RUNTIME_SOURCE')
        calculated = hashlib.sha256(json.dumps({k:runtime[k] for k in ('revision','files')},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        ci.need(calculated==runtime['sha256'],'ORIGINAL_RUNTIME_DIGEST')
        return runtime
    env = {**os.environ,'GIT_NO_LAZY_FETCH':'1'}
    def git(*args):
        return subprocess.run(['git',*args],cwd=repo,env=env,check=True,capture_output=True,text=True).stdout
    paths = git('ls-tree','-r','--name-only',ci.RELEASE,'oracle_autopilot','autopilot_phase3b').splitlines()
    paths = [p for p in paths if p.count('/')==1 and p.endswith('.py')]
    paths.append('ops/autopilot/broker-release.json')
    files = {p:git('show',ci.RELEASE+':'+p) for p in paths}
    manifest = json.loads(files['ops/autopilot/broker-release.json'])
    pins = {'AUTOPILOT_TOKEN_BROKER_URL':'broker_url','AUTOPILOT_TOKEN_BROKER_EXPECTED_SOURCE_SHA':'broker_source_sha',
            'AUTOPILOT_TOKEN_BROKER_EXPECTED_ARTIFACT_SHA256':'broker_artifact_sha256',
            'AUTOPILOT_TOKEN_BROKER_EXPECTED_POLICY_SHA256':'broker_policy_sha256',
            'AUTOPILOT_TOKEN_BROKER_EXPECTED_PROVENANCE_SHA256':'broker_provenance_sha256'}
    files['ops/autopilot/broker-hold.env'] = ''.join(k+'='+manifest[pins[k]]+'\n' for k in sorted(pins))
    files['ops/autopilot/previous-hold.conf'] = ('[Service]\nWorkingDirectory=/opt/bridge-school/school-autopilot-production-light/releases/'
              '3244f4d4b17ce99e58c342f01e4715436a09622b\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n')
    runtime = {'revision':ci.RELEASE,'files':files}
    runtime['sha256'] = hashlib.sha256(json.dumps(runtime,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    ci.need(runtime['sha256']=='3f7736a4fa7a6796eb9fa7be10e1d01e6cd04cb803d426991c942a91f7eb1c07','ORIGINAL_RUNTIME_SOURCE')
    return runtime


def prepare(plan, output, runtime_manifest=None):
    ci.validate_plan(plan)
    repo = Path(__file__).resolve().parents[1]
    runtime = original_runtime(repo,runtime_manifest)
    output.mkdir(exist_ok=False)
    hashes = {}
    for name in SOURCE_FILES:
        raw = (repo/name).read_bytes()
        target = output/name
        target.parent.mkdir(exist_ok=True)
        with target.open('xb') as f:
            f.write(raw)
        hashes[name] = ci.sha(raw)
    manifest = {'files':hashes,'runtime_sha256':runtime['sha256']}
    package_sha = ci.sha(ci.canonical(manifest))
    write_new(output/'source-manifest.json',manifest,0o644)
    write_new(output/'control.json',{'plan':plan,'runtime':runtime,'registration':None},0o644)
    for name, request in ci.owner_requests(plan).items():
        write_new(output/(name+'.arguments.json'),request,0o644)
    write_new(output/'LOCAL_PREPARATION.json',{'status':'PREPARED_NOT_STAGED_OR_EXECUTED',
              'package_sha256':package_sha,'plan_sha256':ci.sha(ci.canonical(plan)),
              'production_calls':0,'activation':False},0o644)
    return {'status':'PREPARED_NOT_EXECUTED','package_sha256':package_sha,
            'plan_sha256':ci.sha(ci.canonical(plan))}


def accepted_control(package, accepted_package, accepted_plan, *, reader=None, live=True):
    # Offline handoff preparation supplies a bounded local reader. Administrative
    # callers retain root-owned reads and a live window by default. Historical
    # inspection may disable only the live clock gate, never launch validation.
    reader = reader or ci.read_root
    raw = reader(package/'source-manifest.json')
    manifest = ci.strict_json(raw)
    ci.need(ci.sha(ci.canonical(manifest)) == accepted_package and set(manifest)=={'files','runtime_sha256'}
            and set(manifest['files'])==set(SOURCE_FILES), 'PACKAGE_ACCEPTANCE')
    for name, digest in manifest['files'].items():
        ci.need(ci.sha(reader(package/name))==digest,'PACKAGE_SOURCE_DRIFT')
    control = ci.strict_json(reader(package/'control.json'))
    ci.need(set(control)=={'plan','runtime','registration'} and control['registration'] is None,'CONTROL_FIELDS')
    plan = ci.validate_plan(control['plan'],live=live)
    ci.need(ci.sha(ci.canonical(plan)) == accepted_plan
            and manifest['runtime_sha256']==control['runtime']['sha256'], 'PLAN_ACCEPTANCE')
    for name, request in ci.owner_requests(plan).items():
        ci.need(reader(package/(name+'.arguments.json'))==ci.canonical(request)+b'\n', 'OWNER_REQUEST_DRIFT')
    return control


def command(plan, job, control_path, control_hash, *, probe):
    props = ci.unit_properties(plan,job,probe=probe)
    # Use normalized systemd input names; readback uses its *USec property names.
    rename = {'RuntimeMaxUSec':'RuntimeMaxSec','TimeoutStartUSec':'TimeoutStartSec','TimeoutStopUSec':'TimeoutStopSec',
              'CPUQuotaPerSecUSec':'CPUQuota'}
    argv = ['/usr/bin/systemd-run','--quiet','--wait','--no-ask-password','--expand-environment=no',
            '--unit='+(plan['unit'].replace('.service','-probe.service') if probe else plan['unit'])]
    for key,value in props.items():
        if key == 'CPUQuotaPerSecUSec':
            value = '25%'
        argv.append('--property='+rename.get(key,key)+'='+value)
    # Preserve the original0600 EnvironmentFile. Passing DSN through --setenv
    # would expose it as a unit Environment property over systemd D-Bus.
    argv += ['--property=EnvironmentFile=/etc/school-autopilot-production-light.env',
             '--setenv=AUTOPILOT_ADMISSION_MODE='+('HOLD' if probe else 'ACTIVE'),
             '--setenv=PYTHONDONTWRITEBYTECODE=1',ci.PYTHON,'-I',
             str(control_path.parent/'ops/light_ci_once.py'),'probe' if probe else 'run',
             '--control',str(control_path),'--control-sha256',control_hash]
    return argv


def child_environment():
    return {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}


def current_main():
    import urllib.request
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):
            raise ci.Blocked('MAIN_REDIRECT')
    request = urllib.request.Request('https://api.github.com/repos/'+ci.REPOSITORY+'/git/ref/heads/main',
                                    headers={'User-Agent':'school-light-ci-once'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=10) as response:
        ci.need(response.geturl()==request.full_url and response.status==200,'MAIN_RESPONSE')
        body=response.read(65537)
    ci.need(len(body)<=65536,'MAIN_RESPONSE_SIZE')
    return ci.strict_json(body)['object']['sha']


def checked_service_record(path, uid):
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        info = os.fstat(f.fileno())
        ci.need(stat.S_ISREG(info.st_mode) and info.st_uid==uid and stat.S_IMODE(info.st_mode)==0o600
                and info.st_size<16384,'SERVICE_RECORD_METADATA')
        return ci.strict_json(f.read(16384))


def root_directory(path, mode=0o755):
    if not path.exists():
        path.mkdir(mode=mode)
    info = path.lstat()
    ci.need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==mode,'ROOT_DIRECTORY')


def exited_unit(identity, plan, *, probe):
    """Read back this exact invocation and its entire cgroup after systemd wait."""
    unit = plan['unit'].replace('.service','-probe.service') if probe else plan['unit']
    ci.need(set(identity)=={'unit','invocation_id','control_group'}
            and identity['unit']==unit and identity['control_group']=='/system.slice/'+unit
            and ci.re.fullmatch('[0-9a-f]{32}',identity['invocation_id']), 'EXIT_IDENTITY')
    result = subprocess.run(['/usr/bin/systemctl','show','--all',unit,'--property=LoadState',
              '--property=ActiveState','--property=MainPID','--property=ControlPID','--property=InvocationID'],
              env=child_environment(),capture_output=True,text=True,timeout=5)
    actual = dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    ci.need(set(actual)=={'LoadState','ActiveState','MainPID','ControlPID','InvocationID'},'EXIT_READBACK')
    ci.need(actual['MainPID']==actual['ControlPID']=='0'
            and actual['ActiveState'] in ('inactive','failed')
            and (actual['InvocationID']==identity['invocation_id']
                 or actual['LoadState']=='not-found' and actual['InvocationID']==''), 'EXIT_PROCESS_REMAINS')
    group = Path('/sys/fs/cgroup')/identity['control_group'].lstrip('/')
    if group.exists():
        ci.need(not group.is_symlink() and group.is_dir(),'EXIT_CGROUP_PATH')
        for directory, children, files in os.walk(group,followlinks=False):
            ci.need(not any((Path(directory)/name).is_symlink() for name in children),'EXIT_CGROUP_LINK')
            ci.need('cgroup.procs' in files and (Path(directory)/'cgroup.procs').read_text().strip()=='',
                    'EXIT_CGROUP_NOT_EMPTY')
    return {'unit':unit,'invocation_id':identity['invocation_id'],'cgroup_empty':True,'main_pid':0}


def supervise(operation, package, accepted_package, accepted_plan, registration_path=None):
    import fcntl
    import pwd
    from ops import oracle_light_active_hold_attest as hold
    ci.need(os.name=='posix' and os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic','EXISTING_ROOT_ADMIN_REQUIRED')
    control = accepted_control(package,accepted_package,accepted_plan)
    plan = control['plan']
    # Permanent existing route lock; no DB-wide lock or book-pilot pause.
    lockfd = os.open(hold.ROUTE/'route.lock',os.O_RDONLY|os.O_NOFOLLOW)
    try:
        fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before = hold.service_hold_identity()
        ci.need(before.release==str(ci.INSTALLED),'INSTALLED_RELEASE')
        # Current main is checked using public GET, with no credential fallback.
        ci.need(current_main()==plan['source_main'],'CURRENT_MAIN')
        account = pwd.getpwnam('school-autopilot')
        root_directory(ci.ROOT)
        root_directory(ci.ROOT/'jobs')
        root_directory(ci.ROOT/'control',0o700)
        key = ci.sha(plan['task_key'].encode())
        job = ci.ROOT/'jobs'/key
        private = ci.ROOT/'control'/key
        if operation=='preflight':
            # Any retained directory is a no-replay latch, even after a failed probe.
            private.mkdir(mode=0o700)
            job.mkdir(mode=0o700)
            os.chown(job,account.pw_uid,account.pw_gid)
        else:
            ci.need(private.is_dir() and job.is_dir(),'PREFLIGHT_REQUIRED')
            proof = ci.strict_json(ci.read_root(private/'preflight.json'))
            ci.need(proof['plan_sha256']==accepted_plan and proof['package_sha256']==accepted_package
                    and proof['hold_identity']==asdict(before)
                    and 0<=(ci.utc()-ci.timestamp(proof['observed_at'])).total_seconds()<=600,'PREFLIGHT_STALE_OR_DRIFT')
            raw = ci.read_root(registration_path)
            control['registration'] = ci.strict_json(raw)
            ci.registration(control['registration'],plan)
        ci.need(hold.service_hold_identity()==before,'PRE_LAUNCH_HOLD_DRIFT')
        # Root-owned durable reservation before systemd call. Unknown launch is
        # inspect-only; this path cannot be retried, overwrite or clear records.
        write_new(private/(operation+'-intent.json'),{'plan_sha256':accepted_plan,'at':ci.utc().isoformat()})
        control_path = package/('probe-control.json' if operation=='preflight' else 'run-control.json')
        write_new(control_path,control,0o644)
        argv = command(plan,job,control_path,ci.sha(ci.canonical(control)+b'\n'),probe=operation=='preflight')
        outcome = subprocess.run(argv,env=child_environment(),capture_output=True,timeout=45 if operation=='preflight' else 185)
        # No subprocess output or exception text crosses the boundary.
        ci.need(outcome.returncode==0,'UNIT_NOT_ACCEPTED_INSPECT_ONLY')
        identity = checked_service_record(job/('probe-unit.json' if operation=='preflight' else 'run-unit.json'),account.pw_uid)
        exit_proof = exited_unit(identity,plan,probe=operation=='preflight')
        write_new(private/(operation+'-exit.json'),exit_proof)
        ci.need(hold.service_hold_identity()==before,'POST_LAUNCH_HOLD_DRIFT')
        if operation=='preflight':
            probe = checked_service_record(job/'probe-pass.json',account.pw_uid)
            ci.need(probe['plan_sha256']==accepted_plan and probe['public_head']==plan['goal']['expected_head_sha'],'PROBE_BINDING')
            write_new(private/'preflight.json',{'plan_sha256':accepted_plan,'package_sha256':accepted_package,
                       'hold_identity':asdict(before),'observed_at':ci.utc().isoformat(),'probe':probe})
            return {'status':'HOST_PREFLIGHT_PASS','plan_sha256':accepted_plan,'task_ready_created':False}
        terminal = checked_service_record(job/'terminal.json',account.pw_uid)
        ci.need(terminal['plan_sha256']==accepted_plan
                and terminal['task_id']==control['registration']['task_id'],'TERMINAL_BINDING')
        write_new(private/'runtime-result.json',terminal)
        return {'status':'RUNTIME_DONE_OWNER_ACCEPTANCE_REQUIRED','task_id':terminal['task_id'],
                'processing_activated_persistently':False,'model_cost_microusd':0}
    finally:
        os.close(lockfd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['prepare','preflight','launch','accept'])
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--package',type=Path)
    parser.add_argument('--accept-package-sha256')
    parser.add_argument('--accept-plan-sha256')
    parser.add_argument('--registration',type=Path)
    parser.add_argument('--runtime-manifest',type=Path)
    parser.add_argument('--owner-before',type=Path)
    parser.add_argument('--owner-after',type=Path)
    parser.add_argument('--owner-result',type=Path)
    parser.add_argument('--task-id')
    args = parser.parse_args()
    if args.operation=='prepare':
        ci.need(args.plan and args.output,'PREPARE_ARGUMENTS')
        print(json.dumps(prepare(ci.strict_json(args.plan.read_bytes()),args.output,args.runtime_manifest)))
    elif args.operation=='accept':
        ci.need(all((args.plan,args.owner_before,args.owner_after,args.owner_result,args.task_id)), 'ACCEPT_ARGUMENTS')
        values = [ci.strict_json(path.read_bytes()) for path in
                  (args.plan,args.owner_before,args.owner_after,args.owner_result)]
        print(json.dumps(ci.accept_owner(values[0],args.task_id,*values[1:])))
    else:
        ci.need(args.package and args.accept_package_sha256 and args.accept_plan_sha256,'ACCEPTANCE_REQUIRED')
        if args.operation=='launch':
            ci.need(args.registration is not None,'REGISTRATION_REQUIRED')
        print(json.dumps(supervise(args.operation,args.package,args.accept_package_sha256,
                                   args.accept_plan_sha256,args.registration)))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status':'NOT_ACCEPTED','code':str(exc) if isinstance(exc,ci.Blocked) else 'UNCLASSIFIED_INSPECT_ONLY'}))
        raise SystemExit(2) from None
