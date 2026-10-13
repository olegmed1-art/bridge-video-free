"""Local-only passive evidence candidate; NOT attached to any runtime.

The future caller must verify the pinned binary PGconn class and every emitted
callsite in an isolated, single-threaded, single-connect runtime first. Native
Linux callsite metadata has NOT been established by this local candidate.

Reads only PGconn.status, never polls/connects/formats an object. No logger
configuration, disk callback, psycopg import, network, or executable entrypoint.
Observed states are partial samples, never a failure phase or causal diagnosis.
"""
from __future__ import annotations

import ast
import ipaddress
import logging
import re
import threading

SCHEMA = "incident-1911-observed-libpq-states-local-v1"
START = "connection attempt: %s"
FAILED = "connection failed: %s: %s"
SUCCEEDED = "connection succeeded: %s"
INNER_START = "connection started: %s"
INNER_POLL = "connection polled: %s"
MESSAGES = (START, FAILED, SUCCEEDED, INNER_START, INNER_POLL)
OUTER_LINES = {START: 105, FAILED: 111, SUCCEEDED: 116}
MAX_ATTEMPTS = 16
MAX_SAMPLES = 16
MAX_COUNTER = 1000000
MAX_DESCRIPTOR = 4096
# ConnStatus, NOT PollingStatus. No inference from order, absence or last state.
STATES = {
    0: "OK", 1: "BAD", 2: "STARTED", 3: "MADE", 4: "AWAITING_RESPONSE",
    5: "AUTH_OK", 6: "SETENV", 7: "SSL_STARTUP", 8: "NEEDED",
    9: "CHECK_WRITABLE", 10: "CONSUME", 11: "GSS_STARTUP",
    12: "CHECK_TARGET", 13: "CHECK_STANDBY", 14: "ALLOCATED",
    15: "AUTHENTICATING",
}


def _family(descriptor):
    """Return only address family; never retain text or a hash of it."""
    if type(descriptor) is not str or len(descriptor) > MAX_DESCRIPTOR:
        return None
    match = re.fullmatch(r"host: (.+), port: (.+), hostaddr: (.+)", descriptor)
    if match is None:
        return None
    try:
        address = ast.literal_eval(match[3])
        if type(address) is not str or len(address) > 64 or "%" in address:
            return None
        return "ipv4" if ipaddress.ip_address(address).version == 4 else "ipv6"
    except (ValueError, SyntaxError, TypeError, RecursionError):
        return None


def _increment(value):
    return min(MAX_COUNTER, value + 1)


