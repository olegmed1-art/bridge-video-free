"""Read-only RDC diagnostics, with credential-like strings removed before output."""
import json
import re
import subprocess


def redact(message):
    message = re.sub(r'\b[A-Z0-9]{4}-[A-Z0-9]{4}\b', '<device-code>', message)
    message = re.sub(r'https?://\S+|wss?://\S+', '<url>', message)
    message = re.sub(r'[\w.+-]+@[\w.-]+', '<email>', message)
    message = re.sub(r'(?i)(bearer|token|password|secret|authorization|api[_ -]?key|pairing code|verification code)\s*[:= ]+.*', r'\1 <redacted>', message)
    message = re.sub(r'[A-Za-z0-9_+/=.-]{24,}', '<opaque>', message)
    message = re.sub(r'\b\d{4,}\b', '<number>', message)
    return message[:400]


def main():
    props = ['User', 'Group', 'WorkingDirectory', 'FragmentPath', 'Restart',
             'RestartUSec', 'ExecMainCode', 'ExecMainStatus', 'NRestarts', 'UnitFileState']
    r = subprocess.run(['systemctl', 'show', 'remote-desktop-commander.service',
                        *['--property=' + p for p in props]], capture_output=True, text=True, timeout=15)
    print(json.dumps({'service': dict(x.split('=', 1) for x in r.stdout.splitlines() if '=' in x)}))
    r = subprocess.run(['sudo', '-n', 'journalctl', '-u', 'remote-desktop-commander.service',
                        '-n', '80', '--no-pager', '-o', 'json'], capture_output=True, text=True, timeout=20)
    if r.returncode:
        print(json.dumps({'journal': 'UNAVAILABLE'}))
        return
    # Agent service log only; never reads application logs or credential files.
    for line in r.stdout.splitlines():
        try:
            obj = json.loads(line)
            message = str(obj.get('MESSAGE', ''))
        except (ValueError, AttributeError):
            continue
        print(json.dumps({'time': obj.get('__REALTIME_TIMESTAMP'), 'message': redact(message)}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'failure_type': type(exc).__name__}))
        raise SystemExit(1)
