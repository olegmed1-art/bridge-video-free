"""Reviewed stdin bundle runner. Root attestation, one mode bit, postattestation.

Only fixed sources from the exact reviewed main are bundled by the workflow.
Never invokes the stage module's install entrypoint.
"""
import contextlib
import io
import json
import os
import pwd
import subprocess
import sys


def main():
    raw = sys.stdin.buffer.read(131073)
    if len(raw) > 131072:
        raise ValueError('BUNDLE_TOO_LARGE')
    bundle = json.loads(raw)
    if set(bundle) != {'repair', 'attest', 'stage', 'mode', 'expected_mode'}:
        raise ValueError('BUNDLE_KEYS')
    if bundle['mode'] not in ('inspect', 'repair'):
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
    except BaseException:
        print('{"audit":"BLOCKED","code":"NVM_REPAIR_RUNNER_FAILED"}', flush=True)
        sys.exit(2)
