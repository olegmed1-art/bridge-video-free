"""Host operations for one transient native pilot and independent HOLD restore.

The fixed supervisor (not an SSH shell) owns these operations. This module has
no standalone launch CLI. A reviewed request, armed restoration hook and live
coordination guard must be supplied by the deployment controller.
"""
from dataclasses import asdict
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import time
import tempfile

from ops import light_native_service_plan as plan
from ops import light_native_pilot_release as release
from ops import oracle_light_active_hold_attest as hold

require = plan.require
CONTROL = Path('/etc/bridge-school/light-native-pilot')
CGROUP_ROOT = Path('/sys/fs/cgroup')


def command(*args, timeout=45):
    result = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, 'PILOT_HOST_COMMAND_REFUSED')
    return result.stdout.strip()


def show(unit, fields):
    output = command('/usr/bin/systemctl', 'show', unit, *['--property='+key for key in fields])
    result = {}
    for line in output.splitlines():
        key, sep, value = line.partition('=')
        require(sep and key in fields and key not in result, 'PILOT_HOST_UNIT_FIELDS')
        result[key] = value
    require(set(result) == set(fields), 'PILOT_HOST_UNIT_FIELDS')
    return result


def unit_absent(unit):
    state = show(unit, ['LoadState','ActiveState','MainPID'])
    require(state == {'LoadState':'not-found','ActiveState':'inactive','MainPID':'0'},
            'PILOT_HOST_PRIOR_UNIT_REQUIRES_RECONCILIATION')


def expected_files(prior):
    plan.check_prior(prior)
    return {str(hold.BASE): (0o644,65536), str(hold.DROP): (0o644,4096),
             str(hold.ENV): (0o600,262144),
             prior.release+'/ops/autopilot/broker-hold.env': (0o444,4096),
             str(hold.ROUTE/'route.json'): (0o644,4096)}


def protect_snapshot(prior):
    """Private record: hashes of secrets stay only in the root request ledger."""
    identity = hold.service_hold_identity()
    require(asdict(identity) == asdict(prior), 'PILOT_HOST_HOLD_CHANGED')
    files = expected_files(prior)
    return {name: {'mode':mode,'limit':limit,'sha256':hashlib.sha256(hold.read(Path(name),mode,limit)).hexdigest()}
            for name,(mode,limit) in files.items()}


def unchanged_files(protected, prior, accepted_digest):
    files=expected_files(prior)
    require(type(protected) is dict and set(protected) == set(files)
            and hashlib.sha256(release.encoded(protected)).hexdigest() == accepted_digest,
            'PILOT_HOST_CONFIG_INVENTORY')
    for name,row in protected.items():
        require(type(row) is dict and set(row) == {'mode','limit','sha256'}
                and (row['mode'],row['limit']) == files[name], 'PILOT_HOST_CONFIG_METADATA')
        require(hashlib.sha256(hold.read(Path(name),row['mode'],row['limit'])).hexdigest() == row['sha256'],
                'PILOT_HOST_PROTECTED_CONFIG_CHANGED')


def attest_hardening(unit=hold.UNIT, *, pilot=False):
    expected={**plan.HARDENING}
    expected.pop('CPUQuota'); expected.pop('TimeoutStopSec')
    expected.update(CPUQuotaPerSecUSec='1s',TimeoutStopUSec='30s',
                    MemoryHigh='536870912',MemoryMax='805306368',
                    Restart='no' if pilot else 'on-failure')
    require(show(unit,list(expected)) == expected, 'PILOT_HOST_HARDENING_CHANGED')


