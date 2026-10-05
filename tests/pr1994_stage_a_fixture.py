"""Lifecycle for this synthetic PostgreSQL CI fixture only; never production."""
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

IMAGE = "postgres@sha256:9e73daeb439141c2b11eea2463f5f1a3b269fd90d897b41cddb7cb440f21aa5d"
ID = re.compile(r"[0-9a-f]{64}")

class FixtureFailure(RuntimeError):
    pass

def checked(ok, code):
    if not ok:
        raise FixtureFailure(code)

def connection_category(exc):
    text = str(exc).lower()
    for category, needles in (
        ("tls", ("certificate", "ssl error", "sslmode")),
        ("authentication", ("password authentication", "channel binding", "scram")),
        ("transport", ("connection refused", "timeout", "network is unreachable")),
        ("configuration", ("unrecognized configuration", "invalid value for parameter"))):
        if any(n in text for n in needles):
            return category
    return "other"

def endpoint_from_inspect(record, cid, network, network_id):
    checked(record["Id"] == cid, "FIXTURE_ID_MISMATCH")
    checked(not record["HostConfig"].get("PortBindings"), "FIXTURE_PORT_PUBLISHED")
    nets = record["NetworkSettings"]["Networks"]
    checked(set(nets) == {network} and nets[network]["NetworkID"] == network_id,
            "FIXTURE_NETWORK_MISMATCH")
    addr = ipaddress.IPv4Address(nets[network]["IPAddress"])
    checked(addr.is_private and not addr.is_loopback and not addr.is_unspecified,
            "FIXTURE_ADDRESS_INVALID")
    return str(addr)

