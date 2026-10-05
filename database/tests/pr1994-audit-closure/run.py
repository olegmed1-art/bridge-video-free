"""Exercise the actual fixed PR1994 closure SQL in a disposable network-none DB.

Synthetic rows only. Three real public work-item trigger functions are used.
This is not a rehearsal of private production DDL or a production approval.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import select
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
SQL_PATH = ROOT / "database/scripts/reconcile_pr1994_audit.sql"
PACKET_PATH = ROOT / "database/scripts/pr1994_audit_closure_evidence.json"
IMAGE = "postgres@sha256:9e73daeb439141c2b11eea2463f5f1a3b269fd90d897b41cddb7cb440f21aa5d"
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"}
WID = "00000000-0000-4000-8000-000000000010"
TID = "00000000-0000-4000-8000-000000000011"
DID = "00000000-0000-4000-8000-000000000012"
HEAD = "4c26b6eda7617563a6372d1fc0eb82beadd4e910"
STAMP = "2020-01-02T03:04:05Z"
CODE = "AUDIT_FINDINGS_REPORTED"
OTHER = "00000000-0000-4000-8000-000000000002"
CHILD = "00000000-0000-4000-8000-000000000003"

SCHEMA = """
DROP SCHEMA IF EXISTS autopilot CASCADE;
CREATE SCHEMA autopilot;
CREATE TABLE autopilot.role_registry(
 role_id text PRIMARY KEY,enabled boolean NOT NULL,execution_scope text NOT NULL);
INSERT INTO autopilot.role_registry VALUES ('AUTOPILOT',true,'REPOSITORY');
CREATE TABLE autopilot.task(
 task_id uuid PRIMARY KEY,goal_type text NOT NULL,status text NOT NULL,
 terminal_reason_code text,updated_at timestamptz NOT NULL DEFAULT now(),
 completed_at timestamptz,goal_json jsonb NOT NULL DEFAULT '{"synthetic_private":"retained"}');
CREATE TABLE autopilot.project_work_item(
 work_item_id uuid PRIMARY KEY,work_key text NOT NULL UNIQUE,
 repository text NOT NULL DEFAULT 'olegmed1-art/bridge-video-free',
 role text NOT NULL DEFAULT 'AUTOPILOT' REFERENCES autopilot.role_registry,
 task_kind text NOT NULL DEFAULT 'REPOSITORY_AUDIT',target_pr integer NOT NULL DEFAULT 1994,
 mailbox_pr integer NOT NULL DEFAULT 1703,priority integer NOT NULL DEFAULT 0,
 state text NOT NULL CHECK(state IN ('READY','ACTIVE','BLOCKED','DONE','PAUSED','WAITING_DEPENDENCY')),
 depends_on_work_item_id uuid REFERENCES autopilot.project_work_item,
 generation integer NOT NULL DEFAULT 1,last_observed_head_sha text,last_task_id uuid REFERENCES autopilot.task,
 result_code text,not_before timestamptz NOT NULL DEFAULT '2020-01-02T03:04:05Z',
 probe_lease_owner text,probe_lease_epoch bigint NOT NULL DEFAULT 0,probe_lease_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT '2020-01-02T03:00:00Z',
 updated_at timestamptz NOT NULL DEFAULT '2020-01-02T03:04:05Z',completed_at timestamptz,
 hold_reason text,hold_until timestamptz,progress_token text,blocker_fingerprint text,
 objective text NOT NULL DEFAULT 'synthetic retained objective',
 result_summary text NOT NULL DEFAULT 'synthetic retained summary',
 task_spec_json jsonb NOT NULL DEFAULT '{"synthetic_private":"retained"}',
 CHECK((state='DONE' AND completed_at IS NOT NULL) OR (state<>'DONE' AND completed_at IS NULL)));
CREATE TABLE autopilot.project_work_task(
 work_item_id uuid REFERENCES autopilot.project_work_item,
 task_id uuid REFERENCES autopilot.task,run_kind text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(work_item_id,task_id));
