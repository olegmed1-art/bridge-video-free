"""Fresh predecessor verification and create-only root acceptance storage.

No autonomous issuer, cursor transition, permission grant or queue producer.
The authenticated owner controller must bind its retained Plan/receipt and
verified immutable release before calling the acceptance writer.
"""
import os
import pwd
import subprocess

from database import light_native_pilot_intake as intake
from oracle_autopilot import codex_cli_bridge as bridge
from oracle_autopilot import light_native_lane as lane
from oracle_autopilot.light_native_adapter import ProviderTarget
from ops import light_native_pilot_release as release

require = lane.require


def fresh_provider_result(source, request, environment_id):
    """Use only the service profile and existing journal; never create/resume."""
    require(os.geteuid() == 0 and type(source) is str and bridge.SHA.fullmatch(source),
            'LANE_OWNER_IDENTITY')
    bridge.validate_request(request)
    ProviderTarget(environment_id)
    candidate = bridge.LIGHT_ROOT / 'releases' / source
    user = pwd.getpwnam('school-autopilot')
    def identity():
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    program = '''import sys,json
sys.path.insert(0,sys.argv[1])
from oracle_autopilot import codex_cli_bridge as bridge
request=json.loads(sys.stdin.buffer.read(65537))
binding={'profile':'light','environment_id':sys.argv[2],'repository':'olegmed1-art/bridge-video-free'}
state=bridge.LIGHT_ROOT/'runtime/codex-dispatch'
journal=bridge.lookup(request,state_dir=state,binding=binding)
if (not journal or journal.get('state')!='SUBMITTED'
    or journal.get('prompt_sha256')!=bridge.digest(bridge.prompt_for(request))):
    raise RuntimeError('LANE_OWNER_JOURNAL')
def runner(args):return bridge.run_cli(args,timeout=20,profile='light')
value=bridge._collect(request['dispatch_id'],state_dir=state,binding=binding,runner=runner)
if value.get('provider_task_id')!=journal['provider_task_id']:raise RuntimeError('LANE_OWNER_JOURNAL')
print(bridge.canonical(value))
'''
    result = subprocess.run([release.PYTHON, '-I', '-B', '-c', program, str(candidate), environment_id],
        cwd=candidate, env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'},
        preexec_fn=identity, capture_output=True, timeout=45, input=lane.encoded(request))
    require(result.returncode == 0 and len(result.stdout) <= 65536, 'LANE_OWNER_PROVIDER_READBACK')
    return bridge.parse(result.stdout.decode().strip())


def verify_terminal(conn, plan, receipt, intent_raw, terminal_raw, permit_raw):
    """No acceptance from a service marker, a supplied report, or a cached diff."""
    require(type(plan) is intake.Plan, 'LANE_OWNER_PLAN')
    intent = lane.entry(intent_raw, plan.value['source'])
    record = lane.parse(terminal_raw)
    expected = lane.acceptance_record(intent_raw, terminal_raw)
    request, result = record['request'], record['result']
    require(intent['dispatch_id'] == receipt['dispatch_id']
            and lane.digest(permit_raw) == intent['permit_sha256'], 'LANE_OWNER_BINDING')
    permit = lane.parse(permit_raw)
    require(permit['source'] == intent['source']
            and permit['dispatch'] == {k: v for k, v in request.items() if k != 'reservation_id'},
            'LANE_OWNER_BINDING')
    provider = fresh_provider_result(intent['source'], request, permit['environment_id'])
    terminal = result['terminal']
    require(provider.get('state') == 'RESULT_RETRIEVED'
            and provider.get('provider_task_id') == result['provider_task_id']
            and type(provider.get('changes')) is str and not provider['changes'].strip()
            and bridge.digest(bridge.canonical(provider)) == terminal['provider_evidence_sha256']
            and type(provider.get('report')) is dict
            and all(provider['report'].get(k) == terminal[k] for k in ('status', 'result_code', 'summary')),
            'LANE_OWNER_PROVIDER_MISMATCH')
    envelope = dict(version=1, plan_sha256=plan.digest, dispatch_id=intent['dispatch_id'],
        task_id=receipt['task_id'], provider_task_id=result['provider_task_id'],
        request=request, result=terminal)
    raw = lane.encoded(envelope)
    # Exact DB identity, native receipt, original goal/task/work, callback v4,
    # one mapped task, no successor and consistent terminal in a read-only tx.
    intake.observe_terminal(conn, plan, receipt, raw, lane.digest(raw))
    return lane.encoded(expected)