class SyntheticFixture:
    def __init__(self):
        self.temp = Path(tempfile.mkdtemp(prefix="pr1994-stage-a-"))
        self.name = "stage-a-" + uuid.uuid4().hex
        self.network = self.name + "-net"
        self.cid = None
        self.network_id = None
        self.volumes = set()
        self.mount_inventory_complete = False
        self.cleanup_done = False
        self.network_creation_attempted = False
        self.container_creation_attempted = False
        self.connection_errors = {}

    def command(self, *args, docker=True):
        argv = ["docker", "--host", "unix:///var/run/docker.sock", *args] if docker else list(args)
        # Ignore remote Docker contexts; never print environment or raw command errors.
        env = {k:v for k,v in os.environ.items()
               if k not in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")}
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=90, env=env)
        except (OSError, subprocess.TimeoutExpired):
            raise FixtureFailure("FIXTURE_COMMAND_UNAVAILABLE") from None

    def run(self, *args, docker=True):
        result = self.command(*args, docker=docker)
        checked(result.returncode == 0, "FIXTURE_COMMAND_FAILED")
        return result.stdout

    def inspect(self):
        record = json.loads(self.run("inspect", self.cid))[0]
        checked(record["Id"] == self.cid, "FIXTURE_ID_MISMATCH")
        return record

    def remember_volumes(self, record):
        checked(record["Id"] == self.cid, "FIXTURE_ID_MISMATCH")
        mounts = record["Mounts"]
        checked(type(mounts) is list and all(type(m) is dict and
                m.get("Type") in ("volume", "bind", "tmpfs") for m in mounts),
                "FIXTURE_MOUNT_INVENTORY_INVALID")
        names = {m["Name"] for m in mounts if m["Type"] == "volume"}
        checked(all(ID.fullmatch(n) for n in names), "FIXTURE_NONANONYMOUS_VOLUME")
        self.volumes.update(names)
        self.mount_inventory_complete = True

    def start(self):
        checked(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
                and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
                "FIXTURE_HOSTED_CI_ONLY")
        self.run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                 "-keyout", str(self.temp/"server.key"), "-out", str(self.temp/"server.crt"),
                 docker=False)
        self.network_creation_attempted = True
        self.network_id = self.run("network", "create", "--internal", self.network).strip()
        checked(ID.fullmatch(self.network_id), "FIXTURE_NETWORK_ID_INVALID")
        self.container_creation_attempted = True
        self.cid = self.run("run", "-d", "--name", self.name, "--network", self.network,
            "-e", "POSTGRES_PASSWORD=synthetic-only-password",
            "-v", str(self.temp)+":/tls", "--entrypoint", "sh", IMAGE,
            "-c", "set -eu; chmod 755 /tls; chown postgres:postgres /tls/server.key; "
                  "chmod 600 /tls/server.key; exec docker-entrypoint.sh postgres -c ssl=on "
                  "-c ssl_cert_file=/tls/server.crt -c ssl_key_file=/tls/server.key").strip()
        checked(ID.fullmatch(self.cid), "FIXTURE_CONTAINER_ID_INVALID")
        record = self.inspect()
        self.remember_volumes(record)
        # Docker documents direct host access to an internal bridge container IP.
        # Keep host=localhost for TLS certificate name and client identity checks.
        address = endpoint_from_inspect(record, self.cid, self.network, self.network_id)
        network = json.loads(self.run("network", "inspect", self.network_id))[0]
        checked(network["Id"] == self.network_id and network["Internal"] is True,
                "FIXTURE_NETWORK_NOT_INTERNAL")
        return dict(host="localhost", hostaddr=address, port=5432, dbname="postgres",
            user="postgres", password="synthetic-only-password", sslmode="verify-full",
            sslrootcert=str(self.temp/"server.crt"), channel_binding="require",
            connect_timeout=2, options="-c neon.branch_id=fixture-branch")

    def wait_ready(self, connect, operational_error, kwargs):
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            try:
                conn = connect(**kwargs, autocommit=True)
                conn.close()
                return
            except operational_error as exc:
                category = connection_category(exc)
                self.connection_errors[category] = self.connection_errors.get(category, 0)+1
                time.sleep(.2)
        raise FixtureFailure("SYNTHETIC_DB_NOT_READY") from None

    def diagnostics(self):
        report = {"fixture_diagnostics": True, "connection_errors": self.connection_errors,
                  "mount_inventory_complete": self.mount_inventory_complete}
        try:
            if self.cid and ID.fullmatch(self.cid):
                record = self.inspect()
                state = record["State"]
                report["running"] = bool(state["Running"])
                report["exit_code"] = int(state["ExitCode"])
                report["oom_killed"] = bool(state["OOMKilled"])
                ready = self.command("exec", self.cid, "pg_isready", "-U", "postgres", "-d", "postgres")
                report["server_ready_inside_container"] = ready.returncode == 0
                logs = self.command("logs", "--tail", "80", self.cid)
                checked(logs.returncode == 0, "FIXTURE_LOGS_UNAVAILABLE")
                # Only fixed categories leave the process, never logs/DSN/key/env/row data.
                tail = (logs.stdout+logs.stderr)[-16384:].lower()
                report["server_markers"] = [label for label,needle in (
                    ("ready", "ready to accept connections"),
                    ("tls_key_permissions", "private key file"),
                    ("tls_certificate", "could not load server certificate"),
                    ("initdb_error", "initdb: error"),
                    ("permission_error", "permission denied")) if needle in tail]
        except Exception:
            report["diagnostic_unavailable"] = True
        print(json.dumps(report, sort_keys=True))

    def listed(self, *args):
        return set(self.run(*args).splitlines())

    def cleanup(self):
        if self.cleanup_done:
            return
        errors = []
        if (self.network_creation_attempted and not self.network_id) or (
                self.container_creation_attempted and not self.cid):
            errors.append("FIXTURE_CREATION_OUTCOME_UNKNOWN")
        def attempt(action):
            try:
                action()
            except Exception:
                errors.append("FIXTURE_CLEANUP_STEP_FAILED")
        def container():
            if not self.cid:
                return
            checked(ID.fullmatch(self.cid), "FIXTURE_CONTAINER_ID_INVALID")
            if self.cid in self.listed("ps", "-aq", "--no-trunc"):
                self.remember_volumes(self.inspect())
                self.run("rm", "-f", "-v", self.cid)
            checked(self.mount_inventory_complete, "FIXTURE_MOUNT_INVENTORY_UNAVAILABLE")
            checked(self.cid not in self.listed("ps", "-aq", "--no-trunc"),
                    "CONTAINER_CLEANUP_NOT_PROVEN")
        def volume(name):
            checked(ID.fullmatch(name), "FIXTURE_VOLUME_ID_INVALID")
            if name in self.listed("volume", "ls", "-q"):
                self.run("volume", "rm", name)
            checked(name not in self.listed("volume", "ls", "-q"),
                    "VOLUME_CLEANUP_NOT_PROVEN")
        def volumes():
            for name in sorted(self.volumes):
                attempt(lambda name=name:volume(name))
        def network():
            if not self.network_id:
                return
            checked(ID.fullmatch(self.network_id), "FIXTURE_NETWORK_ID_INVALID")
            if self.network_id in self.listed("network", "ls", "-q", "--no-trunc"):
                self.run("network", "rm", self.network_id)
            checked(self.network_id not in self.listed("network", "ls", "-q", "--no-trunc"),
                    "NETWORK_CLEANUP_NOT_PROVEN")
        # Continue independent cleanup after failure; do not mask startup diagnostics.
        for action in (container, volumes, network, lambda:shutil.rmtree(self.temp)):
            attempt(action)
        checked(not errors, "FIXTURE_CLEANUP_NOT_PROVEN")
        self.cleanup_done = True
        print("STAGE_A_FIXTURE_CLEANUP=true")
