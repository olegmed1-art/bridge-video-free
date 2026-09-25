"""One user-scoped Codex CLI install on Light HOLD; no login or dispatch.

Package/version/integrity are pinned. The target must be absent. npm runs with
scripts disabled in a disposable private staging directory; the final rename
is atomic and never replaces an existing installation.
"""
import json
import ctypes
import grp
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile

UNIT = 'school-autopilot-production-light.service'
HOME = Path('/home/ubuntu')
PARENT = HOME / '.local/share'
TARGET = PARENT / 'slavik-codex'
NODE = Path('/home/ubuntu/.nvm/versions/node/v22.23.2/bin/node')
NPM = Path('/home/ubuntu/.nvm/versions/node/v22.23.2/bin/npm')
VERSION = '0.157.0'
INTEGRITIES = {
    'node_modules/@openai/codex':
        ('0.157.0', 'sha512-st1R2MhP3ndngOqj2SVh1qk6ED1lpgtlDxipDUyxlKfbsna0imwU2FdTnCjohFQpVh4bR5D5m1hA05AuW2v8Xg=='),
    'node_modules/@openai/codex-linux-arm64':
        ('0.157.0-linux-arm64', 'sha512-67Y2HL4s+DEKtWr1oFz86iN+wLDRsdm6fuq8rhaxlt8QeIK79aO7Eg82Wfucou4QSisq/SSWYkwa9q7hWi8fLA=='),
}


def require_host():
    if (os.geteuid() != pwd.getpwnam('ubuntu').pw_uid
            or os.uname().nodename != 'autopilot-lite-vnic'
            or os.uname().machine != 'aarch64'):
        raise ValueError('HOST_OR_IDENTITY_INVALID')
    output=subprocess.run(['systemctl','show',UNIT,'-pActiveState','-pSubState','-pEnvironment'],
                          check=True,capture_output=True,text=True,timeout=15).stdout
    fields=dict(line.split('=',1) for line in output.splitlines() if '=' in line)
    admission=[token for token in fields.get('Environment','').split()
               if token.startswith('AUTOPILOT_ADMISSION_MODE=')]
    if (fields.get('ActiveState')!='active' or fields.get('SubState')!='running'
            or admission!=['AUTOPILOT_ADMISSION_MODE=HOLD']):
        raise ValueError('NOT_ACTIVE_HOLD')
    if os.statvfs(HOME).f_bavail * os.statvfs(HOME).f_frsize < 4*1024**3:
        raise ValueError('DISK_CAPACITY_LOW')


def check_parent(path,uid):
    try:
        info=path.lstat()
    except FileNotFoundError:
        return False
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0,uid)
            or info.st_mode & 0o022):
        raise ValueError('UNSAFE_INSTALL_PARENT')
    return True


def check_binary(path,uid):
    # npm is normally a symlink inside this pinned NVM tree. A symlink outside
    # that tree, or through a writable shared parent, is not trusted.
    prefix=NODE.parent.parent
    resolved=path.resolve(strict=True)
    if not resolved.is_relative_to(prefix):
        raise ValueError('RUNTIME_OUTSIDE_NVM')
    for parent in (*path.parents,*resolved.parents):
        if parent==Path('/'):
            continue
        check_parent(parent,uid)
    info=resolved.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,uid)
            or not info.st_mode & stat.S_IXUSR or info.st_mode & 0o022):
        raise ValueError('RUNTIME_UNTRUSTED')


def rename_no_replace(source,target):
    """Install atomically, failing if another process created the target."""
    libc=ctypes.CDLL(None,use_errno=True)
    renameat2=getattr(libc,'renameat2',None)
    if renameat2 is None:
        raise ValueError('ATOMIC_RENAME_UNAVAILABLE')
    renameat2.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,
                        ctypes.c_char_p,ctypes.c_uint]
    renameat2.restype=ctypes.c_int
    if renameat2(-100,os.fsencode(source),-100,os.fsencode(target),1):
        error=ctypes.get_errno()
        raise OSError(error,os.strerror(error))


def verify_package(install):
    lock=json.loads((install/'package-lock.json').read_text())
    packages=lock['packages']
    for name,(version,integrity) in INTEGRITIES.items():
        item=packages[name]
        if (item['version']!=version or item['integrity']!=integrity or
                item['resolved']!=f'https://registry.npmjs.org/@openai/codex/-/codex-{version}.tgz'):
            raise ValueError('PACKAGE_INTEGRITY_INVALID')
    if set((install/'node_modules/@openai').iterdir()) != {
            install/'node_modules/@openai/codex',
            install/'node_modules/@openai/codex-linux-arm64'}:
        raise ValueError('PACKAGE_SCOPE_INVALID')
    metadata=json.loads((install/'node_modules/@openai/codex/package.json').read_text())
    if (metadata.get('name')!='@openai/codex' or metadata.get('version')!=VERSION
            or metadata.get('bin',{}).get('codex')!='bin/codex.js'):
        raise ValueError('PACKAGE_MANIFEST_INVALID')
    link=install/'node_modules/.bin/codex'
    if not link.is_symlink() or os.readlink(link)!='../@openai/codex/bin/codex.js':
        raise ValueError('PACKAGE_ENTRYPOINT_INVALID')
    native=install/'node_modules/@openai/codex-linux-arm64/vendor/aarch64-unknown-linux-musl/bin/codex'
    info=native.lstat()
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & stat.S_IXUSR:
        raise ValueError('PACKAGE_NATIVE_INVALID')