class PhaseObserver(logging.Handler):
    """Passive handler with explicit source/type contracts and bounded memory.

    `callsites` maps each MESSAGES entry to (exact pathname, funcName, lineno).
    Inner Cython callsite tuples must come from a separately verified runtime,
    not guesses from .pyx line numbers. A matching tuple is an integrity guard,
    not authentication against malicious code in the same Python process.

    One generator must own the native PGconn throughout each outer attempt.
    We retain only its integer identity, never extend its lifetime. Any contract
    violation disables collection permanently; it never interrupts the client.
    Unknown numeric ConnStatus values are expected and do not disable collection.
    Retention bounds affect evidence only, never the number of connection attempts.
    """

    def __init__(self, *, pgconn_type, callsites):
        if not isinstance(pgconn_type, type):
            raise ValueError("invalid_pgconn_type")
        if (type(callsites) is not dict or len(callsites) != len(MESSAGES)
                or any(type(k) is not str or k not in MESSAGES for k in callsites)):
            raise ValueError("invalid_callsites")
        for message, site in callsites.items():
            if (type(site) is not tuple or len(site) != 3
                    or type(site[0]) is not str or not 0 < len(site[0]) <= 1024
                    or type(site[1]) is not str or not 0 < len(site[1]) <= 256
                    or type(site[2]) is not int or not 0 < site[2] <= MAX_COUNTER):
                raise ValueError("invalid_callsite")
            if message in OUTER_LINES and site[1:] != ("connect", OUTER_LINES[message]):
                raise ValueError("unsupported_outer_callsite")
        super().__init__(logging.DEBUG)
        self._pgconn_type = pgconn_type
        self._callsites = dict(callsites)
        self._thread = threading.get_ident()
        self._attempts = []
        self._active = None
        self._object_id = None
        self._started = 0
        self._completed = 0
        self._ignored = 0
        self._errors = 0
        self._disarmed = False
        self._overflow = False

    def _disarm(self):
        self._errors = _increment(self._errors)
        self._disarmed = True
        self._object_id = None
        self._active = None

    def emit(self, record):
        # Do NOT call handleError(), getMessage(), a formatter or raw sink.
        # Ordinary observation errors must not escape into the client call.
        try:
            if not self._disarmed:
                self._observe(record)
        except Exception:
            self._disarm()

    def _observe(self, record):
        if type(record) is not logging.LogRecord:
            self._disarm()
            return
        if (type(record.name) is not str or record.name != "psycopg"
                or type(record.levelno) is not int or record.levelno != logging.DEBUG
                or type(record.msg) is not str or record.msg not in MESSAGES):
            self._ignored = _increment(self._ignored)
            return
        site = self._callsites[record.msg]
        if (type(record.pathname) is not str or record.pathname != site[0]
                or type(record.funcName) is not str or record.funcName != site[1]
                or type(record.lineno) is not int or record.lineno != site[2]
                or type(record.thread) is not int or record.thread != self._thread
                or threading.get_ident() != self._thread
                or type(record.args) is not tuple
                or record.exc_info is not None or record.exc_text is not None
                or record.stack_info is not None):
            self._disarm()
            return
        if record.msg in OUTER_LINES:
            self._outer(record)
        else:
            self._inner(record)

    def _outer(self, record):
        expected = 2 if record.msg == FAILED else 1
        # The error string is type-checked ONLY. Never inspect its content,
        # length, representation, exception class, or apparent code/phase.
        if (len(record.args) != expected or type(record.args[0]) is not str
                or (expected == 2 and type(record.args[1]) is not str)):
            self._disarm()
            return
        family = _family(record.args[0])
        if family is None:
            self._disarm()
            return
        if record.msg == START:
            if self._active is not None:
                self._disarm()
                return
            self._started = _increment(self._started)
            self._active = {
                "index": self._started, "family": family, "outcome": "incomplete",
                "state_samples": [], "observed_state_count": 0,
                "unknown_status_count": 0, "sample_retention_overflow": False,
                "bad_observed": False,
            }
            self._object_id = None
            if len(self._attempts) < MAX_ATTEMPTS:
                self._attempts.append(self._active)
            else:
                self._overflow = True
            return
        if self._active is None or family != self._active["family"]:
            self._disarm()
            return
        self._active["outcome"] = "failed" if record.msg == FAILED else "attempt_succeeded"
        self._completed = _increment(self._completed)
        self._active = None
        self._object_id = None

    def _inner(self, record):
        if (self._active is None or len(record.args) != 1
                or type(record.args[0]) is not self._pgconn_type):
            self._disarm()
            return
        conn = record.args[0]
        if record.msg == INNER_START:
            if self._object_id is not None:
                self._disarm()
                return
            self._object_id = id(conn)
        elif self._object_id is None or id(conn) != self._object_id:
            self._disarm()
            return
        # Verified native property calls PQstatus only; never PQconnectPoll.
        # This local reference ends on return. No record/PGconn is stored.
        status = conn.status
        if type(status) is not int:
            self._disarm()
            return
        row = self._active
        row["observed_state_count"] = _increment(row["observed_state_count"])
        label = STATES.get(status)
        if label is None:
            row["unknown_status_count"] = _increment(row["unknown_status_count"])
            label = "UNRECOGNIZED"
        if label == "BAD":
            row["bad_observed"] = True
        if len(row["state_samples"]) < MAX_SAMPLES:
            row["state_samples"].append(label)
        else:
            row["sample_retention_overflow"] = True

    def snapshot(self):
        """Fixed safe schema; copies all mutable data, no raw/native objects."""
        return {
            "schema": SCHEMA, "mode": "local_candidate_passive_samples",
            "disarmed": self._disarmed, "contract_errors": self._errors,
            "ignored_records": self._ignored,
            "observed_starts": self._started,
            "observed_completions": self._completed,
            "attempt_retention_overflow": self._overflow,
            "pending_observation": self._active is not None,
            "outer_pairs_complete": bool(self._started) and not (
                self._disarmed or self._overflow or self._active is not None
            ) and self._started == self._completed,
            "complete_state_trace_available": False,
            "dns_evidence": "not_observed",
            "per_attempt_exception_metadata": "not_exposed",
            "internal_libpq_transport_attempt_count": None,
            "public_call_returned": None,
            "hard_attempt_cap_enforced": False,
            "attempts": [{
                **row, "state_samples": list(row["state_samples"]),
                "state_evidence": "observed_samples" if row["observed_state_count"] else "not_observed",
                "failure_phase": None, "reason": "unknown",
                "sqlstate": None, "errno": None, "exception_class": None,
            } for row in self._attempts],
        }
