"""Linux owner-operation supervisor. No import-time activity or external transport.

A guardian survives loss of the caller long enough to kill its owned worker
process group. No callback, exception text or credential is written to disk.
This is a deadline on an attempted operation, not proof that an interrupted
filesystem operation had no effect. Callers must preserve uncertain records.
"""
import json
import os
import select
import signal
import threading
import time

LIMIT = 4096


def _no_detach():
    """Inherited seccomp restriction; trusted subprocesses cannot escape cleanup.

    Linux UAPI syscall tables: arch/x86/entry/syscalls/syscall_64.tbl and
    include/uapi/asm-generic/unistd.h. Unknown architectures refuse execution.
    This only adds restrictions to the worker; it never removes host NNP.
    """
    import ctypes
    machine=os.uname().machine
    if machine=='x86_64':arch,clone,deny=0xc000003e,56,(109,112,272,308)
    elif machine=='aarch64':arch,clone,deny=0xc00000b7,220,(154,157,97,268)
    else:raise RuntimeError('LANE_RETIREMENT_SUPERVISOR_ARCH')
    class Filter(ctypes.Structure):
        _fields_=[('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint)]
    class Program(ctypes.Structure):
        _fields_=[('len',ctypes.c_ushort),('filter',ctypes.POINTER(Filter))]
    code=[(0x20,0,0,4),(0x15,1,0,arch),(0x06,0,0,0x80000000),
          (0x20,0,0,0),(0x45,0,1,0x40000000),(0x06,0,0,0x00050001)]
    for nr in deny:code.extend([(0x15,0,1,nr),(0x06,0,0,0x00050001)])
    # ENOSYS permits libc's safe fallback from clone3 to inspected clone.
    code.extend([(0x15,0,1,435),(0x06,0,0,0x00050026),
                 (0x15,0,3,clone),(0x20,0,0,16),
                 (0x45,0,1,0x7e020080),(0x06,0,0,0x00050001),
                 (0x06,0,0,0x7fff0000)])
    filters=(Filter*len(code))(*(Filter(*item) for item in code))
    program=Program(len(code),filters);libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(38,1,0,0,0)!=0 or libc.prctl(22,2,ctypes.byref(program),0,0)!=0:
        raise RuntimeError('LANE_RETIREMENT_SUPERVISOR_FILTER')


def _kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _kill_owned(pid):
    # pid is our unreaped direct child. Its PID cannot be recycled here.
    # Before setsid there are no descendants; kill(pid) handles that startup.
    _kill_group(pid)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _write(fd, raw):
    while raw:
        count = os.write(fd, raw)
        if count <= 0:
            raise RuntimeError('LANE_RETIREMENT_UNKNOWN')
        raw = raw[count:]


def _json(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > LIMIT:
        raise RuntimeError('LANE_RETIREMENT_UNKNOWN')
    return raw


def _group_live(pgid):
    """Metadata only, before reaping our group leader: no PID-reuse kill race."""
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            with open('/proc/'+name+'/stat','rb') as stream:
                raw=stream.read(4096)
        except (FileNotFoundError,ProcessLookupError):
            continue
        fields=raw[raw.rfind(b')')+2:].split()
        if len(fields)<3:
            raise RuntimeError('LANE_RETIREMENT_UNKNOWN')
        if int(fields[2])==pgid and fields[0] not in (b'Z',b'X'):
            return True
    return False


def _guardian(fn, result_fd, alive_fd, timeout):
    """Guardian and all worker descendants share one new owned process group."""
    worker = None
    read_fd = write_fd = None
    try:
        os.setsid()
        # A stopped/nonreading caller must never delay the unconditional group
        # kill. An atomic-size nonblocking frame either fits or fails closed.
        os.set_blocking(result_fd, False)
        read_fd, write_fd = os.pipe()
        worker = os.fork()
        if worker == 0:
            os.close(read_fd)
            os.close(result_fd)
            os.close(alive_fd)
            try:
                _no_detach()
                _write(write_fd, b'R')
                value = {'ok': True, 'value': fn()}
                _write(write_fd, _json(value))
                os._exit(0)
            except BaseException:
                # Never serialize arbitrary exceptions, stdout or stderr.
                try:
                    _write(write_fd, b'{"ok":false}')
                except BaseException:
                    pass
                os._exit(2)
        os.close(write_fd)
        write_fd = None
        deadline = time.monotonic() + timeout
        data = bytearray()
        ready = False
        complete = False
        while time.monotonic() < deadline:
            readable, _, _ = select.select([alive_fd, read_fd], [], [], max(0, deadline-time.monotonic()))
            if alive_fd in readable and not os.read(alive_fd, 1):
                break  # caller exited; do not leave a detached writer running
            if read_fd in readable:
                chunk = os.read(read_fd, 8192)
                if not chunk:
                    complete = True
                    break
                if not ready:
                    if chunk[:1] != b'R':
                        break
                    ready = True
                    chunk = chunk[1:]
                data.extend(chunk)
                if len(data) > LIMIT:
                    break
        value = json.loads(data) if ready and complete and len(data) <= LIMIT else {'ok': False}
        if type(value) is not dict or set(value) not in ({'ok'}, {'ok', 'value'}):
            value = {'ok': False}
        if complete:
            # Reap the normal direct worker; group identity is the guardian PID.
            until=time.monotonic()+0.2
            while time.monotonic()<until:
                if os.waitpid(worker,os.WNOHANG)[0]==worker:
                    worker=None;break
                time.sleep(0.001)
            if worker is not None:
                value={'ok':False}
        _write(result_fd, _json(value))
    except BaseException:
        try:
            _write(result_fd, b'{"ok":false}')
        except BaseException:
            pass
    finally:
        # Signal the whole group, including this guardian; order is unspecified.
        # Caller verifies no live members BEFORE reaping its owned group leader.
        _kill_group(os.getpid())
        os._exit(2)


def run(fn, *, seconds=55):
    """Run one trusted callback; timeout/crash/caller loss means UNKNOWN.

    Input is never a caller-selected import/command. Existing reviewed owner
    entrypoints supply the closure. No operation is retried by this supervisor.
    """
    if not callable(fn) or type(seconds) not in (int, float) or not 0 < seconds <= 55:
        raise RuntimeError('LANE_RETIREMENT_DEADLINE')
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL or threading.active_count()!=1:
        raise RuntimeError('LANE_RETIREMENT_SUPERVISOR_IDENTITY')
    result_read, result_write = os.pipe()
    alive_read, alive_write = os.pipe()
    guardian = os.fork()
    if guardian == 0:
        os.close(result_read)
        os.close(alive_write)
        _guardian(fn, result_write, alive_read, seconds)
    os.close(result_write)
    os.close(alive_read)
    data = bytearray()
    deadline = time.monotonic() + seconds + 2
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([result_read], [], [], max(0, deadline-time.monotonic()))
            if not readable:
                break
            chunk = os.read(result_read, 8192)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > LIMIT:
                raise RuntimeError('LANE_RETIREMENT_UNKNOWN')
        value = json.loads(data)
        if (type(value) is not dict or set(value) != {'ok', 'value'}
                or value['ok'] is not True):
            raise RuntimeError('LANE_RETIREMENT_UNKNOWN')
        return value['value']
    except BaseException:
        raise RuntimeError('LANE_RETIREMENT_UNKNOWN') from None
    finally:
        os.close(alive_write)
        _kill_owned(guardian)
        os.close(result_read)
        cleanup_deadline=time.monotonic()+1
        reaped=False
        while time.monotonic()<cleanup_deadline:
            if _group_live(guardian):
                time.sleep(0.001);continue
            try:
                pid,_=os.waitpid(guardian,os.WNOHANG)
            except ChildProcessError:
                break  # violated owned-child contract; never certify success
            if pid==guardian:
                reaped=True;break
            time.sleep(0.001)
        if not reaped:
            raise RuntimeError('LANE_RETIREMENT_UNKNOWN')
