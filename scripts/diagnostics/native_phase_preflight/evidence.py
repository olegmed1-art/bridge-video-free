"""Closed vocabulary: no error objects or arbitrary child data in output."""
import json

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
           "marker_invalid", "marker_incomplete", "worker_status_conflict")
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
    """Fixed sequence only. Last marker proves entry, not an inferred cause."""
    if data is None: return {"state": "not_observed", "last_phase": None}
    if type(data) is not bytes or len(data) > 64: return {"state": "invalid", "last_phase": None}
    try: lines = data.decode("ascii").split("\n")[:-1]
    except (UnicodeError, ValueError): return {"state": "invalid", "last_phase": None}
    failed = bool(lines and lines[-1] == "F")
    if failed: lines = lines[:-1]
    if (not lines or len(lines) > len(CHILD_PHASES) or not data.endswith(b"\n") or
            lines != [str(i) for i in range(1, len(lines) + 1)]):
        return {"state": "invalid", "last_phase": None}
    return {"state": "failed" if failed else "observed", "last_phase": CHILD_PHASES[len(lines) - 1]}


class Evidence:
    def __init__(self, emit):
        self.emit, self.phase = emit, "arguments"
        self.units = {x: "NOT_RUN" for x in COMPILE + ("canary",) + NATIVE}
        self.children = {x: {"state": "not_observed", "last_phase": None} for x in NATIVE}
        self.confirmed, self.events = [], 0
        self.native_launch_calls = 0

    def event(self, state, reason="none", scope="harness"):
        if (self.phase not in PHASES or state not in ("STARTED", "PASS", "HARNESS_FAILED", "ISOLATION_FAILED", "CHECK_FAILED")
                or reason not in REASONS or scope not in SCOPES):
            raise ValueError("invalid_evidence")
        self.events += 1
        if self.events > 100: raise ValueError("evidence_budget")
        if self.phase in self.units: self.units[self.phase] = state
        if state == "PASS": self.confirmed.append(self.phase)
        self.emit({"schema": "native-preflight-phase-v2", "phase": self.phase,
                   "state": state, "reason": reason, "scope": scope})

    def step(self, phase, fn, *, false_reason="rejected", error_reason="read_failed", scope="harness"):
        self.phase = phase
        self.event("STARTED", scope=scope)
        try:
            value = fn()
            if value is False: raise Failure(false_reason, scope)
        except Failure: raise
        except BaseException: raise Failure(error_reason, scope) from None
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
                    all(self.units[x] == "PASS" for x in COMPILE + ("canary",)) and
                    set(CONTEXT + ("merge_callsites", "write_callsites", "complete")) <= set(self.confirmed))
        if status == "PASS" and not verified:
            status, reason, scope = "HARNESS_FAILED", "unexpected_failure", "harness"
        return {"schema": "incident1911-native-preflight-receipt-v2", "status": status,
                "failure_scope": None if verified else scope,
                "failure_phase": None if verified else self.phase, "reason": reason,
                "last_confirmed_phase": self.confirmed[-1] if self.confirmed else None,
                "units": self.units, "child_progress": self.children,
                "native_expected": 14, "native_checks_started": native_started,
                "native_launch_calls": self.native_launch_calls,
                "native_passed": native_pass, "native_not_run": 14 - native_started,
                "native_preflight_verified": verified,
                "application_tested": False, "tls_verified": False, "auth_verified": False,
                "production_gate_enabled": False}


def print_safe(payload):
    print(json.dumps(payload, sort_keys=True, ensure_ascii=True), flush=True)
