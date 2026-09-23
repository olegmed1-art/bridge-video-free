#!/usr/bin/env python3
"""One authenticated Oracle -> IBM GET, using an in-memory short-lived token.

Run on GitHub with its scoped IBM secret. SSH must use the existing pinned
Oracle Light host key. Neither the API key nor a token file is sent to Oracle.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

try:
    from .ibm_vpc_power import BoundedClientError, obtain_token
except ImportError:
    from ibm_vpc_power import BoundedClientError, obtain_token

ORACLE_HOST = "ubuntu@92.5.47.149"
INSTANCE_ID = "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2"
INSTANCE_NAME = "bridge-school-compute-ibm"
SERVICE_ID = "ServiceId-e08e417c-dd54-49ec-9159-4df0b147c837"
ACCOUNT_ID = "7892cf9edf9b4ee480f9370d73e96138"

# Source is from this checkout, not from provider data. It is loaded in memory
# so no credentials or scripts are left on the remote host.
REMOTE_READ = '''import json, sys, types
payload = json.load(sys.stdin)
client = types.ModuleType("ibm_vpc_power")
sys.modules[client.__name__] = client
exec(compile(payload["client_source"], "ibm_vpc_power.py", "exec"), client.__dict__)
print("ORACLE_IBM_AUTHENTICATED_REQUEST=YES", flush=True)
try:
    instance = client.read_instance(payload["token"], region="eu-de",
        instance_id=payload["instance_id"], name=payload["instance_name"])
except client.BoundedClientError as exc:
    print("ORACLE_IBM_READ_RESULT=FAIL reason=" + str(exc), flush=True)
    sys.exit(3)
print("ORACLE_IBM_READ_RESULT=PASS")
print("ORACLE_IBM_INSTANCE_STATUS=" + instance.status)
'''


def verify_identity(token: str, *, now: float | None = None) -> None:
    """Check identity on the token just obtained over TLS from IBM IAM."""
    try:
        parts = token.split(".")
        if len(parts) != 3 or len(token) > 16384:
            raise ValueError
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        identity = claims.get("iam_id") or claims.get("id")
        account = claims.get("account", {}).get("bss") or claims.get("account_id")
        current = time.time() if now is None else now
        expiry = claims.get("exp")
        if (identity not in (SERVICE_ID, "iam-" + SERVICE_ID) or account != ACCOUNT_ID
                or not isinstance(expiry, (float, int)) or not current + 60 < expiry <= current + 7200):
            raise ValueError
    except (ValueError, TypeError, AttributeError, UnicodeDecodeError) as exc:
        raise BoundedClientError("oracle_probe_token_identity_or_expiry_invalid") from exc


def probe_over_ssh(token: str, *, key: str, known_hosts: str) -> int:
    verify_identity(token)
    print("ORACLE_IBM_IAM_IDENTITY_MATCH=YES", flush=True)
    payload = json.dumps({
        "client_source": Path(__file__).with_name("ibm_vpc_power.py").read_text(),
        "token": token, "instance_id": INSTANCE_ID, "instance_name": INSTANCE_NAME,
    })
    command = [
        "ssh", "-F", "/dev/null", "-i", key,
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + known_hosts,
        "-o", "ConnectTimeout=15", "-o", "ConnectionAttempts=1",
        "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
        ORACLE_HOST, "python3 -c " + shlex.quote(REMOTE_READ),
    ]
    try:
        result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BoundedClientError("oracle_probe_ssh_failed") from exc
    # Never echo SSH diagnostics or exception representations containing stdin.
    lines = result.stdout.splitlines()
    for line in lines:
        if line.startswith(("ORACLE_IBM_AUTHENTICATED_REQUEST=", "ORACLE_IBM_READ_RESULT=",
                            "ORACLE_IBM_INSTANCE_STATUS=")) and len(line) <= 256:
            print(line)
    if result.returncode == 0 and "ORACLE_IBM_READ_RESULT=PASS" in lines:
        return 0
    if result.returncode == 3 and any(line.startswith("ORACLE_IBM_READ_RESULT=FAIL") for line in lines):
        return 3
    raise BoundedClientError("oracle_probe_ssh_or_remote_execution_failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--known-hosts", required=True)
    args = parser.parse_args(argv)
    try:
        token = obtain_token(os.environ.get("IBM_CLOUD_API_KEY", ""))
        return probe_over_ssh(token, key=args.ssh_key, known_hosts=args.known_hosts)
    except BoundedClientError as exc:
        print(f"ORACLE_IBM_PROBE_RESULT=FAIL reason={exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