CREATE TABLE autopilot.role_dispatch_outbox(
 dispatch_id uuid PRIMARY KEY,task_id uuid NOT NULL UNIQUE REFERENCES autopilot.task,
 repository text NOT NULL DEFAULT 'olegmed1-art/bridge-video-free',
 role text NOT NULL DEFAULT 'AUTOPILOT',target_pr integer NOT NULL DEFAULT 1994,
 mailbox_pr integer NOT NULL DEFAULT 1703,status text NOT NULL,
 expected_head_sha text NOT NULL,mode text NOT NULL DEFAULT 'READ_ONLY',
 github_dispatch_comment_id bigint NOT NULL DEFAULT 2067,delivery_contract_version integer NOT NULL DEFAULT 4,
 updated_at timestamptz NOT NULL DEFAULT now(),completed_at timestamptz,
 private_fixture_body jsonb NOT NULL DEFAULT '{"synthetic_private":"retained"}');
CREATE TABLE autopilot.native_cli_receipt(
 dispatch_id uuid PRIMARY KEY REFERENCES autopilot.role_dispatch_outbox,
 owner_name name NOT NULL DEFAULT SESSION_USER,request jsonb NOT NULL DEFAULT '{"synthetic_private":"retained"}',
 state text NOT NULL,provider_task_id text,prompt_sha256 text,terminal jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),submission_started_at timestamptz,
 submitted_at timestamptz,completed_at timestamptz);
CREATE TABLE autopilot.native_cli_config(
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled boolean NOT NULL DEFAULT false,cutover_at timestamptz NOT NULL DEFAULT now());
INSERT INTO autopilot.native_cli_config(singleton) VALUES(true);
CREATE TABLE autopilot.project_planner_state(
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled boolean NOT NULL DEFAULT true,decision_count bigint NOT NULL DEFAULT 7,
 last_decision_code text NOT NULL DEFAULT 'WORK_ITEM_DONE',
 last_work_item_id uuid,last_decision_at timestamptz NOT NULL DEFAULT '2020-01-02T04:00:00Z');
INSERT INTO autopilot.project_planner_state(singleton) VALUES(true);
"""

def literal(value):
    return "'" + str(value).replace("'", "''") + "'"

def public_function(path, name):
    source = (ROOT / path).read_text()
    pattern = r"CREATE(?: OR REPLACE)? FUNCTION autopilot\." + name + r"\([^)]*\)[\s\S]*?AS \$\$[\s\S]*?\$\$;"
    found = re.search(pattern, source)
    assert found, "PUBLIC_TRIGGER_SOURCE_MISSING:" + name
    return found.group()

def main():
    name = "pr1994-closure-" + str(os.getpid())
    checks = []
    report = {"status": "FAIL", "scope": "FIXED_PR1994_REAL_SQL_SYNTHETIC_TABLES_PUBLIC_TRIGGERS",
              "network": "none", "checks": checks,
              "limitations": ["Not private production DDL", "No live target or approval",
                             "Original audit artifact digest not independently verified"]}
    script = SQL_PATH.read_text()
    packet_sha = hashlib.sha256(PACKET_PATH.read_bytes()).hexdigest()
    assert "packet_sha constant text := '" + packet_sha + "'" in script
    catalog_query = script.split("$catalog_query$")[1]
    receipt_definition = (ROOT / "database/migrations/0351_autopilot_paused_evidence_reconcile.sql").read_text()
    receipt_definition = re.search(r"CREATE TABLE autopilot\.paused_work_reconcile_receipt[\s\S]*?\n\);", receipt_definition).group()
    functions = "\n".join([
        public_function("database/migrations/0323_autopilot_failure_continuation.sql", "role_blocker_requires_owner"),
        public_function("database/migrations/0324a_autopilot_dynamic_role_registry.sql", "role_is_enabled"),
        public_function("database/migrations/0324a_autopilot_dynamic_role_registry.sql", "enforce_enabled_role"),
        public_function("database/migrations/0325_autopilot_durable_dependency_wakeup.sql", "set_project_work_dependency_state"),
        public_function("database/migrations/0325_autopilot_durable_dependency_wakeup.sql", "release_project_work_dependents"),
    ])
    triggers = """
