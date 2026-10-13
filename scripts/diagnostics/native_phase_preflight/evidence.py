"""Closed vocabulary: no error objects or arbitrary child data in output."""
import json
from pathlib import Path
from diagnostics import exception_summary, validate_exception
from process_runner import validate_result
from route_table import summary, validate_summary

CASES = ("bad", "started", "ssl", "auth", "unknown", "success", "mixed")
CONTEXT = ("arguments", "platform", "python_version", "environment", "identity", "groups",
           "namespace", "interfaces", "ipv4_routes", "ipv6_addresses", "capabilities",
           "no_new_privileges", "file_integrity", "compiler_available")
COMPILE = ("compile_guard", "compile_guard_shared", "compile_shim")
NATIVE = tuple(mode + "_" + case for mode in ("discover", "observe") for case in CASES)
PHASES = CONTEXT + COMPILE + ("canary",) + NATIVE + ("merge_callsites", "write_callsites", "complete")
REASONS = ("none", "rejected", "unavailable", "read_failed", "timeout", "nonzero_exit",
           "unexpected_signal", "canary_not_sigsys", "launch_failed", "worker_failed",
           "guard_rejected", "worker_sigsys", "worker_sigkill", "worker_signal",
           "receipt_missing", "receipt_size", "receipt_invalid", "contract_mismatch",
           "callsite_conflict", "callsite_missing", "write_failed", "unexpected_failure",
           "marker_invalid", "marker_incomplete", "worker_status_conflict", "cleanup_failed",
           "capture_failed", "diagnostic_missing", "diagnostic_invalid", "process_result_invalid", "shim_rejected")
CHILD_PHASES = ("context", "guard_load", "guard_lockdown", "library_discovery", "shim_load",
                "shim_init", "client_import", "abi_identity", "observer_config",
                "capture_config", "public_connect", "post_connect", "observer_validation",
                "payload_validation", "receipt_write", "complete")
SCOPES = ("harness", "isolation", "check")


class Failure(Exception):
    def __init__(self, reason, scope="harness"):
        if reason not in REASONS or scope not in SCOPES:
            raise ValueError("invalid_fixed_failure")
        self.reason, self.scope = reason, scope


def child_progress(data):
    """Strict BEGIN/PASS pairs plus an optional FAIL for the active phase."""
    empty = {"state": "not_observed", "last_phase": None, "last_completed_phase": None}
    invalid = {"state": "invalid", "last_phase": None, "last_completed_phase": None}
    if data is None: return empty
    if type(data) is not bytes or not data or len(data) > 256 or not data.endswith(b"\n"): return invalid
    try: lines = data.decode("ascii").split("\n")[:-1]
    except UnicodeError: return invalid
    passed, active, failed = 0, None, False
    for token in lines:
        if failed: return invalid
        if active is None:
            if passed >= len(CHILD_PHASES) or token != "B" + str(passed + 1): return invalid
            active = passed + 1
        elif token == "P" + str(active):
            passed, active = active, None
        elif token == "F" + str(active): failed = True
        else: return invalid
    last = active or passed
    return {"state": "failed" if failed else "observed",
            "last_phase": CHILD_PHASES[last - 1],
            "last_completed_phase": CHILD_PHASES[passed - 1] if passed else None}


