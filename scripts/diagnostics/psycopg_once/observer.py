"""Offline-reviewed passive observer for psycopg 3.3.4 connection logging.

No import of psycopg, environment/secret reads, logger setup or connection.
An optional callback publishes only the wrapper's validated safe snapshot.
The retention limit bounds observations only, NEVER connection attempts.
Install only in a separately reviewed isolated runtime, never in the API.
"""
from __future__ import annotations

import ast
import ipaddress
import logging
import re
import time

SCHEMA = "incident-1911-passive-attempts-v1"
CLIENT_VERSION = "3.3.4"
MAX_RETAINED_ATTEMPTS = 128
MAX_TEXT = 32768
MAX_COUNTER = 1000000
MAX_MS = 25000
START = "connection attempt: %s"
FAILED = "connection failed: %s: %s"
SUCCEEDED = "connection succeeded: %s"
EVENTS = {START: (105, 1), FAILED: (111, 2), SUCCEEDED: (116, 1)}
PHRASES = (
    ("dns", ("could not translate host name", "failed to resolve host",
             "name or service not known", "temporary failure in name resolution")),
    ("tcp_refused", ("connection refused",)),
    ("tcp_network_unreachable", ("network is unreachable",)),
    ("tcp_no_route", ("no route to host",)),
    ("tcp_reset", ("connection reset by peer",)),
    ("tls", ("ssl error:", "ssl syscall error:", "certificate verify failed",
             "ssl handshake failed", "tls handshake failed", "server does not support ssl",
             "ssl connection has been closed unexpectedly")),
    ("channel_binding", ("channel binding required", "channel binding is required",
                         "channel binding mismatch")),
    ("auth", ("authentication failed", "no password supplied", "no pg_hba.conf entry")),
    ("server", ("server closed the connection unexpectedly", "database system is starting up",
                "database system is shutting down", "database system is in recovery mode",
                "too many connections", "remaining connection slots are reserved")),
    ("timeout", ("timeout expired", "timed out", "connection timeout")),
)


def hints(message: object) -> list[str]:
    """Fixed hints, not a stage/root-cause proof; never coerce arbitrary objects."""
    if type(message) is not str or len(message) > MAX_TEXT:
        return ["unknown"]
    value = message.lower()
    return [name for name, phrases in PHRASES if any(p in value for p in phrases)] or ["unknown"]


def family(descriptor: object) -> str:
    """Discard all descriptor contents except the address family."""
    if type(descriptor) is not str or len(descriptor) > 4096:
        return "unknown"
    match = re.fullmatch(r"host: (.+), port: (.+), hostaddr: (.+)", descriptor)
    if not match:
        return "unknown"
    try:
        address = ast.literal_eval(match[3])
        if type(address) is not str or len(address) > 64 or "%" in address:
            return "unknown"
        return "ipv4" if ipaddress.ip_address(address).version == 4 else "ipv6"
    except (ValueError, SyntaxError, TypeError, RecursionError):
        return "unknown"


class AttemptObserver(logging.Handler):
    """Collect in memory; NEVER format, emit raw records, raise a cap or stop a call.

    The wrapper establishes a single-threaded, single-call context.
    This object deliberately does not configure or attach any logger.
    No elapsed-time or row-count field constitutes a network budget enforcement.
    """

    def __init__(self, clock=time.monotonic_ns, publish=None):
        super().__init__(logging.DEBUG)
        self._clock = clock
        self._publish = publish
        self._attempts = []
        self._active = None
        self._started = 0
        self._completed = 0
        self._overflow = False
        self._contract_errors = 0
        self._ignored = 0

    def _problem(self):
        self._contract_errors = min(MAX_COUNTER, self._contract_errors + 1)

    def _now(self):
        now = self._clock()
        if type(now) is not int or now < 0:
            raise ValueError("invalid_clock")
        return now

    def emit(self, record):
        # Never invoke handleError: its standard implementation can print the
        # raw LogRecord. The callback receives no record or raw argument.
        try:
            self._observe(record)
            if self._publish is not None:
                self._publish()
        except Exception:
            self._problem()

    def _observe(self, record):
        if type(record) is not logging.LogRecord:
            self._problem()
            return
        if record.name != "psycopg" or record.levelno != logging.DEBUG:
            self._ignored = min(MAX_COUNTER, self._ignored + 1)
            return
        if type(record.msg) is not str or record.msg not in EVENTS:
            self._ignored = min(MAX_COUNTER, self._ignored + 1)
            return
        line, count = EVENTS[record.msg]
        if (record.funcName != "connect" or record.lineno != line or
                type(record.args) is not tuple or len(record.args) != count or
                any(type(arg) is not str or len(arg) > MAX_TEXT for arg in record.args)):
            self._problem()
            return
        now = self._now()
        if record.msg == START:
            if self._active is not None:
                self._problem()
            self._started = min(MAX_COUNTER, self._started + 1)
            current = {"index": self._started, "family": family(record.args[0]),
                       "outcome": "incomplete", "duration_ms": None, "hints": []}
            if len(self._attempts) < MAX_RETAINED_ATTEMPTS:
                self._attempts.append(current)
            else:
                self._overflow = True
            self._active = (now, current)
            return
        if self._active is None:
            self._problem()
            return
        began, current = self._active
        if family(record.args[0]) != current["family"]:
            self._problem()
        current["duration_ms"] = min(MAX_MS, max(0, (now - began) // 1000000))
        current["outcome"] = "failed" if record.msg == FAILED else "attempt_succeeded"
        current["hints"] = hints(record.args[1]) if record.msg == FAILED else []
        self._completed = min(MAX_COUNTER, self._completed + 1)
        self._active = None

    def snapshot(self) -> dict:
        """Only fresh copies of fixed-schema safe observations leave the object."""
        return {
            "schema": SCHEMA,
            "client_source_contract": CLIENT_VERSION,
            "mode": "passive_logging_only",
            "hard_attempt_cap_enforced": False,
            "retained_attempt_limit": MAX_RETAINED_ATTEMPTS,
            "observed_starts": self._started,
            "observed_completions": self._completed,
            "retention_overflow": self._overflow,
            "contract_errors": self._contract_errors,
            "ignored_records": self._ignored,
            "pending_observation": self._active is not None,
            "paired_observations_complete": bool(self._started) and not (
                self._overflow or self._contract_errors or self._active is not None
            ) and self._started == self._completed,
            "internal_libpq_transport_attempt_count": None,
            "individual_sqlstates_available": False,
            "public_call_returned": None,
            "attempts": [{**row, "hints": list(row["hints"])} for row in self._attempts],
        }
