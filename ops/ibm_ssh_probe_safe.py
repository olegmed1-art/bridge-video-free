#!/usr/bin/env python3
"""Prepared diagnostic only. No Start/Stop, service changes, credential export or SSH retries.
Network use requires a separately coordinated authorized running window.
"""
import argparse
import datetime
import errno
import hashlib
import json
import math
import os
import re
import socket
import stat
import subprocess
import time

HOST = "161.156.86.34"
KEY = "/home/ubuntu/.ssh/ibm_bridge_ed25519"
KNOWN = "/home/ubuntu/.ssh/known_hosts"
EXPECTED = "SHA256:o+7KPm2p4VSZSvFFYzIIzA8iiXfze2hhW9tUM58e00w"
VERIFIED_USER = "ubuntu"
PROBE_SECONDS = 28  # must not extend the remote watchdog or controller's 38s cap
TCP_READINESS_SECONDS = 10
TCP_CONNECT_SECONDS = 3
TCP_POLL_SECONDS = 1
TCP_MAX_ATTEMPTS = 11
SSH_SECONDS = 15


class ProbeDeadlineError(Exception):
    pass


class ProbeBudget:
    """One monotonic deadline shared by prechecks, TCP readiness and sole SSH."""
    def __init__(self, deadline):
        self.last = time.monotonic()
        if not math.isfinite(self.last):
            raise ProbeDeadlineError("invalid_monotonic_clock")
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ProbeDeadlineError("invalid_probe_deadline")
        self.deadline = min(self.last + PROBE_SECONDS, deadline) if deadline is not None else self.last + PROBE_SECONDS

    def now(self):
        now = time.monotonic()
        if not math.isfinite(now) or now < self.last:
            raise ProbeDeadlineError("monotonic_clock_regressed")
        self.last = now
        if now >= self.deadline:
            raise ProbeDeadlineError("probe_deadline_expired")
        return now

    def remaining(self):
        return self.deadline - self.now()


def tcp_ready(budget):
    """Poll TCP only; reserve the sole SSH attempt and never reset the deadline."""
    started = budget.now()
    end = min(started + TCP_READINESS_SECONDS, budget.deadline - SSH_SECONDS)
    attempts = 0
    failure, error_number = "TCP_TIMEOUT", None
    while attempts < TCP_MAX_ATTEMPTS:
        left = end - budget.now()
        if left <= 0:
            break
        attempts += 1
        try:
            with socket.create_connection((HOST, 22), timeout=min(TCP_CONNECT_SECONDS, left)):
                elapsed = budget.now() - started
            if budget.now() <= end:
                emit("TCP_OPEN", elapsed_seconds=round(elapsed, 3), attempts=attempts)
                return True
            break  # a late success cannot borrow the reserved SSH time
        except OSError as exc:
            error_number = exc.errno
            failure = {errno.ECONNREFUSED: "TCP_REFUSED", errno.ETIMEDOUT: "TCP_TIMEOUT",
                       errno.EHOSTUNREACH: "NETWORK_UNREACHABLE", errno.ENETUNREACH: "NETWORK_UNREACHABLE"}.get(exc.errno)
            failure = failure or ("TCP_TIMEOUT" if isinstance(exc, TimeoutError) else "TCP_CONNECT_FAILURE")
            # Only early-boot refusal/timeout is eligible for another TCP check.
            if failure not in {"TCP_REFUSED", "TCP_TIMEOUT"}:
                break
        left = end - budget.now()
        if left <= 0 or attempts >= TCP_MAX_ATTEMPTS:
            break
        time.sleep(min(TCP_POLL_SECONDS, left))
    emit(failure, errno=error_number, elapsed_seconds=round(budget.now() - started, 3))
    emit("TCP_READINESS_EXHAUSTED", attempts=attempts,
         elapsed_seconds=round(budget.now() - started, 3))
    return False


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def emit(event, **fields):
    print(json.dumps(dict(event=event, at_utc=utc(), **fields), sort_keys=True), flush=True)


