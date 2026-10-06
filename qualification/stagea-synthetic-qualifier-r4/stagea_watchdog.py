"""Synthetic-only actual supervisor candidate; no production dispatch exists."""
import os, selectors, signal, subprocess, sys, time
from pathlib import Path
from stagea_synthetic_core import ROOT, Refused, need, source_guard, MAX_OUTPUT
from stagea_capture import validate

RUNTIME_SECONDS = 50
TOTAL_SECONDS = 60

def arm_deadline():
    # Fresh isolated Linux supervisor only. Kernel default termination avoids a
    # Python signal handler stuck waiting for cleanup/subprocess completion.
    need(signal.SIGALRM not in signal.pthread_sigmask(signal.SIG_BLOCK, set())
         and signal.getsignal(signal.SIGALRM) == signal.SIG_DFL
         and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    signal.setitimer(signal.ITIMER_REAL, TOTAL_SECONDS)

def disarm_deadline():
    signal.setitimer(signal.ITIMER_REAL, 0)

def profile():
    """Copy exact reviewed standalone lifetime module into a private namespace."""
    import types
    path = ROOT / "ops/native_maintenance_lifetime.py"
    module = types.ModuleType("private_stage_a_lifetime")
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    # A private narrower profile; no change to shared public module/files.
    module.RUNTIME_TEXT = {**module.RUNTIME_TEXT, RUNTIME_SECONDS: "50s"}
    return module

def exchange(process, wire, deadline, maximum=MAX_OUTPUT):
    """Bound output while streaming bounded stdin; never echo stderr or captured bytes."""
    output = bytearray()
    offset = 0
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stdin.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ, "read")
        selector.register(process.stdin, selectors.EVENT_WRITE, "write")
        while selector.get_map():
            left = deadline - time.monotonic()
            need(left > 0)
            events = selector.select(left)
            need(events)
            for key, _ in events:
                if key.data == "write":
                    count = os.write(process.stdin.fileno(), wire[offset:offset + 65536])
                    need(count > 0)
                    offset += count
                    if offset == len(wire):
                        selector.unregister(process.stdin)
                        process.stdin.close()
                else:
                    part = os.read(process.stdout.fileno(), min(4096, maximum + 1 - len(output)))
                    if not part:
                        selector.unregister(process.stdout)
                    else:
                        output.extend(part)
                        need(len(output) <= maximum)
        need(process.wait(timeout=max(0.001, deadline-time.monotonic())) == 0)
        return bytes(output)
    finally:
        selector.close()

def _control_guard(*,qualification=False):
 from stagea_synthetic_core import SOURCE
 from stagea_control import assert_self
 state,_,_ = assert_self(SOURCE,network=qualification)
 return int(state["ActiveEnterTimestampMonotonic"])/1000000

def _rebase_deadline(started):
    remaining = started + TOTAL_SECONDS - time.monotonic()
    need(0 < remaining <= TOTAL_SECONDS)
    # This function only rebases the fresh timer owned by this invocation.
    signal.setitimer(signal.ITIMER_REAL, remaining)

def supervise(manifest_pin, wire, source, run, expected_runtime_id, *, qualification_mode=None):
    """Candidate: actual shared supervisor body; fixed synthetic mode never dispatches owner code."""
    process = None
    unit = None
    group = None
    inode = None
    raw = None
    alarm_armed = False
    started = time.monotonic()
    try:
        need(sys.platform == "linux" and os.getuid() == 0 and os.getgid() == 0)
        need(type(qualification_mode) is str and qualification_mode in ("payload", "hang"))
        arm_deadline()
        alarm_armed = True
        source_guard(manifest_pin)
        started = _control_guard(qualification=qualification_mode is not None)
        need(time.monotonic() <= started + 3)
        _rebase_deadline(started)
        if qualification_mode is not None:
            from stagea_fixture_worker import WIRE, RUNTIME
            need(type(wire) is bytes and wire == WIRE and type(expected_runtime_id) is str and expected_runtime_id == RUNTIME)
        need(type(wire) is bytes and 0 < len(wire) <= 16 * 1024 * 1024)
        from stagea_synthetic_core import SOURCE
        need(source == SOURCE)
        lifetime = profile()
        unit = lifetime.new_unit(source, run)
        import base64
        encoded = base64.b64encode((ROOT/"ops/native_maintenance_lifetime.py").read_bytes()).decode()
        # First fixed frame lets the external supervisor certify this live unique cgroup
        # BEFORE delivering the fixed synthetic wire.
        if qualification_mode is not None:
            # Replace only the fixed worker dispatch. Alarm, profile verification,
            # readiness, deadline, exchange, validation and cleanup are the SAME body.
            code = (lifetime.loader(encoded) +
                "lifetime.RUNTIME_TEXT[50]='50s'\n" +
                # Reject late launch before cold probes or synthetic work.
                "import time\nif time.monotonic()>" + repr(started+5) + ":raise SystemExit(78)\n" +
                "lifetime.assert_self(" + repr(unit) + ",50)\n" +
                "import sys\nsys.stdout.buffer.write(b'READY\\n');sys.stdout.buffer.flush()\n" +
                "sys.path.insert(0," + repr(str(ROOT)) + ")\n" +
                "from stagea_fixture_worker import run\nraise SystemExit(run(" +
                repr(manifest_pin) + "," + repr(qualification_mode) + "))\n")
        command = lifetime.command(unit, code, RUNTIME_SECONDS)
        if qualification_mode is not None:
            index = command.index("/usr/bin/python3")
            command = command[:index] + ["--property=PrivateNetwork=yes",
                "--property=RestrictAddressFamilies=AF_UNIX", "--property=ProtectSystem=strict",
                "--property=CapabilityBoundingSet=", "--property=AmbientCapabilities=",
                "--property=ReadOnlyPaths=" + str(ROOT)] + command[index:]
        process = subprocess.Popen(command,
             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
             env={"PATH": "/usr/bin:/bin"}, close_fds=True, bufsize=0)
        import select
        need(select.select([process.stdout], [], [], max(0, started+5-time.monotonic()))[0])
        # Unbuffered fixed read cannot consume definitions; worker waits for stdin.
        need(os.read(process.stdout.fileno(), 6) == b"READY\n")
        state, group, inode = lifetime.identity(unit, RUNTIME_SECONDS)
        # Never deliver synthetic wire to a late-started worker. Its independent
        # PID1 50s+2s drain must fit well inside our kernel 60s deadline.
        need(time.monotonic() <= started + 5)
        raw = exchange(process, wire, started + 56)
        validate(raw, expected_runtime_id)
    except BaseException:
        raw = None
    finally:
        cleanup_ok = False
        try:
            if unit is not None:
                lifetime.cleanup(unit)
                cleanup_ok = group is not None and lifetime.empty(group, inode)
            if process is not None:
                if process.poll() is None:
                    process.kill()  # Launcher only; PID1 still owns the bounded cgroup.
                process.wait(timeout=max(0.001, started+TOTAL_SECONDS-time.monotonic()))
                for stream in (process.stdin, process.stdout):
                    if stream is not None and not stream.closed:
                        stream.close()
        except BaseException:
            cleanup_ok = False
        if not cleanup_ok or time.monotonic() >= started + TOTAL_SECONDS:
            raw = None
    if alarm_armed:
        disarm_deadline()
    if raw is None:
        raise Refused("PRIVATE_WATCHDOG_REFUSED") from None
    return raw
