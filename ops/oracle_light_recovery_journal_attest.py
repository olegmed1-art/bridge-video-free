"""Read-only, secret-safe journal classification for the held Light incident."""
import json
import os
import subprocess
import sys

UNIT = 'school-autopilot-production-light.service'
SINCE = '2026-09-25 05:27:00 UTC'
SIGNATURES = {
    'worker_traceback': 'Traceback (most recent call last)',
    'worker_admission_held': 'worker_admission_held',
    'worker_hold_connected': 'worker_hold_connected',
    'worker_hold_reconnect': 'held_listener_reconnect',
    'worker_config_rejected': 'AUTOPILOT_RUNTIME_MODE must be SHADOW',
    'worker_admission_rejected': 'AUTOPILOT_ADMISSION_MODE_INVALID',
    'worker_dsn_rejected': 'autopilot DSN uses an unexpected principal',
    'worker_held': 'AUTOPILOT_ADMISSION_HELD',
    'systemd_environment_failure': 'Failed to load environment files',
    'systemd_start_failure': 'Failed to start school-autopilot-production-light.service',
}


def classify(raw):
    flags = dict.fromkeys(SIGNATURES, False)
    records = 0
    for line in raw.splitlines():
        entry = json.loads(line)
        if not isinstance(entry, dict):
            raise RuntimeError('JOURNAL_FORMAT')
        message = entry.get('MESSAGE', '')
        if not isinstance(message, str):
            continue
        records += 1
        for key, needle in SIGNATURES.items():
            flags[key] = flags[key] or needle in message
    return {'audit': 'LIGHT_JOURNAL_CLASSIFIED', 'window_start_utc': SINCE,
            'bounded_records': records, 'markers': flags}


def main():
    if os.geteuid() != 0 or os.uname().nodename != 'autopilot-lite-vnic':
        raise RuntimeError('HOST_IDENTITY')
    output = subprocess.run(['journalctl','--unit',UNIT,'--since',SINCE,
                             '--lines','2000','--output=json','--no-pager'],
                            capture_output=True, timeout=30, check=True)
    if len(output.stdout) > 4_000_000:
        raise RuntimeError('JOURNAL_LIMIT')
    print(json.dumps(classify(output.stdout), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        code = exc.args[0] if isinstance(exc, RuntimeError) else type(exc).__name__
        print(json.dumps({'audit':'INCOMPLETE','code':code if code in {
            'HOST_IDENTITY','JOURNAL_LIMIT','JOURNAL_FORMAT'} else 'UNCLASSIFIED'}))
        sys.exit(2)
