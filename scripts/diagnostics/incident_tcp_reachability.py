"""One bounded, credential-free DNS/TCP observation; no database protocol.

Importing this module performs no I/O. The separate reviewed workflow provides
the outer process deadline and admits one run. No application module is imported.
"""
from __future__ import annotations

import ast
import errno
import hashlib
import ipaddress
import json
from pathlib import Path
import queue
import socket
import sys
import threading
import time


CONFIG_SHA256 = "1c391cd0bfde393994db6b3b2f71af7843f5ed48b79e5476eced02113338234a"
DNS_SECONDS = 5
TCP_SECONDS = 3
MAX_PER_FAMILY = 2
PORT = 5432
CONTROL_HOST = "github.com"
SCHEMA = "incident-1911-tcp-once-v1"


def error_category(exc: Exception) -> str:
    """Only fixed categories, never raw errors or arbitrary attributes as output."""
    code = getattr(exc, "errno", None)
    if isinstance(exc, socket.gaierror):
        if code == socket.EAI_NONAME:
            return "dns_name_or_record_absent"
        if code == socket.EAI_AGAIN:
            return "dns_temporary_failure"
        return "dns_resolver_failure"
    if isinstance(exc, TimeoutError) or code in {errno.ETIMEDOUT, 10060}:
        return "timeout"
    groups = (
        ("local_permission_denied", {errno.EACCES, errno.EPERM, 10013}),
        ("refused", {errno.ECONNREFUSED, 10061}),
        ("network_unreachable", {errno.ENETUNREACH, 10051}),
        ("host_unreachable", {errno.EHOSTUNREACH, 10065}),
        ("reset", {errno.ECONNRESET, 10054}),
        ("family_unsupported", {errno.EAFNOSUPPORT, 10047}),
        ("local_address_unavailable", {errno.EADDRNOTAVAIL, 10049}),
    )
    return next((name for name, values in groups if code in values), "unknown")


def read_target(source: Path) -> str:
    raw = source.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(raw).hexdigest() != CONFIG_SHA256:
        raise ValueError("configuration_rejected")
    definitions = [node for node in ast.parse(raw).body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "PRODUCTION_HOST"
                           for t in node.targets)]
    if len(definitions) != 1 or len(definitions[0].targets) != 1:
        raise ValueError("configuration_rejected")
    value = definitions[0].value
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        raise ValueError("configuration_rejected")
    host = value.value
    if not host.endswith("-pooler.c-5.eu-central-1.aws.neon.tech"):
        raise ValueError("configuration_rejected")
    return host


def elapsed_ms(started: float) -> int:
    return min(60_000, max(0, int((time.monotonic() - started) * 1000)))


def resolve(host: str, port: int) -> tuple[list, str, int]:
    """One system-resolver invocation, not a promise about DNS packet count."""
    result = queue.Queue(maxsize=1)
    started = time.monotonic()

    def worker():
        try:
            rows = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC,
                                     type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
            result.put((rows, "resolved"))
        except Exception as exc:
            result.put(([], error_category(exc)))

    threading.Thread(target=worker, daemon=True).start()
    try:
        rows, status = result.get(timeout=DNS_SECONDS)
    except queue.Empty:
        rows, status = [], "dns_deadline_exceeded"
    return rows, status, elapsed_ms(started)


def addresses_for(rows: list, family: int) -> tuple[list, int]:
    addresses = []
    rejected = 0
    for row in rows:
        if not isinstance(row, tuple) or len(row) != 5 or row[0] != family:
            continue
        address = row[4]
        try:
            ip = ipaddress.ip_address(address[0])
            valid = (row[1] == socket.SOCK_STREAM and row[2] == socket.IPPROTO_TCP
                     and isinstance(address, tuple)
                     and len(address) == (2 if family == socket.AF_INET else 4)
                     and address[1] == PORT and ip.is_global
                     and ip.version == (4 if family == socket.AF_INET else 6)
                     and (family != socket.AF_INET6 or address[2:] == (0, 0)))
        except (ValueError, TypeError, IndexError):
            valid = False
        if not valid:
            rejected += 1
        elif address not in addresses:
            addresses.append(address)
    return addresses, rejected


def tcp_attempt(family: int, address: tuple) -> dict:
    started = time.monotonic()
    try:
        with socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP) as sock:
            sock.settimeout(TCP_SECONDS)
            sock.connect(address)
            # Deliberately no send/recv, TLS, startup packet, login or SQL.
        status = "connected_no_payload"
    except Exception as exc:
        status = error_category(exc)
    return {"status": status, "elapsed_ms": elapsed_ms(started)}


def observe(source: Path) -> dict:
    started = time.monotonic()
    try:
        host = read_target(source)
    except Exception:
        return {"schema": SCHEMA, "status": "configuration_rejected"}
    rows, status, dns_ms = resolve(host, PORT)
    report = {"schema": SCHEMA, "status": "observed",
              "credentials_used": False, "protocol_bytes_sent": 0,
              "dns_budget_seconds": DNS_SECONDS, "tcp_timeout_seconds": TCP_SECONDS,
              "max_addresses_per_family": MAX_PER_FAMILY,
              "target": "reviewed_expected_pooler", "dns_status": status,
              "dns_elapsed_ms": dns_ms, "families": [],
              "control_dns": {"status": "not_needed"}}
    for family, label in ((socket.AF_INET, "ipv4"), (socket.AF_INET6, "ipv6")):
        addresses, rejected = addresses_for(rows, family)
        item = {"family": label, "resolved_address_count": len(addresses),
                "rejected_address_count": rejected, "attempts": []}
        for address in addresses[:MAX_PER_FAMILY]:
            attempt = tcp_attempt(family, address)
            item["attempts"].append(attempt)
            if attempt["status"] in {"connected_no_payload", "local_permission_denied",
                                     "family_unsupported"}:
                break
        report["families"].append(item)
    if not any(x["resolved_address_count"] for x in report["families"]):
        control_rows, control_status, control_ms = resolve(CONTROL_HOST, 443)
        report["control_dns"] = {
            "status": control_status, "elapsed_ms": control_ms,
            "ipv4_record_count": sum(row[0] == socket.AF_INET for row in control_rows),
            "ipv6_record_count": sum(row[0] == socket.AF_INET6 for row in control_rows),
            "tcp_attempted": False,
        }
    report["elapsed_ms"] = elapsed_ms(started)
    return report


def main(argv: list[str] | None = None) -> int:
    if (sys.argv[1:] if argv is None else argv) != ["--run-once"]:
        print(json.dumps({"schema": SCHEMA, "status": "explicit_run_flag_required"}))
        return 2
    try:
        report = observe(Path(__file__).resolve().parents[2] / "bridge_school_api" / "db.py")
    except Exception:
        report = {"schema": SCHEMA, "status": "diagnostic_failed"}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
