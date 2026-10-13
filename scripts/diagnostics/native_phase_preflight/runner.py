"""Preflight supervisor: no DB configuration and no production code."""
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
from contract import CASES, EVENTS, compare, require, validate


def context(previous_namespace, uid):
    require(sys.platform == "linux" and platform.machine() == "x86_64" and sys.version_info[:2] == (3, 12))
    require(dict(os.environ) == {"LC_ALL": "C.UTF-8"} and os.getuid() == int(uid) != 0 and os.geteuid() == os.getuid())
    require(not os.getgroups() and os.readlink("/proc/self/ns/net") != previous_namespace)
    devices = {line.split(":", 1)[0].strip() for line in Path("/proc/net/dev").read_text().splitlines() if ":" in line}
    require(devices == {"lo"})
    require(len(Path("/proc/net/route").read_text().splitlines()) == 1)
    # Kernel null/reject IPv6 routes can exist in a fresh namespace. Only lo
    # and no assigned IPv6 address is the stable non-socket check; the actual
    # native socket/connect barrier is independently required by the canary.
    require(not Path("/proc/net/if_inet6").read_text().strip())
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    require(int(status["CapEff"], 16) == 0 and int(status["CapAmb"], 16) == 0 and int(status["NoNewPrivs"]) == 1)
    lock = json.loads((ROOT / "package-lock.json").read_text())
    for name, pin in lock["files"].items():
        data = (ROOT / name).read_bytes()
        require(len(data) == pin["bytes"] and hashlib.sha256(data).hexdigest() == pin["sha256"])


def execute(argv, timeout):
    return subprocess.run(argv, cwd=ROOT, env={"LC_ALL": "C.UTF-8"},
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, close_fds=True, timeout=timeout, check=False).returncode


def native(case, mode, expected):
    require(execute([str(ROOT / "guard"), sys.executable, "-I", "-S", "-B",
                     str(ROOT / "native_child.py"), case, mode, str(expected)], 2) == 0)
    file = ROOT / (case + "-" + mode + ".json")
    require(file.stat().st_size <= 8192)
    return validate(json.loads(file.read_text(encoding="ascii")), case, mode == "observe")


def main():
    require(len(sys.argv) == 3)
    context(sys.argv[1], sys.argv[2])
    cc = "/usr/bin/cc"
    require(Path(cc).is_file())
    commands = [[cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "guard.c", "-o", "guard"],
                [cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-DNP_SHARED", "-fPIC", "-shared", "guard.c", "-o", "guard.so"],
                [cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIC", "-shared", "shim.c", "-ldl", "-o", "shim.so"]]
    for command in commands:
        require(execute(command, 3) == 0)
    # Same executable/filter as every native child. No socket is created.
    require(execute([str(ROOT / "guard"), "--canary"], 2) == -signal.SIGSYS)
    discoveries, sites = {}, {}
    expected = ROOT / "callsites.json"
    for case in CASES:
        result = native(case, "discover", expected)
        discoveries[case] = result
        for event, value in result["sites"].items():
            require(event not in sites or sites[event] == value)
            sites[event] = value
    require(set(sites) == set(EVENTS))
    with expected.open("x", encoding="ascii") as stream:
        stream.write(json.dumps(sites, sort_keys=True))
    pairs = []
    for case in CASES:
        result = native(case, "observe", expected)
        compare(discoveries[case], result, case)
        pairs.append({"case": case, "result": "PASS", "injected_samples": result["samples"],
                      "abi_emulated_public_return": result["abi_emulated_public_return"],
                      "status_reads_without_observer": discoveries[case]["counters"][2],
                      "status_reads_with_observer": result["counters"][2]})
    receipt = {"schema": "incident1911-native-preflight-receipt-v1", "status": "PASS",
               "seccomp_canary": "SIGSYS", "native_children": 14, "skips": 0,
               "cases": pairs, "callsites": sites, "real_connections": 0,
               "tls_verified": False, "auth_verified": False, "production_gate_enabled": False}
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        print('{"schema":"incident1911-native-preflight-receipt-v1","status":"FAIL","production_gate_enabled":false}')
        raise SystemExit(1)
