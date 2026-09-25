"""Fault injection for one-target install and pinned package integrity."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import tempfile

from ops import oracle_light_native_cli_stage_once as stage


class StageTests(unittest.TestCase):
    def test_active_admission_blocks_before_package_fetch(self):
        with patch.object(stage.os,'geteuid',return_value=1000), patch.object(
                stage.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=1000)), patch.object(
                stage.os,'uname',return_value=SimpleNamespace(nodename='autopilot-lite-vnic',machine='aarch64')), patch.object(
                stage.subprocess,'run',return_value=SimpleNamespace(
                    stdout='ActiveState=active\nSubState=running\nEnvironment=AUTOPILOT_ADMISSION_MODE=ACTIVE\n')) as run:
            with self.assertRaisesRegex(ValueError,'NOT_ACTIVE_HOLD'):
                stage.require_host()
        self.assertEqual(run.call_count,1)

    def test_unsafe_binary_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            nvm=root/'versions/node/v22/bin'
            nvm.mkdir(parents=True)
            outside=root/'outside'
            outside.write_text('untrusted')
            (nvm/'npm').symlink_to(outside)
            with patch.object(stage,'NODE',nvm/'node'):
                with self.assertRaisesRegex(ValueError,'RUNTIME_OUTSIDE_NVM'):
                    stage.check_binary(nvm/'npm',os.geteuid())

    def test_failed_fetch_does_not_create_target_or_leave_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            parent=home/'.local/share'
            target=parent/'slavik-codex'
            with patch.multiple(stage,HOME=home,PARENT=parent,TARGET=target), patch.object(
                    stage,'require_host'), patch.object(stage,'check_binary'), patch.object(
                    stage.subprocess,'run',return_value=SimpleNamespace(returncode=1)) as run:
                with self.assertRaisesRegex(ValueError,'PACKAGE_FETCH_FAILED'):
                    stage.install()
            self.assertFalse(target.exists())
            self.assertEqual(list(parent.iterdir()),[])
            self.assertEqual(run.call_count,1)
            self.assertIn('--ignore-scripts',run.call_args.args[0])
            self.assertNotIn('OPENAI_API_KEY',run.call_args.kwargs['env'])

    def test_rejects_unpinned_lock_and_preserves_target_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            install=Path(temp)
            (install/'package-lock.json').write_text(json.dumps({'packages':{
                name:{'version':version,'integrity':'tampered',
                      'resolved':f'https://registry.npmjs.org/@openai/codex/-/codex-{version}.tgz'}
                for name,(version,_) in stage.INTEGRITIES.items()}}))
            with self.assertRaisesRegex(ValueError,'PACKAGE_INTEGRITY_INVALID'):
                stage.verify_package(install)

    def test_existing_target_blocks_without_fetch(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            parent=home/'.local/share'
            target=parent/'slavik-codex'
            target.mkdir(parents=True)
            with patch.multiple(stage,HOME=home,PARENT=parent,TARGET=target), patch.object(
                    stage,'require_host'), patch.object(stage.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'TARGET_ALREADY_EXISTS'):
                    stage.install()
            run.assert_not_called()

    def test_valid_arm64_package_lock_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            install=Path(temp)
            openai=install/'node_modules/@openai'
            (openai/'codex').mkdir(parents=True)
            (openai/'codex-linux-arm64/vendor/aarch64-unknown-linux-musl/bin').mkdir(parents=True)
            (install/'node_modules/.bin').mkdir()
            (install/'package-lock.json').write_text(json.dumps({'packages':{
                name:{'version':version,'integrity':integrity,
                      'resolved':f'https://registry.npmjs.org/@openai/codex/-/codex-{version}.tgz'}
                for name,(version,integrity) in stage.INTEGRITIES.items()}}))
            (openai/'codex/package.json').write_text(json.dumps({
                'name':'@openai/codex','version':stage.VERSION,'bin':{'codex':'bin/codex.js'}}))
            (install/'node_modules/.bin/codex').symlink_to('../@openai/codex/bin/codex.js')
            native=openai/'codex-linux-arm64/vendor/aarch64-unknown-linux-musl/bin/codex'
            native.write_bytes(b'fixture')
            native.chmod(0o755)
            stage.verify_package(install)

    def test_atomic_rename_never_replaces_new_target(self):
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp)
            source=parent/'stage'
            target=parent/'installed'
            source.mkdir()
            target.mkdir()
            marker=target/'marker'
            marker.write_text('preserve')
            with self.assertRaises(FileExistsError):
                stage.rename_no_replace(source,target)
            self.assertEqual(marker.read_text(),'preserve')
            self.assertTrue(source.is_dir())

    def test_atomic_rename_installs_when_target_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp)
            source=parent/'stage'
            target=parent/'installed'
            source.mkdir()
            stage.rename_no_replace(source,target)
            self.assertFalse(source.exists())
            self.assertTrue(target.is_dir())

    def test_diagnose_disk_guard_is_read_only(self):
        with patch.object(stage,'require_host',side_effect=ValueError('DISK_CAPACITY_LOW')), patch.object(
                stage,'check_binary') as binary, patch.object(stage.subprocess,'run') as run:
            result=stage.diagnose()
        self.assertEqual(result,{'audit':'NATIVE_CLI_STAGE_DIAGNOSTIC',
                                  'guard':'DISK_CAPACITY_LOW','installation_action':'NONE'})
        binary.assert_not_called()
        run.assert_not_called()

    def test_entry_diagnose_never_calls_install(self):
        with patch.object(stage,'diagnose',return_value={'audit':'READ_ONLY'}) as diagnostic, patch.object(
                stage,'install') as install:
            self.assertEqual(stage.entry(['--diagnose']),{'audit':'READ_ONLY'})
        diagnostic.assert_called_once_with()
        install.assert_not_called()

    def test_diagnose_classifies_first_unsafe_runtime_parent_without_path(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            bin_dir=home/'.nvm/versions/node/v22.23.2/bin'
            bin_dir.mkdir(parents=True)
            for name in ('node','npm'):
                runtime=bin_dir/name
                runtime.write_bytes(b'fixture')
                runtime.chmod(0o755)
            bin_dir.chmod(0o775)
            with patch.multiple(stage,HOME=home,PARENT=home/'.local/share',
                                TARGET=home/'.local/share/slavik-codex',
                                NODE=bin_dir/'node',NPM=bin_dir/'npm'), patch.object(
                                    stage,'require_host'):
                report=stage.diagnose()
        self.assertEqual(report['node'],'UNSAFE_INSTALL_PARENT')
        self.assertEqual(report['node_parent_failure'],
                         {'chain':'source','index':0,'reason':'GROUP_WRITABLE'})
        self.assertEqual(report['npm_parent_failure'],report['node_parent_failure'])
        self.assertNotIn(str(home),str(report))

    def test_group_audit_reports_membership_without_account_names(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            bin_dir=home/'.nvm/versions/node/v22.23.2/bin'
            bin_dir.mkdir(parents=True)
            for name in ('node','npm'):
                runtime=bin_dir/name
                runtime.write_bytes(b'fixture')
                runtime.chmod(0o755)
            version=bin_dir.parent
            version.chmod(0o775)
            account=SimpleNamespace(pw_name='ubuntu',pw_uid=os.geteuid(),pw_gid=version.stat().st_gid)
            with patch.multiple(stage,HOME=home,PARENT=home/'.local/share',
                                TARGET=home/'.local/share/slavik-codex',
                                NODE=bin_dir/'node',NPM=bin_dir/'npm'), patch.object(
                                    stage,'require_host'), patch.object(
                                        stage.pwd,'getpwnam',return_value=account), patch.object(
                                            stage.pwd,'getpwall',return_value=[account]), patch.object(
                                                stage.grp,'getgrgid',return_value=SimpleNamespace(gr_mem=['ubuntu'])):
                report=stage.diagnose()
        self.assertEqual(report['version_group_audit'],
                         {'version_owner_ubuntu':True,'version_group_ubuntu_primary':True,
                          'group_has_other_accounts':False,
                          'access_acl':'ABSENT','default_acl':'ABSENT'})

    def test_diagnose_missing_install_parent_does_not_create_it(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            parent=home/'.local/share'
            target=parent/'slavik-codex'
            with patch.multiple(stage,HOME=home,PARENT=parent,TARGET=target), patch.object(
                    stage,'require_host'), patch.object(stage,'check_binary'), patch.object(
                    stage.subprocess,'run') as run:
                result=stage.diagnose()
            self.assertEqual(result['parents'],{'home':'SAFE','local':'MISSING','share':'MISSING'})
            self.assertEqual(result['target'],'ABSENT')
            self.assertEqual(result['node'],'SAFE')
            self.assertEqual(result['npm'],'SAFE')
            self.assertFalse((home/'.local').exists())
            run.assert_not_called()


if __name__=='__main__':
    unittest.main()
