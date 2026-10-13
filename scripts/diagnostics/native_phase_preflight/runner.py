"""Observable harness; no production inputs or unredacted child output."""
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from contract import CASES, EVENTS, compare, validate
from evidence import Evidence, Failure, child_progress, print_safe
from route_table import read_ipv4_routes


def check_context(ev, previous_namespace, uid):
    ev.step("platform", lambda: sys.platform == "linux" and platform.machine() == "x86_64")
    ev.step("python_version", lambda: sys.version_info[:2] == (3, 12))
    ev.step("environment", lambda: dict(os.environ) == {"LC_ALL": "C.UTF-8"})
    ev.step("identity", lambda: os.getuid() == int(uid) != 0 and os.geteuid() == os.getuid())
    ev.step("groups", lambda: not os.getgroups())
    ev.step("namespace", lambda: os.readlink("/proc/self/ns/net") != previous_namespace, scope="isolation")
    def interfaces():
        devices = {line.split(":", 1)[0].strip() for line in Path("/proc/net/dev").read_text().splitlines() if ":" in line}
        return devices == {"lo"}
    ev.step("interfaces", interfaces, scope="isolation")
    def routes():
        ev.set_route_summary(read_ipv4_routes(Path("/proc/net/route")))
        if ev.route_summary["parse_status"] == "read_failed":
            raise Failure("read_failed", "isolation")
        return ev.route_summary["parse_status"] in ("empty", "header_only")
    ev.step("ipv4_routes", routes, scope="isolation")
    ev.step("ipv6_addresses", lambda: not Path("/proc/net/if_inet6").read_text().strip(), scope="isolation")
    def status():
        return dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    ev.step("capabilities", lambda: int(status()["CapEff"], 16) == 0 and int(status()["CapAmb"], 16) == 0, scope="isolation")
    ev.step("no_new_privileges", lambda: int(status()["NoNewPrivs"]) == 1, scope="isolation")
    def integrity():
        lock = json.loads((ROOT / "package-lock.json").read_text())
        for name, pin in lock["files"].items():
            data = (ROOT / name).read_bytes()
            if len(data) != pin["bytes"] or hashlib.sha256(data).hexdigest() != pin["sha256"]: return False
        return True
    ev.step("file_integrity", integrity)
    ev.step("compiler_available", lambda: Path("/usr/bin/cc").is_file(), false_reason="unavailable")


def execute(argv, timeout):
    return subprocess.run(argv, cwd=ROOT, env={"LC_ALL": "C.UTF-8"},
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, close_fds=True, timeout=timeout, check=False).returncode


def command(argv, timeout, execute_fn, *, canary=False):
    scope = "isolation" if canary else "harness"
    try: rc = execute_fn(argv, timeout)
    except subprocess.TimeoutExpired: raise Failure("timeout", scope) from None
    except BaseException: raise Failure("launch_failed", scope) from None
    if type(rc) is not int: raise Failure("unexpected_failure", scope)
    if canary:
        if rc != -signal.SIGSYS: raise Failure("canary_not_sigsys", scope)
    elif rc:
        raise Failure("unexpected_signal" if rc < 0 else "nonzero_exit")
    return True


def progress_for(case, mode):
    try:
        with (ROOT / (case + "-" + mode + ".progress")).open("rb") as stream: data = stream.read(65)
    except FileNotFoundError: data = None
    except BaseException: data = b""
    return child_progress(data)


