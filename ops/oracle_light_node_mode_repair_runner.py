"""Reviewed stdin bundle runner. Root attestation, one mode bit, postattestation.

Only fixed sources from the exact reviewed main are bundled by the workflow.
Never invokes the stage module's install entrypoint.
"""
import contextlib
import errno
import grp
import io
import json
import os
import pwd
from pathlib import Path
import stat
import subprocess
import sys


def path_inventory(version):
    """Bounded metadata only; never grants mutation permission."""
    user = pwd.getpwnam('ubuntu')
    paths = {version, *version.parents}
    for name in ('node', 'npm'):
        source = version / 'bin' / name
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(version):
            raise ValueError('RUNTIME_OUTSIDE_NVM')
        paths.update((source, resolved, *source.parents, *resolved.parents))
    if len(paths) > 24:
        raise ValueError('INVENTORY_TOO_LARGE')
    accounts = pwd.getpwall()
    rows = []
    stable = True
    for path in sorted(paths, key=lambda p: (len(p.parts), str(p))):
        before = path.lstat()
        mode = stat.S_IMODE(before.st_mode)
        acl = {}
        for kind in ('access', 'default'):
            try:
                os.getxattr(path, 'system.posix_acl_' + kind, follow_symlinks=False)
                acl[kind] = 'PRESENT'
            except OSError as exc:
                acl[kind] = 'ABSENT' if exc.errno == errno.ENODATA else 'UNKNOWN'
        try:
            members = {u.pw_name for u in accounts if u.pw_gid == before.st_gid}
            members.update(grp.getgrgid(before.st_gid).gr_mem)
            other_accounts = bool(members - {'ubuntu'})
        except KeyError:
            other_accounts = None
        after = path.lstat()
        unchanged = (before.st_dev, before.st_ino, before.st_uid, before.st_gid,
                     before.st_mode, before.st_ctime_ns) == (
                     after.st_dev, after.st_ino, after.st_uid, after.st_gid,
                     after.st_mode, after.st_ctime_ns)
        stable = stable and unchanged
        rows.append({'path':str(path),'mode':format(mode,'04o'),
                     'kind':'directory' if stat.S_ISDIR(before.st_mode) else
                            'symlink' if stat.S_ISLNK(before.st_mode) else
                            'regular' if stat.S_ISREG(before.st_mode) else 'other',
                     'owner':'root' if before.st_uid == 0 else
                             'ubuntu' if before.st_uid == user.pw_uid else 'other',
                     'ubuntu_primary_group':before.st_gid == user.pw_gid,
                     'group_has_other_accounts':other_accounts,
                     'group_write':bool(mode & 0o020),'world_write':bool(mode & 0o002),
                     'acl':acl,'metadata_unchanged_during_row':unchanged})
    return {'audit':'NVM_PATH_METADATA_ONLY','mutation_authorized':False,
            'row_metadata_stable':stable,'entries':rows}


def failure_code(exc):
    allowed = {'ACL_UNKNOWN','ACL_PRESENT','NOT_DIRECTORY','IDENTITY_DRIFT',
               'PATH_DRIFT','UNSAFE_ANCESTOR','OWNER_DRIFT','HOST_IDENTITY',
               'MODE_UNEXPECTED','PRE_WRITE_DRIFT','POST_MODE_DRIFT','POST_WRITE_DRIFT',
               'GROUP_NOT_PRIVATE','BUNDLE_TOO_LARGE','BUNDLE_KEYS','MODE',
               'LIVE_HOLD_DRIFT','DIAGNOSTIC_FAILED','DIAGNOSTIC_DRIFT','CAUSE_DRIFT',
               'EXPECTED_MODE','RUNTIME_OUTSIDE_NVM','INVENTORY_TOO_LARGE'}
    allowed.update('UNSAFE_ANCESTOR_' + str(i) for i in range(6))
    value = exc.args[0] if exc.args else None
    if isinstance(exc, (RuntimeError, ValueError)) and isinstance(value, str) and value in allowed:
        return value
    if isinstance(exc, OSError):
        return 'OS_ERROR_' + str(exc.errno) if isinstance(exc.errno, int) else 'OS_ERROR'
    return 'NVM_REPAIR_RUNNER_FAILED'