def empty_runtime_backends(candidate):
    """Short-lived direct runtime connection; no pooled observer is retained."""
    import pwd
    user = pwd.getpwnam('school-autopilot')
    env = hold.env(hold.read(hold.ENV,0o600,262144))
    child_env = {'AUTOPILOT_DATABASE_URL':env['AUTOPILOT_DATABASE_URL'],
                 'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'}
    program = '''import os,sys
sys.path.insert(0,sys.argv[1])
import psycopg
from oracle_autopilot import light_native_loader as loader
with psycopg.connect(**loader.runtime_parameters(os.environ['AUTOPILOT_DATABASE_URL'])) as c:
 c.read_only=True
 loader.runtime_identity(c)
 row=c.execute("SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE usename=current_user AND pid<>pg_catalog.pg_backend_pid()").fetchone()
 if row != (0,): sys.exit(3)
print('PILOT_RUNTIME_BACKENDS_EMPTY')
'''
    def identity():
        os.setgroups([]); os.setgid(user.pw_gid); os.setuid(user.pw_uid)
    result = subprocess.run([plan.PYTHON,'-I','-B','-c',program,str(candidate)],env=child_env,
                            cwd=candidate,preexec_fn=identity,capture_output=True,timeout=25)
    require(result.returncode == 0 and result.stdout == b'PILOT_RUNTIME_BACKENDS_EMPTY\n',
            'PILOT_HOST_RUNTIME_BACKENDS_NOT_DRAINED')


def no_processes(unit):
    require(unit in (hold.UNIT,plan.PILOT_UNIT), 'PILOT_HOST_UNIT_SCOPE')
    state = show(unit,['MainPID','ControlPID','ActiveState','ControlGroup'])
    require(state['MainPID'] == state['ControlPID'] == '0'
            and state['ActiveState'] in ('inactive','failed'), 'PILOT_HOST_PROCESS_REMAINS')
    expected='/system.slice/'+unit
    require(state['ControlGroup'] in ('',expected),'PILOT_HOST_CGROUP_PATH')
    directory=CGROUP_ROOT/expected.lstrip('/')
    if directory.exists():
        # cgroup.events populated covers all descendants, not just cgroup.procs.
        events=dict(line.split() for line in (directory/'cgroup.events').read_text().splitlines())
        require(events.get('populated') == '0','PILOT_HOST_CGROUP_POPULATED')


def duration_seconds(value):
    """Parse systemd's normalized finite duration without accepting leftovers."""
    require(type(value) is str, 'PILOT_HOST_SUPERVISOR_DURATION')
    terms=re.findall(r'(\d+)(h|min|s|ms|us)',value)
    require(terms and ' '.join(n+unit for n,unit in terms)==value,
            'PILOT_HOST_SUPERVISOR_DURATION')
    factors={'h':3600,'min':60,'s':1,'ms':0.001,'us':0.000001}
    return sum(int(n)*factors[unit] for n,unit in terms)


def attest_pilot_execution(source, prior, seconds, request_digest):
    """Check systemd's accepted command and both environment files, not argv alone."""
    launch=plan.pilot_command(source,prior,seconds,request_digest=request_digest)
    arguments=launch[launch.index('--')+1:]
    state=show(plan.PILOT_UNIT,['ExecStart','Environment','EnvironmentFiles','RuntimeMaxUSec'])
    expected='{ path='+plan.PYTHON+' ; argv[]='+' '.join(arguments)+' ; ignore_errors=no ;'
    require(state['ExecStart'].startswith(expected) and state['ExecStart'].endswith(' }')
            and state['ExecStart'].count('{') == state['ExecStart'].count('}') == 1,
            'PILOT_HOST_EXECUTION_CHANGED')
    require(state['EnvironmentFiles'] == str(hold.ENV)+' (ignore_errors=no) '+
            prior.release+'/ops/autopilot/broker-hold.env (ignore_errors=no)',
            'PILOT_HOST_ENVIRONMENT_FILES_CHANGED')
    require(sorted(state['Environment'].split()) == sorted([
            'PYTHONDONTWRITEBYTECODE=1','PYTHONUNBUFFERED=1','AUTOPILOT_ADMISSION_MODE=PILOT'])
            and duration_seconds(state['RuntimeMaxUSec']) == seconds,
            'PILOT_HOST_ENVIRONMENT_CHANGED')


