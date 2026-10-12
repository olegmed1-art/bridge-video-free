"""One native call in an isolated child. No retries, address rewriting or SQL."""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import platform
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from observer import AttemptObserver, hints
from protocol import RESULT_SCHEMA, atomic_result, read_result, valid_runtime, valid_offline

SOURCE_SHA256 = "5dfacd2ef35f748c5a60c07c8bf5d6e6a2fa25438ee1794591b4a4e4e70afd7e"
DB_SHA256 = "1c391cd0bfde393994db6b3b2f71af7843f5ed48b79e5476eced02113338234a"
CONNECT_KWARGS = {"connect_timeout": 10, "application_name": "incident-1911-readonly-probe",
                  "options": "-c default_transaction_read_only=on -c statement_timeout=5000"}
REPO = "olegmed1-art/bridge-video-free"
PREFLIGHT_BRANCH = "codex-incident-1911-psycopg-preflight-20261012"
LIVE_BRANCH = "codex-incident-1911-psycopg-live-20261012"
WORKFLOW = "incident-psycopg-two-stage.yml"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime(root=ROOT):
    # Called only by explicitly selected Linux CI modes, NEVER on module import.
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        raise ValueError("runtime_platform")
    site = root / "runtime"
    if sha256(site / "psycopg/connection.py") != SOURCE_SHA256:
        raise ValueError("runtime_source")
    if any(k.startswith("PG") or k == "PSYCOPG_IMPL" for k in os.environ):
        raise ValueError("runtime_environment")
    sys.path.insert(0, str(site))
    import psycopg
    if (Path(psycopg.__file__).resolve() != (site / "psycopg/__init__.py").resolve() or
            Path(psycopg.Connection.connect.__func__.__code__.co_filename).resolve() != (site / "psycopg/connection.py").resolve()):
        raise ValueError("runtime_origin")
    report = {"psycopg": psycopg.__version__, "binary": importlib.metadata.version("psycopg-binary"),
              "typing_extensions": importlib.metadata.version("typing-extensions"),
              "implementation": psycopg.pq.__impl__, "python": list(sys.version_info[:3]),
              "libpq": psycopg.pq.version(), "build_libpq": psycopg.pq.__build_version__}
    if not valid_runtime(report) or report is None:
        raise ValueError("runtime_versions")
    return psycopg, report


def base_result(observer):
    return {"schema": RESULT_SCHEMA, "status": "initializing", "connect_calls": 0,
            "public_call_returned": False, "close_attempted": False, "close_succeeded": False,
            "publication_failed": False, "failure_hints": [], "runtime": None,
            "observations": observer.snapshot()}


def message_hints(exc):
    try:
        return hints(str(exc))
    except Exception:
        return ["unknown"]


def connect_once(client, dsn, state, publish):
    """Single lexical connect site; caller supplies safe publication only."""
    connection = None
    state["status"] = "connecting"
    state["connect_calls"] = 1
    publish()
    try:
        connection = client.connect(dsn, **CONNECT_KWARGS)
        state["public_call_returned"] = True
        state["status"] = "call_returned"
        publish()
    except Exception as exc:
        state["status"] = "connect_failed"
        state["failure_hints"] = message_hints(exc)
        publish()
    finally:
        if connection is not None:
            state["close_attempted"] = True
            publish()
            try:
                connection.close()
                state["close_succeeded"] = True
            except Exception as exc:
                state["status"] = "cleanup_failed"
                state["failure_hints"] = message_hints(exc)
            publish()


