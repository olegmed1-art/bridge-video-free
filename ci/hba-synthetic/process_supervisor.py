"""Generic non-root fixture supervisor. No host privilege/security changes.

The dedicated single-threaded CI test process becomes a subreaper for its own tree.
Timeout termination is not accepted as proof until every adopted child is
actually waited/reaped and waitpid(-1, WNOHANG) returns ECHILD.
"""
import base64
import ctypes
import errno
import json
import os
import signal
import subprocess
import sys
import time
import threading
from pathlib import Path


def require_linux_nonroot():
    if sys.platform != "linux" or os.geteuid() == 0:
        raise RuntimeError("Existing non-root Linux required")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("Existing pidfd support required; never install")


def owned_children():
    # This dedicated single-threaded supervisor owns every child listed here.
    path = "/proc/self/task/" + str(os.getpid()) + "/children"
    with open(path, "rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192 or any(not token.isdigit() for token in raw.split()):
        raise RuntimeError("Owned-child metadata unavailable")
    return [int(token) for token in raw.split()]


def terminate_owned_child(pid):
    # PIDFD plus waitid child proof avoids signals to reused/foreign PIDs.
    descriptor = os.pidfd_open(pid)
    try:
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        os.close(descriptor)


def drain_owned_descendants(process):
    # Reap the direct child first; only then reap adopted fixture descendants.
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=2)
    deadline = time.monotonic() + 3
    adopted = 0
    while True:
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return {"all_owned_descendants_drained": True, "echild_verified": True,
                        "direct_child_reaped": True, "adopted_children_reaped": adopted}
            if pid == 0:
                break
            adopted += 1
        if time.monotonic() >= deadline:
            raise RuntimeError("Drain deadline exceeded: UNKNOWN, never PASS")
        for pid in owned_children():
            terminate_owned_child(pid)
        time.sleep(0.01)


def supervise(request):
    require_linux_nonroot()
    if threading.current_thread() is not threading.main_thread() or threading.active_count() != 1:
        raise RuntimeError("Single-threaded dedicated test process required")
    if owned_children():
        raise RuntimeError("Preexisting child process: refuse before launching a fixture")
    timeout = request.get("timeout")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 20:
        raise RuntimeError("Invalid bounded timeout")
    argv = request.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
        raise RuntimeError("Invalid fixture command")
    env = request.get("env")
    if env != {"PATH": "/usr/bin:/bin", "LANG": "C"}:
        raise RuntimeError("Only fixed credential-free environment supported")
    libc = ctypes.CDLL(None, use_errno=True)
    # Per-process adoption attribute, not host security policy or capabilities.
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise RuntimeError("Subreaper unavailable; never install/escalate")
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, start_new_session=True)
    timed_out = False
    stdout = stderr = b""
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            receipt = drain_owned_descendants(process)
        # All pipe owners are dead/reaped now; a short read cannot await a child.
        stdout, stderr = process.communicate(timeout=1)
        return {"returncode": process.returncode, "timed_out": timed_out,
                "stdout": base64.b64encode(stdout).decode(),
                "stderr": base64.b64encode(stderr).decode(), "drain": receipt}
    finally:
        if process.returncode is None or owned_children():
            # Failures still attempt bounded cleanup; a failed drain is UNKNOWN.
            drain_owned_descendants(process)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def run_fixture_process(argv, *, timeout, env):
    # Direct in-process supervision avoids an unowned timeout wrapper entirely.
    report = supervise({"argv": argv, "timeout": timeout, "env": env})
    if report["drain"].get("all_owned_descendants_drained") is not True:
        raise RuntimeError("Missing actual descendant drain proof")
    if report["timed_out"]:
        error = subprocess.TimeoutExpired(argv, timeout)
        error.supervisor_returncode = report["returncode"]
        error.drain_receipt = report["drain"]
        raise error
    return subprocess.CompletedProcess(argv, report["returncode"],
                                       base64.b64decode(report["stdout"]),
                                       base64.b64decode(report["stderr"]))


if __name__ == "__main__":
    raise SystemExit("Review-only generic fixture supervisor; use run_ci.py")
