"""One disposable PostgreSQL 18 acceptance; no production DSN input.

Default is offline. --execute installs pinned 0056..0060 only in a new, empty
localhost service and exercises six SQL cases. No worker/Drive/IBM entrypoint.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_ROOT = ROOT / "_schema"
SCHEMA_COMMIT = "744d068a2cddfb8347411c8e4fb2bd69d19c09ab"
WORKER_ROOT = ROOT / "_worker"
WORKER_BASE = "1489af80e9a10b8de8726f845aaf1ea07a700731"
WORKER_PATCH_SHA = "f2394f9c5dd9b6e829a9b85462496754f3b1e0594b9c93aa0f777752e5a5e96c"
DATABASE = "subset_crash_retry_ci"
DSN = "postgresql://postgres:postgres@127.0.0.1:5432/" + DATABASE
WORKER_DSN = "postgresql://subset_ci_worker:subset_ci_worker@127.0.0.1:5432/" + DATABASE
CASES = ("concurrent_double_finish", "stale_finish_after_reclaim",
         "disconnect_before_finish_commit", "disconnect_after_finish_commit",
         "technical_retry_exhaustion", "crash_retry_exhaustion")
MIGRATIONS = ("0056_universal_video_queue.sql", "0057_universal_video_canary_review_gate.sql",
              "0058_universal_video_terminal_v2_gate.sql", "0059_universal_video_terminal_size_guard.sql",
              "0060_fixed_subset_admission.sql")


def require(value, message):
    if not value:
        raise AssertionError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def connect(login="postgres", *, autocommit=True):
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(host="127.0.0.1", port=5432, dbname=DATABASE,
        user=login, password=login, connect_timeout=5, autocommit=autocommit,
        options="-c statement_timeout=10000 -c lock_timeout=8000",
        application_name="synthetic-subset-crash-retry", row_factory=dict_row)


def wait_for(predicate, message):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(message)


def load_fixture():
    actual = subprocess.check_output(["git", "-C", str(SCHEMA_ROOT), "rev-parse", "HEAD"], text=True).strip()
    require(actual == SCHEMA_COMMIT, "wrong immutable schema checkout")
    require(not subprocess.check_output(["git", "-C", str(SCHEMA_ROOT), "status", "--porcelain"], text=True),
            "schema checkout is modified")
    sys.path.insert(0, str(SCHEMA_ROOT))
    source = SCHEMA_ROOT / "database/tests/fixed_subset_admission/runtime_fixture.py"
    spec = importlib.util.spec_from_file_location("immutable_runtime_fixture", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Only pure synthetic contract/output builders and SQL fixture methods are reused.
    import requests
    def no_http(*args, **kwargs):
        raise AssertionError("HTTP forbidden in SQL acceptance")
    requests.sessions.Session.request = no_http
    return module


def load_worker():
    actual = subprocess.check_output(["git", "-C", str(WORKER_ROOT), "rev-parse", "HEAD"], text=True).strip()
    require(actual == WORKER_BASE, "wrong worker base")
    patch = Path(__file__).with_name("worker-v2.patch")
    require(hashlib.sha256(patch.read_bytes()).hexdigest() == WORKER_PATCH_SHA, "wrong V2 patch")
    subprocess.run(["git", "-C", str(WORKER_ROOT), "apply", "--reverse", "--check", str(patch)], check=True,
                   capture_output=True, timeout=10)
    spec = importlib.util.spec_from_file_location("exact_worker_v2", WORKER_ROOT / "universal_video/subset_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(admin, fixture):
    fresh = admin.execute("""SELECT current_database() AS db, session_user::text AS login,
      current_setting('server_version_num')::int AS version,
      (SELECT count(*) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')) AS schemas,
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public') AS relations,
      (SELECT count(*) FROM pg_roles WHERE rolname !~ '^pg_' AND rolname<>'postgres') AS roles""").fetchone()
    require(fresh["db"] == DATABASE and fresh["login"] == "postgres" and 180000 <= fresh["version"] < 190000,
            "wrong service identity")
    require(all(fresh[k] == 0 for k in ("schemas", "relations", "roles")), "service must be empty")
    admin.execute("""CREATE EXTENSION pgcrypto WITH SCHEMA public;
      CREATE ROLE bridge_school_reader NOLOGIN;
      CREATE ROLE bridge_school_app NOLOGIN;
      CREATE ROLE bridge_school_worker NOLOGIN;
      CREATE TABLE public.schema_migration(migration_key text PRIMARY KEY);""")
    for name in MIGRATIONS:
        completed = subprocess.run(["psql", DSN, "-X", "-v", "ON_ERROR_STOP=1", "-f",
            str(SCHEMA_ROOT / "database/migrations" / name)],
            env={"PATH": os.environ["PATH"], "PGCONNECT_TIMEOUT": "5",
                 "PGOPTIONS": "-c statement_timeout=60000 -c lock_timeout=5000"},
            capture_output=True, text=True, timeout=90)
        require(completed.returncode == 0, "migration failed: " + name + "\n" + completed.stderr)
    admin.execute(fixture.actor_setup_sql())
    require(admin.execute("SELECT count(*) AS n FROM schema_migration").fetchone()["n"] == 5,
            "unexpected migration registry")
    return fresh


def child_finish(mode):
    """Real process exit closes the backend; no adapter or mock commit is used."""
    import psycopg
    api = load_worker()
    data = json.load(sys.stdin)
    real_connect = psycopg.connect

    class CrashBoundary:
        def __init__(self, *args, **kwargs):
            require(args == (WORKER_DSN,), "unexpected client database")
            self.conn = real_connect(*args, **kwargs)

        def __enter__(self):
            return self.conn.__enter__()

        def __exit__(self, error, value, trace):
            if error is not None:
                return self.conn.__exit__(error, value, trace)
            pid = self.conn.info.backend_pid
            if mode == "after":
                self.conn.__exit__(None, None, None)  # Real commit and close.
            print(canonical({"backend_pid": pid, "boundary": mode}), flush=True)
            os._exit(73)  # Before real commit, or after it but before V2 receives its row.

    psycopg.connect = CrashBoundary
    adapter = api.SubsetWorkerAdapter(lambda *_a, **_k: data["observation"])
    adapter.finish_job(WORKER_DSN, data["claim"], "subset_ci_worker",
                      before_work_observation_id=data["pre"], outcome="REVIEW_READY", output=data["output"])
    raise AssertionError("crash boundary was not reached")


def run_cases(fixture, api, connections, report):
    import psycopg
    from psycopg.types.json import Jsonb
    f = fixture.Fixture(connections, {"cases": []})
    admin, worker = connections["owner"], connections["worker"]
    login = fixture.ACTORS["worker"][0]
    pre = {}

    def identity(claim):
        return claim["job_id"], claim["lease_token"], login

    def snapshot(item):
        data = f.snapshot(item)
        data["subset"] = admin.execute("""SELECT jsonb_build_object(
          'approval',(SELECT to_jsonb(a) FROM video_queue.subset_approval a WHERE approval_id=%s),
          'observations',(SELECT jsonb_agg(to_jsonb(o) ORDER BY observation_id) FROM video_queue.source_observation o WHERE approval_id=%s),
          'admission',(SELECT to_jsonb(a) FROM video_queue.subset_admission a WHERE batch_id=%s),
          'reservations',(SELECT jsonb_agg(to_jsonb(r) ORDER BY source_identity) FROM video_queue.source_reservation r WHERE batch_id=%s)) AS state""",
          (item["approval"], item["approval"], item["batch"], item["batch"])).fetchone()["state"]
        return data

    def reject_unchanged(item, call, message="SUBSET_LEASE_LOST", state="P0001"):
        before = snapshot(item)
        try:
            call()
        except psycopg.Error as error:
            require(error.sqlstate == state and (message is None or error.diag.message_primary == message),
                    "unexpected SQL rejection: " + str(error))
        else:
            raise AssertionError("invalid finish accepted")
        require(snapshot(item) == before, "rejected call changed rows/events/observations")
        return sha(before)

    def prework(item, claim):
        adapter = api.SubsetWorkerAdapter(lambda *_a, **_k: str(f.observe(item, claim)))
        pre[str(claim["lease_token"])] = adapter.before_work(WORKER_DSN, claim, login)

    def raw_finish(claim, output, observation):
        adapter = api.SubsetWorkerAdapter(lambda *_a, **_k: str(observation))
        return adapter.finish_job(WORKER_DSN, claim, login,
            before_work_observation_id=pre[str(claim["lease_token"])], outcome="REVIEW_READY", output=output)

    def expire(claim, column="lease_expires_at"):
        require(column in ("lease_expires_at", "next_attempt_at"), "wrong test clock column")
        # Only the fixture administrator accelerates time; runtime roles cannot write tables.
        changed = admin.execute(f"UPDATE video_queue.job SET {column}=clock_timestamp()-interval '1 second' WHERE job_id=%s AND source_file_id LIKE 'synthetic-%%' RETURNING job_id",
                                (claim["job_id"],)).fetchall()
        require(len(changed) == 1, "synthetic clock update missed job")

    def shape(item, claim, *, success=True):
        status = "REVIEW_READY" if success else "FAILED"
        rows = admin.execute("SELECT job_id,status,attempt_count,output FROM video_queue.job WHERE batch_id=%s ORDER BY sequence", (item["batch"],)).fetchall()
        require(len(rows) == 2 and rows[0]["job_id"] == claim["job_id"] and rows[0]["status"] == status,
                "unexpected terminal canary")
        require(rows[1]["status"] == "PENDING_CANARY" and rows[1]["attempt_count"] == 0,
                "sibling released or claimed")
        events = admin.execute("SELECT count(*) AS n FROM video_queue.job_event WHERE job_id=%s AND event_type=%s",
                               (claim["job_id"], status)).fetchone()["n"]
        require(events == 1, "terminal outcome accepted more/less than once")
        require(f.one("worker", "SELECT * FROM video_queue.claim_job(%s,900,%s,%s)",
                      (login, fixture.PROFILE, fixture.REVISION)) is None, "unexpected extra claim")
        if success:
            require(rows[0]["output"] == fixture.terminal_output(claim), "wrong accepted result")
            consumed = admin.execute("SELECT count(*) AS n FROM video_queue.source_observation WHERE job_id=%s AND lease_token=%s AND phase='ATTEMPT' AND consumed_at IS NOT NULL",
                                     (claim["job_id"], claim["lease_token"])).fetchone()["n"]
            require(consumed == 2, "pre/post observation consumption is not exact")
        return {"terminal_events": events, "sibling_status": rows[1]["status"],
                "attempt_count": rows[0]["attempt_count"], "snapshot_sha256": sha(snapshot(item))}

    for name in CASES:
        item = f.prepare("crash-" + name.replace("_", "-"), 2)
        first = f.claim(item)
        prework(item, first)
        output, post = fixture.terminal_output(first), f.observe(item, first)
        evidence = {"case": name}
        # 0060 deliberately denies the legacy finish entrypoint to runtime logins.
        reject_unchanged(item, lambda: worker.execute("SELECT * FROM video_queue.finish_job(%s,%s,%s,'REVIEW_READY',%s,NULL)",
                         (*identity(first), Jsonb(output))), message=None, state="42501")

        if name == "concurrent_double_finish":
            def competing_finish():
                try:
                    return raw_finish(first, output, post)
                except psycopg.Error as error:
                    return error.sqlstate, error.diag.message_primary
            with ThreadPoolExecutor(max_workers=2) as pool:
                with admin.transaction():
                    admin.execute("SELECT job_id FROM video_queue.job WHERE job_id=%s FOR UPDATE", (first["job_id"],))
                    pending = [pool.submit(competing_finish) for _ in range(2)]
                    def both_waiting():
                        admin.execute("SELECT pg_stat_clear_snapshot()")
                        return admin.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE application_name='universal-video-subset-worker' AND wait_event_type='Lock'").fetchone()["n"] == 2
                    wait_for(both_waiting, "two real V2 finish lock waits not observed")
                results = [future.result(timeout=10) for future in pending]
                require(results.count(dict(job_status="REVIEW_READY", batch_status="CANARY_REVIEW", released_jobs=0)) == 1
                        and results.count(("P0001", "SUBSET_LEASE_LOST")) == 1, "concurrent V2 finish not fenced")
            evidence["real_lock_wait_observed"] = True
            evidence["unchanged_repeat_sha256"] = reject_unchanged(item, lambda: raw_finish(first, output, post))
            evidence.update(shape(item, first))

        elif name == "stale_finish_after_reclaim":
            expire(first)
            second = f.claim(item)
            require(second["attempt_count"] == 2 and second["lease_token"] != first["lease_token"], "lease not rotated")
            evidence["unchanged_stale_sha256"] = reject_unchanged(item, lambda: raw_finish(first, output, post))
            prework(item, second)
            raw_finish(second, fixture.terminal_output(second), f.observe(item, second))
            evidence.update(shape(item, second))

        elif name.startswith("disconnect_"):
            mode = "before" if name == "disconnect_before_finish_commit" else "after"
            before = snapshot(item)
            child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child-finish", mode],
                input=canonical(dict(claim=first, pre=pre[str(first["lease_token"])], output=output, observation=post)),
                capture_output=True, text=True, timeout=25,
                env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1",
                     "SUBSET_CRASH_RETRY_DISPOSABLE": "1"})
            require(child.returncode == 73, "client did not reach deliberate exit: " + child.stderr)
            boundary = json.loads(child.stdout)
            require(boundary["boundary"] == mode, "wrong child boundary")
            wait_for(lambda: admin.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE pid=%s",
                                           (boundary["backend_pid"],)).fetchone()["n"] == 0, "crashed backend still alive")
            evidence["client_exit_code"] = 73
            if mode == "before":
                require(snapshot(item) == before, "disconnect before commit was not atomic rollback")
                evidence["rollback_snapshot_sha256"] = sha(before)
                expire(first)
                second = f.claim(item)
                require(second["attempt_count"] == 2 and second["lease_token"] != first["lease_token"], "crash did not reclaim")
                reject_unchanged(item, lambda: raw_finish(first, output, post))
                prework(item, second)
                raw_finish(second, fixture.terminal_output(second), f.observe(item, second))
                evidence.update(shape(item, second))
            else:
                # Child really committed then exited without returning a receipt to this caller.
                # This is client death after commit, not a network COMMIT-ACK fault injector.
                evidence["unchanged_repeat_sha256"] = reject_unchanged(item, lambda: raw_finish(first, output, post))
                evidence.update(shape(item, first))

        else:
            current = first
            for attempt in range(1, 4):
                require(current["attempt_count"] == attempt, "wrong retry attempt")
                if name == "technical_retry_exhaustion":
                    adapter = api.SubsetWorkerAdapter(lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected retry observation")))
                    result = adapter.retry_job(WORKER_DSN, job_id=str(current["job_id"]), lease_token=str(current["lease_token"]),
                                               worker_key=login, error_code="UV_SYNTHETIC_RETRY", base_delay_seconds=1)
                    require(result["job_status"] == ("FAILED" if attempt == 3 else "QUEUED"), "wrong retry status")
                    require((result["retry_after"] is None) is (attempt == 3), "wrong retry timestamp")
                    if attempt < 3:
                        expire(current, "next_attempt_at")
                else:
                    expire(current)
                old = current
                if attempt < 3:
                    current = f.claim(item)
                    require(current["lease_token"] != old["lease_token"], "retry reused lease")
                    reject_unchanged(item, lambda: raw_finish(old, fixture.terminal_output(old), post))
                    prework(item, current)
                else:
                    require(f.one("worker", "SELECT * FROM video_queue.claim_job(%s,900,%s,%s)",
                                  (login, fixture.PROFILE, fixture.REVISION)) is None, "fourth attempt admitted")
            evidence.update(shape(item, current, success=False))
            stable = snapshot(item)
            require(f.one("worker", "SELECT * FROM video_queue.claim_job(%s,900,%s,%s)",
                          (login, fixture.PROFILE, fixture.REVISION)) is None, "exhausted claim revived")
            require(snapshot(item) == stable, "repeat exhaustion changed terminal history")
        evidence["status"] = "PASS"
        report["cases"].append(evidence)
        f.scalar("approver", "SELECT video_queue.revoke_subset_approval(%s)", (item["approval"],))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--child-finish", choices=("before", "after"))
    args = parser.parse_args()
    if not args.execute and not args.child_finish:
        print(canonical(dict(status="NOT_RUN", cases=CASES, schema_commit=SCHEMA_COMMIT, production_execution_allowed=False)))
        return
    require(os.name == "posix" and os.environ.get("SUBSET_CRASH_RETRY_DISPOSABLE") == "1", "disposable Linux service required")
    if args.child_finish:
        child_finish(args.child_finish)
    report = dict(status="FAIL_OR_UNKNOWN", schema_commit=SCHEMA_COMMIT, worker_base=WORKER_BASE,
                  worker_patch_sha256=WORKER_PATCH_SHA, cases=[], production_execution_allowed=False)
    try:
        fixture = load_fixture()
        api = load_worker()
        with ExitStack() as stack:
            admin = stack.enter_context(connect())
            report["initial_service"] = install(admin, fixture)
            connections = {"owner": admin}
            for actor, (login, capability) in fixture.ACTORS.items():
                conn = stack.enter_context(connect(login))
                row = conn.execute("SELECT session_user::text AS login, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, pg_has_role(session_user,%s,'MEMBER') AS capable FROM pg_roles WHERE rolname=session_user", (capability,)).fetchone()
                require(row["login"] == login and row["capable"] and not any(row[k] for k in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolbypassrls")), "wrong runtime login authority")
                require(not conn.execute("SELECT has_table_privilege(session_user,'video_queue.job','INSERT,UPDATE,DELETE') AS writable").fetchone()["writable"], "runtime can write tables")
                connections[actor] = conn
            run_cases(fixture, api, connections, report)
            require([case["case"] for case in report["cases"]] == list(CASES), "missing acceptance case")
        with connect() as final:
            report["final_census"] = final.execute("""SELECT
              (SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid()) AS other_clients,
              (SELECT count(*) FROM pg_prepared_xacts) AS prepared,
              (SELECT count(*) FROM pg_locks WHERE locktype='advisory') AS advisory_locks,
              (SELECT count(*) FROM video_queue.subset_approval WHERE runtime_enabled OR revoked_at IS NULL) AS active_approvals""").fetchone()
            require(not any(report["final_census"].values()), "fixture left active state")
        report["status"] = "PASS_ISOLATED_SUBSET_CRASH_RETRY"
    except BaseException as error:
        report["error"] = dict(type=type(error).__name__, message=str(error))
        raise
    finally:
        print("SUBSET_CRASH_RETRY_EVIDENCE " + canonical(report), flush=True)


if __name__ == "__main__":
    main()
