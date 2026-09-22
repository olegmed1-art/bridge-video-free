"""Emit only the fixed bounded private archive over the existing SSH channel."""
import os
from pathlib import Path
import pwd
import stat
import sys

path=Path('/home/ubuntu/autopilot-db-migration-20260922/candidate-backup-20260922.tar.gz')
assert not any(p.is_symlink() for p in [path,*path.parents])
before=path.lstat()
assert stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==0o600
assert before.st_uid==pwd.getpwnam('ubuntu').pw_uid
assert 0<before.st_size<32*1024**2
with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as source:
    current=os.fstat(source.fileno())
    assert (before.st_dev,before.st_ino,before.st_size)==(current.st_dev,current.st_ino,current.st_size)
    content=source.read(32*1024**2)
    assert len(content)==before.st_size
sys.stdout.buffer.write(content)
