"""Hermetic test guard inherited by every Python child via PYTHONPATH."""
import os
import sys


def audit(event, args):
    if event.startswith('socket.'):
        raise RuntimeError('NETWORK_FORBIDDEN_IN_CONTRACT')
    if event == 'subprocess.Popen':
        executable = os.path.realpath(os.fsdecode(args[0]))
        allowed = {os.path.realpath(sys.executable), os.path.realpath('/usr/bin/python3')}
        if executable not in allowed:
            raise RuntimeError('NON_PYTHON_PROCESS_FORBIDDEN_IN_CONTRACT')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0]).replace('\\', '/')
        if '/.ssh/' in path or '/.aws/' in path or path.endswith('/known_hosts'):
            raise RuntimeError('CREDENTIAL_PATH_FORBIDDEN_IN_CONTRACT')


sys.addaudithook(audit)
