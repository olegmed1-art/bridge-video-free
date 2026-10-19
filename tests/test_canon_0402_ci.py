"""Adversarial, database-free checks of the one-job isolation boundary."""
from copy import deepcopy
import unittest

from tools.canon_0402_ci import ADMIN_DSN, DSN, validate_environment, validate_topology


def environment():
    return {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux", "GITHUB_REPOSITORY": "olegmed1-art/bridge-video-free",
        "GITHUB_WORKFLOW": "Bridge School Database CI", "GITHUB_JOB": "postgres-18-ci",
        "GITHUB_RUN_ATTEMPT": "1", "CI_REPOSITORY_PRIVATE": "false",
        "DATABASE_URL": DSN, "ADMIN_DATABASE_URL": ADMIN_DSN,
        "GITHUB_EVENT_NAME": "pull_request", "CI_POSTGRES_CONTAINER_ID": "a" * 64,
        "CI_EXPECTED_HEAD_SHA": "b" * 40, "GITHUB_RUN_ID": "123"}


def topology():
    return {"Config": {"Image": "postgres:18"},
        "HostConfig": {"NetworkMode": "github_network_test", "Privileged": False,
                       "PublishAllPorts": False, "CapAdd": None, "Binds": None},
        "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]},
                            "Networks": {"github_network_test": {}}},
        "Mounts": [{"Type": "volume", "Name": "c" * 64, "Destination": "/var/lib/postgresql"}]}


def network():
    return {"Name": "github_network_test", "Driver": "bridge", "EnableIPv6": False, "Options": {}}


class IsolationTests(unittest.TestCase):
    def test_minimal_test_only_environment(self):
        validate_environment(environment())

    def test_rejects_scope_expansion(self):
        mutations = {"RUNNER_ENVIRONMENT": "self-hosted", "CI_REPOSITORY_PRIVATE": "true",
            "GITHUB_REPOSITORY": "other/repo", "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "DATABASE_URL": "postgresql://prod.invalid/db",
            "ADMIN_DATABASE_URL": "postgresql://prod.invalid/db", "DOCKER_HOST": "tcp://remote:2375",
            "DOCKER_CONTEXT": "production", "BRIDGE_WORKER_DATABASE_URL": "postgresql://prod.invalid/db",
            "CANON_SYNTHETIC_PG_ADMIN_DSN": "postgresql://local/postgres",
            "CI_POSTGRES_CONTAINER_ID": "", "CI_EXPECTED_HEAD_SHA": "main"}
        for key, value in mutations.items():
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_environment(environment() | {key: value})

    def test_one_private_bridge_and_loopback(self):
        validate_topology("28.0.0", topology(), network(), {})

    def test_rejects_every_other_publish_address_and_extra_port(self):
        for address in ("0.0.0.0", "::", "::1", "127.0.0.2", "192.0.2.1", ""):
            value = topology(); value["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostIp"] = address
            with self.subTest(address=address), self.assertRaises(RuntimeError):
                validate_topology("28.0.0", value, network(), {})
        value = topology(); value["NetworkSettings"]["Ports"]["80/tcp"] = []
        with self.assertRaises(RuntimeError): validate_topology("28.0.0", value, network(), {})

    def test_rejects_privileged_host_and_extra_capability(self):
        for key, value in (("Privileged", True), ("PublishAllPorts", True),
                           ("CapAdd", ["NET_ADMIN"]), ("Binds", ["/host:/data"]), ("NetworkMode", "host")):
            item = topology(); item["HostConfig"][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_topology("28.0.0", item, network(), {})

    def test_rejects_direct_routing_ipv6_and_old_docker(self):
        variants = [("27.5.1", network(), {}), ("28.0.0", network(), {"allow-direct-routing": True}),
                    ("28.0.0", network() | {"Driver": "host"}, {}),
                    ("28.0.0", network() | {"EnableIPv6": True}, {})]
        for option in ("routed", "nat-unprotected", "isolated"):
            variants.append(("28.0.0", network() | {"Options": {"com.docker.network.bridge.gateway_mode_ipv4": option}}, {}))
        variants.append(("28.0.0", network() | {"Options": {"com.docker.network.bridge.trusted_host_interfaces": "eth0"}}, {}))
        for version, net, daemon in variants:
            with self.subTest(version=version, network=net, daemon=daemon), self.assertRaises(RuntimeError):
                validate_topology(version, topology(), net, daemon)

    def test_rejects_persistent_or_foreign_volume(self):
        for mutation in ({"Type": "bind"}, {"Name": "persistent-school-data"}, {"Destination": "/prod"}):
            value = topology(); value["Mounts"][0].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                validate_topology("28.0.0", value, network(), {})

    def test_inputs_unchanged(self):
        value, net = topology(), network()
        previous = deepcopy((value, net))
        validate_topology("28.0.0", value, net, {})
        self.assertEqual((value, net), previous)


if __name__ == "__main__":
    unittest.main()
