import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from ops import oracle_light_node_mode_repair_runner as runner


class RunnerTests(unittest.TestCase):
    def test_inventory_collects_all_parents_without_chmod(self):
        with tempfile.TemporaryDirectory() as temp:
            version = Path(temp)/'version'
            (version/'bin').mkdir(parents=True)
            (version/'lib').mkdir()
            (version/'bin/node').write_text('not executed')
            (version/'lib/npm-cli.js').write_text('not executed')
            (version/'bin/npm').symlink_to('../lib/npm-cli.js')
            version.chmod(0o775)
            user = SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
            with patch.object(runner.pwd,'getpwnam',return_value=user), \
                 patch.object(runner.os,'chmod',side_effect=AssertionError('mutation')), \
                 patch.object(runner.os,'fchmod',side_effect=AssertionError('mutation')):
                result = runner.path_inventory(version)
            rows = {r['path']:r for r in result['entries']}
            self.assertEqual(rows[str(version)]['mode'],'0775')
            self.assertTrue(rows[str(version)]['group_write'])
            self.assertIn(str(version/'lib'),rows)
            self.assertIn('/',rows)
            self.assertFalse(result['mutation_authorized'])

    def test_inventory_rejects_binary_outside_version(self):
        with tempfile.TemporaryDirectory() as temp:
            version = Path(temp)/'version'
            (version/'bin').mkdir(parents=True)
            outside = Path(temp)/'outside'
            outside.write_text('not executed')
            (version/'bin/node').symlink_to(outside)
            with patch.object(runner.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=0,pw_gid=0)):
                with self.assertRaisesRegex(ValueError,'RUNTIME_OUTSIDE_NVM'):
                    runner.path_inventory(version)

    def test_failure_code_never_includes_raw_exception(self):
        self.assertEqual(runner.failure_code(RuntimeError('ACL_PRESENT')), 'ACL_PRESENT')
        self.assertEqual(runner.failure_code(RuntimeError('secret-value')), 'NVM_REPAIR_RUNNER_FAILED')
        self.assertEqual(runner.failure_code(OSError(13, 'secret-path')), 'OS_ERROR_13')
        self.assertEqual(runner.failure_code(RuntimeError('UNSAFE_ANCESTOR_5')), 'UNSAFE_ANCESTOR_5')
        self.assertEqual(runner.failure_code(RuntimeError('UNSAFE_ANCESTOR_99')), 'NVM_REPAIR_RUNNER_FAILED')

    def bundle(self):
        live = {'audit':'ACTIVE_HOLD_PASS','queue_nonterminal':0,
                'database_login':'READ_ONLY_PASS','admission':'HOLD','same_invocation':True}
        return {'repair': 'def require(ok,code):\n if not ok: raise ValueError(code)\n'
                         'def run(mode,pre,post):\n pre()\n print("TEST_MUTATION")\n post()\n',
                'attest': 'import json\ndef service(): return {"invocation":"one"}\n'
                          'def main(): print(json.dumps('+repr(live)+'))\n',
                'stage':'raise RuntimeError("INSTALL_MUST_NOT_EXECUTE")',
                'mode':'repair','expected_mode':'0775'}

    def result(self, after=False):
        result = {'guard':'SAFE','target':'ABSENT','installation_action':'NONE',
                  'node':'SAFE' if after else 'UNSAFE_INSTALL_PARENT',
                  'npm':'SAFE' if after else 'UNSAFE_INSTALL_PARENT',
                  'parents':{'home':'SAFE','local':'SAFE','share':'SAFE'}}
        if not after:
            result.update(node_parent_failure={'chain':'source','index':1,'reason':'GROUP_WRITABLE'},
                          npm_parent_failure={'chain':'source','index':1,'reason':'GROUP_WRITABLE'},
                          version_group_audit={'access_acl':'ABSENT','default_acl':'ABSENT',
                          'group_has_other_accounts':False,'version_group_ubuntu_primary':True,
                          'version_owner_ubuntu':True})
        return SimpleNamespace(returncode=0, stdout=json.dumps(result))

    def execute(self, bundle, results):
        self.output = io.StringIO()
        with patch.object(runner.sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(bundle).encode()))), \
             patch.object(runner.os, 'geteuid', return_value=0), \
             patch.object(runner.os, 'uname', return_value=SimpleNamespace(nodename='autopilot-lite-vnic', machine='aarch64')), \
             patch.object(runner.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000,pw_gid=1000)), \
             patch.object(runner.subprocess, 'run', side_effect=results) as proc, \
             contextlib.redirect_stdout(self.output):
            runner.main()
        return proc

    def test_pre_and_post_only_invoke_diagnostic(self):
        proc = self.execute(self.bundle(), [self.result(), self.result(True)])
        self.assertEqual(proc.call_count, 2)
        for call in proc.call_args_list:
            self.assertEqual(call.args[0], ['/usr/bin/python3','-','--diagnose'])
            self.assertEqual(call.kwargs['cwd'], '/')
        self.assertEqual(self.output.getvalue().count('ACTIVE_HOLD_PASS'), 2)

    def test_bad_hold_blocks_before_mutation(self):
        bundle = self.bundle()
        bundle['attest'] = bundle['attest'].replace("'HOLD'", "'OPEN'")
        with self.assertRaises(ValueError):
            self.execute(bundle, [self.result()])
        self.assertNotIn('TEST_MUTATION', self.output.getvalue())

    def test_inventory_mode_never_calls_repair(self):
        bundle = self.bundle()
        bundle['mode'] = 'inventory'
        bundle['repair'] += "\nPATH = 'fixed'\n"
        with patch.object(runner, 'path_inventory', return_value={'audit':'NVM_PATH_METADATA_ONLY'}):
            proc = self.execute(bundle, [self.result()])
        self.assertEqual(proc.call_count, 1)
        self.assertNotIn('TEST_MUTATION', self.output.getvalue())
        self.assertEqual(self.output.getvalue().count('ACTIVE_HOLD_PASS'), 2)

    def test_wrong_cause_blocks_before_mutation(self):
        with self.assertRaises(ValueError):
            self.execute(self.bundle(), [self.result(True)])
        self.assertNotIn('TEST_MUTATION', self.output.getvalue())

    def test_bad_post_diagnostic_fails(self):
        with self.assertRaises(ValueError):
            self.execute(self.bundle(), [self.result(), self.result()])
        self.assertIn('TEST_MUTATION', self.output.getvalue())


if __name__ == '__main__':
    unittest.main()