def arm_identity(request_digest, supervisor_script, expected_seconds):
    require(plan.ROOT/request_digest/'supervisor.py' == supervisor_script,
            'PILOT_HOST_SUPERVISOR_PATH')
    state=show(plan.SUPERVISOR_UNIT,['MainPID','ActiveState','User','Group','NoNewPrivileges',
                                    'Restart','ExecStopPost','RuntimeMaxUSec','TimeoutStopUSec'])
    require(state['MainPID'] == str(os.getpid()) and state['ActiveState'] in ('active','activating')
            and state['User'] == state['Group'] == 'root' and state['NoNewPrivileges'] == 'yes'
            and state['Restart'] == 'no', 'PILOT_HOST_SUPERVISOR_NOT_ARMED')
    expected='/usr/bin/python3 -I -S -B '+str(supervisor_script)+' restore '+request_digest
    hook=state['ExecStopPost']
    require(hook.count('{') == hook.count('}') == 1
            and hook.startswith('{ path=/usr/bin/python3 ; argv[]='+expected+' ; ignore_errors=no ;')
            and hook.endswith(' }')
            and duration_seconds(state['RuntimeMaxUSec']) == expected_seconds
            and duration_seconds(state['TimeoutStopUSec']) == 120,
            'PILOT_HOST_RESTORE_NOT_ARMED')
    row=supervisor_script.lstat()
    require(stat.S_ISREG(row.st_mode) and row.st_uid == 0 and row.st_nlink == 1
            and stat.S_IMODE(row.st_mode) == 0o600, 'PILOT_HOST_SUPERVISOR_FILE')
    return state


