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