def configuration(client, root=ROOT):
    if os.environ.get("VERCEL_ENV") != "production" or any(k.startswith("PG") for k in os.environ):
        raise ValueError("configuration_environment")
    path = root / "db_contract.py"
    if sha256(path) != DB_SHA256:
        raise ValueError("configuration_source")
    spec = importlib.util.spec_from_file_location("incident_verified_db_contract", path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    dsn = config.database_dsn()
    raw_host = (urlsplit(config._unquote(os.environ.get("BRIDGE_APP_DATABASE_URL", ""))).hostname or "").lower()
    params = client.conninfo.conninfo_to_dict(dsn)
    allowed = {"user", "password", "dbname", "host", "port", "sslmode", "channel_binding"}
    valid = (params.get("user") == config.EXPECTED_PRINCIPAL and
             params.get("dbname") == config.EXPECTED_DATABASE and
             params.get("host") == config.PRODUCTION_HOST and
             params.get("port", "5432") == "5432" and
             params.get("sslmode") in {"require", "verify-full"} and
             params.get("channel_binding") == "require" and bool(params.get("password")) and
             not (params.keys() - allowed) and raw_host in {config.PRODUCTION_HOST, config.PRODUCTION_DIRECT_HOST})
    if not valid:
        raise ValueError("effective_parameters")
    return dsn


def child(output):
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    observer = AttemptObserver()
    state = base_result(observer)

    def publish():
        state["observations"] = observer.snapshot()
        try:
            atomic_result(output, state)
        except Exception:
            state["publication_failed"] = True

    observer._publish = publish
    publish()
    try:
        client, state["runtime"] = runtime()
    except Exception:
        state["status"] = "runtime_rejected"
        publish()
        return 1
    try:
        dsn = configuration(client)
    except Exception:
        state["status"] = "configuration_rejected"
        publish()
        return 1
    logger = logging.getLogger("psycopg")
    if (logging.getLogRecordFactory() is not logging.LogRecord or logger.handlers or logger.filters or
            logger.disabled or logging.root.handlers or logging.root.manager.disable >= logging.DEBUG):
        state["status"] = "runtime_rejected"
        publish()
        return 1
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(observer)
    try:
        connect_once(client, dsn, state, publish)
    finally:
        logger.removeHandler(observer)
    return 0 if state["public_call_returned"] and state["close_succeeded"] and not state["publication_failed"] else 1


def supervise(command, output, environment, popen=subprocess.Popen):
    """External 20s + 5s boundary, with last validated atomic snapshot on expiry."""
    process = popen(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    outcome = "exited"
    try:
        exit_code = process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        outcome = "terminated_at_20s"
        process.terminate()
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            outcome = "killed_after_grace"
            process.kill()
            exit_code = process.wait()
    observation = read_result(output)
    complete = (outcome == "exited" and observation is not None and
                observation["status"] in {"call_returned", "connect_failed", "cleanup_failed", "configuration_rejected", "runtime_rejected"} and
                not observation["publication_failed"] and not observation["observations"]["retention_overflow"] and
                not observation["observations"]["contract_errors"] and not observation["observations"]["pending_observation"])
    if complete:
        returned = observation["status"] == "call_returned"
        complete = (type(exit_code) is int and exit_code == (0 if returned else 1) and
                    (not returned or (observation["public_call_returned"] and observation["close_succeeded"])))
    return {"schema": "incident-1911-supervisor-v2", "process_outcome": outcome,
            "diagnostic_complete": complete, "snapshot": observation}


def context_allowed(env, stage):
    branch, number = (PREFLIGHT_BRANCH, "1") if stage == "preflight" else (LIVE_BRANCH, "2")
    sha = env.get("GITHUB_SHA", "")
    return (stage in {"preflight", "live"} and len(sha) == 40 and all(c in "0123456789abcdef" for c in sha) and
            env.get("GITHUB_REPOSITORY") == REPO and env.get("GITHUB_EVENT_NAME") == "create" and
            env.get("GITHUB_REF") == "refs/heads/" + branch and env.get("GITHUB_REF_NAME") == branch and
            env.get("GITHUB_RUN_ATTEMPT") == "1" and env.get("GITHUB_RUN_NUMBER") == number and
            env.get("GITHUB_WORKFLOW_SHA") == sha)


def live(root=ROOT):
    if not context_allowed(os.environ, "live"):
        return {"status": "live_context_rejected"}, 1
    try:
        sha = os.environ["GITHUB_SHA"]
        gate = json.loads((root / "stage-gate.json").read_text())
        offline = json.loads((root / "offline-result.json").read_text())
        if gate != {"schema": "incident-1911-gate-v2", "head_sha": sha, "preflight_passed": True}:
            raise ValueError("gate")
        if not valid_offline(offline, sha):
            raise ValueError("offline")
        with (root / "live-invocation.claim").open("x") as claim:
            claim.write("one invocation claimed\n")
        output = root / "safe-result.json"
        if output.exists():
            raise ValueError("old_output")
        environment = dict(os.environ)
        environment["VERCEL_ENV"] = "production"
        result = supervise([sys.executable, "-I", "-S", str(root / "probe.py"), "child", str(output)], output, environment)
        return result, 0 if result["diagnostic_complete"] else 1
    except Exception:
        return {"status": "live_wrapper_failed"}, 1


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "child":
        try:
            return child(Path(sys.argv[2]))
        except BaseException:
            return 1
    if sys.argv[1:] == ["live"]:
        value, code = live()
        print(json.dumps(value, separators=(",", ":")))
        return code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