CREATE TRIGGER project_work_item_enabled_role BEFORE INSERT OR UPDATE OF role
 ON autopilot.project_work_item FOR EACH ROW EXECUTE FUNCTION autopilot.enforce_enabled_role();
CREATE TRIGGER autopilot_project_work_dependency_state BEFORE INSERT OR UPDATE OF depends_on_work_item_id
 ON autopilot.project_work_item FOR EACH ROW EXECUTE FUNCTION autopilot.set_project_work_dependency_state();
CREATE TRIGGER autopilot_project_work_dependency_release AFTER UPDATE OF state
 ON autopilot.project_work_item FOR EACH ROW EXECUTE FUNCTION autopilot.release_project_work_dependents();
"""
    terminal = json.dumps({"status": "SUCCEEDED", "result_code": CODE, "target_head_sha": HEAD,
                          "provider_evidence_sha256": "3c5763d016c26246a8b8906f3eacf399cb980b20b7efd076acc4435ffd307789",
                          "summary": "synthetic retained audit summary"})
    seed = f"""
INSERT INTO autopilot.task(task_id,goal_type,status,terminal_reason_code,completed_at)
 VALUES('{TID}','CHATGPT_ROLE_DISPATCH_V1','DONE','{CODE}','{STAMP}');
INSERT INTO autopilot.project_work_item(work_item_id,work_key,state,last_observed_head_sha,last_task_id,result_code)
 VALUES('{WID}','synthetic-pr1994-case','BLOCKED','{HEAD}','{TID}','{CODE}');
INSERT INTO autopilot.project_work_item(work_item_id,work_key,state,target_pr,hold_reason)
 VALUES('{OTHER}','synthetic-owner-hold','PAUSED',1853,'OWNER_HOLD');
INSERT INTO autopilot.project_work_task(work_item_id,task_id,run_kind) VALUES('{WID}','{TID}','AUDIT');
INSERT INTO autopilot.role_dispatch_outbox(dispatch_id,task_id,status,expected_head_sha,completed_at)
 VALUES('{DID}','{TID}','CALLBACK_ACCEPTED','{HEAD}','{STAMP}');
INSERT INTO autopilot.native_cli_receipt(dispatch_id,state,provider_task_id,prompt_sha256,terminal,submitted_at,completed_at)
 VALUES('{DID}','TERMINAL','task_fixture_pr1994',
 'a22d46319bc8433360517a62306b26befdc30ac1a738e2f7ebd7c9148c93e6d7',
 {literal(terminal)},'{STAMP}','{STAMP}');