def deny_admission():
    """Deny first on every outcome, including expired/uncertain attempts."""
    path=CONTROL/'admission'
    raw=hold.read(path,0o640,16)
    require(raw in (b'PILOT\n',b'HOLD\n'),'PILOT_HOST_ADMISSION_DRIFT')
    row=path.lstat()
    fd,name=tempfile.mkstemp(prefix='.restore-admission-',dir=CONTROL)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),0o640)
            os.fchown(stream.fileno(),0,row.st_gid)
            stream.write(b'HOLD\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
        release.staging.fsync_directory(CONTROL)
    finally:
        if temporary.exists(): temporary.unlink()


def restore(prior, protected, candidate, *, protected_digest, request_digest, pilot_seconds, pilot_invocation=None):
    """Cleanup has no permit-renewal/main-freshness gate and never retries work.

    A config conflict or unowned unit refuses restart. The private request and
    provider/native journals are retained for separate reconciliation.
    """
    plan.check_prior(prior)
    deny_admission()
    state=show(plan.PILOT_UNIT,['LoadState','InvocationID','WorkingDirectory','Description'])
    if state['LoadState'] != 'not-found':
        require((pilot_invocation is None or state['InvocationID'] == pilot_invocation)
                and state['WorkingDirectory'] == str(candidate)
                and state['Description'] == 'Bridge native pilot '+request_digest,
                'PILOT_HOST_UNOWNED_UNIT')
        attest_hardening(plan.PILOT_UNIT,pilot=True)
        attest_pilot_execution(candidate.name,prior,pilot_seconds,request_digest)
        command('/usr/bin/systemctl','stop',plan.PILOT_UNIT)
        no_processes(plan.PILOT_UNIT)
    else:
        # Supervisor failed before stopping HOLD/starting a pilot. With no
        # loaded pilot unit, the exact unchanged original invocation needs no
        # restart and its expected database connection is not a drain failure.
        no_processes(plan.PILOT_UNIT)
        try:
            observed=hold.service_hold_identity()
        except hold.Blocked:
            observed=None
        if observed is not None and asdict(observed) == asdict(prior):
            unchanged_files(protected,prior,protected_digest)
            attest_hardening()
            return observed
    empty_runtime_backends(candidate)
    unchanged_files(protected,prior,protected_digest)
    command('/usr/bin/systemctl','start',hold.UNIT)
    # Type=simple can return before chdir/environment setup. Accept only the
    # same restored invocation through a bounded readiness interval.
    restored=show(hold.UNIT,['MainPID','InvocationID','NRestarts'])
    for attempt in range(51):
        require(show(hold.UNIT,['MainPID','InvocationID','NRestarts']) == restored,
                'PILOT_HOST_RESTORE_RESTARTED')
        try:
            observed=hold.service_hold_identity()
            require(observed.release == prior.release, 'PILOT_HOST_RESTORE_RELEASE')
            unchanged_files(protected,prior,protected_digest)
            attest_hardening()
            return observed
        except hold.Blocked:
            if attempt == 50: raise
            time.sleep(0.1)
    raise RuntimeError('PILOT_HOST_RESTORE_UNCONFIRMED')


def activate_admission():
    """Called only inside the armed supervisor after all stop/drain checks."""
    require(hold.read(CONTROL/'admission',0o640,16) == b'HOLD\n','PILOT_HOST_ALREADY_ADMITTED')
    row=(CONTROL/'admission').lstat()
    fd,name=tempfile.mkstemp(prefix='.activate-admission-',dir=CONTROL)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),0o640);os.fchown(stream.fileno(),0,row.st_gid)
            stream.write(b'PILOT\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,CONTROL/'admission')
        release.staging.fsync_directory(CONTROL)
    finally:
        if temporary.exists():temporary.unlink()


def run_once(*, prior, protected, protected_digest, source, request_digest,
             seconds, agreement, scope, dispatch_id, directory):
    """One root supervisor invocation; systemd ExecStopPost owns restoration.

    A terminal journal marker only ends waiting. The caller must independently
    verify the native receipt, task, role and continuation state after restore.
    No queue registration, database grants or provider resubmission occurs here.
    """
    from ops.native_maintenance_agreement import Agreement
    require(type(agreement) is Agreement, 'PILOT_HOST_AGREEMENT_REQUIRED')
    require(directory == plan.ROOT/request_digest, 'PILOT_HOST_REQUEST_DIRECTORY')
    candidate=plan.source_path(source)
    # Validate all launch arguments before stopping the existing service.
    launch=plan.pilot_command(source,prior,seconds,request_digest=request_digest)
    plan.supervisor_command(source,request_digest,seconds)
    def guard():
        agreement.assert_held(scope)
        unchanged_files(protected,prior,protected_digest)
    guard()
    arm_identity(request_digest,directory/'supervisor.py',seconds)
    unit_absent(plan.PILOT_UNIT)
    require(asdict(hold.service_hold_identity()) == asdict(prior), 'PILOT_HOST_PRIOR_CHANGED')
    attest_hardening()
    release.staging.require_current_main(source)
    guard()
    # The restoration hook is armed before the first service mutation.
    command('/usr/bin/systemctl','stop',hold.UNIT)
    no_processes(hold.UNIT)
    empty_runtime_backends(candidate)
    guard()
    release.staging.require_current_main(source)
    release.retain(directory/'launch-intent.json',release.encoded(dict(
        source=source,request_digest=request_digest,pilot_unit=plan.PILOT_UNIT)))
    require(hold.read(CONTROL/'admission',0o640,16)==b'HOLD\n',
            'PILOT_HOST_ALREADY_ADMITTED')
    # Lost ACK is not retried. ExecStopPost identifies this exact unit using
    # the durable request-specific Description plus candidate and hardening.
    command(*launch)
    state=show(plan.PILOT_UNIT,['MainPID','InvocationID','NRestarts','ActiveState','WorkingDirectory','Description'])
    require(state['ActiveState']=='active' and int(state['MainPID'])>0 and state['NRestarts']=='0'
            and state['WorkingDirectory']==str(candidate)
            and state['Description']=='Bridge native pilot '+request_digest,'PILOT_HOST_START_UNCONFIRMED')
    release.retain(directory/'pilot-unit.json',release.encoded(state))
    attest_pilot_execution(source,prior,seconds,request_digest)
    attest_hardening(plan.PILOT_UNIT,pilot=True)
    guard()
    release.staging.require_current_main(source)
    activate_admission()
    while True:
        guard()
        require(hold.read(CONTROL/'admission',0o640,16)==b'PILOT\n','PILOT_HOST_ADMISSION_CLOSED')
        require(show(plan.PILOT_UNIT,list(state))==state,'PILOT_HOST_PILOT_CHANGED')
        attest_hardening(plan.PILOT_UNIT,pilot=True)
        text=command('/usr/bin/journalctl','--no-pager','-o','cat','-u',plan.PILOT_UNIT,
                     '_SYSTEMD_INVOCATION_ID='+state['InvocationID'],'-n','40')
        result=plan.classify_journal(text,dispatch_id)
        if result['state']!='RUNNING':
            release.retain(directory/'outcome.json',release.encoded(result))
            return result
        time.sleep(2)
