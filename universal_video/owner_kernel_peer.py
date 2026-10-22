"""Linux SO_PEERCRED/pidfd caller identity. No service mutations or argv reads."""
import os
import select
import socket
import struct

from .owner_signed_policy import need


class KernelPeer:
    """Use at the broker accept boundary, before any caller payload is trusted.

    unit_by_pid is the approved read-only systemd GetUnitByPID fallback adapter.
    It must return actual Id/InvocationID/ControlGroup and installed pins from
    the selected unit. Its source/runtime binding is a mandatory deployment
    input; no guessed busctl ABI or cgroup-only replacement is supplied.
    PID/start/pidfd/cgroup are checked around that possibly blocking lookup.
    """
    def __init__(self, connected_socket, *, caller, unit_by_pid):
        need(connected_socket.family == socket.AF_UNIX
             and callable(unit_by_pid), 'OWNER_UNIX_PEER_REQUIRED')
        self.sock, self.caller, self.lookup = connected_socket, dict(caller), unit_by_pid
        self.pid, self.uid, self.gid = struct.unpack('3i', connected_socket.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        need(self.pid > 1 and self.uid == caller['uid'] and self.gid == caller['gid'], 'OWNER_KERNEL_CALLER')
        self.fd = None
        self.start = self._start()
        try:
            self.fd = os.pidfd_open(self.pid, 0)
            self.identity = self._snapshot()
        except BaseException:
            self.close()
            raise

    def _proc(self, leaf):
        need(leaf in ('stat', 'cgroup'), 'OWNER_PROC_SCOPE')
        fd = os.open('/proc/' + str(self.pid) + '/' + leaf,
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            raw = os.read(fd, 16385)
            need(0 < len(raw) <= 16384 and not os.read(fd, 1), 'OWNER_PROC_BOUND')
            return raw
        finally:
            os.close(fd)

    def _start(self):
        raw = self._proc('stat')
        need(raw.rfind(b')') > 0, 'OWNER_PROCESS_STAT')
        suffix = raw[raw.rfind(b')') + 2:].split()
        need(len(suffix) >= 20 and suffix[0] not in (b'Z', b'X')
             and suffix[19].isdigit() and int(suffix[19]) > 0, 'OWNER_PEER_DEAD')
        return int(suffix[19])

    def _alive(self):
        need(self.fd is not None and not select.select([self.fd], [], [], 0)[0], 'OWNER_PEER_DEAD')
        need(self._start() == self.start, 'OWNER_PID_REUSED')
        peer = struct.unpack('3i', self.sock.getsockopt(socket.SOL_SOCKET,
                                   socket.SO_PEERCRED, struct.calcsize('3i')))
        need(peer == (self.pid, self.uid, self.gid), 'OWNER_PEER_CHANGED')

    def _snapshot(self):
        self._alive()
        before = self._proc('cgroup')
        unit = self.lookup(self.pid)  # Read-only GetUnitByPID; no cached user row.
        self._alive()
        need(before == self._proc('cgroup'), 'OWNER_CGROUP_RACE')
        keys = {'Id', 'InvocationID', 'ControlGroup', 'unit_sha256', 'source_sha256', 'binding_sha256'}
        need(type(unit) is dict and set(unit) == keys
             and unit['Id'] == self.caller['unit'] and type(unit['InvocationID']) is str
             and len(unit['InvocationID']) == 32 and all(c in '0123456789abcdef' for c in unit['InvocationID'])
             and type(unit['ControlGroup']) is str and unit['ControlGroup'].startswith('/')
             and all(unit[key] == self.caller[key]
                     for key in ('unit_sha256', 'source_sha256', 'binding_sha256')),
             'OWNER_UNIT_IDENTITY')
        # Require the exact unified hierarchy path; substring unit matches fail.
        need(b'0::' + unit['ControlGroup'].encode() in before.splitlines(), 'OWNER_CGROUP_IDENTITY')
        self._alive()
        return tuple(sorted(unit.items()))

    def check(self):
        need(self._snapshot() == self.identity, 'OWNER_INVOCATION_CHANGED')

    def close(self):
        if self.fd is not None:
            # close may take effect and then report an uncertain ACK. Never
            # retry its integer after another scope may have reused that fd.
            fd, self.fd = self.fd, None
            os.close(fd)
