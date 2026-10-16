"""Offline, root-operated, pinned native CLI staging on active Light HOLD.

No login, service configuration, database writes or task dispatch. Trusted root
operators must exclude concurrent maintenance; locks are advisory. Never invoke
through a channel which prohibits privileged execution.
"""
import argparse
import base64
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
import urllib.request

ROOT = Path('/opt/bridge-school/school-autopilot-production-light')
TARGET = ROOT / 'runtime-bin'
ARCHIVE_URL = 'https://registry.npmjs.org/@openai/codex/-/codex-0.157.0-linux-arm64.tgz'
INTEGRITY = '67Y2HL4s+DEKtWr1oFz86iN+wLDRsdm6fuq8rhaxlt8QeIK79aO7Eg82Wfucou4QSisq/SSWYkwa9q7hWi8fLA=='
NATIVE = 'vendor/aarch64-unknown-linux-musl/bin/codex'
MAX_ARCHIVE = 256 * 1024**2
MAX_EXPANDED = 512 * 1024**2
MAX_FILE = 300 * 1024**2
VENDOR = 'vendor/aarch64-unknown-linux-musl/'
EXECUTABLES = {VENDOR + name for name in (
    'bin/codex', 'bin/codex-code-mode-host', 'codex-path/rg',
    'codex-resources/bwrap', 'codex-resources/voice/bin/codex-voice-host',
    'codex-resources/zsh/bin/zsh')}

WRAPPER = ('#!/bin/sh\nPATH="'+str(TARGET)+'/vendor/aarch64-unknown-linux-musl/codex-path:/usr/bin:/bin"\n'
           'export PATH\nexec "'+str(TARGET/NATIVE)+'" "$@"\n')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def identity(info):
    return info.st_dev, info.st_ino, info.st_uid, info.st_gid


def no_acl(fd):
    import errno
    for kind in ('access','default'):
        try:
            os.getxattr(fd, 'system.posix_acl_'+kind)
        except OSError as exc:
            require(exc.errno == errno.ENODATA, 'ACL_UNKNOWN')
        else:
            raise RuntimeError('ACL_PRESENT')