"""
    def docker(*args, data=None, ok=True, timeout=90):
        result = subprocess.run(["docker", *args], input=data, text=True, capture_output=True,
                                env=ENV, timeout=timeout)
        if ok and result.returncode:
            raise RuntimeError(result.stderr[-2400:])
        return result

    def sql(source, variables=None, ok=True, app=None):
        args = ["exec", "-i"]
        if app:
            args += ["-e", "PGAPPNAME=" + app]
        args += [name, "psql", "-X", "-q", "-At", "-U", "postgres", "-v", "ON_ERROR_STOP=1"]
        for key, value in (variables or {}).items():
            args += ["-v", key + "=" + str(value)]
        return docker(*args, data="SET neon.branch_id='fixture-branch';\n" + source, ok=ok)

    def value(source):
        return sql(source).stdout.strip()

    def baseline():
        sql(SCHEMA + receipt_definition + functions + triggers + seed)

    def params(action="close"):
        return {
            "expected_database": "postgres", "expected_branch": "fixture-branch",
            "expected_owner": "postgres",
            "expected_work_item_id": WID, "expected_work_key": "synthetic-pr1994-case",
            "expected_task_id": TID, "expected_dispatch_id": DID,
            "expected_provider_task_id": "task_fixture_pr1994",
            "expected_prompt_sha256": hashlib.sha256(b"synthetic-prompt").hexdigest(),
            "expected_original_evidence_sha256": hashlib.sha256(b"synthetic-evidence").hexdigest(),
            "expected_original_completed_at": STAMP,
            "expected_updated_at": value(f"SELECT updated_at FROM autopilot.project_work_item WHERE work_item_id='{WID}';"),
            "expected_catalog_sha256": value(catalog_query + ";"), "action": action,
        }

    all_tables = ("role_registry", "task", "project_work_item", "project_work_task",
                  "role_dispatch_outbox", "native_cli_receipt", "native_cli_config",
                  "project_planner_state", "paused_work_reconcile_receipt")
    def snapshot():
        queries = ",".join(literal(table) + ",(SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY to_jsonb(x)::text),'[]'::jsonb)"
                           + " FROM autopilot." + table + " x)" for table in all_tables)
        return json.loads(value("SELECT jsonb_build_object(" + queries + ");"))

    def denied(expected, variables=None):
        before = snapshot()
        result = sql(script, variables=variables or params(), ok=False)
        assert result.returncode != 0, "UNEXPECTED_CLOSURE_SUCCESS"
        assert expected in result.stderr, (expected, result.stderr[-2000:])
        assert snapshot() == before, "REJECTED_TRANSACTION_CHANGED_FIXTURE_STATE"

    def writer(source):
        command = ["docker", "exec", "-i", "-e", "PGAPPNAME=pr1994-fixture-writer", name,
                   "psql", "-X", "-q", "-At", "-U", "postgres", "-v", "ON_ERROR_STOP=1"]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=ENV)
        process.stdin.write(source)
        process.stdin.close()
        process.stdin = None
        for _ in range(60):
            if value("SELECT count(*) FROM pg_stat_activity WHERE application_name='pr1994-fixture-writer' AND state='active';") == "1":
                return process
            time.sleep(0.05)
        process.kill()
        process.communicate()
        raise AssertionError("FIXTURE_WRITER_NOT_OBSERVED")

    try:
        docker("run", "-d", "--name", name, "--network", "none",
               "-e", "POSTGRES_HOST_AUTH_METHOD=trust", IMAGE,
               "-c", "max_prepared_transactions=10")
        for _ in range(120):
            if docker("exec", name, "pg_isready", "-U", "postgres", ok=False).returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("FIXTURE_DB_NOT_READY")

        baseline()
        before = snapshot()
        p = params()
        sql(script, variables=p)
        after = snapshot()
        before_row = next(x for x in before["project_work_item"] if x["work_item_id"] == WID)
        after_row = next(x for x in after["project_work_item"] if x["work_item_id"] == WID)
        assert after_row["state"] == "DONE" and after_row["result_code"] == "TARGET_SUPERSEDED_BY_CURRENT_MAIN"
        assert after_row["completed_at"] == after_row["updated_at"]
        allowed = {"state", "result_code", "completed_at", "updated_at"}
        assert {k: v for k, v in before_row.items() if k not in allowed} == {
            k: v for k, v in after_row.items() if k not in allowed}
        assert len(after["paused_work_reconcile_receipt"]) == 1
        receipt = after["paused_work_reconcile_receipt"][0]
        assert receipt["action"] == "CLOSE_SUPERSEDED" and receipt["evidence_token"] == packet_sha
        assert receipt["result_code"] == CODE and receipt["followup_task_id"] is None
        assert receipt["blocker_fingerprint"] == hashlib.sha256((CODE + "|" + packet_sha).encode()).hexdigest()
        for table in all_tables:
            if table not in ("project_work_item", "paused_work_reconcile_receipt"):
                assert before[table] == after[table], table
        assert [x for x in before["project_work_item"] if x["work_item_id"] != WID] == [
            x for x in after["project_work_item"] if x["work_item_id"] != WID]
        denied("PR1994_CLOSE_PRECONDITION_DRIFT")
        checks.append("one_item_one_existing_ledger_receipt_all_other_rows_payloads_controls_unchanged")

        sql(script, variables=params("reverse"))
        reversed_state = snapshot()
        reversed_row = next(x for x in reversed_state["project_work_item"] if x["work_item_id"] == WID)
        assert reversed_row["state"] == "BLOCKED" and reversed_row["result_code"] == CODE
        assert reversed_row["completed_at"] is None
        assert reversed_state["paused_work_reconcile_receipt"] == after["paused_work_reconcile_receipt"]
        for table in all_tables:
            if table != "project_work_item":
                assert reversed_state[table] == after[table], table
        assert {k: v for k, v in before_row.items() if k != "updated_at"} == {
            k: v for k, v in reversed_row.items() if k != "updated_at"}
        denied("PR1994_REVERSE_PRECONDITION_DRIFT", params("reverse"))
        denied("PR1994_CLOSE_PRECONDITION_DRIFT")
        checks.append("guarded_compensation_retains_receipt_and_replays_refuse")

        for field, bad in (("expected_database", "other"), ("expected_branch", "other"),
                           ("expected_owner", "other"), ("action", "other")):
            baseline()
            p = params()
            p[field] = bad
            denied("PR1994_TARGET_DRIFT", p)
        baseline()
        p = params()
        p["expected_updated_at"] = "2026-09-29T00:00:00Z"
        denied("PR1994_WORK_DRIFT", p)
        checks.append("target_direction_owner_branch_timestamp_guards")

        for change in (
            f"UPDATE autopilot.project_work_item SET generation=2 WHERE work_item_id='{WID}';",
            f"UPDATE autopilot.project_work_item SET hold_reason='OWNER_HOLD' WHERE work_item_id='{WID}';",
            f"UPDATE autopilot.project_work_item SET probe_lease_owner='fixture',probe_lease_until=now()+interval '1 minute' WHERE work_item_id='{WID}';",
            f"UPDATE autopilot.project_work_item SET last_observed_head_sha=repeat('0',40) WHERE work_item_id='{WID}';",
        ):
            baseline()
            sql(change)
            denied("PR1994_WORK_DRIFT")
        checks.append("exact_item_generation_head_hold_lease_guards")

        for child_state in ("WAITING_DEPENDENCY", "DONE"):
            baseline()
            sql(f"INSERT INTO autopilot.project_work_item(work_item_id,work_key,state,depends_on_work_item_id) VALUES('{CHILD}','synthetic-child','WAITING_DEPENDENCY','{WID}');")
            if child_state == "DONE":
                sql(f"UPDATE autopilot.project_work_item SET state='DONE',completed_at=now() WHERE work_item_id='{CHILD}';")
            denied("PR1994_LIVE_WORK_OR_CONTROL_DRIFT")
        checks.append("incoming_dependencies_any_state_refuse_without_wakeup")

        for change in (
            "UPDATE autopilot.native_cli_config SET enabled=true;",
            "UPDATE autopilot.role_registry SET enabled=false;",
            "UPDATE autopilot.role_registry SET execution_scope='OWNER_GATED';",
            f"UPDATE autopilot.task SET status='WAITING_EXTERNAL' WHERE task_id='{TID}';",
            f"UPDATE autopilot.role_dispatch_outbox SET status='PENDING' WHERE dispatch_id='{DID}';",
        ):
            baseline()
            sql(change)
            denied("PR1994_LIVE_WORK_OR_CONTROL_DRIFT")
        baseline()
        sql("CREATE OR REPLACE FUNCTION autopilot.role_blocker_requires_owner(p_result_code text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $fixture$ SELECT true $fixture$;")
        denied("PR1994_LIVE_WORK_OR_CONTROL_DRIFT")
        checks.append("live_task_dispatch_native_role_and_owner_gate_guards")

        baseline()
        sql(f"UPDATE autopilot.native_cli_receipt SET terminal=jsonb_set(terminal,'{{provider_evidence_sha256}}',to_jsonb(repeat('0',64))) WHERE dispatch_id='{DID}';")
        denied("PR1994_AUDIT_LINEAGE_DRIFT")
        baseline()
        sql(f"DELETE FROM autopilot.project_work_task WHERE work_item_id='{WID}';")
        denied("PR1994_AUDIT_LINEAGE_DRIFT")
        checks.append("native_terminal_digest_reference_and_audit_mapping_guards")

        baseline()
        sql(f"INSERT INTO autopilot.paused_work_reconcile_receipt(work_item_id,evidence_token,action) VALUES('{WID}',repeat('a',64),'NO_CHANGE');")
        denied("PR1994_CLOSE_PRECONDITION_DRIFT")
        checks.append("existing_receipt_refuses_duplicate_closure")

        baseline()
        p = params()
        sql("ALTER TABLE autopilot.project_work_item ADD COLUMN unexpected integer;")
        denied("PR1994_CATALOG_DRIFT", p)
        baseline()
        sql("ALTER TABLE autopilot.project_work_item ENABLE ROW LEVEL SECURITY;")
        denied("PR1994_UNREVIEWED_RELATION_OR_TRIGGER")
        baseline()
        sql("CREATE RULE unexpected_work_rule AS ON UPDATE TO autopilot.project_work_item DO ALSO NOTIFY fixture_unexpected;")
        denied("PR1994_UNREVIEWED_RELATION_OR_TRIGGER")
        baseline()
        sql("CREATE FUNCTION autopilot.unexpected_trigger() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$; CREATE TRIGGER unexpected BEFORE UPDATE ON autopilot.project_work_item FOR EACH ROW EXECUTE FUNCTION autopilot.unexpected_trigger();")
        denied("PR1994_UNREVIEWED_RELATION_OR_TRIGGER")
        checks.append("catalog_column_rls_rule_and_unknown_trigger_guards")

        baseline()
        sql("""CREATE OR REPLACE FUNCTION autopilot.release_project_work_dependents()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$ BEGIN UPDATE autopilot.project_planner_state SET decision_count=decision_count+1; RETURN NEW; END $$;""")
        denied("PR1994_POSTCHECK_SIDE_EFFECT")
        checks.append("review_miss_known_trigger_side_effect_causes_atomic_rollback")

        baseline()
        sql("ALTER TABLE autopilot.paused_work_reconcile_receipt ADD CONSTRAINT fixture_reject_close CHECK(action<>'CLOSE_SUPERSEDED');")
        denied("fixture_reject_close")
        checks.append("receipt_insert_failure_rolls_back_prior_item_update")


        # Force the unsafe session default, freeze the first statement snapshot,
        # then commit a child or native-control change before protected table locks.
        # A fixture-only advisory barrier supplies deterministic ordering.
        def first_query_race(change, expected, legacy=False, stage="before_locks"):
            baseline()
            p = params()
            holder_name = "pr1994-fixture-barrier"
            closer_name = "pr1994-fixture-closer"
            barrier_key = 19942118
            def process(app, variables=None):
                args = ["docker", "exec", "-i", "-e", "PGAPPNAME=" + app, name,
                        "psql", "-X", "-q", "-At", "-U", "postgres",
                        "-v", "ON_ERROR_STOP=1"]
                for key, val in (variables or {}).items():
                    args += ["-v", key + "=" + str(val)]
                return subprocess.Popen(args, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, env=ENV)
            def await_condition(query):
                for _ in range(100):
                    if value(query) == "1":
                        return
                    time.sleep(0.05)
                raise AssertionError("FIXTURE_FIRST_QUERY_BARRIER_NOT_OBSERVED")
            holder = process(holder_name)
            closer = None
            background = None
            try:
                holder.stdin.write(f"SELECT pg_advisory_lock({barrier_key});\n")
                holder.stdin.flush()
                await_condition(
                    "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid "
                    f"WHERE a.application_name='{holder_name}' AND l.locktype='advisory' AND l.granted;")
                raced_script = script
                if stage == "before_locks":
                    if legacy:
                        raced_script = raced_script.replace(
                            "BEGIN ISOLATION LEVEL READ COMMITTED;", "BEGIN;", 1)
                        raced_script = raced_script.replace(
                            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED;\n", "", 1)
                    barrier = ("\nDO $signal$ BEGIN RAISE NOTICE 'FIXTURE_BARRIER_READY'; END $signal$;\n"
                               "SET LOCAL lock_timeout='8s';\n"
                               f"SELECT pg_advisory_xact_lock({barrier_key});\n"
                               "SELECT pg_sleep(0.2);\nSET LOCAL lock_timeout='2s';\n")
                    assert raced_script.count("\nDO $capacity$") == 1
                    raced_script = raced_script.replace("\nDO $capacity$",
                                                         barrier + "\nDO $capacity$", 1)
                else:
                    if legacy:
                        pos = raced_script.rindex(" PERFORM pg_stat_clear_snapshot();")
                        raced_script = (raced_script[:pos] +
                                        raced_script[pos:].replace(" PERFORM pg_stat_clear_snapshot();", "", 1))
                    marker = " EXECUTE catalog_sql INTO catalog_after;"
                    assert raced_script.count(marker) == 1
                    raced_script = raced_script.replace(
                        marker, marker + "\n RAISE NOTICE 'FIXTURE_BARRIER_READY';"
                        "\n PERFORM set_config('lock_timeout','8s',true);"
                        f"\n PERFORM pg_advisory_xact_lock({barrier_key});"
                        "\n PERFORM pg_sleep(0.2);"
                        "\n PERFORM set_config('lock_timeout','2s',true);", 1)
                closer = process(closer_name, p)
                closer.stdin.write("SET default_transaction_isolation='repeatable read';\n"
                                   "SET neon.branch_id='fixture-branch';\n" + raced_script)
                closer.stdin.close()
                closer.stdin = None
                # Observe a client NOTICE rather than issue an active DB query
                # that could itself trip the conservative writer precheck.
                deadline = time.monotonic() + 10
                while True:
                    remaining = deadline - time.monotonic()
                    assert remaining > 0, "FIXTURE_CLOSER_BARRIER_TIMEOUT"
                    readable, _, _ = select.select([closer.stderr], [], [], remaining)
                    assert readable, "FIXTURE_CLOSER_BARRIER_TIMEOUT"
                    line = closer.stderr.readline()
                    assert line, "FIXTURE_CLOSER_EXITED_BEFORE_BARRIER"
                    if "FIXTURE_BARRIER_READY" in line:
                        break
                if stage == "before_locks":
                    sql(change)
                else:
                    # Keep the competing transaction open until the closer exits.
                    background = process("pr1994-fixture-writer")
                    background.stdin.write("BEGIN; SELECT 1;\n")
                    background.stdin.flush()
                    await_condition(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name='pr1994-fixture-writer' "
                        "AND state='idle in transaction';")
                after_control_commit = snapshot()
                # Closing this session releases its session advisory lock.
                holder.stdin.write("\\q\n")
                holder.stdin.close()
                holder.stdin = None
                _, holder_stderr = holder.communicate(timeout=10)
                assert holder.returncode == 0, holder_stderr
                _, closer_stderr = closer.communicate(timeout=15)
                if background is not None:
                    assert background.poll() is None, "COMPETING_TRANSACTION_EXITED_EARLY"
                    assert value(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name='pr1994-fixture-writer' "
                        "AND state='idle in transaction';") == "1", "COMPETING_TRANSACTION_NOT_RETAINED"
                if legacy:
                    assert closer.returncode == 0, closer_stderr[-2400:]
                    stale = snapshot()
                    row = next(x for x in stale["project_work_item"] if x["work_item_id"] == WID)
                    assert row["state"] == "DONE", "LEGACY_RACE_NOT_REPRODUCED"
                else:
                    assert closer.returncode != 0, "STALE_SNAPSHOT_CLOSURE_SUCCEEDED"
                    assert expected in closer_stderr, closer_stderr[-2400:]
                    assert snapshot() == after_control_commit, "RACE_REJECTION_CHANGED_ROWS"
            finally:
                if background is not None:
                    background.stdin.write("ROLLBACK;\n\\q\n")
                    background.stdin.close()
                    background.stdin = None
                    _, writer_stderr = background.communicate(timeout=15)
                    assert background.returncode == 0, writer_stderr
                for proc in (closer, holder):
                    if proc is not None and proc.poll() is None:
                        proc.kill()
                        proc.communicate(timeout=10)

        child_commit = (f"INSERT INTO autopilot.project_work_item"
                        "(work_item_id,work_key,state,depends_on_work_item_id) "
                        f"VALUES('{CHILD}','synthetic-race-child','WAITING_DEPENDENCY','{WID}');")
        control_commit = "UPDATE autopilot.native_cli_config SET enabled=true;"
        first_query_race(child_commit, "PR1994_LIVE_WORK_OR_CONTROL_DRIFT", legacy=True)
        first_query_race(child_commit, "PR1994_LIVE_WORK_OR_CONTROL_DRIFT")
        first_query_race(control_commit, "PR1994_LIVE_WORK_OR_CONTROL_DRIFT")
        checks.append("repeatable_read_default_child_and_control_commits_before_locks_refuse")

        first_query_race(None, "PR1994_POSTCHECK_SIDE_EFFECT", legacy=True, stage="postcheck")
        first_query_race(None, "PR1994_POSTCHECK_SIDE_EFFECT", stage="postcheck")
        checks.append("fresh_postcheck_statistics_detect_writer_started_after_precheck")

        for isolation in ("REPEATABLE READ", "SERIALIZABLE"):
            baseline()
            before = snapshot()
            p = params()
            result = sql("BEGIN ISOLATION LEVEL " + isolation + ";\nSELECT 1;\n" + script,
                         variables=p, ok=False)
            assert result.returncode != 0, "ENCLOSING_SNAPSHOT_CLOSURE_SUCCEEDED"
            assert "must be called before any query" in result.stderr, result.stderr[-2400:]
            assert snapshot() == before, "ENCLOSING_SNAPSHOT_REJECTION_CHANGED_ROWS"
        checks.append("enclosing_higher_isolation_snapshot_refuses_before_case_reads")

        for source, expected in (
            ("BEGIN; SELECT 1; SELECT pg_sleep(8); COMMIT;", "PR1994_UNSERIALIZED_WRITER"),
            ("SELECT pg_advisory_lock(hashtextextended('autopilot.role-worker-capacity-v1',0)); SELECT pg_sleep(8);", "PR1994_CAPACITY_WRITER_PRESENT"),
            ("BEGIN; LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE; SELECT pg_sleep(8); COMMIT;", "lock timeout"),
        ):
            baseline()
            p = params()
            process = writer(source)
            try:
                denied(expected, p)
            finally:
                _, stderr = process.communicate(timeout=15)
                assert process.returncode == 0, stderr
        checks.append("concurrent_active_capacity_and_table_writers_refuse")

        baseline()
        sql("BEGIN; SELECT 1; PREPARE TRANSACTION 'pr1994-fixture-prepared';")
        try:
            denied("PR1994_UNSERIALIZED_WRITER")
        finally:
            sql("ROLLBACK PREPARED 'pr1994-fixture-prepared';")
        checks.append("prepared_writer_refuses")

        baseline()
        p = params()
        sql(script, variables=p)
        sql(f"UPDATE autopilot.project_work_item SET generation=2 WHERE work_item_id='{WID}';")
        denied("PR1994_WORK_DRIFT", params("reverse"))
        checks.append("reversal_after_intervening_generation_refuses")

        report.update(status="PASS",sql_sha256=hashlib.sha256(SQL_PATH.read_bytes()).hexdigest(),
                      packet_sha256=packet_sha,check_groups=len(checks))
    finally:
        try:
            cleanup = docker("rm", "-f", "-v", name, ok=False)
            remaining = docker("ps", "-a", "--format", "{{.Names}}", ok=False)
            report["cleanup"] = cleanup.returncode == 0 and remaining.returncode == 0 and name not in remaining.stdout.splitlines()
        except Exception:
            report["cleanup"] = False
        if not report["cleanup"]:
            report["status"] = "FAIL_CLEANUP"
        print(json.dumps(report, sort_keys=True))
        if not report["cleanup"]:
            raise RuntimeError("OWNED_FIXTURE_CLEANUP_NOT_PROVEN")

if __name__ == "__main__":
    main()
