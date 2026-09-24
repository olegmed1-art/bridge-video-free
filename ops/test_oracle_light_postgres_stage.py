import sys
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
import oracle_light_postgres_stage as target


class StageSafety(unittest.TestCase):
    def fixture(self):
        return dict(Image=target.IMAGE,Config={'Labels':{'managed_by':target.LABEL},'Cmd':['postgres','-c','config_file=/run/bridge-config/postgresql.conf'],'Env':['POSTGRES_PASSWORD_FILE=/run/secrets/admin-password','POSTGRES_INITDB_ARGS=--data-checksums --locale-provider=builtin --locale=C.UTF-8']},
                    HostConfig={'NetworkMode':'host','PortBindings':{},'NanoCpus':1000000000,'PidsLimit':128,'LogConfig':{'Type':'json-file','Config':{'max-size':'10m','max-file':'3'}},
                                'Memory':2*1024**3,'Privileged':False,'RestartPolicy':{'Name':'no'}},
                    Mounts=[dict(Destination=dest,Source=src,RW=rw) for dest,src,rw in (
                        ('/var/lib/postgresql',str(target.ROOT/'postgresql'),True),
                        ('/run/bridge-config',str(target.CONF/'config'),False),
                        ('/run/bridge-tls',str(target.CONF/'tls'),False),
                        ('/run/secrets/admin-password',str(target.CONF/'admin-password'),False))])

    def test_exact_container_accepted(self):
        target.verify_container(self.fixture())

    def test_public_exposure_rejected(self):
        item=self.fixture()
        item['HostConfig']['PortBindings']={'5432/tcp':[{'HostIp':'0.0.0.0','HostPort':'5432'}]}
        with self.assertRaises(AssertionError):
            target.verify_container(item)

    def test_runtime_drift_rejected(self):
        for key,value in [('NanoCpus',0),('PidsLimit',-1),('LogConfig',{}),('NetworkMode','bridge')]:
            with self.subTest(key=key):
                item=self.fixture()
                item['HostConfig'][key]=value
                with self.assertRaises(AssertionError):
                    target.verify_container(item)
        for key,value in [('Cmd',['postgres']),('Env',[])]:
            with self.subTest(key=key):
                item=self.fixture()
                item['Config'][key]=value
                with self.assertRaises((AssertionError,KeyError)):
                    target.verify_container(item)

    def test_wrong_disk_rejected(self):
        item=self.fixture()
        item['Mounts'][0]['Source']='/var/lib/other'
        with self.assertRaises(AssertionError):
            target.verify_container(item)

    def test_docker_autostart_without_mount_guard_rejected(self):
        item=self.fixture()
        item['HostConfig']['RestartPolicy']['Name']='always'
        with self.assertRaises(AssertionError):
            target.verify_container(item)

    def test_writable_secret_rejected(self):
        item=self.fixture()
        item['Mounts'][-1]['RW']=True
        with self.assertRaises(AssertionError):
            target.verify_container(item)

    def test_foreign_image_rejected(self):
        item=self.fixture()
        item['Image']='foreign'
        with self.assertRaises(AssertionError):
            target.verify_container(item)


class PrivateFileCreation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_private_at_creation_and_final_permissions_before_write(self):
        path = self.root / 'password'
        original_fdopen = os.fdopen
        observations = []

        class ObservedFile:
            def __init__(self, file):
                self.file = file

            def __enter__(self):
                self.file.__enter__()
                return self

            def __exit__(self, *args):
                return self.file.__exit__(*args)

            def fileno(self):
                return self.file.fileno()

            def write(self, content):
                observations.append(('write', stat.S_IMODE(os.fstat(self.fileno()).st_mode)))
                return self.file.write(content)

        def observe_open(fd, *args, **kwargs):
            observations.append(('create', stat.S_IMODE(os.fstat(fd).st_mode)))
            return ObservedFile(original_fdopen(fd, *args, **kwargs))

        def observe_owner(fd, uid, gid):
            observations.append(('owner', uid, gid))

        previous = os.umask(0)
        try:
            with patch.object(target.os, 'fdopen', side_effect=observe_open), \
                 patch.object(target.os, 'fchown', side_effect=observe_owner):
                target.write_new(path, 'test-only-password\n', 0o400, 999)
        finally:
            os.umask(previous)
        self.assertEqual(observations, [('create', 0o600), ('owner', 999, 999), ('write', 0o400)])
        self.assertEqual(path.read_text(), 'test-only-password\n')

    def test_existing_file_and_symlink_are_not_modified(self):
        existing = self.root / 'existing'
        existing.write_text('preserve')
        link = self.root / 'link'
        link.symlink_to(existing)
        dangling = self.root / 'dangling'
        dangling.symlink_to(self.root / 'absent')
        for path in (existing, link, dangling):
            with self.subTest(path=path.name), self.assertRaises(FileExistsError):
                target.write_new(path, 'must-not-write')
        self.assertEqual(existing.read_text(), 'preserve')
        self.assertFalse((self.root / 'absent').exists())

    def test_path_replacement_cannot_redirect_permissions_or_content(self):
        path = self.root / 'new'
        original = self.root / 'original'

        def replace_path(fd, uid, gid):
            path.rename(original)
            path.write_text('replacement')
            path.chmod(0o644)

        with patch.object(target.os, 'fchown', side_effect=replace_path):
            target.write_new(path, 'original-content', 0o400, 999)
        self.assertEqual(path.read_text(), 'replacement')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(original.read_text(), 'original-content')
        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o400)

    def test_owner_failure_writes_no_content_and_closes_descriptor(self):
        path = self.root / 'failed'
        descriptors = []

        def deny_owner(fd, uid, gid):
            descriptors.append(fd)
            raise PermissionError('test owner failure')

        with patch.object(target.os, 'fchown', side_effect=deny_owner):
            with self.assertRaises(PermissionError):
                target.write_new(path, 'must-not-write', 0o400, 999)
        self.assertEqual(path.read_bytes(), b'')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])


if __name__=='__main__':
    unittest.main()