def retain_acceptance(conn, plan, receipt, sequence):
    """Persist fresh acceptance of exact on-host records; never advance/RUN.

    The authenticated owner controller supplies its retained Plan/receipt. It
    must already have verified the immutable release. No standalone CLI or
    credential input, cursor publication, permit issuance or reset is exposed.
    """
    import stat
    from ops import light_native_lane_install as install
    from ops import oracle_light_active_hold_attest as hold
    require(os.geteuid() == 0 and type(plan) is intake.Plan
            and type(sequence) is int and 0 <= sequence < lane.MAX_JOBS,
            'LANE_OWNER_IDENTITY')
    dispatch = receipt['dispatch_id']
    require(type(dispatch) is str and bridge.UUID.fullmatch(dispatch), 'LANE_OWNER_BINDING')
    user = pwd.getpwnam('school-autopilot')
    job = lane.CONTROL / 'jobs' / dispatch
    install.root_parent(job)
    permit_path = job/'permit.json'
    permit_raw = hold.read(permit_path,0o640,65536)
    meta = permit_path.lstat()
    require(meta.st_nlink == 1 and meta.st_gid == user.pw_gid, 'LANE_OWNER_PERMIT_METADATA')
    state_fd = os.open(lane.STATE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        state = os.fstat(state_fd)
        require(state.st_uid == user.pw_uid and stat.S_IMODE(state.st_mode) == 0o700,
                'LANE_OWNER_STATE_METADATA')
        def read(name):
            fd = os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=state_fd)
            with os.fdopen(fd,'rb') as stream:
                row = os.fstat(stream.fileno())
                require(stat.S_ISREG(row.st_mode) and row.st_uid == user.pw_uid
                        and stat.S_IMODE(row.st_mode) == 0o600 and row.st_nlink == 1
                        and 0 < row.st_size <= 262144, 'LANE_OWNER_RECORD_METADATA')
                raw = stream.read(262145)
            require(0 < len(raw) <= 262144, 'LANE_OWNER_RECORD_SIZE')
            return raw
        names = [f'{sequence:08d}-intent.json', f'{sequence:08d}-terminal.json']
        intent, terminal = map(read,names)
        bound = lane.entry(intent,plan.value['source'])
        require(bound['sequence'] == sequence and bound['dispatch_id'] == dispatch,
                'LANE_OWNER_BINDING')
        raw = verify_terminal(conn,plan,receipt,intent,terminal,permit_raw)
        require([read(name) for name in names] == [intent,terminal]
                and hold.read(permit_path,0o640,65536) == permit_raw,
                'LANE_OWNER_RECORD_CHANGED')
        current = lane.STATE.lstat()
        require((current.st_dev,current.st_ino) == (state.st_dev,state.st_ino), 'LANE_OWNER_STATE_CHANGED')
        accepted_path = job/'accepted-terminal.json'
        try:
            install.write_new(accepted_path,raw,0o640,user.pw_gid)
        except FileExistsError:
            # Retry always redoes primary verification; it never trusts a cached ACK.
            require(hold.read(accepted_path,0o640,4096) == raw, 'LANE_OWNER_ACCEPTANCE_CONFLICT')
        row = accepted_path.lstat()
        require(row.st_nlink == 1 and row.st_gid == user.pw_gid
                and hold.read(accepted_path,0o640,4096) == raw, 'LANE_OWNER_ACCEPTANCE_CONFLICT')
        return dict(state='ACCEPTED',dispatch_id=dispatch,sequence=sequence,
                    acceptance_sha256=lane.digest(raw))
    finally:
        os.close(state_fd)
