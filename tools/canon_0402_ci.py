"""Bounded 0402 DDL rehearsal on the GitHub-hosted disposable database only.

No real source, production DSN, verifier LOGIN, or positive Canon rehearsal.
The existing migration/test role model is retained. Isolation precedes all DDL.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
KEY = "0402_video_canon_firewall_unicode_boundary"
BASE_KEY = "0329_workflow_video_canon_ai_promotion"
MIGRATION_SHA = "dabae2b23d82c6ac2a6c3831ece936d81950f25474db4396f81c00d7b6fa19a5"
BEFORE_SHA = "3d2c7273c4c466024b164c016e1042cc69c7a3b76461ddb79ba95f72ff414f8c"
AFTER_SHA = "18e9b121997cbc89bac0b7cf112b2cd812a460332c957fbdbb4882bc935019f1"
DSN = "postgresql://bridge_ci_owner:bridge_ci_owner@localhost:5432/bridge_school_ci"
ADMIN_DSN = "postgresql://postgres:postgres@localhost:5432/bridge_school_ci"
OLD_RUSSIAN = "(?:^|[^[:alnum:]_])(?:рука|карты)"
NEW_RUSSIAN = "(?:^|[^[:alnum:]_А-Яа-яЁё])(?:рука|карты)"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(args, *, text=None, check=True, env=None, timeout=30):
    result = subprocess.run(args, input=text, text=True, encoding="utf-8",
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=ROOT, env=env, timeout=timeout)
    if check and result.returncode:
        # Inputs are pinned public CI fixtures; never print a DSN or environment.
        raise RuntimeError("CI_COMMAND_FAILED: " + result.stderr[-3000:])
    return result


def validate_environment(env):
    expected = {
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux", "GITHUB_REPOSITORY": "olegmed1-art/bridge-video-free",
        "GITHUB_WORKFLOW": "Bridge School Database CI", "GITHUB_JOB": "postgres-18-ci",
        "GITHUB_RUN_ATTEMPT": "1", "CI_REPOSITORY_PRIVATE": "false",
        "DATABASE_URL": DSN, "ADMIN_DATABASE_URL": ADMIN_DSN,
    }
    require(all(env.get(k) == v for k, v in expected.items()), "DISPOSABLE_CI_SCOPE_REQUIRED")
    require(env.get("GITHUB_EVENT_NAME") in {"pull_request", "push"}, "UNEXPECTED_CI_EVENT")
    require(re.fullmatch(r"[0-9a-f]{64}", env.get("CI_POSTGRES_CONTAINER_ID", "")), "SERVICE_ID_REQUIRED")
    require(re.fullmatch(r"[0-9a-f]{40}", env.get("CI_EXPECTED_HEAD_SHA", "")), "HEAD_SHA_REQUIRED")
    require(re.fullmatch(r"[0-9]+", env.get("GITHUB_RUN_ID", "")), "RUN_ID_REQUIRED")
    require(not env.get("DOCKER_HOST") and not env.get("DOCKER_CONTEXT"), "REMOTE_DOCKER_FORBIDDEN")
    require(not any(v for k, v in env.items() if k.endswith("DATABASE_URL")
                    and k not in {"DATABASE_URL", "ADMIN_DATABASE_URL"}), "EXTERNAL_DATABASE_ENV_FORBIDDEN")
    require(not env.get("CANON_SYNTHETIC_PG_ADMIN_DSN"), "POSITIVE_REHEARSAL_EXCLUDED")


def validate_topology(version, container, network, daemon):
    require(int(version.split(".")[0]) >= 28, "DOCKER_28_REQUIRED")
    require(container["Config"]["Image"] == "postgres:18", "EXPECTED_POSTGRES_IMAGE_REQUIRED")
    host = container["HostConfig"]
    require(not host.get("Privileged") and not host.get("PublishAllPorts")
            and not host.get("CapAdd") and not host.get("Binds"), "UNSAFE_CONTAINER_HOST_CONFIG")
    require(container["NetworkSettings"]["Ports"] == {
        "5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]}, "LOOPBACK_ONLY_PORT_REQUIRED")
    networks = container["NetworkSettings"]["Networks"]
    require(len(networks) == 1 and next(iter(networks)).startswith("github_network_"), "ISOLATED_JOB_NETWORK_REQUIRED")
    require(host.get("NetworkMode") == network["Name"] and network["Name"] in networks,
            "JOB_NETWORK_IDENTITY_MISMATCH")
    require(network["Driver"] == "bridge" and not network.get("EnableIPv6"), "BRIDGE_IPV4_REQUIRED")
    options = network.get("Options") or {}
    require(options.get("com.docker.network.bridge.gateway_mode_ipv4", "nat") == "nat"
            and not options.get("com.docker.network.bridge.trusted_host_interfaces")
            and not daemon.get("allow-direct-routing", False), "DIRECT_ROUTING_FORBIDDEN")
    mounts = container.get("Mounts", [])
    require(mounts and all(m["Type"] == "volume"
        and re.fullmatch(r"[0-9a-f]{64}", m.get("Name", ""))
        and m["Destination"] in {"/var/lib/postgresql", "/var/lib/postgresql/data"}
        for m in mounts), "ONLY_ANONYMOUS_POSTGRES_VOLUMES_ALLOWED")


def daemon_configuration():
    pids = command(["pgrep", "-x", "dockerd"]).stdout.split()
    require(len(pids) == 1, "ONE_LOCAL_DOCKER_DAEMON_REQUIRED")
    args = Path("/proc", pids[0], "cmdline").read_bytes().decode().split("\0")
    require(not any(arg.startswith("--allow-direct-routing") for arg in args), "DIRECT_ROUTING_FLAG_FORBIDDEN")
    config_path = Path("/etc/docker/daemon.json")
    for index, arg in enumerate(args):
        if arg.startswith("--config-file="):
            config_path = Path(arg.split("=", 1)[1])
        elif arg == "--config-file":
            config_path = Path(args[index + 1])
    return json.loads(config_path.read_text()) if config_path.exists() else {}


def port_open(host):
    try:
        with socket.create_connection((host, 5432), timeout=0.5):
            return True
    except OSError:
        return False


def identity():
    validate_environment(os.environ)
    require(command(["git", "rev-parse", "HEAD"]).stdout.strip() == os.environ["CI_EXPECTED_HEAD_SHA"],
            "CHECKOUT_HEAD_MISMATCH")
    context = json.loads(command(["docker", "context", "inspect", "default"]).stdout)[0]
    require(context["Endpoints"]["docker"]["Host"] == "unix:///var/run/docker.sock", "LOCAL_DOCKER_SOCKET_REQUIRED")
    return os.environ["CI_POSTGRES_CONTAINER_ID"]


def report_path():
    root = Path(os.environ["RUNNER_TEMP"]).resolve()
    require(root.is_dir(), "RUNNER_TEMP_REQUIRED")
    return root / "canon0402-isolation.json"


def isolate():
    container_id = identity()
    # Test-only service metadata is inspected locally; Config.Env is never logged.
    container = json.loads(command(["docker", "inspect", container_id]).stdout)[0]
    networks = container["NetworkSettings"]["Networks"]
    require(len(networks) == 1, "ONE_SERVICE_NETWORK_REQUIRED")
    network = json.loads(command(["docker", "network", "inspect", next(iter(networks))]).stdout)[0]
    version = command(["docker", "version", "--format", "{{.Server.Version}}"]).stdout.strip()
    validate_topology(version, container, network, daemon_configuration())
    require(port_open("127.0.0.1"), "LOCAL_POSTGRES_UNREACHABLE")
    addresses = json.loads(command(["ip", "-j", "-4", "addr", "show"]).stdout)
    outside = sorted({info["local"] for interface in addresses for info in interface["addr_info"]
                      if info["family"] == "inet" and not ipaddress.ip_address(info["local"]).is_loopback})
    require(outside and not any(port_open(address) for address in outside + ["::1"]), "NON_LOOPBACK_PORT_REACHABLE")
    report = {"status": "CI_0402_ISOLATION_PASS", "run_id": os.environ["GITHUB_RUN_ID"],
        "head_sha": os.environ["CI_EXPECTED_HEAD_SHA"], "container_id": container_id,
        "docker_version": version, "image_id": container["Image"], "network": network["Name"],
        "ports": container["NetworkSettings"]["Ports"], "non_loopback_probe_count": len(outside) + 1,
        "anonymous_volumes": [m["Name"] for m in container["Mounts"]],
        "production_secrets_supplied": False, "positive_rehearsal_included": False}
    report_path().write_text(json.dumps(report, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))


def snapshot():
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        require(conn.execute("SHOW server_version_num").fetchone()["server_version_num"].startswith("18"), "PG18_REQUIRED")
        result = conn.execute("""SELECT p.oid, pg_get_userbyid(p.proowner) AS owner,
            p.proacl::text AS acl, p.prosecdef, p.proleakproof, p.provolatile,
            p.proparallel, p.proisstrict, p.proconfig,
            encode(digest(p.prosrc,'sha256'),'hex') AS body_sha,
            pg_get_functiondef(p.oid) AS definition
            FROM pg_proc p WHERE p.oid='bidding.contains_forbidden_hidden_value(jsonb)'::regprocedure""").fetchone()
        result["migration"] = conn.execute("SELECT checksum FROM schema_migration WHERE migration_key=%s", (KEY,)).fetchone()
        result["source_rows"] = conn.execute("SELECT count(*) AS n FROM bidding.video_canon_source_policy").fetchone()["n"]
        result["candidate_rows"] = conn.execute("SELECT count(*) AS n FROM public.analysis_candidate WHERE candidate_type='video_school_canon_candidate'").fetchone()["n"]
        result["login_roles"] = [row["rolname"] for row in conn.execute("SELECT rolname FROM pg_roles WHERE rolcanlogin ORDER BY rolname")]
        return result


def migrate(directory=None):
    env = dict(os.environ)
    if directory:
        env["MIGRATIONS_DIR"] = str(directory)
    else:
        env.pop("MIGRATIONS_DIR", None)
    command(["bash", "database/scripts/migrate.sh"], env=env, timeout=240)


def emit_snapshot(phase, state):
    evidence = {key: value for key, value in state.items() if key != "definition"}
    evidence["definition_sha256"] = hashlib.sha256(state["definition"].encode()).hexdigest()
    print(json.dumps({"status": "CI_0402_READBACK", "phase": phase,
                      "snapshot": evidence}, sort_keys=True), flush=True)


def rejected_trial(script, marker, original):
    result = command(["psql", DSN, "-X", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose"],
                     text=script, check=False)
    # Check committed state even when psql reports an unexpected failure.
    require(snapshot() == original, "FAILED_MIGRATION_CHANGED_COMMITTED_STATE")
    sqlstate = re.search(r"(?m)^ERROR:\s+([0-9A-Z]{5}):", result.stderr)
    require(result.returncode != 0 and marker in result.stderr,
            "EXPECTED_MIGRATION_REJECTION_MISSING: expected=" + marker
            + " exit=" + str(result.returncode)
            + " sqlstate=" + (sqlstate.group(1) if sqlstate else "not_reported"))
    print(json.dumps({"status": "CI_0402_REJECTION_PASS", "marker": marker,
                      "committed_state_unchanged": True}, sort_keys=True))


def source_drift_trial(migration_sql, definition):
    require(definition.count(OLD_RUSSIAN) == 4, "RUSSIAN_GUARD_PREIMAGE_DRIFT")
    drift = definition.replace(OLD_RUSSIAN, NEW_RUSSIAN, 1).rstrip()
    # pg_get_functiondef returns a complete statement without its terminator.
    if not drift.endswith(";"):
        drift += ";"
    return migration_sql.replace("BEGIN;\n", "BEGIN;\n" + drift + "\n", 1)


def rehearse():
    import psycopg
    # Recheck actual runtime topology after dependency/contract-test steps.
    isolate()
    container_id = identity()
    proof = json.loads(report_path().read_text())
    require(proof["container_id"] == container_id and proof["head_sha"] == os.environ["CI_EXPECTED_HEAD_SHA"]
            and proof["run_id"] == os.environ["GITHUB_RUN_ID"], "ISOLATION_PROOF_MISMATCH")
    migration = ROOT / "database/migrations" / (KEY + ".sql")
    require(hashlib.sha256(migration.read_bytes()).hexdigest() == MIGRATION_SHA, "0402_SOURCE_DRIFT")
    source = migration.read_text()
    require(source.count("BEGIN;\n") == source.count("COMMIT;\n") == 1, "0402_TRANSACTION_SHAPE_DRIFT")
    with psycopg.connect(DSN) as conn:
        require(conn.execute("SELECT current_user,current_database(),to_regclass('public.schema_migration')").fetchone()
                == ("bridge_ci_owner", "bridge_school_ci", None), "CLEAN_DISPOSABLE_DATABASE_REQUIRED")
    # Preserve immutable source bytes; only omit the one migration under test.
    with tempfile.TemporaryDirectory(prefix="canon0402-baseline-", dir=os.environ["RUNNER_TEMP"]) as work:
        staged = Path(work) / "migrations"
        shutil.copytree(ROOT / "database/migrations", staged, ignore=shutil.ignore_patterns(KEY + ".sql"))
        migrate(staged)
    before = snapshot()
    require(before["body_sha"] == BEFORE_SHA and before["migration"] is None, "EXACT_0329_BASELINE_REQUIRED")
    require(before["login_roles"] == ["bridge_ci_owner", "postgres"], "UNEXPECTED_LOGIN_ROLE")
    require(before["source_rows"] == before["candidate_rows"] == 0, "CANON_FIXTURE_ALREADY_PRESENT")
    emit_snapshot("baseline", before)
    rejected_trial(source.replace("BEGIN;\n", "BEGIN;\nUPDATE schema_migration SET migration_key='ci_hidden_0329' WHERE migration_key='" + BASE_KEY + "';\n", 1),
                   "VIDEO_CANON_FIREWALL_REQUIRES_0329", before)
    rejected_trial(source_drift_trial(source, before["definition"]),
                   "VIDEO_CANON_FIREWALL_SOURCE_DRIFT", before)
    rejected_trial(source.replace("COMMIT;\n", "DO $$ BEGIN RAISE EXCEPTION 'CI_0402_FORCED_ROLLBACK'; END $$;\nCOMMIT;\n", 1),
                   "CI_0402_FORCED_ROLLBACK", before)
    migrate()
    after = snapshot()
    require(after["body_sha"] == AFTER_SHA and after["migration"] == {"checksum": MIGRATION_SHA}, "0402_READBACK_MISMATCH")
    unchanged = set(before) - {"body_sha", "definition", "migration"}
    require(all(before[k] == after[k] for k in unchanged), "0402_CHANGED_IDENTITY_ACL_OR_DATA")
    emit_snapshot("0402_committed", after)
    # Test-only committed preimage restoration + postimage restoration. Do not
    # edit migration history; production rollback needs its own forward migration.
    with psycopg.connect(DSN) as conn:
        conn.execute(before["definition"])
    restored = snapshot()
    require(restored["body_sha"] == BEFORE_SHA and all(restored[k] == after[k] for k in unchanged), "PREIMAGE_RESTORE_FAILED")
    emit_snapshot("preimage_restored", restored)
    with psycopg.connect(DSN) as conn:
        conn.execute(after["definition"])
    final = snapshot()
    require(final == after, "POSTIMAGE_RESTORE_FAILED")
    emit_snapshot("postimage_restored", final)
    print(json.dumps({"status": "CI_0402_DDL_PASS", "migration_sha256": MIGRATION_SHA,
        "before_body_sha256": BEFORE_SHA, "after_body_sha256": AFTER_SHA,
        "rollback_rejection_cases": 3, "committed_function_restore_roundtrip": True,
        "oid_acl_owner_attributes_unchanged": True, "source_and_candidate_rows": 0,
        "login_roles": after["login_roles"], "positive_rehearsal_executed": False,
        "production_migration_executed": False}, sort_keys=True))


def cleanup():
    container_id = identity()
    require(report_path().is_file(), "NO_VERIFIED_DISPOSABLE_SERVICE_FOR_EXPLICIT_CLEANUP")
    proof = json.loads(report_path().read_text())
    require(proof["container_id"] == container_id and proof["run_id"] == os.environ["GITHUB_RUN_ID"]
            and proof["head_sha"] == os.environ["CI_EXPECTED_HEAD_SHA"], "CLEANUP_IDENTITY_MISMATCH")
    current = json.loads(command(["docker", "inspect", container_id]).stdout)[0]
    require(current["Id"] == container_id and current["Image"] == proof["image_id"]
            and sorted(m.get("Name", "") for m in current.get("Mounts", [])) == sorted(proof["anonymous_volumes"])
            and all(m["Type"] == "volume" for m in current["Mounts"]), "CLEANUP_TARGET_CHANGED")
    command(["docker", "rm", "--force", "--volumes", container_id])
    require(not command(["docker", "ps", "-aq", "--no-trunc", "--filter", "id=" + container_id]).stdout.strip(),
            "SERVICE_REMAINS_AFTER_CLEANUP")
    for volume in proof["anonymous_volumes"]:
        require(re.fullmatch(r"[0-9a-f]{64}", volume), "INVALID_ANONYMOUS_VOLUME")
        require(not command(["docker", "volume", "ls", "-q", "--filter", "name=^" + volume + "$"]).stdout.strip(),
                "TEST_VOLUME_REMAINS_AFTER_CLEANUP")
    require(not port_open("127.0.0.1"), "POSTGRES_ENDPOINT_REMAINS_AFTER_CLEANUP")
    print(json.dumps({"status": "CI_0402_CLEANUP_PASS", "container_id": container_id,
                      "anonymous_volumes_removed": len(proof["anonymous_volumes"]),
                      "postgres_endpoint_closed": True}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("isolate", "rehearse", "cleanup"))
    arguments = parser.parse_args()
    globals()[arguments.action]()
