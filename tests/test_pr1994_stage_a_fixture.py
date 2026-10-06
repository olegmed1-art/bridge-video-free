"""Deterministic synthetic-fixture lifecycle regressions; no Docker/DB/network."""
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tests.pr1994_stage_a_fixture import (
    SyntheticFixture, FixtureFailure, connection_category, endpoint_from_inspect)

CID="a"*64
NID="b"*64
VOL="c"*64

def record():
    return {"Id":CID,"HostConfig":{"PortBindings":{}},
            "NetworkSettings":{"Networks":{"owned-net":{"NetworkID":NID,"IPAddress":"172.19.0.2"}}},
            "Mounts":[{"Type":"volume","Name":VOL}],
            "State":{"Running":True,"ExitCode":0,"OOMKilled":False}}

class FakeDocker:
    def __init__(self, *, daemon_error=False, retain_volume=False, fail_remove=False):
        self.containers={CID}; self.volumes={VOL}; self.networks={NID}
        self.daemon_error=daemon_error; self.retain_volume=retain_volume
        self.fail_remove=fail_remove; self.calls=[]
    def command(self,*args,docker=True):
        self.calls.append(args)
        rc=0; output=""
        if self.daemon_error: rc=1
        elif args==("ps","-aq","--no-trunc"): output="\n".join(self.containers)
        elif args==("volume","ls","-q"): output="\n".join(self.volumes)
        elif args==("network","ls","-q","--no-trunc"): output="\n".join(self.networks)
        elif args==("inspect",CID): output=json.dumps([record()])
        elif args==("rm","-f","-v",CID):
            if self.fail_remove:rc=1
            else:
                self.containers.clear()
                if not self.retain_volume:self.volumes.clear()
        elif len(args)==3 and args[:2]==("volume","rm"):
            if self.fail_remove:rc=1
            else:self.volumes.discard(args[2])
        elif args==("network","rm",NID):self.networks.clear()
        elif args==("exec",CID,"pg_isready","-U","postgres","-d","postgres"):pass
        elif args==("logs","--tail","80",CID):
            output="database system is ready to accept connections PRIVATE_PAYLOAD password=not-exported"
        else:raise AssertionError(args)
        return SimpleNamespace(returncode=rc,stdout=output,stderr="no such volume" if rc else "")