def native(ev, case, mode, expected, execute_fn):
    phase = mode + "_" + case
    # Fresh run directory only. Never accept old receipts/markers as this child.
    if any((ROOT / (case + "-" + mode + suffix)).exists() for suffix in (".json", ".progress")):
        raise Failure("worker_status_conflict")
    ev.native_launch_calls += 1
    try:
        rc = execute_fn([str(ROOT / "guard"), sys.executable, "-I", "-S", "-B",
                         str(ROOT / "native_child.py"), case, mode, str(expected)], 2)
    except subprocess.TimeoutExpired: raise Failure("timeout") from None
    except BaseException: raise Failure("launch_failed") from None
    finally: ev.children[phase] = progress_for(case, mode)
    progress = ev.children[phase]
    if type(rc) is not int: raise Failure("unexpected_failure")
    if progress["state"] == "invalid": raise Failure("marker_invalid")
    if rc:
        if progress["last_phase"] == "complete": reason = "worker_status_conflict"
        elif rc == -signal.SIGSYS: reason = "worker_sigsys"
        elif rc == -signal.SIGKILL: reason = "worker_sigkill"
        elif rc < 0: reason = "worker_signal"
        elif 90 <= rc <= 96: reason = "guard_rejected"
        else: reason = "worker_failed"
        raise Failure(reason)
    if progress != {"state": "observed", "last_phase": "complete"}: raise Failure("marker_incomplete")
    try:
        with (ROOT / (case + "-" + mode + ".json")).open("rb") as stream: data = stream.read(8193)
    except FileNotFoundError: raise Failure("receipt_missing") from None
    except BaseException: raise Failure("read_failed") from None
    if len(data) > 8192: raise Failure("receipt_size")
    try: payload = json.loads(data)
    except BaseException: raise Failure("receipt_invalid") from None
    try: return validate(payload, case, mode == "observe")
    except BaseException: raise Failure("contract_mismatch", "check") from None


def main(argv=None, *, execute_fn=execute, emit=print_safe, context_fn=check_context):
    ev = Evidence(emit)
    args = sys.argv[1:] if argv is None else argv
    try:
        ev.step("arguments", lambda: type(args) is list and len(args) == 2)
        context_fn(ev, args[0], args[1])
        cc = "/usr/bin/cc"
        commands = [
            ("compile_guard", [cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "guard.c", "-o", "guard"]),
            ("compile_guard_shared", [cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-DNP_SHARED", "-fPIC", "-shared", "guard.c", "-o", "guard.so"]),
            ("compile_shim", [cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIC", "-shared", "shim.c", "-ldl", "-o", "shim.so"]),
        ]
        for phase, argv in commands:
            ev.step(phase, lambda argv=argv: command(argv, 3, execute_fn))
        ev.step("canary", lambda: command([str(ROOT / "guard"), "--canary"], 2, execute_fn, canary=True), scope="isolation")
        discoveries, sites = {}, {}
        expected = ROOT / "callsites.json"
        for case in CASES:
            discoveries[case] = ev.step("discover_" + case, lambda case=case: native(ev, case, "discover", expected, execute_fn))
        def merge():
            for payload in discoveries.values():
                for event, value in payload["sites"].items():
                    if event in sites and sites[event] != value: raise Failure("callsite_conflict", "check")
                    sites[event] = value
            if set(sites) != set(EVENTS): raise Failure("callsite_missing", "check")
            return True
        ev.step("merge_callsites", merge, scope="check")
        def write():
            with expected.open("x", encoding="ascii") as stream: stream.write(json.dumps(sites, sort_keys=True))
            return True
        ev.step("write_callsites", write, error_reason="write_failed")
        for case in CASES:
            def observe(case=case):
                payload = native(ev, case, "observe", expected, execute_fn)
                try: compare(discoveries[case], payload, case)
                except BaseException: raise Failure("contract_mismatch", "check") from None
                return True
            ev.step("observe_" + case, observe, scope="check")
        ev.step("complete", lambda: True, scope="check")
    except BaseException as exc: receipt = ev.finish(exc)
    else: receipt = ev.finish()
    emit(receipt)
    return receipt


if __name__ == "__main__":
    try:
        (ROOT / "runner-entered.flag").write_text("entered\n", encoding="ascii")
        output = main()
    except BaseException:
        print_safe({"schema": "native-preflight-entry-v2", "status": "HARNESS_FAILED",
                    "phase": "entry", "reason": "unexpected_failure", "production_gate_enabled": False})
        raise SystemExit(1)
    raise SystemExit(0 if output["native_preflight_verified"] else 1)
