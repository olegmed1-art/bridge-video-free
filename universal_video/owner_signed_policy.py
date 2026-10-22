"""Bounded SSHSIG delegation policy. No key creation, signing or access setup."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import stat
import struct
import subprocess

NAMESPACE = 'bridge-diana20-owner-v1'
SIGNER = 'owner-oleg'
SOURCE = 'e5a600d8d532a4f10adef191a9bfdfbb962854429849cd55821266ad71d6868f'
RC2 = {
    'factory_sha256': 'b19a56c2c94ed56b4cc37d93ef9cf8f4c6a83efb845b2ce9a55e1ecdafd86e00',
    'loader_sha256': '5b90dfdff97d59f07c97f21bbbca8b9613a58a0d446dc9256862c2838f71e528',
    'archive_sha256': '7fa09a1cf8ea0e91c69fce692e0d2acc58d4f8ab0d9e38012fc240f0fee7520b',
}
RUNTIME_PINS = dict(
    entry_sha256='67153cbc85924be6e1106fe4205ced92aba148aa01212f074e16ca12cb990787',
    pointer_sha256='667f5f893727c9412287799cb731e712897f956ada691cd8f7c1037539451078',
    release_manifest_sha256='d7b506e5535fce14e18b5a590ea1d8cb4de5c51c739ad2721eab32ef3444f2a4',
    worker_sha256='d907fc1fbbe2b1ee3f061b81b4fd60180d58ee14b1478c94ea53bfb9a674e56a',
    release_layer_sha256='569981c5b417ea7fb6ccb8ca6abddca642561ca1cb6e86f3e3e938efc4c96c18',
    worker_pins_sha256='00f35fa9afba2c6f6fd3f90fbb4531dd4ad493f48dddcb4655919a10c082a061',
    sprite_sha256='52eb9c97c9685758f48beb6e84013e9289bdb97f8a31b4ae8e46a0ecee3a37fb',
    component_sha256='26843f04d258834d497affd0a340cef1ef1f657c2d2852c0d26ffea2ea11e626')


class OwnerChannelRefused(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise OwnerChannelRefused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()


def strict_json(raw, limit=65536):
    need(type(raw) is bytes and 0 < len(raw) <= limit, 'OWNER_JSON_BOUND')
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value, 'OWNER_DUPLICATE_FIELD')
            value[key] = item
        return value
    try:
        return json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs,
                          parse_constant=lambda _: need(False, 'OWNER_JSON_NONFINITE'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise OwnerChannelRefused('OWNER_JSON_INVALID') from None


def closed(value, keys):
    need(type(value) is dict and set(value) == set(keys.split()), 'OWNER_POLICY_SCHEMA')


def utc(value):
    need(type(value) is str, 'OWNER_POLICY_DATE')
    try:
        parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise OwnerChannelRefused('OWNER_POLICY_DATE') from None
    need(parsed.strftime('%Y-%m-%dT%H:%M:%SZ') == value, 'OWNER_POLICY_DATE')
    return parsed.timestamp()


def signer_fingerprint(allowed):
    """Exactly one enrolled raw Ed25519 public signer and namespace, no CA."""
    need(type(allowed) is bytes and len(allowed) <= 4096, 'OWNER_SIGNER_BOUND')
    match = re.fullmatch(rb'owner-oleg namespaces="bridge-diana20-owner-v1" ssh-ed25519 ([A-Za-z0-9+/]+={0,2})\n?', allowed)
    need(match is not None, 'OWNER_SIGNER_SCHEMA')
    try:
        blob = base64.b64decode(match[1], validate=True)
    except ValueError:
        raise OwnerChannelRefused('OWNER_SIGNER_ENCODING') from None
    need(len(blob) == 51 and blob[:19] == struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32),
         'OWNER_SIGNER_KEY')
    return 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')


def metadata(info):
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
                 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


class RootPublicFiles:
    """Read only explicitly enrolled public paths; root-owned, no links/writers."""
    def __init__(self, allowed_paths):
        self.allowed = frozenset(allowed_paths)

    def read(self, path, limit=65536):
        need(path in self.allowed, 'OWNER_PUBLIC_PATH_NOT_ENROLLED')
        parts = PurePosixPath(path).parts
        need(parts and parts[0] == '/' and '..' not in parts, 'OWNER_PUBLIC_PATH')
        fds = []
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            fds.append(fd)
            for name in parts[1:-1]:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                fds.append(fd)
                info = os.fstat(fd)
                need(info.st_uid == 0 and not info.st_mode & 0o022, 'OWNER_PUBLIC_ANCESTOR')
            parent = fd
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            fds.append(fd)
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                 and not info.st_mode & 0o6022 and 0 < info.st_size <= limit, 'OWNER_PUBLIC_METADATA')
            data = bytearray()
            while len(data) <= limit:
                part = os.read(fd, min(65536, limit + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
            need(metadata(info) == metadata(os.fstat(fd))
                 == metadata(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False))
                 and len(data) == info.st_size, 'OWNER_PUBLIC_CHANGED')
            return bytes(data), metadata(info)
        finally:
            for fd in reversed(fds):
                os.close(fd)


class SSHSIGVerifier:
    """Fixed verification operation only. Public files already enrolled by owner.

    No key signing/generation and no configurable command/identity/namespace.
    The root-managed path namespace is trusted against concurrent root changes;
    exact before/after snapshots reject observed changes. Not an atomic root fence.
    """
    ALLOWED = '/etc/bridge-diana20/owner_allowed_signers'
    SIGNATURE = '/etc/bridge-diana20/policy.json.sig'
    EXECUTABLE = '/usr/bin/ssh-keygen'

    def __init__(self, *, public_files, executable_sha256):
        need(pin(executable_sha256), 'OWNER_VERIFIER_PIN_REQUIRED')
        self.files, self.executable_sha = public_files, executable_sha256

    def verify(self, raw, signature, allowed):
        need(type(raw) is bytes and 0 < len(raw) <= 65536
             and type(signature) is bytes and 0 < len(signature) <= 16384,
             'OWNER_SIGNATURE_BOUND')
        paths = [(self.ALLOWED, 4096), (self.SIGNATURE, 16384), (self.EXECUTABLE, 4194304)]
        before = [self.files.read(path, limit) for path, limit in paths]
        need(before[0][0] == allowed and before[1][0] == signature
             and sha(before[2][0]) == self.executable_sha, 'OWNER_VERIFIER_SOURCE_CHANGED')
        argv = [self.EXECUTABLE, '-Y', 'verify', '-f', self.ALLOWED, '-I', SIGNER,
                '-n', NAMESPACE, '-s', self.SIGNATURE]
        try:
            result = subprocess.run(argv, input=raw, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=2, check=False, shell=False,
                close_fds=True, cwd='/', env={'PATH':'/usr/bin:/bin', 'LANG':'C', 'LC_ALL':'C'})
        except (OSError, subprocess.TimeoutExpired):
            raise OwnerChannelRefused('OWNER_SIGNATURE_UNVERIFIED') from None
        after = [self.files.read(path, limit) for path, limit in paths]
        need(before == after and result.returncode == 0, 'OWNER_SIGNATURE_UNVERIFIED')


class InstalledMaterial:
    """Actual root-managed public file reader, never caller-supplied policy.

    registry_path is selected/enrolled at future approval; no default path is
    invented here. Its owner must provide durable, monotonic epoch/revocation
    updates across restarts. This read-only accessor cannot prove that backend's
    write/anti-rollback implementation or replace it with an unsigned caller row.
    """
    POLICY = '/etc/bridge-diana20/policy.json'

    def __init__(self, *, public_files, registry_path, enrolled_fingerprint):
        need(type(registry_path) is str and registry_path.startswith('/etc/bridge-diana20/')
             and registry_path not in (self.POLICY, SSHSIGVerifier.SIGNATURE, SSHSIGVerifier.ALLOWED),
             'OWNER_REGISTRY_PATH_REQUIRED')
        self.files, self.registry, self.fingerprint = public_files, registry_path, enrolled_fingerprint

    def __call__(self):
        before = self.files.read(self.registry, 65536)
        installed = strict_json(before[0])
        closed(installed, 'policy_sha256 epoch signer_fingerprint policy_revoked signer_revoked')
        need(installed['signer_fingerprint'] == self.fingerprint, 'OWNER_SIGNER_NOT_ENROLLED')
        raw = self.files.read(self.POLICY, 65536)[0]
        signature = self.files.read(SSHSIGVerifier.SIGNATURE, 16384)[0]
        allowed = self.files.read(SSHSIGVerifier.ALLOWED, 4096)[0]
        need(before == self.files.read(self.registry, 65536), 'OWNER_REGISTRY_CHANGED')
        return raw, signature, allowed, installed


def _verify_policy(raw, signature, allowed, *, verifier, installed, now, production=False):
    """installed is fresh root-managed durable state, never a caller copy.

    Its read/anti-rollback implementation is an explicit pinned runtime input.
    This parser provides no boolean policy or test-signature fallback.
    """
    need(type(raw) is bytes and 0 < len(raw) <= 65536, 'OWNER_POLICY_BOUND')
    closed(installed, 'policy_sha256 epoch signer_fingerprint policy_revoked signer_revoked')
    installed = strict_json(encoded(installed))
    need(pin(installed['policy_sha256']) and sha(raw) == installed['policy_sha256']
         and type(installed['epoch']) is int and installed['epoch'] > 0
         and installed['policy_revoked'] is False and installed['signer_revoked'] is False,
         'OWNER_POLICY_NOT_CURRENT')
    fingerprint = signer_fingerprint(allowed)
    need(fingerprint == installed['signer_fingerprint'], 'OWNER_SIGNER_NOT_ENROLLED')
    need(verifier.verify(raw, signature, allowed) is None, 'OWNER_SIGNATURE_CONTRACT')
    value = strict_json(raw)  # Exact signed bytes, parse only after verification.
    keys = 'kind policy_id epoch owner_signer_fingerprint not_before_UTC not_after_UTC caller broker target rc2 assignment pins limits'
    closed(value, keys + (' production' if production else ''))
    need(value['kind'] == ('BRIDGE_DIANA20_PRODUCTION_OWNER_POLICY_V2' if production else 'BRIDGE_DIANA20_OWNER_POLICY_V1') and pin(value['policy_id'])
         and type(value['epoch']) is int and value['epoch'] == installed['epoch']
         and value['owner_signer_fingerprint'] == fingerprint, 'OWNER_POLICY_BINDING')
    start, end = utc(value['not_before_UTC']), utc(value['not_after_UTC'])
    need(0 < end - start <= 90*86400 and start <= now < end, 'OWNER_POLICY_EXPIRED')
    closed(value['caller'], 'uid gid unit unit_sha256 source_sha256 binding_sha256')
    caller = value['caller']
    need(type(caller['uid']) is int and caller['uid'] == 996 and type(caller['gid']) is int
         and caller['gid'] == 988 and caller['unit'] == ('school-autopilot-production-light.service' if production else 'school-autopilot-native-light.service'), 'OWNER_CALLER_POLICY')
    closed(value['broker'], 'unit release_sha256 transport_key_fingerprint replay_binding_sha256')
    need(value['broker']['unit'] == 'bridge-diana20-broker.service', 'OWNER_BROKER_POLICY')
    closed(value['target'], 'endpoint port login uid gid hostkey_fingerprint')
    target = value['target']
    need(target['endpoint'] == '161.156.86.34' and type(target['port']) is int and target['port'] == 22
         and target['login'] == 'bridge-diana20-transport' and type(target['uid']) is int
         and target['uid'] == 993 and type(target['gid']) is int and target['gid'] == 985, 'OWNER_TARGET_POLICY')
    for fingerprint in (value['broker']['transport_key_fingerprint'], target['hostkey_fingerprint']):
        need(type(fingerprint) is str and re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', fingerprint), 'OWNER_FINGERPRINT')
    closed(value['rc2'], 'archive_sha256 loader_sha256 factory_sha256 context_sha256 capsule_sha256 assignment_provider_sha256 callsite_sha256 records_binding_sha256 anchors_binding_sha256 durability_binding_sha256')
    need(all(value['rc2'][key] == expected for key, expected in RC2.items()), 'OWNER_RC2_SOURCE')
    closed(value['assignment'], 'queue_namespace source_sha256 generation_sha256 manifest_sha256 request_sha256 admission_sha256 parent_sha256')
    assignment = value['assignment']
    need(type(assignment['queue_namespace']) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}', assignment['queue_namespace'])
         and assignment['source_sha256'] == SOURCE, 'OWNER_ASSIGNMENT_POLICY')
    closed(value['pins'], 'entry_sha256 pointer_sha256 release_manifest_sha256 worker_sha256 release_layer_sha256 worker_pins_sha256 sprite_sha256 component_sha256')
    if production:
        closed(value['production'], 'owner handoff_sha256 target_identity_sha256 worker_login')
        owner = value['production']['owner']
        closed(owner, 'uid gid unit unit_sha256 source_sha256 binding_sha256')
        need(type(owner['uid']) is int and owner['uid'] == 1001
             and type(owner['gid']) is int and owner['gid'] == 1001
             and owner['unit'] == 'bridge-diana20-broker.service'
             and all(pin(owner[k]) for k in ('unit_sha256','source_sha256','binding_sha256'))
             and pin(value['production']['handoff_sha256'])
             and pin(value['production']['target_identity_sha256'])
             and value['production']['worker_login'] == 'autopilot_light_worker_login',
             'OWNER_PRODUCTION_PROFILE')
        # New release pins must be explicitly signed/enrolled, not inherited
        # from native-light. KernelPeer/Busctl compare actual installed files.
        need(value['pins']['worker_sha256'] == caller['source_sha256'],
             'OWNER_PRODUCTION_SOURCE_BINDING')
    else:
        need(value['pins'] == RUNTIME_PINS, 'OWNER_RUNTIME_PINS')
    closed(value['limits'], 'scope_seconds heartbeat_seconds challenge_seconds frames image_bytes ingress_bytes observations_bytes job_bytes replay_bytes worker_seconds close_seconds cleanup_seconds')
    maximums = dict(scope_seconds=3600, heartbeat_seconds=5, challenge_seconds=2, frames=232,
        image_bytes=16777216, ingress_bytes=134217728, observations_bytes=33554432,
        job_bytes=268435456, replay_bytes=4194304, worker_seconds=300, close_seconds=60, cleanup_seconds=10)
    need(all(type(value['limits'][key]) is int and 0 < value['limits'][key] <= maximum
             for key, maximum in maximums.items()), 'OWNER_POLICY_LIMITS')
    pin_values = [caller[key] for key in ('unit_sha256','source_sha256','binding_sha256')]
    pin_values += [value['broker'][key] for key in ('release_sha256','replay_binding_sha256')]
    pin_values += list(value['rc2'].values()) + list(value['pins'].values())
    pin_values += [v for k,v in assignment.items() if k != 'queue_namespace']
    need(all(pin(v) for v in pin_values), 'OWNER_POLICY_PINS')
    return value


def verify_policy(raw, signature, allowed, *, verifier, installed, now):
    """Original native V1 profile; no production or UID1001 fallback."""
    return _verify_policy(raw, signature, allowed, verifier=verifier,
                          installed=installed, now=now)


def verify_production_policy(raw, signature, allowed, *, verifier, installed, now):
    """Separately signed V2, exclusively production996 -> real owner1001."""
    return _verify_policy(raw, signature, allowed, verifier=verifier,
                          installed=installed, now=now, production=True)
