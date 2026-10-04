"""Linux durable evidence and exact symlink operations. No existing modes changed."""
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import stat
import ctypes
import uuid
from protocol import Refused, encoded, require, TARGETS

MAX_FILE = 1024*1024

def fsync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)

def secure_dir(path, create=False):
    path=Path(path)
    if create and not path.exists():
        os.mkdir(path,0o700)
        fsync_dir(path.parent)
    s=path.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid()
            and not (s.st_mode & 0o022), 'UNSAFE_EVIDENCE_DIRECTORY')
    return path

def read_bytes(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
                and s.st_nlink == 1 and not(s.st_mode & 0o022) and s.st_size <= MAX_FILE,'UNSAFE_EVIDENCE_FILE')
        with os.fdopen(fd,'rb',closefd=False) as f:
            raw=f.read(MAX_FILE+1)
        require(len(raw)<=MAX_FILE,'EVIDENCE_TOO_LARGE')
        return raw
    finally: os.close(fd)

def write_once(path, raw):
    require(len(raw)<=MAX_FILE,'EVIDENCE_TOO_LARGE')
    path=Path(path)
    # Interrupted writes remain unreferenced pending files, never torn final records.
    temporary=path.parent/('.pending-'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb',closefd=False) as f:
            f.write(raw); f.flush(); os.fsync(fd)
    finally: os.close(fd)
    require(os.name=='posix','LINUX_ATOMIC_PUBLICATION_REQUIRED')
    libc=ctypes.CDLL(None,use_errno=True)
    rename=libc.renameat2
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    require(rename(-100,os.fsencode(temporary),-100,os.fsencode(path),1)==0,'ATOMIC_PUBLICATION_REFUSED')
    fsync_dir(Path(path).parent)
    require(read_bytes(path)==raw,'EVIDENCE_READBACK_FAILED')

def sync_verified(path,raw):
    require(read_bytes(path)==raw,'EVIDENCE_CONFLICT')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)
    fsync_dir(Path(path).parent)
    require(read_bytes(path)==raw,'EVIDENCE_READBACK_FAILED')

class Store:
    def __init__(self, root, operation, create=False):
        require(fcntl is not None,'LINUX_REQUIRED')
        self.root=secure_dir(root,create=create)
        self.directory=secure_dir(self.root/operation,create=create)
        self.lockfd=os.open(self.root/'lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        st=os.fstat(self.lockfd)
        try:
            require(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1
                    and not(st.st_mode&0o022),'UNSAFE_LOCK')
            fcntl.flock(self.lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:
            os.close(self.lockfd)
            raise
    def once(self,name,value): write_once(self.directory/(name+'.json'),encoded(value))
    def ensure(self,name,value):
        p=self.directory/(name+'.json')
        if p.exists(): sync_verified(p,encoded(value))
        else: write_once(p,encoded(value))
    def attempted(self): return os.path.lexists(self.root/'mutation-intent.json')
    def claim_value(self):
        try: return json.loads(read_bytes(self.root/'mutation-intent.json'))
        except Exception: raise Refused('CLAIM_RECORD_UNREADABLE') from None
    def claim(self,value): write_once(self.root/'mutation-intent.json',encoded(value))
    def manifest(self): return json.loads(read_bytes(self.directory/'baseline.json'))

def link_parent(path):
    p=Path(path)
    allowed={Path('/etc/systemd/system/multi-user.target.wants'),Path('/etc/systemd/system/timers.target.wants')}
    require(p.name in TARGETS and p.parent in allowed,'LINK_SCOPE')
    # Every ancestor must be an actual directory; reject redirection.
    for parent in [p.parent,*p.parent.parents]:
        s=parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and not stat.S_ISLNK(s.st_mode)
                and s.st_uid==0 and not(s.st_mode & 0o022),'LINK_PARENT_UNSAFE')
    return p,os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)

def exact_link(path,target,restore=False):
    p,fd=link_parent(path)
    try:
        try:
            s=os.stat(p.name,dir_fd=fd,follow_symlinks=False)
        except FileNotFoundError:
            require(restore,'LINK_DISAPPEARED')
            os.symlink(target,p.name,dir_fd=fd)
        else:
            require(stat.S_ISLNK(s.st_mode) and os.readlink(p.name,dir_fd=fd)==target,'LINK_CONFLICT')
            if not restore: os.unlink(p.name,dir_fd=fd)
        os.fsync(fd)
        if restore: require(os.readlink(p.name,dir_fd=fd)==target,'RESTORE_LINK_READBACK')
        else:
            try: os.stat(p.name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise Refused('UNLINK_READBACK')
    finally: os.close(fd)

def verified_copy(directory, raw, backup_identity):
    manifest=json.loads(raw)
    require(raw==encoded(manifest),'NONCANONICAL_BASELINE')
    require(backup_identity != manifest['identity'],'COPY_NOT_OFFHOST')
    directory=secure_dir(directory,create=True)
    path=directory/(manifest['operation']+'.baseline.json')
    if path.exists(): sync_verified(path,raw)
    else: write_once(path,raw)
    actual=hashlib.sha256(read_bytes(path)).hexdigest()
    receipt={'version':2,'baseline_sha':actual,'verified_sha':actual,'operation':manifest['operation'],
             'guest_identity':manifest['identity'],'backup_identity':backup_identity,
             'location':str(path),'durable':True}
    rp=directory/(manifest['operation']+'.receipt.json')
    if rp.exists(): sync_verified(rp,encoded(receipt))
    else: write_once(rp,encoded(receipt))
    return receipt
