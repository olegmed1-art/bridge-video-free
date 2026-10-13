"""Only executed behind guard + empty netns. No production imports/inputs."""
import ctypes
import gc
import hashlib
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from contract import CASES, EMBEDDED_SOURCE, EVENTS, MESSAGES, SCHEMA, require, site, validate
MARKER_FILE = None


def mark(code):
    # No arbitrary data; file is opened exclusively once by main in a fresh run.
    require(type(code) is int and 1 <= code <= 16 and MARKER_FILE is not None)
    with MARKER_FILE.open("a", encoding="ascii") as stream:
        stream.write(str(code) + "\n")


def run(case, observed, output, expected_path):
    mark(1)
    require(sys.platform == "linux" and sys.version_info[:2] == (3, 12))
    require(dict(os.environ) == {"LC_ALL": "C.UTF-8"})
    require(os.getuid() != 0 and os.geteuid() != 0)
    mark(2)
    guard = ctypes.CDLL(str(ROOT / "guard.so"))
    guard.np_lockdown.restype = ctypes.c_int
    mark(3)
    require(guard.np_lockdown() == 0)
    mark(4)
    runtime = ROOT / "runtime"
    libs = list((runtime / "psycopg_binary.libs").glob("libpq-*.so*"))
    require(len(libs) == 1)
    mark(5)
    shim = ctypes.CDLL(str(ROOT / "shim.so"), mode=ctypes.RTLD_GLOBAL)
    shim.np_init.argtypes = (ctypes.c_char_p, ctypes.c_int)
    shim.np_init.restype = ctypes.c_int
    shim.np_counter.argtypes = (ctypes.c_int,)
    shim.np_counter.restype = ctypes.c_int
    mark(6)
    require(shim.np_init(os.fsencode(libs[0].resolve()), CASES[case][0]) == 0)
    sys.path.insert(0, str(runtime))
    mark(7)
    import psycopg
    from psycopg_binary import _psycopg
    from psycopg import pq, generators
    mark(8)
    extension = Path(_psycopg.__file__).resolve()
    require(extension.is_relative_to(runtime.resolve()) and extension.suffix == ".so")
    require(psycopg.__version__ == "3.3.4" and pq.__impl__ == "binary" and pq.version() == 180000)
    require(pq.PGconn.__module__ == "psycopg_binary.pq" and generators.connect.__module__ == "psycopg_binary._psycopg")
    require(hashlib.sha256((runtime / "psycopg/connection.py").read_bytes()).hexdigest() ==
            "5dfacd2ef35f748c5a60c07c8bf5d6e6a2fa25438ee1794591b4a4e4e70afd7e")
    mark(9)
    phase = None
    if observed:
        expected = json.loads(Path(expected_path).read_text(encoding="ascii"))
        require(set(expected) == set(EVENTS))
        from contract import validate_sites
        validate_sites(expected)
        from phase_observer import PhaseObserver
        callsites = {}
        for message, event in zip(MESSAGES, EVENTS):
            rel, func, line, digest = expected[event]
            path = extension if rel == EMBEDDED_SOURCE else runtime / rel
            require(hashlib.sha256(path.read_bytes()).hexdigest() == digest)
            callsites[message] = (rel if rel == EMBEDDED_SOURCE else str(path), func, line)
        phase = PhaseObserver(pgconn_type=pq.PGconn, callsites=callsites)
    seen, sites = [], {}

    class Capture(logging.Handler):
        def emit(self, record):
            require(type(record) is logging.LogRecord and record.name == "psycopg" and
                    record.levelno == logging.DEBUG and type(record.msg) is str and record.msg in MESSAGES)
            event = EVENTS[MESSAGES.index(record.msg)]
            require(len(seen) < 16 and type(record.args) is tuple)
            if event in ("started", "polled"):
                require(len(record.args) == 1 and type(record.args[0]) is pq.PGconn)
            else:
                require(len(record.args) == (2 if event == "failed" else 1) and
                        all(type(x) is str for x in record.args))
            normalized = site(record, runtime, extension)
            require(event not in sites or sites[event] == normalized)
            sites[event] = normalized
            seen.append(event)
            if phase is not None:
                before = shim.np_counter(2)
                phase.emit(record)
                require(shim.np_counter(2) == before + (1 if event in ("started", "polled") else 0))

    mark(10)
    capture = Capture(logging.DEBUG)
    logger = logging.getLogger("psycopg")
    require(not logger.handlers)
    old_level, old_propagate = logger.level, logger.propagate
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(capture)
    returned = False
    mark(11)
    try:
        # Public test-net literals, no host name / service / defaults / credentials.
        addresses = "192.0.2.1,2001:db8::1" if case == "mixed" else "192.0.2.1"
        try:
            connection = psycopg.Connection.connect(
                host=addresses, hostaddr=addresses, port="5432", user="synthetic",
                dbname="synthetic", password="synthetic", sslmode="disable", gssencmode="disable",
                connect_timeout="2", application_name="abi-emulation-only")
        except psycopg.Error as exc:
            if exc.pgconn is not None:
                exc.pgconn.finish()
        else:
            returned = True
            connection.pgconn.finish()
            connection._closed = True
            del connection
    finally:
        logger.removeHandler(capture)
        logger.setLevel(old_level)
        logger.propagate = old_propagate
        capture.close()
    mark(12)
    gc.collect()
    mark(13)
    samples, unknown = [], 0
    if phase is not None:
        snapshot = phase.snapshot()
        require(not snapshot["disarmed"] and snapshot["contract_errors"] == 0 and
                snapshot["outer_pairs_complete"] and not snapshot["pending_observation"] and
                not snapshot["attempt_retention_overflow"] and not snapshot["complete_state_trace_available"])
        require(snapshot["dns_evidence"] == "not_observed" and snapshot["per_attempt_exception_metadata"] == "not_exposed")
        families = ["ipv4", "ipv6"] if case == "mixed" else ["ipv4"]
        require([r["family"] for r in snapshot["attempts"]] == families)
        for row in snapshot["attempts"]:
            require(row["failure_phase"] is None and row["reason"] == "unknown" and
                    all(row[k] is None for k in ("errno", "sqlstate", "exception_class")) and
                    not row["sample_retention_overflow"])
            samples.append(row["state_samples"])
            unknown += row["unknown_status_count"]
        phase.close()
    payload = {"schema": SCHEMA, "case": case, "observed": observed,
               "sites": sites, "events": seen, "counters": [shim.np_counter(i) for i in range(6)],
               "abi_emulated_public_return": returned, "real_connection": False,
               "tls_verified": False, "auth_verified": False, "production_gate_enabled": False,
               "samples": samples, "unknown_count": unknown,
               "phase_claim": None, "errno": None, "sqlstate": None, "exception_class": None}
    mark(14)
    validate(payload, case, observed)
    data = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    require(len(data) <= 8192)
    mark(15)
    with Path(output).open("x", encoding="ascii") as stream:
        stream.write(data)
    mark(16)


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 4 and sys.argv[1] in CASES and sys.argv[2] in ("discover", "observe"))
        MARKER_FILE = ROOT / (sys.argv[1] + "-" + sys.argv[2] + ".progress")
        with MARKER_FILE.open("x", encoding="ascii"):
            pass
        run(sys.argv[1], sys.argv[2] == "observe", ROOT / (sys.argv[1] + "-" + sys.argv[2] + ".json"), sys.argv[3])
    except BaseException:
        if MARKER_FILE is not None:
            try:
                with MARKER_FILE.open("a", encoding="ascii") as stream:
                    stream.write("F\n")
            except BaseException:
                pass
        os._exit(98)