class FixtureTests(unittest.TestCase):
    def fixture(self,fake):
        f=SyntheticFixture(); self.addCleanup(lambda:shutil.rmtree(f.temp,ignore_errors=True))
        f.cid=CID;f.network_id=NID;f.network="owned-net";f.command=fake.command
        return f
    def test_internal_direct_ip_without_published_ports(self):
        self.assertEqual(endpoint_from_inspect(record(),CID,"owned-net",NID),"172.19.0.2")
    def test_foreign_identity_network_and_published_port_refused(self):
        variants=[]
        r=record();r["Id"]="d"*64;variants.append(r)
        r=record();r["NetworkSettings"]["Networks"]["other"]={};variants.append(r)
        r=record();r["NetworkSettings"]["Networks"]["owned-net"]["NetworkID"]="d"*64;variants.append(r)
        r=record();r["HostConfig"]["PortBindings"]={"5432/tcp":[{}]};variants.append(r)
        r=record();r["NetworkSettings"]["Networks"]["owned-net"]["IPAddress"]="127.0.0.1";variants.append(r)
        for r in variants:
            with self.assertRaises((FixtureFailure,ValueError)):
                endpoint_from_inspect(r,CID,"owned-net",NID)
    def test_deleted_anonymous_volume_is_proven_without_error_text(self):
        docker=FakeDocker();f=self.fixture(docker)
        with patch("sys.stdout",new_callable=io.StringIO):f.cleanup();f.cleanup()
        self.assertTrue(f.cleanup_done);self.assertFalse(f.temp.exists())
        self.assertFalse(docker.containers|docker.volumes|docker.networks)
        self.assertEqual(docker.calls.count(("rm","-f","-v",CID)),1)
    def test_owned_remaining_volume_is_removed(self):
        docker=FakeDocker(retain_volume=True);f=self.fixture(docker)
        with patch("sys.stdout",new_callable=io.StringIO):f.cleanup()
        self.assertIn(("volume","rm",VOL),docker.calls);self.assertTrue(f.cleanup_done)
    def test_cleanup_failure_continues_network_and_temp_but_never_passes(self):
        docker=FakeDocker(fail_remove=True);f=self.fixture(docker)
        with patch("sys.stdout",new_callable=io.StringIO) as out:
            with self.assertRaisesRegex(FixtureFailure,"^FIXTURE_CLEANUP_NOT_PROVEN$"):f.cleanup()
        self.assertFalse(f.cleanup_done);self.assertNotIn("CLEANUP=true",out.getvalue())
        self.assertFalse(f.temp.exists());self.assertFalse(docker.networks)
    def test_daemon_unavailable_does_not_mean_resources_absent(self):
        f=self.fixture(FakeDocker(daemon_error=True))
        with self.assertRaises(FixtureFailure):f.cleanup()
        self.assertFalse(f.cleanup_done)
    def test_diagnostics_only_fixed_fields_never_raw_payload(self):
        f=self.fixture(FakeDocker())
        f.connection_errors={"transport":3}
        with patch("sys.stdout",new_callable=io.StringIO) as out:f.diagnostics()
        s=out.getvalue();d=json.loads(s)
        self.assertTrue(d["server_ready_inside_container"])
        self.assertEqual(d["server_markers"],["ready"])
        for private in ("PRIVATE_PAYLOAD","password=","not-exported",CID,VOL):
            self.assertNotIn(private,s)
    def test_connection_errors_classified_without_outputting_original(self):
        self.assertEqual(connection_category(RuntimeError("connection refused PRIVATE")),"transport")
        self.assertEqual(connection_category(RuntimeError("certificate verify failed PRIVATE")),"tls")
        self.assertEqual(connection_category(RuntimeError("channel binding required PRIVATE")),"authentication")
    def test_ready_connection_is_closed(self):
        f=self.fixture(FakeDocker());calls=[]
        c=SimpleNamespace(close=lambda:calls.append("close"))
        f.wait_ready(lambda **kw:c,RuntimeError,{})
        self.assertEqual(calls,["close"])
    def test_readiness_failure_is_fixed_and_counted(self):
        f=self.fixture(FakeDocker())
        def fail(**kw):raise RuntimeError("connection refused PRIVATE")
        with patch("tests.pr1994_stage_a_fixture.time.monotonic",side_effect=[0,1,61]), \
             patch("tests.pr1994_stage_a_fixture.time.sleep"):
            with self.assertRaisesRegex(FixtureFailure,"^SYNTHETIC_DB_NOT_READY$"):
                f.wait_ready(fail,RuntimeError,{})
        self.assertEqual(f.connection_errors,{"transport":1})
    def test_start_guard_rejects_nonhosted_environment_before_commands(self):
        f=self.fixture(FakeDocker())
        with patch.dict("os.environ",{"GITHUB_ACTIONS":"false"},clear=True):
            with self.assertRaisesRegex(FixtureFailure,"^FIXTURE_HOSTED_CI_ONLY$"):f.start()

    def test_start_returns_direct_address_and_strict_tls_without_port_publish(self):
        f=SyntheticFixture();self.addCleanup(lambda:shutil.rmtree(f.temp,ignore_errors=True))
        calls=[]
        def command(*args,docker=True):
            calls.append((args,docker));out=""
            if args[:2]==("network","create"):out=NID
            elif args[0]=="run":out=CID
            elif args==("inspect",CID):
                r=record()
                r["NetworkSettings"]["Networks"]={f.network:{"NetworkID":NID,"IPAddress":"172.19.0.2"}}
                out=json.dumps([r])
            elif args==("network","inspect",NID):out=json.dumps([{"Id":NID,"Internal":True}])
            elif docker:raise AssertionError(args)
            return SimpleNamespace(returncode=0,stdout=out,stderr="")
        f.command=command
        with patch("tests.pr1994_stage_a_fixture.sys.platform","linux"), \
             patch.dict("os.environ",{"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted"}):
            kw=f.start()
        self.assertEqual((kw["host"],kw["hostaddr"]),("localhost","172.19.0.2"))
        self.assertEqual((kw["sslmode"],kw["channel_binding"]),("verify-full","require"))
        run=next(args for args,_ in calls if args[0]=="run")
        self.assertNotIn("-p",run);self.assertNotIn("--publish",run)
        self.assertIn("--internal",calls[1][0]);self.assertIn("set -eu;",run[-1])
    def test_docker_command_is_local_even_with_remote_environment(self):
        f=SyntheticFixture();self.addCleanup(lambda:shutil.rmtree(f.temp,ignore_errors=True))
        with patch.dict("os.environ",{"DOCKER_HOST":"tcp://private-host:2376",
                                     "DOCKER_CONTEXT":"private","DOCKER_CERT_PATH":"private-path"}), \
             patch("tests.pr1994_stage_a_fixture.subprocess.run") as run:
            run.return_value=SimpleNamespace(returncode=0,stdout="",stderr="")
            f.command("ps","-aq","--no-trunc")
        args,kw=run.call_args
        self.assertEqual(args[0][:3],["docker","--host","unix:///var/run/docker.sock"])
        self.assertNotIn("DOCKER_CONTEXT",kw["env"]);self.assertNotIn("DOCKER_HOST",kw["env"])
        self.assertNotIn("DOCKER_CERT_PATH",kw["env"])
    def test_unknown_creation_outcome_cannot_claim_cleanup_pass(self):
        f=SyntheticFixture();self.addCleanup(lambda:shutil.rmtree(f.temp,ignore_errors=True))
        f.container_creation_attempted=True
        with patch("sys.stdout",new_callable=io.StringIO) as out:
            with self.assertRaisesRegex(FixtureFailure,"^FIXTURE_CLEANUP_NOT_PROVEN$"):f.cleanup()
        self.assertNotIn("CLEANUP=true",out.getvalue());self.assertFalse(f.cleanup_done)


    def test_absent_container_without_inventory_retained_volume_refuses_pass(self):
        docker=FakeDocker();docker.containers.clear()
        f=self.fixture(docker)
        self.assertFalse(f.mount_inventory_complete);self.assertEqual(f.volumes,set())
        with patch("sys.stdout",new_callable=io.StringIO) as out:
            with self.assertRaisesRegex(FixtureFailure,"^FIXTURE_CLEANUP_NOT_PROVEN$"):f.cleanup()
        self.assertFalse(f.cleanup_done);self.assertNotIn("CLEANUP=true",out.getvalue())
        self.assertEqual(docker.volumes,{VOL})
        self.assertFalse(docker.networks);self.assertFalse(f.temp.exists())
    def test_absent_container_with_complete_inventory_cleans_retained_volume(self):
        docker=FakeDocker();docker.containers.clear()
        f=self.fixture(docker);f.remember_volumes(record())
        self.assertTrue(f.mount_inventory_complete)
        with patch("sys.stdout",new_callable=io.StringIO):f.cleanup()
        self.assertTrue(f.cleanup_done);self.assertFalse(docker.volumes)
    def test_invalid_mount_inventory_does_not_certify(self):
        f=self.fixture(FakeDocker())
        r=record();r["Mounts"]=[{"Type":"unrecognized","Name":VOL}]
        with self.assertRaises(FixtureFailure):f.remember_volumes(r)
        self.assertFalse(f.mount_inventory_complete);self.assertEqual(f.volumes,set())
    def test_failed_logs_are_unavailable_without_marker_or_payload_egress(self):
        docker=FakeDocker();f=self.fixture(docker)
        original=docker.command
        def command(*args,docker=True):
            if args==("logs","--tail","80",CID):
                return SimpleNamespace(returncode=1,stdout="ready to accept connections PRIVATE",
                                       stderr="password=not-exported")
            return original(*args,docker=docker)
        f.command=command
        with patch("sys.stdout",new_callable=io.StringIO) as out:f.diagnostics()
        raw=out.getvalue();result=json.loads(raw)
        self.assertTrue(result["diagnostic_unavailable"])
        self.assertNotIn("server_markers",result)
        self.assertNotIn("PRIVATE",raw);self.assertNotIn("password=",raw)
    def test_failed_volume_cleanup_continues_other_owned_volumes(self):
        other="d"*64;docker=FakeDocker(retain_volume=True);docker.volumes.add(other)
        f=self.fixture(docker);original=docker.command
        def command(*args,docker=True):
            if args==("inspect",CID):
                r=record();r["Mounts"].append({"Type":"volume","Name":other})
                return SimpleNamespace(returncode=0,stdout=json.dumps([r]),stderr="")
            if args==("volume","rm",VOL):
                return SimpleNamespace(returncode=1,stdout="",stderr="fixed failure")
            return original(*args,docker=docker)
        f.command=command
        with patch("sys.stdout",new_callable=io.StringIO) as out:
            with self.assertRaises(FixtureFailure):f.cleanup()
        self.assertIn(("volume","rm",other),docker.calls)
        self.assertEqual(docker.volumes,{VOL});self.assertFalse(docker.networks)
        self.assertFalse(f.temp.exists());self.assertNotIn("CLEANUP=true",out.getvalue())


if __name__=="__main__":unittest.main(verbosity=2)
