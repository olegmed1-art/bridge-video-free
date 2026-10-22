"""Unix broker / forced gateway bodies. No CLI or default authority loader.

The broker's RC2 owner supplies authority.open(ticket, peer, control=endpoint)
with job: Job, assert_live() and reserve_dispatch(claim): None-or-raise. The
remote owner_session keeps its narrower open(ticket, peer) data-only port.
These are explicit integration ports, not an existing provider ABI claim.
The owner controls trusted launcher construction. Wire data cannot select it.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Mapping
from pathlib import Path
import os
import signal
import stat
import subprocess
import threading
import time

from . import diana20_transport_protocol as p
from .diana20_shared_control import SharedControl

SELECTOR = 'diana20-frames-v1'
KEY = '/run/credentials/bridge-diana20-broker.service/ibm_transport'
HOSTS = '/etc/bridge-diana20/known_hosts'
SSH = ('/usr/bin/ssh', '-F', '/dev/null', '-T', '-i', KEY,
       '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
       '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + HOSTS,
       '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'UpdateHostKeys=no',
       '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
       '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
       '-o', 'ConnectionAttempts=1', '-o', 'ConnectTimeout=8',
       '-p', '22', 'bridge-diana20-transport@161.156.86.34', SELECTOR)
SUDO = ('/usr/bin/sudo', '-n', '-u', 'universal-video', '-g', 'universal-video',
        '--', '/usr/local/libexec/bridge-diana20-owner-v1')
CLEAN_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}


def forced_gateway(original_command, *, execve=None):
    """Login entry: one exact selector, then fixed sudo without caller env/argv.

    No auth implementation here. sshd's new dedicated-account policy must first
    establish the restricted broker identity; inner owner verifies its ticket.
    Tests pass an explicit execve double. Nothing runs at module import.
    """
    p.need(type(original_command) is str and original_command == SELECTOR, 'COMMAND')
    (os.execve if execve is None else execve)(SUDO[0], list(SUDO), dict(CLEAN_ENV))
    p.need(False, 'EXEC_RETURNED')


class OwnedSSH:
    def __init__(self, guard, *, popen=None):
        self.child = None
        self.closed = self.completed = False
        try:
            self.child = (subprocess.Popen if popen is None else popen)(list(SSH), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, shell=False, close_fds=True,
                start_new_session=True, env=dict(CLEAN_ENV))
            self.records = p.Records(p.FdBytes(self.child.stdout.fileno(),
                self.child.stdin.fileno(), guard), guard)
        except BaseException:
            self.abort()
            raise

    def finish(self):
        try:
            self.child.stdin.close()
            self.records.eof()  # poll checks owner guard/deadline while waiting
            p.need(self.child.wait(timeout=10) == 0, 'CHILD_EXIT')
            self.completed = True
        finally:
            if not self.completed:
                self.abort()
            else:
                self.child.stdout.close()
                self.closed = True

    def abort(self):
        if self.closed:
            return
        child = self.child
        if child is None:
            self.closed = True
            return
        try:
            if child.poll() is None:
                # Only this new session/process group, never an existing worker.
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass  # exited between poll and kill; STILL wait/reap
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait(timeout=8)
            else:
                child.wait(timeout=0)
            self.closed = True  # never claim completion before wait succeeded
        finally:
            for stream in (child.stdin, child.stdout):
                if stream is not None:
                    stream.close()


def opening(raw):
    p.need(type(raw) is bytes and 32 < len(raw) <= 32 + p.MAX_TICKET, 'OPEN')
    return raw[:32].hex(), raw[32:]


def seal(job, raw, result_raw):
    return p.canonical(dict(job_sha256=job.sha256, source_sha256=p.SOURCE,
        stream_sha256=p.digest(raw), size=len(raw), frames=len(job.frames),
        result_sha256=p.digest(result_raw)))


def receive_output(records, job):
    chunks, size = [], 0
    for frame in job.frames:
        raw = records.read(p.OBSERVATION)
        p.observation(job, frame, raw)
        size += len(raw)
        p.need(size <= p.MAX_OBSERVATIONS, 'OBSERVATION_TOTAL')
        chunks.append(raw)
    raw = b''.join(chunks)
    result_raw = records.read(p.RESULT)
    result = p.parse(result_raw)
    p.result_binding(job, result, raw)
    p.need(records.read(p.SEAL) == seal(job, raw, result_raw), 'SEAL')
    return raw, result_raw


class UnixBroker:
    """One connected request; systemd owns socket creation/activation separately."""
    def __init__(self, *, authority, replay, child_factory=OwnedSSH,
                 peer_factory=p.LinuxPeer, clock=time.monotonic, control_factory=SharedControl):
        p.need(callable(getattr(authority, 'open', None))
               and callable(getattr(replay, 'reserve', None)), 'OWNER_PORT_REQUIRED')
        self.authority, self.replay = authority, replay
        self.child_factory, self.peer_factory, self.clock = child_factory, peer_factory, clock
        self.control_factory = control_factory
        self.lock = threading.Lock()

    def serve_connected(self, connected):
        """Production kernel-peer path; receives an already connected socket."""
        peer = None
        try:
            peer = self.peer_factory(connected)
            initial = p.Guard(peer.check, lifetime=8, clock=self.clock)
            front = p.Records(p.FdBytes(connected.fileno(), connected.fileno(), initial), initial)
            self._serve(front, peer, connection=connected)
        finally:
            if peer is not None:
                peer.close()
            connected.close()

    def serve_listener(self, listener):
        """Accept one on a preopened systemd AF_UNIX listener; never bind/listen."""
        p.need(listener.family == __import__('socket').AF_UNIX, 'LISTENER')
        connected, _ = listener.accept()
        self.serve_connected(connected)

    def _serve(self, front, peer, *, connection):
        # Private transport core; tests use explicit in-memory peer/I/O doubles.
        p.need(self.lock.acquire(blocking=False), 'BUSY')
        child = None
        control = None
        completed = False
        try:
            control = self.control_factory(front, connection=connection, role='server',
                assert_io=peer.check, clock=self.clock).start()
            front = control.records
            opening_kind, open_raw = front.read((p.OPEN, p.PRECLAIM), timeout=8)
            if opening_kind == p.PRECLAIM:
                # Recognize v3 explicitly. Never downgrade a preclaim message
                # into old Job OPEN, never invoke legacy authority/dispatch.
                value = p.parse(open_raw)
                p.need(type(value) is dict and set(value) ==
                       {'version', 'intent', 'intent_sha256', 'policy_sha256'}
                       and type(value['version']) is int and value['version'] == 3
                       and p.canonical(value) == open_raw, 'PRECLAIM_ENVELOPE')
                intent = p.Intent(p.canonical(value['intent']))
                p.need(value['intent_sha256'] == intent.sha256 and p.pin(value['policy_sha256']),
                       'PRECLAIM_ENVELOPE')
                p.need(False, 'OWNER_UID1001_ISSUER_CAPABILITY_AND_BINDING_UNPROVEN')
            expected_sha, ticket = opening(open_raw)
            # The genuine provider wires its semantic control_connection_check
            # to THIS endpoint and original accepted socket. No native bridge here.
            with self.authority.open(ticket, peer, control=control) as grant:
                job = grant.job
                p.need(type(job) is p.Job and job.sha256 == expected_sha, 'ASSIGNMENT')
                def live():
                    peer.check()
                    p.need(grant.assert_live() is None, 'OWNER_GUARD')
                control.bind_server(job, live)
                front.guard = live
                live()
                front.write(p.CONTROL_READY, p.canonical({'version': 2, 'job_sha256': job.sha256}))
                # Outer control entry precedes queue/native admission. It must
                # NEVER start SSH merely because the owner scope is OPEN.
                first_frame = front.read(p.FRAME)
                p.unpack_frame(job.frames[0], first_frame)
                guard = p.Guard(live, clock=self.clock)
                front.guard = guard
                # Reserve durably BEFORE starting SSH; failure remains consumed.
                with self.replay.reserve(job.replay_key):
                    p.need(callable(getattr(grant, 'reserve_dispatch', None)), 'OWNER_DISPATCH_PORT_REQUIRED')
                    p.need(grant.reserve_dispatch(job.claim) is None, 'OWNER_DISPATCH_REFUSED')
                    guard()
                    child = self.child_factory(guard)
                    back = child.records
                    back.write(p.OPEN, open_raw)
                    for index, frame in enumerate(job.frames):
                        raw = first_frame if index == 0 else front.read(p.FRAME)
                        p.unpack_frame(frame, raw)
                        back.write(p.FRAME, raw)
                        back.read(p.ACK)
                        front.write(p.ACK)
                    front.read(p.RUN)
                    back.write(p.RUN)
                    raw, result_raw = receive_output(back, job)
                    for line in raw.splitlines(keepends=True):
                        front.write(p.OBSERVATION, line)
                    front.write(p.RESULT, result_raw)
                    front.write(p.SEAL, seal(job, raw, result_raw))
                    front.read(p.ACK)
                    back.write(p.ACK)
                    p.need(back.read(p.DONE) == p.canonical({'job_sha256': job.sha256}), 'DONE')
                    child.finish()  # EOF/exit0/reap before acknowledging to app
                    guard()
                    control.mark_data_complete(job, raw, result_raw)
                    front.write(p.DONE, p.canonical({'job_sha256': job.sha256}))
                    # Data is complete. Keep grant, peer, serialization and BOTH
                    # socket directions alive through the real persistence ACK.
                    control.wait_close()
                completed = True  # reservation exit must also have succeeded
            p.need(completed, 'OWNER_SCOPE_INCOMPLETE')
            # Final owner __exit__ may itself issue a fresh challenge. Only
            # after all exits succeeded can control CLOSED be delivered.
            control.deliver_closed()
            control.wait_eof()
        finally:
            try:
                if child is not None:
                    child.abort()
            finally:
                try:
                    if control is not None:
                        control.stop()
                finally:
                    self.lock.release()


class FrameStager:
    """Existing approved UID993 private job directory; create-only image files."""
    def __init__(self, path):
        self.path = Path(path)
        from . import diana20_recognizer as native
        native._directory(self.path, private=True)
        p.need(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'), 'OWNER_FILESYSTEM')
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            s = os.fstat(self.fd)
            p.need((s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) == (993, 985, 0o700), 'STAGE_OWNER')
        except BaseException:
            self.close()
            raise
        self.paths = []
        self.total = 0

    def put(self, frame, raw):
        p.need(self.fd is not None, 'STAGER_CLOSED')
        p.frame_bytes(frame, raw)
        p.need(len(self.paths) < p.MAX_FRAMES and self.total + len(raw) <= p.MAX_IMAGES,
               'STAGE_LIMIT')
        name = f'frame-{len(self.paths):04d}.jpg'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            s = os.fstat(fd)
            p.need((s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode), s.st_nlink) ==
                   (993, 985, 0o600, 1), 'STAGE_FILE')
            view = memoryview(raw)
            while view:
                size = os.write(fd, view[:65536])
                p.need(size > 0, 'STAGE_WRITE')
                view = view[size:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self.fd)
        self.paths.append(self.path / name)
        self.total += len(raw)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class NativeRouteBackend:
    """Runs UNCHANGED f1e2948a route/terminal at UID993, not on Oracle UID996.

    Approved launcher supplies route_factory(job, staged_paths)->(route, full_claim).
    It owns native source/binding/decoder/completed-report inputs; transport does
    not manufacture RC2 native objects or substitute a terminal implementation.
    """
    def __init__(self, *, job, stager, route_factory, guard):
        self.job, self.stager, self.route_factory, self.guard = job, stager, route_factory, guard
        self.route = None
        self.used = self.closed = False

    def __enter__(self):
        p.need(not self.closed, 'BACKEND_CLOSED')
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        if self.closed:
            return
        try:
            if self.route is not None:
                self.route._cleanup()  # original client's owned abort/reap only
        finally:
            self.stager.close()  # leave evidence files intact, including failures
        self.closed = True

    def run(self):
        from .diana20_route import SavedFrameVideoRoute
        from . import diana20_recognizer as native
        p.need(not self.used and not self.closed, 'BACKEND_STATE')
        self.used = True
        self.guard()
        route, full_claim = self.route_factory(self.job, tuple(self.stager.paths))
        p.need(type(route) is SavedFrameVideoRoute and type(full_claim) is dict,
               'NATIVE_ROUTE_REQUIRED')
        self.route = route
        p.need(p.canonical({k: full_claim[k] for k in p.CLAIM}) == self.job.claim_raw,
               'NATIVE_CLAIM')
        p.need(route.claim_binding.source_sha256 == self.job.source_sha256
               and route.claim_binding.recognizer_job_id == p.RECOGNIZER_JOB, 'NATIVE_SOURCE')
        original_guard = route.assert_authorized
        def combined(claim):
            self.guard()
            p.need(original_guard(claim) is None, 'OWNER_GUARD')
        route.assert_authorized = combined
        try:
            with route.video_scope(full_claim):
                p.need(len(route._rows) == len(self.job.frames), 'NATIVE_FRAME_COUNT')
                for row, frame, path in zip(route._rows, self.job.frames, self.stager.paths):
                    p.need(p.canonical(row) == p.canonical(dict(id=frame.id, frame_path=str(path),
                        frame_sha256=frame.sha256, source_sha256=self.job.source_sha256,
                        timestamp_ms=frame.timestamp_ms)), 'NATIVE_FRAME_BINDING')
                result = route.video_processor(full_claim)
                p.need(isinstance(result, Mapping), 'TERMINAL_MAPPING')
                result = p.parse(p.canonical(dict(result)))
            self.guard()
            route._verify_observations()
            raw = native._read(route.sealed.path, p.MAX_OBSERVATIONS, owner=True, private=True)
            p.need(p.digest(raw) == route.sealed.sha256 and len(raw) == route.sealed.size
                   and route.sealed.frames == len(self.job.frames), 'NATIVE_SEAL')
            session = route._client.session
            p.need(session is not None and session.completed is True
                   and session.cleanup_confirmed is True, 'WORKER_CLEANUP')
            native._directory(route._client.binding.scratch, private=True)
            p.need(not os.listdir(route._client.binding.scratch), 'SCRATCH_NOT_EMPTY')
            route._verify_observations()
            return raw, result
        finally:
            route.assert_authorized = original_guard


def owner_session(records, *, authority, replay, backend_factory, peer, clock=time.monotonic):
    """Inner fixed owner helper, called by root-owned trusted launcher only.

    peer is supplied from established dedicated SSH-account authentication by
    the launcher/provider, NEVER from a JSON uid/allow flag in this protocol.
    backend_factory(grant, guard) returns a context-managed NativeRouteBackend.
    """
    p.need(hasattr(os, 'geteuid') and os.geteuid() == 993 and os.getegid() == 985,
           'OWNER_IDENTITY')
    open_raw = records.read(p.OPEN)
    expected_sha, ticket = opening(open_raw)
    with authority.open(ticket, peer) as grant:
        job = grant.job
        p.need(type(job) is p.Job and job.sha256 == expected_sha, 'ASSIGNMENT')
        guard = p.Guard(grant.assert_live, clock=clock)
        records.guard = guard
        if isinstance(records.stream, p.FdBytes):
            records.stream.guard = guard
        guard()
        completed = False
        with replay.reserve(job.replay_key), backend_factory(grant, guard) as backend:
            p.need(type(backend) is NativeRouteBackend, 'NATIVE_BACKEND_REQUIRED')
            for frame in job.frames:
                raw = p.unpack_frame(frame, records.read(p.FRAME))
                backend.stager.put(frame, raw)
                records.write(p.ACK)
            records.read(p.RUN)
            guard()
            raw, result = backend.run()
            p.need(isinstance(result, Mapping), 'TERMINAL_MAPPING')
            result = p.parse(p.canonical(dict(result)))
            p.need(type(raw) is bytes and 0 < len(raw) <= p.MAX_OBSERVATIONS, 'OBSERVATION_TOTAL')
            lines = raw.splitlines(keepends=True)
            p.need(len(lines) == len(job.frames), 'OBSERVATION_COUNT')
            for frame, line in zip(job.frames, lines):
                p.observation(job, frame, line)
            p.result_binding(job, result, raw)
            for line in lines:
                records.write(p.OBSERVATION, line)
            result_raw = p.canonical(result)
            records.write(p.RESULT, result_raw)
            records.write(p.SEAL, seal(job, raw, result_raw))
            records.read(p.ACK)
            guard()
            completed = True
        # Backend context exit must close/reap its OWN worker and stager or raise.
        p.need(completed, 'BACKEND_SCOPE_INCOMPLETE')
        guard()
        records.write(p.DONE, p.canonical({'job_sha256': job.sha256}))
        records.eof()
