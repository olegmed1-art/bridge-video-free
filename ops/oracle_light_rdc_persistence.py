"""One post-authorization restart to test RDC session persistence."""
import json
import subprocess
import time

UNIT = 'remote-desktop-commander.service'
MARKER = '/var/lib/bridge-light-rdc-persistence-20260922'


def call(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def main():
    before = call(['systemctl', 'is-active', UNIT])
    if before.returncode:
        raise RuntimeError('agent_not_active')
    if call(['sudo', '-n', 'mkdir', '--', MARKER]).returncode:
        raise RuntimeError('already_attempted_or_marker_unavailable')
    if call(['sudo', '-n', 'systemctl', 'restart', UNIT], timeout=45).returncode:
        raise RuntimeError('restart_failed')
    time.sleep(15)
    r = call(['systemctl', 'show', UNIT, '-p', 'ActiveState', '-p', 'SubState',
              '-p', 'UnitFileState', '-p', 'Restart', '-p', 'NRestarts'])
    state = dict(x.split('=', 1) for x in r.stdout.splitlines() if '=' in x)
    print(json.dumps({'state': state,
                      'connector_verification': 'REQUIRED'}))
    if r.returncode:
        raise RuntimeError('state_unavailable')
    if state.get('ActiveState') != 'active' or state.get('SubState') != 'running':
        raise RuntimeError('agent_not_running_after_restart')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'failure_type': type(exc).__name__}))
        raise SystemExit(1)
