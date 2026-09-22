import sys
from pathlib import Path
import unittest
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


if __name__=='__main__':
    unittest.main()