class ParentChain:
    def __init__(self, path):
        self.fds = []
        self.names = path.parts[1:]
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            self.fds.append(os.open('/', flags))
            for name in self.names:
                self.fds.append(os.open(name, flags, dir_fd=self.fds[-1]))
            self.ids = [identity(os.fstat(fd)) for fd in self.fds]
            self.validate()
            fcntl.flock(self.fds[-1], fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.close()
            raise

    def validate(self):
        for i,fd in enumerate(self.fds):
            info=os.fstat(fd)
            require(identity(info)==self.ids[i] and stat.S_ISDIR(info.st_mode)
                    and info.st_uid==0 and not info.st_mode & 0o022, 'PARENT_DRIFT')
            no_acl(fd)
            if i:
                entry=os.stat(self.names[i-1],dir_fd=self.fds[i-1],follow_symlinks=False)
                require(stat.S_ISDIR(entry.st_mode) and identity(entry)==identity(info),'PATH_DRIFT')

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds=[]


def verified_archive(source, destination):
    """Copy and hash one open input; extract only the immutable private copy."""
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as src, os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as dst:
        info=os.fstat(src.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= MAX_ARCHIVE,'ARCHIVE_SIZE')
        digest=hashlib.sha512()
        total=0
        while chunk:=src.read(1024**2):
            total+=len(chunk)
            require(total<=MAX_ARCHIVE,'ARCHIVE_SIZE')
            digest.update(chunk)
            dst.write(chunk)
        require(total==info.st_size and base64.b64encode(digest.digest()).decode()==INTEGRITY,
                'ARCHIVE_INTEGRITY')
        dst.flush()
        os.fsync(dst.fileno())


def directory_fd(root_fd, parts):
    fd = os.dup(root_fd)
    try:
        for part in parts:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def unpack(archive, destination):
    """Validate all headers, then create through held descriptors exclusively."""
    entries = []
    seen = set()
    total = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            name = member.name
            path = PurePosixPath(name)
            require(len(entries) < 128 and len(name) <= 512 and len(path.parts) <= 12
                    and str(path) == name and not path.is_absolute()
                    and '..' not in path.parts and len(path.parts) > 1
                    and path.parts[0] == 'package', 'ARCHIVE_PATH')
            relative = PurePosixPath(*path.parts[1:])
            require(str(relative) not in seen and relative.parts[0] != 'codex', 'ARCHIVE_DUPLICATE')
            seen.add(str(relative))
            require((member.isdir() or member.isreg()) and member.sparse is None
                    and not any('sparse' in key.lower() for key in member.pax_headers), 'ARCHIVE_TYPE')
            total += member.size
            require(0 <= member.size <= MAX_FILE and total <= MAX_EXPANDED
                    and (not member.isdir() or member.size == 0), 'EXPANDED_SIZE')
            entries.append((member, relative))
        root_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for member, relative in entries:
                parent_fd = directory_fd(root_fd, relative.parts if member.isdir() else relative.parts[:-1])
                try:
                    if member.isdir():
                        continue
                    fd = os.open(relative.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent_fd)
                    with tar.extractfile(member) as src, os.fdopen(fd, 'wb') as dst:
                        remaining = member.size
                        while remaining:
                            chunk = src.read(min(1024**2, remaining))
                            require(bool(chunk), 'ARCHIVE_TRUNCATED')
                            dst.write(chunk)
                            remaining -= len(chunk)
                        require(not src.read(1), 'ARCHIVE_SIZE_DRIFT')
                        dst.flush()
                        os.fchmod(dst.fileno(), 0o555 if str(relative) in EXECUTABLES else 0o444)
                        os.fsync(dst.fileno())
                finally:
                    os.close(parent_fd)
            manifest = destination / 'package.json'
            require(manifest.stat().st_size <= 65536, 'PACKAGE_MANIFEST_SIZE')
            package = json.loads(manifest.read_text())
            require(package.get('name') == '@openai/codex' and
                    package.get('version') == '0.157.0-linux-arm64' and
                    package.get('os') == ['linux'] and package.get('cpu') == ['arm64'], 'PACKAGE_MANIFEST')
            require(all((destination / name).is_file() for name in EXECUTABLES), 'RESOURCES_MISSING')
            fd = os.open('codex', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
            with os.fdopen(fd, 'w') as dst:
                dst.write(WRAPPER)
                dst.flush()
                os.fchmod(dst.fileno(), 0o555)
                os.fsync(dst.fileno())
            for path in sorted(destination.rglob('*'), key=lambda p: len(p.parts), reverse=True):
                if path.is_dir():
                    path.chmod(0o555)
            os.fchmod(root_fd, 0o555)
        finally:
            os.close(root_fd)


def inventory(root):
    result = {}
    total = 0
    for path in (root, *sorted(root.rglob('*'))):
        info = path.lstat()
        require(info.st_uid == 0 and info.st_gid == 0 and not stat.S_ISLNK(info.st_mode), 'STAGE_OWNER')
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'STAGE_TYPE')
        mode = stat.S_IMODE(info.st_mode)
        relative = str(path.relative_to(root))
        expected = 0o555 if stat.S_ISDIR(info.st_mode) or relative in EXECUTABLES | {'codex'} else 0o444
        require(mode == expected, 'STAGE_MODE')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') if stat.S_ISREG(info.st_mode) else directory_handle(fd) as handle:
            opened = os.fstat(fd)
            require(identity(opened) == identity(info), 'STAGE_IDENTITY')
            no_acl(fd)
            digest = None
            if stat.S_ISREG(info.st_mode):
                require(opened.st_nlink == 1 and opened.st_size <= MAX_FILE, 'STAGE_FILE')
                total += opened.st_size
                require(total <= MAX_EXPANDED and len(result) < 256, 'STAGE_BOUNDS')
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            result[relative] = (identity(info), mode, digest)
    return result


class directory_handle:
    def __init__(self, fd):
        self.fd = fd
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        os.close(self.fd)


def sync_tree(root):
    for path in (*sorted(root.rglob('*'),key=lambda p:len(p.parts),reverse=True),root):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def rename_no_replace(parent_fd, source, target):
    libc=ctypes.CDLL(None,use_errno=True)
    rename=getattr(libc,'renameat2',None)
    require(rename is not None,'ATOMIC_RENAME_UNAVAILABLE')
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    if rename(parent_fd,os.fsencode(source),parent_fd,os.fsencode(target),1):
        error=ctypes.get_errno()
        raise OSError(error,os.strerror(error))


def transact(parent, archive, preflight, postflight, emit):
    chain=ParentChain(parent)
    temporary=None
    attempted=False
    succeeded=False
    temporary_id=None
    temporary_fd=None
    baseline=None
    try:
        preflight()
        chain.validate()
        require(not (parent/'runtime-bin').exists() and not (parent/'runtime-bin').is_symlink(),'TARGET_EXISTS')
        require(os.statvfs(parent).f_bavail*os.statvfs(parent).f_frsize>=2*1024**3,'DISK_CAPACITY')
        temporary=Path(tempfile.mkdtemp(prefix='.native-cli-stage-',dir=parent))
        temporary_fd=os.open(temporary,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        temporary_id=identity(os.fstat(temporary_fd))
        require(identity(temporary.lstat())==temporary_id,'TEMPORARY_DRIFT')
        verified_archive(archive,temporary/'archive.tgz')
        tree=temporary/'tree'
        tree.mkdir(mode=0o700)
        unpack(temporary/'archive.tgz',tree)
        baseline=inventory(tree)
        sync_tree(tree)
        preflight()  # Reattest after bounded extraction and hashing.
        chain.validate()
        require(inventory(tree)==baseline,'STAGE_DRIFT')
        emit({'audit':'SERVICE_CLI_STAGE','action':'PREPARED','version':'0.157.0'})
        chain.validate()
        require(identity(temporary.lstat())==temporary_id and
                identity(os.fstat(temporary_fd))==temporary_id,'TEMPORARY_DRIFT')
        os.fsync(temporary_fd)
        os.fsync(chain.fds[-1])
        attempted=True
        rename_no_replace(chain.fds[-1],str(tree.relative_to(parent)),'runtime-bin')
        os.fsync(temporary_fd)
        os.fsync(chain.fds[-1])
        require(inventory(parent/'runtime-bin')==baseline,'PROMOTED_DRIFT')
        postflight()
        chain.validate()
        require(inventory(parent/'runtime-bin')==baseline,'POST_STAGE_DRIFT')
        succeeded=True
        emit({'audit':'SERVICE_CLI_STAGE','action':'STAGED_VERIFIED',
              'version':'0.157.0','auth':'NOT_CHECKED','tasks_started':False})
    except BaseException:
        rollback='NOT_NEEDED'
        if attempted:
            rollback='UNCERTAIN'
            try:
                chain.validate()
                require(identity(temporary.lstat())==temporary_id,'QUARANTINE_DRIFT')
                target=parent/'runtime-bin'
                if target.exists() or target.is_symlink():
                    require(inventory(target)==baseline,'ROLLBACK_TARGET_DRIFT')
                    require(identity(os.fstat(temporary_fd))==temporary_id,'QUARANTINE_DRIFT')
                    os.fsync(temporary_fd)
                    os.fsync(chain.fds[-1])
                    rename_no_replace(chain.fds[-1],'runtime-bin',str((temporary/'tree').relative_to(parent)))
                    os.fsync(temporary_fd)
                    os.fsync(chain.fds[-1])
                else:
                    require(inventory(temporary/'tree')==baseline,'ROLLBACK_SOURCE_DRIFT')
                require(not target.exists() and not target.is_symlink(),'ROLLBACK_TARGET_EXISTS')
                sync_tree(temporary/'tree')
                rollback='TARGET_ABSENT_VERIFIED'
            except BaseException:
                pass
        hold='NOT_RECHECKED'
        if attempted:
            try:
                postflight()
                hold='VERIFIED'
            except BaseException:
                hold='UNVERIFIED'
        emit({'audit':'BLOCKED','rollback':rollback,'post_rollback_hold':hold,
              'quarantine_retained':attempted})
        raise
    finally:
        # Only remove our private staging directory when its parent chain is intact.
        if temporary is not None and (not attempted or succeeded):
            try:
                chain.validate()
                require(identity(temporary.lstat())==temporary_id,'TEMPORARY_DRIFT')
                shutil.rmtree(temporary)
            except BaseException:
                emit({'audit':'STAGING_CLEANUP_UNCERTAIN'})
        if temporary_fd is not None:
            os.close(temporary_fd)
        chain.close()


def current_main(expected):
    require(re.fullmatch('[a-f0-9]{40}',expected) is not None,'EXPECTED_MAIN')
    req=urllib.request.Request('https://api.github.com/repos/olegmed1-art/bridge-video-free/git/ref/heads/main',
                               headers={'Accept':'application/vnd.github+json','User-Agent':'light-cli-stage'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *unused, **kwargs):
            raise RuntimeError('MAIN_REDIRECT')
    with urllib.request.build_opener(NoRedirect).open(req,timeout=15) as response:
        raw=response.read(65537)
    require(len(raw)<=65536 and json.loads(raw)['object']['sha']==expected,'MAIN_DRIFT')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',required=True,type=Path)
    parser.add_argument('--expected-main',required=True)
    args=parser.parse_args()
    os.umask(0o077)
    require(os.geteuid()==0 and os.getegid()==0 and os.uname().nodename=='autopilot-lite-vnic'
            and os.uname().machine=='aarch64','HOST_IDENTITY')
    import oracle_light_active_hold_attest as attest
    initial=attest.service()
    def live():
        current_main(args.expected_main)
        attest.main()
        require(attest.service()==initial,'INVOCATION_DRIFT')
    transact(ROOT,args.archive,live,live,lambda value:print(json.dumps(value,sort_keys=True),flush=True))


if __name__=='__main__':
    try:
        main()
    except BaseException as exc:
        if isinstance(exc,SystemExit):
            raise
        print(json.dumps({'audit':'BLOCKED','code':'SERVICE_CLI_STAGE_FAILED'}),flush=True)
        raise SystemExit(2)
