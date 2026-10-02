"""Linux-only, fixed incident file collector; never executes repository code.

Default output is metadata + hashes. --private-bytes also exports journal bytes;
capture into a private LOCAL file, never a public Actions log or repository.
No DB, provider, service, credentials, replay, ACK or record creation.
"""
import base64
import hashlib
import json
import os
import stat
import sys
import time

POLICY = 'f5e135d1ecea5f866b6762c435cf421b2234c4ab2f6b7c9103e67d19eda74a7f'
PLAN = 'edb5ab64165471581644add31944c2946742a59b104772e3c49a332bc8a0d04d'
BASE = 'var/lib/bridge-light-native-lane-owner'
PHASES = ('prepare', 'publish', 'permit', 'execute', 'terminal', 'restore')
SCOPES = {
    BASE+'/issuers/'+POLICY: ('policy.json',),
    BASE+'/issuers/'+POLICY+'/0000': ('intent.json', 'done.json'),
    BASE+'/cycles/'+PLAN: ('intent.json', 'complete.json', 'incident.json',
        'refusal.json', 'contain-intent.json', 'contain-done.json') + tuple(
            phase+'-'+kind+'.json' for phase in PHASES for kind in ('intent', 'done')),
    BASE+'/'+PLAN: ('plan.json', 'baseline.json', 'before.json', 'prepare.json',
        'intake.json', 'contain-intent.json', 'contained.json', 'publication-intent.json',
        'broker.json', 'discovery.json', 'permit-intent.json', 'permit.json',
        'terminal.json', 'restore-intent.json', 'restored.json', 'publication.json',
        'execution.json', 'controls-restored.json', 'restart-intent.json', 'complete.json',
        'recovery-intent.json', 'recovery-before.json', 'recovery-after.json', 'recovered.json',
        'runtime-package.json', 'wheels.tar') + tuple(
            phase+'-claim-refresh-'+kind+'.json' for phase in PHASES
            for kind in ('intent', 'before', 'after')),
}


def require(ok):
    if not ok:
        raise ValueError('INCIDENT_COLLECTION_REFUSED')


def meta(s):
    return dict(dev=s.st_dev, ino=s.st_ino, uid=s.st_uid, gid=s.st_gid,
                mode=stat.S_IMODE(s.st_mode), nlink=s.st_nlink,
                size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)


def validate_binding(records):
    require(records[BASE+'/issuers/'+POLICY+'/policy.json']['sha256'] == POLICY)
    require(records[BASE+'/'+PLAN+'/plan.json']['sha256'] == PLAN)


def collect(private=False):
    import fcntl
    require(sys.platform == 'linux' and os.geteuid() == 0 and os.getegid() == 0)
    # NOATIME has no fallback: even access-time updates are not requested.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK
    fds = {}
    links = []
    lock_fds = []
    started = time.monotonic_ns()

    def directory(path):
        if path in fds:
            return fds[path]
        if not path:
            fd = os.open('/', flags | os.O_DIRECTORY)
        else:
            parent, _, name = path.rpartition('/')
            pfd = directory(parent)
            fd = os.open(name, flags | os.O_DIRECTORY, dir_fd=pfd)
            links.append((pfd, name, fd))
        fds[path] = fd
        s = os.fstat(fd)
        require(s.st_uid == 0 and stat.S_IMODE(s.st_mode) & 0o022 == 0)
        if path == BASE or path.startswith(BASE+'/'):
            require(stat.S_IMODE(s.st_mode) == 0o700)
        return fd

    def read(path, name):
        dfd = directory(path)
        try:
            fd = os.open(name, flags, dir_fd=dfd)
        except FileNotFoundError:
            return {'present': False}
        try:
            s = os.fstat(fd)
            require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                    and stat.S_IMODE(s.st_mode) == 0o600 and 0 < s.st_size <= 32*1024*1024)
            h = hashlib.sha256()
            # Large packages are hashed only. Private bytes are fixed JSON records.
            export = private and name.endswith('.json') and name != 'runtime-package.json'
            require(not export or s.st_size <= 4*1024*1024)
            chunks = []
            count = 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                count += len(chunk)
                require(count <= 32*1024*1024)
                h.update(chunk)
                if export:
                    chunks.append(chunk)
            require(count == s.st_size and meta(os.fstat(fd)) == meta(s))
            require(meta(os.stat(name, dir_fd=dfd, follow_symlinks=False)) == meta(s))
            value = dict(present=True, metadata=meta(s), sha256=h.hexdigest())
            if export:
                value['base64'] = base64.b64encode(b''.join(chunks)).decode('ascii')
            return value
        finally:
            os.close(fd)

    def snapshot():
        records, inventories = {}, {}
        for path, names in SCOPES.items():
            dfd = directory(path)
            entries = os.listdir(dfd)
            require(len(entries) <= 128)
            # Fixed names only in output; hash unexpected names instead of exposing them.
            inventories[path] = dict(metadata=meta(os.fstat(dfd)), count=len(entries),
                names_sha256=hashlib.sha256(json.dumps(sorted(entries)).encode()).hexdigest(),
                unexpected_count=len(set(entries)-set(names)-({'0000'} if path.endswith(POLICY) else set())))
            for name in names:
                records[path+'/'+name] = read(path, name)
        validate_binding(records)
        return records, inventories

    try:
        for path in (BASE+'/issuers', BASE+'/cycles'):
            fd = os.open('cycle.lock', flags, dir_fd=directory(path))
            lock_fds.append(fd)
            s = os.fstat(fd)
            require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                    and stat.S_IMODE(s.st_mode) == 0o600)
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            links.append((directory(path), 'cycle.lock', fd))
        first = snapshot()
        require(snapshot() == first)
        for parent, name, fd in links:
            require(meta(os.stat(name, dir_fd=parent, follow_symlinks=False)) == meta(os.fstat(fd)))
        require(time.monotonic_ns()-started < 30_000_000_000)
        return dict(kind='INCIDENT_FILE_OBSERVATION_ONLY', policy_sha256=POLICY,
                    plan_sha256=PLAN, collected_at_ns=time.time_ns(),
                    host=os.uname().nodename, records=first[0], inventories=first[1],
                    live_verified=False, issue_allowed=False)
    finally:
        for fd in reversed(lock_fds):
            os.close(fd)
        for fd in reversed(list(fds.values())):
            os.close(fd)


if __name__ == '__main__':
    try:
        require(sys.argv[1:] in ([], ['--private-bytes']))
        result = collect(bool(sys.argv[1:]))
    except BaseException:
        print('{"state":"INCIDENT_COLLECTION_REFUSED","issue_allowed":false}')
        sys.exit(2)
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