def stderr_info(text, user):
    """Return exact known safe client error lines; preserve a hash of unknown text."""
    rules = (
        ("HOSTKEY_FAILURE", r"Host key verification failed\.|REMOTE HOST IDENTIFICATION HAS CHANGED|No ED25519 host key is known"),
        ("LOCAL_KEY_FAILURE", r"UNPROTECTED PRIVATE KEY FILE|bad permissions|(?:Load key|Identity file).*ibm_bridge_ed25519.*(?:Permission denied|not accessible|invalid format|libcrypto|passphrase)"),
        ("AUTH_FAILURE", r"Permission denied \(publickey[^)]*\)\."),
        ("TCP_REFUSED", r"Connection refused"),
        ("SSH_BANNER_TIMEOUT", r"Connection timed out during banner exchange"),
        ("TCP_TIMEOUT", r"Connection timed out|Connection timeout"),
        ("NETWORK_UNREACHABLE", r"No route to host|Network is unreachable"),
        ("CONNECTION_CLOSED", r"Connection closed|Connection reset|closed by remote host"),
        ("SSH_NEGOTIATION_FAILURE", r"Unable to negotiate|no matching host key type|no matching key exchange method"),
    )
    classes = [next((name for name, pattern in rules if re.search(pattern, text, re.I)), "UNCLASSIFIED")] if text else []
    exact = []
    safe_line = (
        r"ssh: connect to host 161\.156\.86\.34 port 22: (?:Connection refused|Connection timed out|No route to host|Network is unreachable)",
        re.escape(user + "@" + HOST) + r": Permission denied \(publickey(?:,keyboard-interactive)?\)\.",
        r"Host key verification failed\.",
        r"Connection timed out during banner exchange",
        r"Load key \"" + re.escape(KEY) + r"\": (?:Permission denied|invalid format|error in libcrypto|incorrect passphrase supplied to decrypt private key)",
    )
    for line in text.splitlines():
        if any(re.fullmatch(pattern, line) for pattern in safe_line):
            exact.append(line)
    return dict(error_classes=classes, stderr_safe_exact_lines=exact,
                stderr_bytes=len(text.encode("utf-8")), stderr_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                raw_stderr_exported=False, wrong_user="NOT_ESTABLISHED")


def probe(user, *, deadline=None):
    try:
        return _probe(user, ProbeBudget(deadline))
    except ProbeDeadlineError as exc:
        emit("PRECHECK_BLOCKED", reason=str(exc))
        return 3


def _probe(user, budget):
    budget.remaining()
    if user != VERIFIED_USER:
        emit("PRECHECK_BLOCKED", reason="previously_verified_ubuntu_required")
        return 3
    meta = os.stat(KEY)
    if not stat.S_ISREG(meta.st_mode) or not os.access(KEY, os.R_OK):
        emit("PRECHECK_BLOCKED", reason="existing_key_not_readable", key_contents_read=False)
        return 3
    found = subprocess.run(["/usr/bin/ssh-keygen", "-F", HOST, "-f", KNOWN], capture_output=True, text=True, timeout=min(5, budget.remaining()))
    records = "\n".join(line for line in found.stdout.splitlines()
                        if line and not line.startswith("#") and "ssh-ed25519" in line)
    pin = subprocess.run(["/usr/bin/ssh-keygen", "-lf", "-", "-E", "sha256"],
                         input=records + "\n", capture_output=True, text=True, timeout=min(5, budget.remaining()))
    budget.remaining()
    fingerprints = {line.split()[1] for line in pin.stdout.splitlines() if len(line.split()) > 1}
    if not records or fingerprints != {EXPECTED}:
        emit("PRECHECK_BLOCKED", reason="exact_known_host_pin_missing_or_conflicting")
        return 3
    emit("TCP_PROBE_STARTED", user=user, target=HOST, power_mutations=False)
    if not tcp_ready(budget):
        return 3
    args = ["/usr/bin/ssh", "-F", "/dev/null", "-T", "-i", KEY, "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UpdateHostKeys=no", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "VerifyHostKeyDNS=no", "-o", "CheckHostIP=no",
            "-o", "AddKeysToAgent=no", "-o", "ForwardAgent=no",
            "-o", "ClearAllForwardings=yes", "-o", "ConnectionAttempts=1",
            "-o", "UserKnownHostsFile=" + KNOWN,
            "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "ConnectTimeout=8",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1",
            user + "@" + HOST, "id -u"]
    timeout = min(SSH_SECONDS, budget.remaining())
    emit("SSH_PROBE_STARTED", user=user)
    started = budget.now()
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=min(timeout, budget.remaining()))
    except subprocess.TimeoutExpired as exc:
        error_text = exc.stderr or b""
        if isinstance(error_text, bytes):
            error_text = error_text.decode("utf-8", errors="replace")
        emit("SSH_WALL_TIMEOUT", elapsed_seconds=round(time.monotonic() - started, 3), **stderr_info(error_text, user))
        return 3
    budget.remaining()  # do not accept success returned after the shared deadline
    info = stderr_info(result.stderr, user)
    emit("SSH_PROBE_FINISHED", ssh_exit=result.returncode,
         elapsed_seconds=round(time.monotonic() - started, 3), **info)
    if result.returncode == 0 and re.fullmatch(r"[0-9]+\n?", result.stdout):
        emit("SSH_AUTHENTICATED", uid=int(result.stdout), user=user)
        return 0
    emit("SSH_NOT_CONFIRMED", stdout_exported=False)
    return 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", choices=[VERIFIED_USER], help="Verified IBM login from 2026-10-04 evidence; no user cycling.")
    parser.add_argument("--execute", action="store_true", help="Use only inside a newly authorized running window.")
    args = parser.parse_args()
    if not args.execute:
        emit("PREPARED_ONLY", network_requests=0, power_mutations=False)
        return 0
    if not args.user:
        emit("PRECHECK_BLOCKED", reason="verified_user_required")
        return 3
    try:
        return probe(args.user)
    except (OSError, subprocess.SubprocessError) as exc:
        emit("LOCAL_PRECHECK_FAILED", error_type=type(exc).__name__, raw_error_exported=False)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
