"""Independent synthetic Linux fault tests; no production imports or transports.

Each process test has an external test-only deadline. The guardian under test
does not supervise its own test oracle. Temporary process identities are pinned
before cleanup, and no system/service process is targeted.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from ops import light_native_retirement as retirement


pytestmark = pytest.mark.skipif(
    sys.platform != 'linux' or getattr(os, 'geteuid', lambda: -1)() != 0,
    reason='Requires isolated Linux/root fixtures, never a production host',
)
REPO = Path(__file__).resolve().parents[1]


def _identity(pid):
    try:
        fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'start': fields[19], 'state': fields[0], 'group': int(fields[2])}
    except (FileNotFoundError, ProcessLookupError):
        return None


def _kill_recorded_groups(directory):
    # Only identities recorded by our synthetic workers; ignore partial records.
    for path in directory.glob('owned-group-*.json'):
        try:
            old = json.loads(path.read_text())
            now = _identity(old['pid'])
            if now and now['start'] == old['start'] and now['group'] == old['pid']:
                os.killpg(old['pid'], signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError, json.JSONDecodeError):
            pass


PRELUDE = '''
import errno, json, os, pathlib, signal, subprocess, sys, time
from ops import light_native_bounded as bounded
ROOT = pathlib.Path(sys.argv[1])
def identity(pid):
    try:
        fields = pathlib.Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        return {'pid':pid, 'start':fields[19], 'state':fields[0], 'group':int(fields[2])}
    except (FileNotFoundError, ProcessLookupError):
        return None
def live(old):
    now = identity(old['pid'])
    return bool(now and now['start']==old['start'] and now['state'] not in ('Z','X'))
def wait_dead(old, seconds=3):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if not live(old):return True
        time.sleep(.01)
    return not live(old)
def remember_group():
    group=identity(os.getpgrp())
    (ROOT/('owned-group-'+str(group['pid'])+'.json')).write_text(json.dumps(group))
def sleeper():
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        close_fds=True)
    info=identity(child.pid)
    assert info is not None
    return child,info
'''


def _run(directory, script):
    process = subprocess.Popen(
        [sys.executable, '-B', '-c', PRELUDE + '\n' + textwrap.dedent(script), str(directory)],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        return json.loads(stdout)
    finally:
        _kill_recorded_groups(directory)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=3)


@pytest.mark.parametrize('operation', ['setsid', 'setpgid'])
def test_worker_cannot_escape_owned_group(tmp_path, operation):
    value = _run(tmp_path, f'''
        def work():
            remember_group()
            try:
                os.{operation}({'' if operation == 'setsid' else '0, 0'})
            except OSError as exc:
                return {{'blocked':exc.errno==errno.EPERM}}
            return {{'blocked':False}}
        print(json.dumps(bounded.run(work,seconds=1)))
    ''')
    assert value == {'blocked': True}


def test_success_waits_for_residual_child_termination(tmp_path):
    value = _run(tmp_path, '''
        def work():
            remember_group()
            child,info=sleeper()
            return info
        info=bounded.run(work,seconds=1)
        # Return must already certify no live owned child, not eventual cleanup.
        print(json.dumps({'live_on_return':live(info)}))
    ''')
    assert value == {'live_on_return': False}


def test_deadline_kills_worker_and_descendant(tmp_path):
    value = _run(tmp_path, '''
        def work():
            remember_group()
            child,info=sleeper()
            (ROOT/'workers.json').write_text(json.dumps([identity(os.getpid()),info]))
            while True:time.sleep(.01)
        started=time.monotonic()
        try:
            bounded.run(work,seconds=.5)
            outcome='SUCCESS'
        except RuntimeError as exc:
            outcome=str(exc)
        elapsed=time.monotonic()-started
        rows=json.loads((ROOT/'workers.json').read_text())
        print(json.dumps({'outcome':outcome,'elapsed':elapsed,
                          'any_live_on_return':any(live(row) for row in rows)}))
    ''')
    assert value['outcome'] == 'LANE_RETIREMENT_UNKNOWN'
    assert value['elapsed'] < 4
    assert not value['any_live_on_return']


def test_caller_death_terminates_worker_and_descendant(tmp_path):
    value = _run(tmp_path, '''
        caller=os.fork()
        if caller==0:
            def work():
                remember_group()
                child,info=sleeper()
                (ROOT/'workers.json').write_text(json.dumps([identity(os.getpid()),info]))
                while True:time.sleep(.01)
            bounded.run(work,seconds=5)
            os._exit(91)
        until=time.monotonic()+3
        rows=None
        while time.monotonic()<until:
            try:
                rows=json.loads((ROOT/'workers.json').read_text());break
            except (FileNotFoundError,json.JSONDecodeError):time.sleep(.01)
        assert rows is not None
        os.kill(caller,signal.SIGKILL)
        os.waitpid(caller,0)
        print(json.dumps({'all_dead':all(wait_dead(row) for row in rows)}))
    ''')
    assert value == {'all_dead': True}


def test_timeout_preserves_partial_file_without_completion(tmp_path):
    value = _run(tmp_path, '''
        def work():
            remember_group()
            fd=os.open(ROOT/'partial.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.write(fd,b'{"partial":');os.fsync(fd)
            while True:time.sleep(.01)
        try:
            bounded.run(work,seconds=.5)
            outcome='SUCCESS'
        except RuntimeError as exc:outcome=str(exc)
        print(json.dumps({'outcome':outcome,
                          'retained':(ROOT/'partial.json').read_bytes().decode()}))
    ''')
    assert value == {'outcome': 'LANE_RETIREMENT_UNKNOWN', 'retained': '{"partial":'}


def _record():
    return retirement.encoded(dict(
        version=1, kind='FAILED_PREPARE_RETIREMENT_PROPOSAL',
        requested_disposition='RETIRE_FAILED_PREPARE_AND_CANCEL_OLD_POLICY_REMAINDER',
        policy_sha256='1'*64, index=0, plan_sha256='2'*64,
        repository='synthetic/example', failed_work_key='synthetic-failed-work', failed_target_pr=7,
        predecessor=dict(sequence=2, plan_sha256='3'*64, terminal_sha256='4'*64),
        incident_sha256='5'*64, historical_controller_source='6'*40,
        historical_controller_sha256='7'*64, retained_runtime_source='8'*40,
        retained_runtime_sha256='9'*64, original_failure='UNKNOWN',
        interpretation='EXPLICIT_NEW_POLICY_HASH_ALLOWLIST_AND_FRESH_GUARDS_REQUIRED',
        **{name: False for name in retirement.FALSE_FLAGS},
    ))


def test_existing_proposal_same_bytes_new_inode_is_drift(tmp_path):
    raw = _record()
    value = retirement.record(raw)
    root = tmp_path/'owner'
    root.mkdir(mode=0o700)
    current = root
    for part in retirement.entry(value).split('/'):
        current = current/part
        current.mkdir(mode=0o700)
    target = current/retirement.NAME
    target.write_bytes(raw)
    target.chmod(0o600)
    original_fd = os.open(target, os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME)
    original_inode = os.fstat(original_fd).st_ino
    calls = 0
    def observe(view, record):
        nonlocal calls
        calls += 1
        view.names(retirement.entry(record), allow_output=True)
        if calls == 2:
            # Retaining the old descriptor prevents accidental inode reuse.
            target.unlink()
            target.write_bytes(raw)
            target.chmod(0o600)
            assert target.stat().st_ino != original_inode
        return {'synthetic': 'stable'}
    try:
        with pytest.raises(RuntimeError, match='LANE_RETIREMENT_(DRIFT|UNKNOWN)'):
            retirement.write_proposal(root, raw, retirement.sha(raw), observe)
        assert target.read_bytes() == raw
        assert calls >= 2  # Rejecting the fixture metadata early is not this test.
    finally:
        os.close(original_fd)