class Evidence:
    def __init__(self, emit):
        self.emit, self.phase = emit, "arguments"
        self.units = {x: "NOT_RUN" for x in COMPILE + ("canary",) + NATIVE}
        self.children = {x: {"state": "not_observed", "last_phase": None, "last_completed_phase": None} for x in NATIVE}
        self.confirmed, self.events = [], 0
        self.native_launch_calls = 0
        self.processes = {}
        self.child_diagnostics = {}
        self.exception = None
        self.toolchain = None
        self.route_summary = summary("not_observed")

    def set_route_summary(self, value):
        validate_summary(value)
        self.route_summary = value.copy()

    def event(self, state, reason="none", scope="harness"):
        if (self.phase not in PHASES or state not in ("STARTED", "PASS", "HARNESS_FAILED", "ISOLATION_FAILED", "CHECK_FAILED")
                or reason not in REASONS or scope not in SCOPES):
            raise ValueError("invalid_evidence")
        self.events += 1
        if self.events > 100: raise ValueError("evidence_budget")
        if self.phase in self.units: self.units[self.phase] = state
        if state == "PASS": self.confirmed.append(self.phase)
        payload = {"schema": "native-preflight-phase-v4", "phase": self.phase,
                   "state": state, "reason": reason, "scope": scope}
        if self.phase == "ipv4_routes":
            validate_summary(self.route_summary)
            payload["ipv4_route_table"] = self.route_summary.copy()
        if state != "STARTED" and self.phase in self.processes:
            validate_result(self.processes[self.phase])
            payload["process"] = self.processes[self.phase]
        if state != "STARTED" and self.phase == "compiler_available":
            payload["toolchain"] = self.toolchain
        if state != "STARTED" and self.exception is not None:
            validate_exception(self.exception)
            payload["exception"] = self.exception
        self.emit(payload)

    def step(self, phase, fn, *, false_reason="rejected", error_reason="read_failed", scope="harness"):
        self.phase = phase
        self.event("STARTED", scope=scope)
        try:
            value = fn()
            if value is False: raise Failure(false_reason, scope)
        except Failure: raise
        except BaseException as exc:
            self.exception = exception_summary(exc, Path(__file__).resolve().parent)
            raise Failure(error_reason, scope) from None
        self.event("PASS", scope=scope)
        return value

    def finish(self, error=None):
        if error is None:
            status, reason, scope = "PASS", "none", "check"
        else:
            reason = error.reason if type(error) is Failure else "unexpected_failure"
            scope = error.scope if type(error) is Failure else "harness"
            status = {"harness": "HARNESS_FAILED", "isolation": "ISOLATION_FAILED", "check": "CHECK_FAILED"}[scope]
            self.event(status, reason, scope)
        native_pass = sum(self.units[x] == "PASS" for x in NATIVE)
        native_started = sum(self.units[x] != "NOT_RUN" for x in NATIVE)
        verified = (status == "PASS" and native_pass == 14 and
                    self.route_summary["parse_status"] in ("empty", "header_only") and
                    all(self.units[x] == "PASS" for x in COMPILE + ("canary",)) and
                    len(self.processes) == 18 and all(p["direct_child_reaped"] and p["group_state"] == "absent" and
                    p["adapter_error"] is None and not p["timed_out"] for p in self.processes.values()) and
                    set(CONTEXT + ("merge_callsites", "write_callsites", "complete")) <= set(self.confirmed))
        if status == "PASS" and not verified:
            status, reason, scope = "HARNESS_FAILED", "unexpected_failure", "harness"
        validate_summary(self.route_summary)
        return {"schema": "incident1911-native-preflight-receipt-v4", "status": status,
                "failure_scope": None if verified else scope,
                "failure_phase": None if verified else self.phase, "reason": reason,
                "last_confirmed_phase": self.confirmed[-1] if self.confirmed else None,
                "units": self.units, "child_progress": self.children,
                "processes": self.processes, "child_diagnostics": self.child_diagnostics,
                "toolchain": self.toolchain, "exception": self.exception,
                "native_processes_started": sum(self.processes.get(x, {}).get("spawned", False) for x in NATIVE),
                "native_processes_reaped": sum(self.processes.get(x, {}).get("direct_child_reaped", False) for x in NATIVE),
                "ipv4_route_table": self.route_summary.copy(),
                "native_expected": 14, "native_checks_started": native_started,
                "native_launch_calls": self.native_launch_calls,
                "native_passed": native_pass, "native_not_run": 14 - native_started,
                "native_preflight_verified": verified,
                "application_tested": False, "tls_verified": False, "auth_verified": False,
                "production_gate_enabled": False}


def print_safe(payload):
    print(json.dumps(payload, sort_keys=True, ensure_ascii=True), flush=True)
