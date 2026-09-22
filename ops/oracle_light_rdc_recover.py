"""Bounded RDC diagnosis and one service restart; never prints raw journal/config."""
import collections
import json
import subprocess
import time

UNIT = 'remote-desktop-commander.service'
PROPERTIES = ['LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'Restart', 'NRestarts', 'Result']


def run(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def state():
    result = run(['systemctl', 'show', UNIT, *['--property=' + p for p in PROPERTIES]])
    if result.returncode:
        raise RuntimeError('service_state_unavailable')
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def journal_summary():
    result = run(['sudo', '-n', 'journalctl', '-u', UNIT, '-n', '150', '--no-pager', '-o', 'json'])
    if result.returncode:
        return {'readable': False}
    patterns = {
        'dns': ['enotfound', 'eai_again', 'name resolution'],
        'connection': ['econnrefused', 'econnreset', 'socket hang up', 'connection closed'],
        'timeout': ['etimedout', 'timed out', 'timeout'],
        'auth': ['unauthorized', 'forbidden', 'invalid token', 'token expired', '401', '403'],
        'permission': ['eacces', 'permission denied', 'eperm'],
        'missing_file': ['enoent', 'cannot find module', 'no such file'],
        'connected': ['connected', 'registered', 'heartbeat'],
        'exit': ['main process exited', 'failed with result'],
    }
    counts = collections.Counter()
    for line in result.stdout.splitlines():
        try:
            message = str(json.loads(line).get('MESSAGE', '')).lower()
        except (ValueError, AttributeError):
            continue
        for label, needles in patterns.items():
            if any(needle in message for needle in needles):
                counts[label] += 1
    return {'readable': True, 'keyword_counts_not_diagnosis': dict(counts)}


def main():
    before = state()
    print(json.dumps({'before': before, 'journal_before': journal_summary()}), flush=True)
    if before.get('LoadState') != 'loaded':
        raise RuntimeError('service_not_loaded')
    privilege = run(['sudo', '-n', '/usr/bin/true'])
    if privilege.returncode:
        raise RuntimeError('noninteractive_sudo_unavailable')
    # Atomic, root-owned one-shot marker. Retained even if restart fails:
    # a retry requires a separately reviewed recovery decision.
    once = run(['sudo', '-n', 'mkdir', '--', '/var/lib/bridge-light-rdc-recovery-20260922'])
    if once.returncode:
        raise RuntimeError('recovery_already_attempted_or_marker_unavailable')
    # Explicit owner-authorized short restart of this agent only. No VM restart,
    # sudoers changes, key export, dependency changes or application service changes.
    restart = run(['sudo', '-n', 'systemctl', 'restart', UNIT], timeout=45)
    print(json.dumps({'rdc_restart_returncode': restart.returncode}), flush=True)
    if restart.returncode:
        raise RuntimeError('rdc_restart_failed')
    time.sleep(15)
    after = state()
    print(json.dumps({'after': after, 'journal_after': journal_summary(),
                      'connector_online': 'VERIFY_EXTERNALLY'}), flush=True)
    if after.get('ActiveState') != 'active':
        raise RuntimeError('rdc_not_active')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'failure_type': type(exc).__name__}))
        raise SystemExit(1)