def diagnose():
    """Read-only fixed-code report for a blocked install; never execute npm."""
    def group_audit(failure):
        if failure!={'chain':'source','index':1,'reason':'GROUP_WRITABLE'}:
            return None
        try:
            info=NODE.parent.parent.lstat()
            user=pwd.getpwnam('ubuntu')
            group=grp.getgrgid(info.st_gid)
            accounts={member.pw_name for member in pwd.getpwall()
                      if member.pw_gid==info.st_gid} | set(group.gr_mem)
            return {'version_owner_ubuntu':info.st_uid==user.pw_uid,
                    'version_group_ubuntu_primary':info.st_gid==user.pw_gid,
                    'group_has_other_accounts':bool(accounts-{'ubuntu'})}
        except (OSError,KeyError):
            return {'status':'METADATA_UNAVAILABLE'}

    def parent_failure(path,uid):
        try:
            resolved=path.resolve(strict=True)
            for chain,parents in (('source',path.parents),('resolved',resolved.parents)):
                for index,parent in enumerate(parents):
                    if parent==Path('/'):
                        continue
                    info=parent.lstat()
                    reason=('NOT_DIRECTORY' if not stat.S_ISDIR(info.st_mode) else
                            'OWNER_OTHER' if info.st_uid not in (0,uid) else
                            'WORLD_WRITABLE' if info.st_mode & 0o002 else
                            'GROUP_WRITABLE' if info.st_mode & 0o020 else None)
                    if reason:
                        return {'chain':chain,'index':index,'reason':reason}
        except OSError:
            return {'reason':'METADATA_UNAVAILABLE'}
        return {'reason':'NO_UNSAFE_PARENT_FOUND'}

    def result(check):
        try:
            value=check()
            return value if value in ('SAFE','MISSING','ABSENT','PRESENT') else 'SAFE'
        except ValueError as exc:
            allowed={'HOST_OR_IDENTITY_INVALID','NOT_ACTIVE_HOLD','DISK_CAPACITY_LOW',
                     'UNSAFE_INSTALL_PARENT','RUNTIME_OUTSIDE_NVM','RUNTIME_UNTRUSTED'}
            return str(exc) if str(exc) in allowed else 'CHECK_FAILED'
        except OSError:
            return 'CHECK_FAILED'

    guard=result(lambda: (require_host(), 'SAFE')[1])
    if guard!='SAFE':
        return {'audit':'NATIVE_CLI_STAGE_DIAGNOSTIC','guard':guard,
                'installation_action':'NONE'}
    uid=os.geteuid()
    parents={name:result(lambda path=path:
               'SAFE' if check_parent(path,uid) else 'MISSING')
             for name,path in (('home',HOME),('local',HOME/'.local'),('share',PARENT))}
    node=result(lambda: (check_binary(NODE,uid),'SAFE')[1])
    npm=result(lambda: (check_binary(NPM,uid),'SAFE')[1])
    node_failure=parent_failure(NODE,uid) if node=='UNSAFE_INSTALL_PARENT' else None
    npm_failure=parent_failure(NPM,uid) if npm=='UNSAFE_INSTALL_PARENT' else None
    return {'audit':'NATIVE_CLI_STAGE_DIAGNOSTIC','guard':guard,
            'parents':parents,
            'target':result(lambda: 'PRESENT' if TARGET.exists() or TARGET.is_symlink()
                            else 'ABSENT'),
            'node':node,'npm':npm,
            'node_parent_failure':node_failure,
            'npm_parent_failure':npm_failure,
            'version_group_audit':group_audit(node_failure) if node_failure==npm_failure else None,
            'installation_action':'NONE'}


def install():
    require_host()
    uid=os.geteuid()
    for parent in (HOME,HOME/'.local',PARENT):
        if not check_parent(parent,uid):
            if parent==HOME:
                raise ValueError('HOME_MISSING')
            parent.mkdir(mode=0o700)
    if TARGET.exists() or TARGET.is_symlink():
        raise ValueError('TARGET_ALREADY_EXISTS')
    check_binary(NODE,uid)
    check_binary(NPM,uid)
    stage=Path(tempfile.mkdtemp(prefix='.codex-stage-',dir=PARENT))
    try:
        destination=stage/'package'
        env={'HOME':str(HOME),'PATH':str(NODE.parent)+':/usr/bin:/bin',
             'LANG':'C.UTF-8','npm_config_userconfig':'/dev/null',
             'npm_config_globalconfig':'/dev/null','npm_config_cache':str(stage/'cache')}
        run=subprocess.run([str(NPM),'install','--prefix',str(destination),
                            '--ignore-scripts','--no-audit','--no-fund',
                            '--registry=https://registry.npmjs.org',
                            '@openai/codex@'+VERSION],cwd='/',env=env,
                           capture_output=True,timeout=300)
        if run.returncode:
            raise ValueError('PACKAGE_FETCH_FAILED')
        verify_package(destination)
        require_host()
        if TARGET.exists() or TARGET.is_symlink():
            raise ValueError('TARGET_CHANGED')
        rename_no_replace(destination,TARGET)
    finally:
        shutil.rmtree(stage)
    return {'audit':'NATIVE_CLI_STAGE','status':'CLI_STAGED_AUTH_UNVERIFIED',
            'profile':'ubuntu','version':VERSION,'admission':'HOLD',
            'service_restart':False,'database_writes':False}


def entry(argv):
    if argv==['--diagnose']:
        return diagnose()
    if not argv:
        return install()
    raise ValueError('ARGUMENT_INVALID')


if __name__=='__main__':
    try:
        print(json.dumps(entry(sys.argv[1:]),sort_keys=True))
    except BaseException:
        print(json.dumps({'audit':'BLOCKED','code':'CLI_STAGE_FAILED'}))
        sys.exit(2)
