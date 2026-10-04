"""Dormant fixed owner CLI. Not installed in any live workflow.

Only a reviewed manual main invocation can use the resident owner credential.
External reviewer pins contract/permit digests; JSON hashes do not authenticate
control-plane observations. Never wire this entrypoint before channel review.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
from hashlib import sha256
from urllib.request import build_opener, ProxyHandler
from . import owner_probe, resident_preflight as resident
from .launch_contract import Launch, Permit, digest, require
from .fixed_adapter import FixedAdapter

PROGRAM = ("pilot_sql.py", "launch_contract.py", "ownership.py", "fixed_adapter.py",
           "bounded_controller.py", "owner_stage.py")


def program_hash():
    root = Path(__file__).resolve().parent
    return digest({name: sha256((root / name).read_bytes()).hexdigest() for name in PROGRAM})


def context(env, git, launch, stage, permit, now):
    # Read named public values only; never copy/iterate env.
    require(stage in ("inspect", "baseline", "initial", "revoke", "reactivate", "emergency"),
            "fixed_stage_required")
    require(env.get("GITHUB_REPOSITORY") == owner_probe.REPOSITORY
        and env.get("GITHUB_REF") == "refs/heads/main"
        and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and env.get("GITHUB_ACTOR") == "olegmed1-art"
        and env.get("GITHUB_TRIGGERING_ACTOR") == "olegmed1-art"
        and env.get("GITHUB_RUN_ATTEMPT") == "1"
        and env.get("GITHUB_RUN_ID", "").isdigit()
        and int(env.get("GITHUB_RUN_ID", "0")) > 0
        and env.get("GITHUB_WORKFLOW_REF") == owner_probe.REPOSITORY + "/" +
            owner_probe.WORKFLOW + "@refs/heads/main"
        and env.get("EXPECTED_MAIN") == launch.runtime_sha
        and env.get("GITHUB_SHA") == launch.runtime_sha
        and env.get("CANON_STAGE_SCOPE") == stage
        and env.get("EXPECTED_CANON_CONTRACT") == launch.fingerprint,
        "stage_context_refused")
    require(git("rev-parse", "HEAD") == launch.runtime_sha, "stage_checkout_refused")
    require(program_hash() == launch.module_hash, "reviewed_program_required")
    if stage not in ("inspect", "emergency"):
        launch.normal(now)
        require(permit is not None and env.get("EXPECTED_CANON_PERMIT") == digest(permit.public()),
                "external_phase_admission_required")
        permit.verify(launch, stage, now)


def invoke(env, git, launch, stage, permit, *, connect, check_source, now):
    context(env, git, launch, stage, permit, now())
    check_source(launch.runtime_sha)  # Before credential access.
    from ops.native_maintenance_owner_attest import parameters, EXPECTED_TARGET
    from bridge_school_api.main import EXPECTED_SCHOOL
    kwargs = parameters(env.get("NATIVE_OWNER_DATABASE_URL", ""))
    kwargs["application_name"] = "canon-fixed-owner-stage"
    with connect(**kwargs, autocommit=True) as conn:
        report = resident.inspect_resident(conn, resident.Binding(**EXPECTED_TARGET["neon"]))
        require(all(report.get(k) is True for k in ("server_binding", "plan_table_privileges",
            "plan_update_privileges", "owned_revoke_privileges", "schema_usage",
            "school_select", "explicit_gate_execute")), "qualified_owner_required")
        with conn.transaction(force_rollback=True):
            conn.execute("SET TRANSACTION READ ONLY")
            resident.catalog_path(conn)
            schools = conn.execute("SELECT school_id FROM public.school WHERE stable_name=%s AND status='active'",
                                   (EXPECTED_SCHOOL,)).fetchall()
            require(len(schools) == 1, "fixed_school_binding_required")
            school = schools[0][0]
        adapter = FixedAdapter(conn, school, launch, lambda: check_source(launch.runtime_sha), now)
        if stage == "inspect":
            return adapter.inspect()
        if stage == "emergency":
            return adapter.recover()
        return adapter.execute(stage, permit)


def main():
    from datetime import datetime, timezone
    import psycopg
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=("inspect","baseline","initial","revoke","reactivate","emergency"))
    parser.add_argument("--contract", required=True)
    parser.add_argument("--permit")
    args = parser.parse_args()
    # Public contract only. No arbitrary SQL, school, role, target or DSN args.
    launch = Launch.parse(json.loads(Path(args.contract).read_text()))
    permit = Permit.parse(json.loads(Path(args.permit).read_text())) if args.permit else None
    root = Path(__file__).resolve().parents[2]
    def git(*parts):
        return subprocess.check_output(["git", *parts], cwd=root, text=True,
            stderr=subprocess.DEVNULL, timeout=5).strip()
    opener = build_opener(owner_probe.NoRedirect(), ProxyHandler({}))
    def check_source(sha):
        owner_probe.source_check(os.environ.get("GH_TOKEN", ""), opener, sha)
    result = invoke(os.environ, git, launch, args.stage, permit, connect=psycopg.connect,
                    check_source=check_source, now=lambda: datetime.now(timezone.utc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:
        # No exception/DSN/target/private row serialization. A missing receipt
        # after dispatch means UNCERTAIN: reconcile/recover, never replay.
        print('{"status":"STOP_OR_COMMIT_UNCERTAIN","replay_allowed":false}')
        code = 2
    raise SystemExit(code)