def main():
    raw = sys.stdin.buffer.read(131073)
    if len(raw) > 131072:
        raise ValueError('BUNDLE_TOO_LARGE')
    bundle = json.loads(raw)
    if set(bundle) != {'repair', 'attest', 'stage', 'mode', 'expected_mode'}:
        raise ValueError('BUNDLE_KEYS')
    if bundle['mode'] not in ('inspect', 'repair', 'inventory'):
        raise ValueError('MODE')
    modules = {}
    for name in ('repair', 'attest'):
        namespace = {'__name__': 'reviewed_' + name}
        exec(compile(bundle[name], '<reviewed_'+name+'>', 'exec'), namespace)
        modules[name] = namespace
    repair, attest = modules['repair'], modules['attest']
    repair['require'](os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic'
                      and os.uname().machine == 'aarch64', 'HOST_IDENTITY')
    initial_service = attest['service']()

    def live():
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            attest['main']()
        result = json.loads(output.getvalue())
        if (result.get('audit') != 'ACTIVE_HOLD_PASS' or
                result.get('queue_nonterminal') != 0 or
                result.get('database_login') != 'READ_ONLY_PASS' or
                result.get('admission') != 'HOLD' or
                result.get('same_invocation') is not True or
                attest['service']() != initial_service):
            raise ValueError('LIVE_HOLD_DRIFT')
        print(json.dumps(result, sort_keys=True), flush=True)

    def diagnostic(after):
        user = pwd.getpwnam('ubuntu')
        def identity():
            os.setgroups([])
            os.setgid(user.pw_gid)
            os.setuid(user.pw_uid)
        proc = subprocess.run(['/usr/bin/python3', '-', '--diagnose'],
                              input=bundle['stage'], text=True, capture_output=True,
                              cwd='/', env={'PATH':'/usr/bin:/bin'},
                              preexec_fn=identity, timeout=30)
        if proc.returncode or len(proc.stdout) > 4096:
            raise ValueError('DIAGNOSTIC_FAILED')
        result = json.loads(proc.stdout)
        expected = 'SAFE' if after else 'UNSAFE_INSTALL_PARENT'
        if (result.get('guard') != 'SAFE' or result.get('target') != 'ABSENT' or
                result.get('node') != expected or result.get('npm') != expected or
                result.get('installation_action') != 'NONE' or
                result.get('parents') != {'home':'SAFE','local':'SAFE','share':'SAFE'}):
            raise ValueError('DIAGNOSTIC_DRIFT')
        if not after:
            failure = {'chain':'source','index':1,'reason':'GROUP_WRITABLE'}
            group = {'access_acl':'ABSENT','default_acl':'ABSENT',
                     'group_has_other_accounts':False,'version_group_ubuntu_primary':True,
                     'version_owner_ubuntu':True}
            if (result.get('node_parent_failure') != failure or
                    result.get('npm_parent_failure') != failure or
                    result.get('version_group_audit') != group):
                raise ValueError('CAUSE_DRIFT')
        print(json.dumps(result, sort_keys=True), flush=True)

    def preflight():
        diagnostic(False)
        live()

    def postflight():
        diagnostic(True)
        live()

    if bundle['mode'] == 'inventory':
        preflight()
        print(json.dumps(path_inventory(repair['PATH']), sort_keys=True), flush=True)
        live()
        return
    if bundle['mode'] == 'inspect':
        preflight()
        user = pwd.getpwnam('ubuntu')
        chain = repair['DirectoryChain'](repair['PATH'], user.pw_uid, user.pw_gid)
        try:
            mode = repair['stat'].S_IMODE(os.fstat(chain.fd).st_mode)
            chain.validate()
            print(json.dumps({'audit':'NVM_MODE_INSPECT','mode':format(mode,'04o'),
                              'proposed_mode':format(mode & ~0o020,'04o')}), flush=True)
        finally:
            chain.close()
        live()
        return
    expected = bundle['expected_mode']
    if (not isinstance(expected, str) or len(expected) != 4 or
            any(c not in '01234567' for c in expected)):
        raise ValueError('EXPECTED_MODE')
    try:
        repair['run'](int(expected, 8), preflight, postflight)
    except BaseException:
        # A mode rollback does not imply restored service health.
        try:
            live()
        except BaseException:
            print('{"audit":"BLOCKED","code":"POST_ROLLBACK_HOLD_UNVERIFIED"}', flush=True)
        raise


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        print(json.dumps({'audit':'BLOCKED','code':failure_code(exc)}), flush=True)
        sys.exit(2)
