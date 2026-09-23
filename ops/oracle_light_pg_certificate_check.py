#!/usr/bin/env python3
"""Read-only expiry check for the Light Oracle PostgreSQL server and local CA."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import sys


DEFAULT_DIRECTORY = Path('/etc/bridge-autopilot-postgres')


def inspect_certificate(path: Path, now: datetime, warning_days: int) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError('certificate missing or symlinked')
    result = subprocess.run(
        ['openssl', 'x509', '-in', str(path), '-noout', '-startdate', '-enddate'],
        capture_output=True, text=True, timeout=10, check=True,
    )
    fields = dict(line.strip().split('=', 1) for line in result.stdout.splitlines())
    start = datetime.strptime(fields['notBefore'], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
    end = datetime.strptime(fields['notAfter'], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
    remaining = int((end - now).total_seconds())
    status = 'CRITICAL' if now < start or remaining <= 0 else (
        'WARNING' if remaining <= warning_days * 86400 else 'OK'
    )
    return {'status': status, 'not_after': end.isoformat(),
            'remaining_seconds': remaining}


def verify_live_certificate(directory: Path) -> None:
    """Authenticate PostgreSQL on loopback and compare its active leaf to disk."""
    ca_path = directory / 'ca.crt'
    server_path = directory / 'tls/server.crt'
    context = ssl.create_default_context(cafile=str(ca_path))
    expected = ssl.PEM_cert_to_DER_cert(server_path.read_text())
    with socket.create_connection(('127.0.0.1', 55432), timeout=10) as connection:
        connection.sendall(struct.pack('!II', 8, 80877103))
        if connection.recv(1) != b'S':
            raise ValueError('PostgreSQL did not accept TLS')
        with context.wrap_socket(connection, server_hostname='localhost') as secure:
            if secure.getpeercert(binary_form=True) != expected:
                raise ValueError('active PostgreSQL certificate differs from disk')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument('--warning-days', type=int, default=30)
    args = parser.parse_args()
    if args.warning_days < 1:
        parser.error('--warning-days must be positive')
    now = datetime.now(timezone.utc)
    certificates = {}
    try:
        for name, relative in [('server', 'tls/server.crt'), ('ca', 'ca.crt')]:
            certificates[name] = inspect_certificate(args.directory / relative, now, args.warning_days)
        verify_live_certificate(args.directory)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, ssl.SSLError) as exc:
        print(json.dumps({'status': 'UNKNOWN', 'error_type': type(exc).__name__,
                          'scope': 'light_postgres_certificate_expiry'}))
        return 2
    status = ('CRITICAL' if any(c['status'] == 'CRITICAL' for c in certificates.values())
              else 'WARNING' if any(c['status'] == 'WARNING' for c in certificates.values())
              else 'OK')
    print(json.dumps({'status': status, 'checked_at': now.isoformat(),
                      'certificates': certificates, 'live_tls': 'VERIFIED',
                      'scope': 'light_postgres_certificate_expiry'}, sort_keys=True))
    return {'OK': 0, 'WARNING': 1, 'CRITICAL': 2}[status]


if __name__ == '__main__':
    sys.exit(main())
