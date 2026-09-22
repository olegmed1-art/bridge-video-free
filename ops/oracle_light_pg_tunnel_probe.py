"""Prove allowed TLS and denied forwarding; does not authenticate to PostgreSQL."""
import hashlib
import json
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import sys
import time

CA_SHA256 = '1ee37914846a8f85aff90523937ed6dde63517f239782e782190eb5038799212'


def options(work):
    return ['ssh', '-F', '/dev/null', '-i', str(work / 'key'), '-o', 'BatchMode=yes',
            '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(work / 'known_hosts'),
            '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=2', '-o', 'ExitOnForwardFailure=yes']


def listening(process, port):
    for _ in range(50):
        assert process.poll() is None, 'ssh_tunnel_exited'
        try:
            return socket.create_connection(('127.0.0.1', port), timeout=2)
        except ConnectionRefusedError:
            time.sleep(0.1)
    raise AssertionError('ssh_tunnel_not_ready')


def stop(process):
    process.terminate()
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=5)


def main(work):
    work = Path(work)
    assert hashlib.sha256((work / 'ca.crt').read_bytes()).hexdigest() == CA_SHA256, 'unexpected_ca'
    base = options(work)
    host = 'autopilot-db-tunnel@92.5.47.149'
    process = subprocess.Popen(base + ['-N', '-T', '-L', '127.0.0.1:55432:127.0.0.1:55432', host],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with listening(process, 55432) as sock:
            sock.sendall(struct.pack('!II', 8, 80877103))
            assert sock.recv(1) == b'S', 'postgresql_ssl_not_accepted'
            context = ssl.create_default_context(cafile=str(work / 'ca.crt'))
            with context.wrap_socket(sock, server_hostname='127.0.0.1') as secured:
                assert secured.getpeercert(), 'tls_certificate_missing'
    finally:
        stop(process)
    for args, marker in [
        (['-T', '-W', '127.0.0.1:22', host], 'administratively prohibited'),
        (['-N', '-T', '-R', '127.0.0.1:0:127.0.0.1:55432', host], 'remote port forwarding failed'),
    ]:
        result = subprocess.run(base + args, capture_output=True, text=True, timeout=25)
        assert result.returncode != 0 and marker in result.stderr.lower(), 'forwarding_denial_not_proved'
    process = subprocess.Popen(base + ['-N', '-T', '-L',
        '127.0.0.1:55434:/run/bridge-autopilot-tunnel-probe-nonexistent.sock', host],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with listening(process, 55434) as sock:
            try:
                assert sock.recv(1) == b''
            except ConnectionResetError:
                pass
    finally:
        _, error = stop(process)
    assert b'administratively prohibited' in error.lower(), 'unix_forwarding_denial_not_proved'
    result = subprocess.run(base + ['-T', host, 'id'], capture_output=True, text=True, timeout=25)
    assert result.returncode != 0 and 'uid=' not in result.stdout, 'remote_command_not_denied'
    result = subprocess.run(base + ['-T', 'ubuntu@92.5.47.149', 'id -un'], capture_output=True, text=True, timeout=25)
    assert result.returncode == 0 and result.stdout.strip() == 'ubuntu', 'ubuntu_access_changed'
    print(json.dumps({'pg_ssh_tunnel': 'PASS', 'tls_hostname_and_ca_verified': True,
                      'other_tcp_destination_denied': True, 'remote_forwarding_denied': True,
                      'unix_forwarding_denied': True, 'command_execution_denied': True,
                      'ubuntu_access_unchanged': True, 'database_writes': False}))


if __name__ == '__main__':
    main(sys.argv[1])
