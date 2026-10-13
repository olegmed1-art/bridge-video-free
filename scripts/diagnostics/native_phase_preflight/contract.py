"""Strict synthetic-result schema. Never a production diagnostic gate."""
import hashlib
from pathlib import Path
import re

SCHEMA = "incident1911-native-abi-emulation-v1"
CASES = {"bad": (0, ["BAD"], False, 1),
         "started": (1, ["STARTED", "BAD"], False, 1),
         "ssl": (2, ["SSL_STARTUP", "BAD"], False, 1),
         "auth": (3, ["AUTHENTICATING", "BAD"], False, 1),
         "unknown": (4, ["UNRECOGNIZED", "BAD"], False, 1),
         "success": (5, ["STARTED", "OK"], True, 1),
         "mixed": (1, ["STARTED", "BAD"], False, 2)}
MESSAGES = ("connection attempt: %s", "connection failed: %s: %s",
            "connection succeeded: %s", "connection started: %s", "connection polled: %s")
EVENTS = ("attempt", "failed", "succeeded", "started", "polled")
OUTER = {"attempt": 105, "failed": 111, "succeeded": 116}
SOURCE_FILES = ("psycopg/connection.py", "psycopg/_connection_base.py", "psycopg/waiting.py")
EMBEDDED_SOURCE = "psycopg_binary/_psycopg/generators.pyx"


def require(ok):
    if not ok:
        raise ValueError("contract_rejected")


def site(record, runtime, extension):
    require(record.msg in MESSAGES)
    if record.pathname == EMBEDDED_SOURCE:
        require(record.msg in MESSAGES[3:] and record.funcName == "connect" and
                type(record.lineno) is int and 0 < record.lineno < 10000)
        return [EMBEDDED_SOURCE, "connect", record.lineno, hashlib.sha256(Path(extension).read_bytes()).hexdigest()]
    require(record.msg in MESSAGES[:3])
    root = Path(runtime).resolve()
    path = Path(record.pathname).resolve()
    require(path.is_relative_to(root))
    relative = path.relative_to(root).as_posix()
    require(relative in SOURCE_FILES and type(record.funcName) is str and
            re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", record.funcName) is not None and
            type(record.lineno) is int and 0 < record.lineno < 10000)
    return [relative, record.funcName, record.lineno, hashlib.sha256(path.read_bytes()).hexdigest()]


def validate_sites(sites):
    require(type(sites) is dict and set(sites) <= set(EVENTS))
    for event, value in sites.items():
        require(type(value) is list and len(value) == 4)
        path, func, line, digest = value
        require(type(path) is str and path in (*SOURCE_FILES, EMBEDDED_SOURCE) and type(func) is str and
                re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", func) is not None and
                type(line) is int and 0 < line < 10000 and type(digest) is str and
                re.fullmatch(r"[a-f0-9]{64}", digest) is not None)
        if event in OUTER:
            require(value[:3] == ["psycopg/connection.py", "connect", OUTER[event]])
        else:
            require(value[:2] == [EMBEDDED_SOURCE, "connect"])


def validate(payload, case, observed):
    require(type(payload) is dict and set(payload) == {
        "schema", "case", "observed", "sites", "events", "counters",
        "abi_emulated_public_return", "real_connection", "tls_verified",
        "auth_verified", "production_gate_enabled", "samples", "unknown_count",
        "phase_claim", "errno", "sqlstate", "exception_class"})
    require(case in CASES and type(observed) is bool)
    mode, samples, returns, count = CASES[case]
    require(payload["schema"] == SCHEMA and payload["case"] == case and payload["observed"] is observed)
    require(payload["abi_emulated_public_return"] is returns)
    for key in ("real_connection", "tls_verified", "auth_verified", "production_gate_enabled"):
        require(payload[key] is False)
    for key in ("phase_claim", "errno", "sqlstate", "exception_class"):
        require(payload[key] is None)
    expected = ["attempt", "started"] + ([] if mode == 0 else ["polled"]) + ["succeeded" if returns else "failed"]
    require(payload["events"] == expected * count)
    validate_sites(payload["sites"])
    require(set(payload["sites"]) == set(expected))
    counters = payload["counters"]
    require(type(counters) is list and len(counters) == 6 and
            all(type(x) is int and 0 <= x <= 100 for x in counters))
    require(counters[0] == count and counters[1] == (0 if mode == 0 else count) and
            counters[3:] == [count, 1 if returns else 0, 0])
    require(payload["samples"] == ([samples] * count if observed else []))
    require(type(payload["unknown_count"]) is int and payload["unknown_count"] == (1 if observed and case == "unknown" else 0))
    return payload


def compare(discovery, observation, case):
    validate(discovery, case, False)
    validate(observation, case, True)
    require(discovery["sites"] == observation["sites"])
    expected_reads = sum(x in ("started", "polled") for x in discovery["events"])
    require(observation["counters"][2] == discovery["counters"][2] + expected_reads)
    require(discovery["counters"][:2] + discovery["counters"][3:] ==
            observation["counters"][:2] + observation["counters"][3:])
