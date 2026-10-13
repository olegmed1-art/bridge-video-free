"""One future, disposable PG18 installer regression; importing/collecting runs no SQL.

The default command only describes the cases. --execute is an explicit mutating
test entrypoint, restricted to the fixed synthetic localhost preflight database.
It must never receive a production DSN. This is not the production migrator.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = "0060_fixed_subset_admission.sql"
KEY = MIGRATION[:-4]
ORIGINAL_SHA256 = "5c633ffeb4b96fd08ae903b514e4142c6cf95fb9786837ca7e9da33564c060f5"
DATABASE = "bridge_school_preflight"
OWNER = "bridge_ci_owner"
ADMIN_DSN = f"postgresql://postgres:postgres@127.0.0.1:5432/{DATABASE}"
OWNER_DSN = f"postgresql://{OWNER}:{OWNER}@127.0.0.1:5432/{DATABASE}"
ROLES = ("video_subset_owner", "video_subset_approver", "video_subset_collector",
         "video_subset_admitter", "video_subset_worker_v2")
TABLES = ("subset_approval", "source_observation", "subset_admission", "source_reservation")
# 0058_universal_video_terminal_v2_gate/001_apply_terminal_v2.sql:74.
# 0060 deliberately retains this bounded read-only legacy capability.
LEGACY_READ_GRANT = ["bridge_school_worker", "video_queue.precanary_idle_snapshot()"]
CREATE_GRANT = ("GRANT CREATE ON SCHEMA video_queue TO video_subset_owner\n"
                "    GRANTED BY CURRENT_USER;\n")
RESTORE_MARKER = "-- BEGIN MANAGED OWNER RESTORE\n"
GRANT_MARKER = "-- BEGIN MANAGED OWNER GRANTS\n"

SERVER_SQL = """
SELECT jsonb_build_object(
 'version', current_setting('server_version_num')::int,
 'database', current_database(), 'session_user', session_user, 'current_user', current_user,
 'superuser', rolsuper, 'createrole', rolcreaterole, 'createdb', rolcreatedb,
 'replication', rolreplication, 'bypassrls', rolbypassrls,
 'self_grant', current_setting('createrole_self_grant'),
 'max_connections', current_setting('max_connections')::int,
 'prepared', (SELECT count(*) FROM pg_prepared_xacts),
 'other_sessions', (SELECT count(*) FROM pg_stat_activity
     WHERE backend_type='client backend' AND pid<>pg_backend_pid()),
 'user_roles', (SELECT coalesce(jsonb_agg(rolname ORDER BY rolname),'[]') FROM pg_roles
     WHERE rolname !~ '^pg_' AND rolname<>'postgres'),
 'user_schemas', (SELECT coalesce(jsonb_agg(nspname ORDER BY nspname),'[]') FROM pg_namespace
     WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')),
 'public_relations', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public'),
 'default_acls', (SELECT count(*) FROM pg_default_acl),
 'database_owner', (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()),
 'public_owner', (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public')
) FROM pg_roles WHERE rolname=current_user;
"""

# Only bootstrap uses postgres, matching database-production.yml. All numbered
# migrations, negative cases, invariant checks and reruns use the actual login M.
BOOTSTRAP_SQL = """
BEGIN;
CREATE EXTENSION pgcrypto;
CREATE EXTENSION btree_gist;
CREATE EXTENSION pg_stat_statements;
CREATE ROLE bridge_ci_owner LOGIN PASSWORD 'bridge_ci_owner'
    CREATEROLE NOSUPERUSER NOCREATEDB NOREPLICATION;
ALTER DATABASE bridge_school_preflight OWNER TO bridge_ci_owner;
ALTER SCHEMA public OWNER TO bridge_ci_owner;
COMMIT;
"""

# Logical/catalog state, not global OID allocators, statistics or sequence gaps.
# Admin reads are needed after COMMIT because M deliberately loses access to O.
# No secrets/password catalog is read.
SNAPSHOT_SQL = """
SELECT jsonb_build_object(
 'roles', (SELECT jsonb_agg(to_jsonb(r) ORDER BY rolname) FROM pg_roles r),
 'memberships', (SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY roleid,member,grantor),'[]') FROM pg_auth_members m),
 'schemas', (SELECT jsonb_agg(to_jsonb(n) ORDER BY nspname) FROM pg_namespace n
     WHERE nspname !~ '^pg_' AND nspname<>'information_schema'),
 'relations', (SELECT jsonb_agg(jsonb_build_array(c.oid,c.relname,c.relkind,c.relowner,c.relacl,
     c.relrowsecurity,c.relforcerowsecurity) ORDER BY c.oid) FROM pg_class c
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue'),
 'columns', (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attrelid,a.attnum) FROM pg_attribute a
     JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='video_queue'),
 'constraints', (SELECT jsonb_agg(to_jsonb(k) ORDER BY k.oid) FROM pg_constraint k
     JOIN pg_namespace n ON n.oid=k.connamespace WHERE n.nspname='video_queue'),
 'triggers', (SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid) FROM pg_trigger t
     JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='video_queue'),
 'functions', (SELECT jsonb_agg(jsonb_build_array(p.oid,p.proowner,p.proacl,pg_get_functiondef(p.oid))
     ORDER BY p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname='video_queue'),
 'defaults', (SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY d.oid),'[]') FROM pg_default_acl d),
 'registry', (SELECT jsonb_agg(to_jsonb(m) ORDER BY migration_key) FROM public.schema_migration m),
 'batches', (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY batch_id),'[]') FROM video_queue.batch b),
 'jobs', (SELECT coalesce(jsonb_agg(to_jsonb(j) ORDER BY job_id),'[]') FROM video_queue.job j),
 'events', (SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY event_id),'[]') FROM video_queue.job_event e)
);
"""

OWNERS_SQL = """
SELECT jsonb_build_object(
 'schema_owner', (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='video_queue'),
 'legacy_tables', (SELECT jsonb_object_agg(relname,pg_get_userbyid(relowner)) FROM pg_class c
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue'
     AND relname IN ('batch','job','job_event')),
 'legacy_functions', (SELECT jsonb_object_agg(p.oid::regprocedure::text,pg_get_userbyid(proowner)) FROM pg_proc p
     JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='video_queue'
     AND proname IN ('enqueue_drive_batch','claim_job','heartbeat_job','retry_job','finish_job')),
 'subset_roles', (SELECT coalesce(jsonb_agg(rolname ORDER BY rolname),'[]') FROM pg_roles
     WHERE rolname IN ('video_subset_owner','video_subset_approver','video_subset_collector',
                      'video_subset_admitter','video_subset_worker_v2')),
 'marker', EXISTS(SELECT FROM schema_migration WHERE migration_key='0060_fixed_subset_admission')
);
"""

POST_SQL = """
SELECT jsonb_build_object(
 'roles', (SELECT jsonb_object_agg(rolname,jsonb_build_array(rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls))
     FROM pg_roles WHERE rolname IN ('video_subset_owner','video_subset_approver',
     'video_subset_collector','video_subset_admitter','video_subset_worker_v2')),
 'tables', (SELECT jsonb_object_agg(relname,pg_get_userbyid(relowner)) FROM pg_class c
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue'
     AND relname IN ('subset_approval','source_observation','subset_admission','source_reservation')),
 'functions', (SELECT jsonb_object_agg(proname,pg_get_userbyid(proowner)) FROM pg_proc p
     JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='video_queue' AND
     (proname LIKE 'subset_%' OR proname IN ('approve_subset','set_subset_runtime','revoke_subset_approval',
     'observe_subset','recover_subset_admission','admit_approved_subset','authorize_subset_attempt',
     'heartbeat_subset_job','retry_subset_job','finish_subset_job','claim_job'))),
 'create', has_schema_privilege('video_subset_owner','video_queue','CREATE'),
 'set', pg_has_role('bridge_ci_owner','video_subset_owner','SET'),
 'usage', pg_has_role('bridge_ci_owner','video_subset_owner','USAGE'),
 'memberships', (SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(roleid),pg_get_userbyid(member),
     pg_get_userbyid(grantor),admin_option,inherit_option,set_option) ORDER BY roleid,member,grantor)
     FROM pg_auth_members WHERE roleid IN (SELECT oid FROM pg_roles WHERE rolname IN
       ('video_subset_owner','video_subset_approver','video_subset_collector','video_subset_admitter','video_subset_worker_v2'))),
 'legacy_execute', jsonb_build_array(
     has_function_privilege('bridge_school_app','video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb)','EXECUTE'),
     has_function_privilege('bridge_school_worker','video_queue.finish_job(uuid,uuid,text,text,jsonb,text)','EXECUTE')),
 'public_subset_execute', (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
     LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
     WHERE n.nspname='video_queue' AND p.proowner='video_subset_owner'::regrole
       AND a.grantee=0 AND a.privilege_type='EXECUTE'),
 'capability_execute', (SELECT coalesce(jsonb_agg(jsonb_build_array(r.rolname,
     p.oid::regprocedure::text) ORDER BY r.rolname,p.oid::regprocedure::text),'[]')
     FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN pg_roles r
     WHERE n.nspname='video_queue' AND r.rolname IN ('video_subset_approver',
     'video_subset_collector','video_subset_admitter','video_subset_worker_v2',
     'bridge_school_app','bridge_school_worker','bridge_school_reader')
     AND has_function_privilege(r.oid,p.oid,'EXECUTE')),
 'direct_table_access', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     CROSS JOIN pg_roles r WHERE n.nspname='video_queue' AND c.relkind IN ('r','p')
     AND r.rolname IN ('video_subset_approver','video_subset_collector','video_subset_admitter',
     'video_subset_worker_v2','bridge_school_app','bridge_school_worker','bridge_school_reader')
     AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))),
 'checksum', (SELECT checksum FROM schema_migration WHERE migration_key='0060_fixed_subset_admission'),
 'new_rows', (SELECT count(*) FROM video_queue.subset_approval)
     + (SELECT count(*) FROM video_queue.source_observation)
     + (SELECT count(*) FROM video_queue.subset_admission)
     + (SELECT count(*) FROM video_queue.source_reservation)
);
"""


class RegressionFailure(RuntimeError):
    """Fail closed; a failed process or unknown outcome never means PASS."""


def require(condition, message):
    if not condition:
        raise RegressionFailure(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(source, old, new):
    require(source.count(old) == 1, "migration fixture anchor changed")
    return source.replace(old, new, 1)


def negative_cases(source):
    original, count = re.subn(
        r"^-- BEGIN MANAGED OWNER (PRECONDITION|GRANTS|RESTORE)\n.*?^-- END MANAGED OWNER \1\n",
        "", source, flags=re.M | re.S)
    require(count == 3 and sha(original.encode()) == ORIGINAL_SHA256,
            "original migration no longer matches reviewed bytes")
    grant_end = "-- END MANAGED OWNER GRANTS\n"
    # A complete successful installation rolled back deliberately also asserts
    # the exact bootstrap/self-grant options while both grants coexist.
    membership_assertion = """
DO $grant_check$
BEGIN
    IF (SELECT count(*) FROM pg_auth_members WHERE roleid='video_subset_owner'::regrole) <> 2
       OR NOT EXISTS (SELECT FROM pg_auth_members WHERE roleid='video_subset_owner'::regrole
          AND member=current_user::regrole AND grantor='postgres'::regrole
          AND admin_option AND NOT inherit_option AND NOT set_option)
       OR NOT EXISTS (SELECT FROM pg_auth_members WHERE roleid='video_subset_owner'::regrole
          AND member=current_user::regrole AND grantor=current_user::regrole
          AND NOT admin_option AND inherit_option AND set_option) THEN
        RAISE EXCEPTION 'SUBSET_BOOTSTRAP_MEMBERSHIP_DRIFT';
    END IF;
END $grant_check$;
"""
    rollback = once(source, grant_end, grant_end + membership_assertion)
    rollback = once(rollback, "COMMIT;\n", "ROLLBACK;\n")
    return [
        ("original_without_fix", original, "42501", None),
        ("without_schema_create", once(source, CREATE_GRANT, ""), "42501", None),
        ("without_set", once(source, "INHERIT TRUE, SET TRUE", "INHERIT TRUE, SET FALSE"), "42501", None),
        ("without_inherit", once(source, "INHERIT TRUE, SET TRUE", "INHERIT FALSE, SET TRUE"), "42501", None),
        ("failure_after_transfers", once(source, RESTORE_MARKER,
         "DO $$ BEGIN RAISE EXCEPTION 'SUBSET_TEST_FAILURE_AFTER_TRANSFERS'; END $$;\n" + RESTORE_MARKER),
         "P0001", "SUBSET_TEST_FAILURE_AFTER_TRANSFERS"),
        ("nonempty_self_grant", "SET createrole_self_grant='set';\n" + source,
         "P0001", "SUBSET_OWNER_INSTALLER_CONFIGURATION"),
        ("full_transaction_rollback", rollback, None, None),
    ]


def check_server(state, *, admin=False):
    require(180000 <= state["version"] < 190000, "PG18 required; PG16 evidence is insufficient")
    require(state["database"] == DATABASE, "unexpected database")
    login = "postgres" if admin else OWNER
    require(state["session_user"] == state["current_user"] == login, "wrong actual login")
    require(state["superuser"] is admin, "wrong superuser mode")
    require(state["prepared"] == state["other_sessions"] == 0, "database is not isolated")
    require(1 <= state["max_connections"] <= 8, "connection budget exceeds eight")
    require(state["self_grant"] == "", "createrole_self_grant must be empty")
    require(state["default_acls"] == 0, "unexpected default privileges")
    if admin:
        require(state["user_roles"] == state["user_schemas"] == [], "cluster is not fresh")
        require(state["public_relations"] == 0, "database is not fresh")
    else:
        require(state["createrole"] is True and all(state[k] is False for k in
                ("createdb", "replication", "bypassrls")), "wrong migration owner attributes")
        require(state["database_owner"] == state["public_owner"] == OWNER, "wrong bootstrap ownership")


def check_pre(state):
    require(state["schema_owner"] == OWNER, "M must own video_queue")
    require(set(state["legacy_tables"] or {}) == {"batch", "job", "job_event"},
            "legacy tables missing")
    require(all(v == OWNER for v in state["legacy_tables"].values()), "wrong table ownership")
    require(len(state["legacy_functions"] or {}) == 5
            and all(v == OWNER for v in state["legacy_functions"].values()), "wrong function ownership")
    require(state["subset_roles"] == [] and state["marker"] is False, "0060 is not fresh")


def expected_api(source):
    names = set(re.findall(r"CREATE (?:OR REPLACE )?FUNCTION video_queue\.(\w+)\(", source))
    grants = []
    for functions, role in re.findall(
            r"GRANT EXECUTE ON FUNCTION (.*?) TO (video_subset_\w+);", source, re.S):
        if role != "video_subset_owner":
            grants.extend([role, signature.replace("timestamptz", "timestamp with time zone")] for signature in
                          re.findall(r"video_queue\.\w+\([^)]*\)", functions))
    require(len(grants) == 11 and len(names) >= 11, "unexpected API fixture")
    grants.append(LEGACY_READ_GRANT.copy())
    return names, sorted(grants)


def check_post(state, checksum, source):
    names, grants = expected_api(source)
    require(state["roles"] == {role: [False] * 5 for role in ROLES}, "definer/capability role drift")
    require(state["tables"] == {table: ROLES[0] for table in TABLES}, "new table ownership drift")
    require(state["functions"] == {name: ROLES[0] for name in names}, "function ownership drift")
    require(state["create"] is state["set"] is state["usage"] is False, "temporary privilege leaked")
    expected = sorted([[role, OWNER, "postgres", True, False, False] for role in ROLES])
    require(sorted(state["memberships"] or []) == expected, "bootstrap membership changed or self-grant leaked")
    require(state["legacy_execute"] == [False, False] and state["public_subset_execute"] == 0,
            "runtime EXECUTE privilege drift")
    require(sorted(state["capability_execute"]) == grants, "capability EXECUTE matrix drift")
    require(state["direct_table_access"] == 0, "direct table/column privilege drift")
    require(state["checksum"] == checksum and state["new_rows"] == 0, "wrong migration result/checksum")


def expect_result(result, *, state=None, message=None):
    if state is None:
        require(result.returncode == 0, "command failed; outcome requires reconciliation")
    else:
        require(result.returncode != 0 and re.search(r"\bERROR:\s+" + state + r":", result.stderr),
                "negative case did not fail with the expected SQLSTATE")
        require(message is None or message in result.stderr, "wrong negative diagnostic")


class Runner:
    def __init__(self, root, evidence):
        self.root = root
        self.evidence = evidence
        self.events = []

    def command(self, label, argv, *, sql=None, extra_env=None, timeout=90):
        # Do not inherit PGOPTIONS, service/pass files, DATABASE_URL or caller auth.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "SYSTEMROOT")
               if key in os.environ}
        env.update(PGCONNECT_TIMEOUT="5", PGAPPNAME="fixed-subset-owner-regression",
                   PGOPTIONS="-c statement_timeout=60000 -c lock_timeout=5000",
                   PGPASSFILE="/dev/null", PGSYSCONFDIR="/nonexistent")
        env.update(extra_env or {})
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=self.root, env=env, start_new_session=True)
        try:
            stdout, stderr = process.communicate(sql, timeout=timeout)
        except BaseException as error:
            cleanup = []
            stdout = stderr = ""
            # Killing bash alone leaves its psql child running. Signal its entire
            # new POSIX session group, even if the group leader has already exited.
            for name, sig in (("SIGTERM", signal.SIGTERM), ("SIGKILL", 9)):
                try:
                    os.killpg(process.pid, sig)
                    cleanup.append({"signal": name, "result": "sent"})
                except ProcessLookupError:
                    cleanup.append({"signal": name, "result": "group_absent"})
                except BaseException as cleanup_error:
                    cleanup.append({"signal": name, "error": str(cleanup_error)})
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    cleanup.append({"wait": "timeout"})
                except BaseException as cleanup_error:
                    cleanup.append({"wait_error": str(cleanup_error)})
            timed_out = isinstance(error, subprocess.TimeoutExpired)
            self.events.append({"label": label, "timeout": timed_out,
                "error": type(error).__name__, "stdout": stdout,
                "stderr": stderr, "cleanup": cleanup, "leader_reaped": process.returncode is not None,
                "database_cleanup": "NOT_PROVEN_DESTROY_ISOLATED_SERVICE"})
            if timed_out:
                raise RegressionFailure("process timeout; reconcile outcome and destroy isolated service") from error
            raise
        result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        self.events.append({"label": label, "returncode": result.returncode,
                            "stdout": result.stdout, "stderr": result.stderr})
        return result

    def psql(self, label, sql, *, admin=False):
        return self.command(label, ["psql", ADMIN_DSN if admin else OWNER_DSN,
            "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose"], sql=sql)

    def read(self, label, sql, *, admin=False):
        result = self.psql(label, sql, admin=admin)
        expect_result(result)
        return json.loads(result.stdout)

    def migrate(self, label, directory):
        result = self.command(label, ["bash", str(self.root / "database/scripts/migrate.sh")],
            extra_env={"DATABASE_URL": OWNER_DSN, "MIGRATIONS_DIR": str(directory),
                       "EXTERNAL_HISTORY_FILE": str(self.root / "database/external_migration_history.tsv")},
            timeout=600)
        expect_result(result)
        return result

    def full_preflight(self, report):
        fixture_path = self.root / "database/tests/fixed_subset_admission/runtime_fixture.py"
        spec = importlib.util.spec_from_file_location("runtime_fixture", fixture_path)
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        paths = sorted((self.root / "database/tests").glob("*.sql"))
        require(len(paths) == 116, "SQL inventory changed; reconcile the full preflight")
        report["sql_inventory"] = {p.name: sha(p.read_bytes()) for p in paths}
        report["sql_results"] = [{"name": p.name, "status": "NOT_RUN"} for p in paths]
        expect_result(self.psql("create_synthetic_runtime_subjects", fixture.actor_setup_sql()))
        primary_error = None
        try:
            # Every file in the production glob runs, in the same order. A test
            # failure is recorded and does not silently omit later known tests.
            # Each psql session exits, rolling back its open transaction on error.
            for path, case in zip(paths, report["sql_results"]):
                result = self.command("full_sql_" + path.name,
                    ["psql", OWNER_DSN, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                     "-v", "VERBOSITY=verbose", "-f", str(path)],
                    extra_env={"FIXED_SUBSET_DISPOSABLE_PREFLIGHT": "1",
                        "FIXED_SUBSET_FIXTURE_EVIDENCE": str(self.evidence / "fixtures"),
                        "PYTHONPATH": str(self.root), "PYTHONDONTWRITEBYTECODE": "1"})
                case.update(status="PASS" if result.returncode == 0 else "FAIL",
                            returncode=result.returncode)
            failures = [c["name"] for c in report["sql_results"] if c["status"] != "PASS"]
            require(not failures, "Full preflight failed: " + ", ".join(failures))
            for number in ("042", "043"):
                evidence = json.loads((self.evidence / "fixtures" / (number+".json")).read_text())
                require(evidence["status"] == "PASS" and evidence["approval_cleanup"]["status"] == "PASS",
                        "runtime fixture evidence incomplete: " + number)
            # The unchanged 043 rollback/reapply intentionally clears the 0059
            # checksum. The normal production rerun reconciles that one marker.
            self.migrate("full_suite_migration_rerun_as_M", self.root / "database/migrations")
            require(self.read("full_suite_missing_checksums", "SELECT count(*) FROM schema_migration WHERE checksum IS NULL;") == 0,
                    "missing migration checksums after full suite")
            report["production_preflight"] = "PASS_DISPOSABLE_ONLY"
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup = self.psql("drop_synthetic_runtime_subjects", fixture.actor_cleanup_sql())
            report["synthetic_subject_cleanup"] = "PASS" if cleanup.returncode == 0 else "FAIL"
            if primary_error is None:
                expect_result(cleanup)
            elif cleanup.returncode != 0:
                primary_error.add_note("Synthetic role cleanup failed; destroy disposable service")

    def execute(self, *, full_preflight=False):
        source = (self.root / "database/migrations" / MIGRATION).read_bytes()
        cases = negative_cases(source.decode("utf-8"))
        migrations = self.root / "database/migrations"
        inventory = {str(p.relative_to(migrations)): sha(p.read_bytes())
                     for p in sorted(migrations.rglob("*.sql"))}
        report = {"status": "FAIL_OR_UNKNOWN", "sql_execution": True,
                  "migration_sha256": sha(source), "migration_inventory": inventory,
                  "invariant_041": "NOT_RUN",
                  "production_preflight": "NOT_RUN_FULL_SUITE"}
        self.evidence.mkdir(parents=True, exist_ok=False)
        primary_error = None
        try:
            check_server(self.read("fresh_admin", SERVER_SQL, admin=True), admin=True)
            expect_result(self.psql("bootstrap_only", BOOTSTRAP_SQL, admin=True))
            check_server(self.read("actual_migration_login", SERVER_SQL))
            # Use the real migrator, retaining the composite 0058 parts and
            # complete history through 0059, not the old minimal admin fixtures.
            with tempfile.TemporaryDirectory(prefix="subset-owner-") as temp:
                prechain = Path(temp) / "migrations"
                prechain.mkdir()
                for path in migrations.glob("*.sql"):
                    if path.name[:4].isdigit() and path.name[:4] <= "0059":
                        shutil.copy2(path, prechain / path.name)
                        parts = path.with_suffix("")
                        if parts.is_dir():
                            shutil.copytree(parts, prechain / parts.name)
                self.migrate("history_through_0059_as_M", prechain)
            invariant = (self.root / "database/tests/041_universal_video_queue.sql").read_text()
            expect_result(self.psql("041_before_cutover", invariant))
            check_pre(self.read("owner_preconditions", OWNERS_SQL))
            before = self.read("before_negatives", SNAPSHOT_SQL, admin=True)
            report["negative_cases"] = []
            for name, sql, state, message in cases:
                result = self.psql(name, sql)
                expect_result(result, state=state, message=message)
                after = self.read(name + "_rollback", SNAPSHOT_SQL, admin=True)
                require(after == before, "rollback changed catalog/ACL/rows: " + name)
                report["negative_cases"].append({"name": name, "status": "PASS"})
            # This deliberately reproduces the complete staged migration set,
            # including migrations after 0060. It is not a 0060-only simulation.
            self.migrate("complete_inventory_as_M", migrations)
            check_post(self.read("post_commit", POST_SQL, admin=True), sha(source), source.decode())
            committed = self.read("before_rerun", SNAPSHOT_SQL, admin=True)
            rerun = self.migrate("idempotence_as_M", migrations)
            require("Applying migration " not in rerun.stdout and
                    "Bootstrapping checksum" not in rerun.stdout, "rerun did not only skip")
            require(self.read("after_rerun", SNAPSHOT_SQL, admin=True) == committed,
                    "idempotent migrator changed catalog/ACL/rows")
            expect_result(self.psql("041_after_cutover", invariant))
            require(self.read("041_read_only", SNAPSHOT_SQL, admin=True) == committed,
                    "041 left catalog/ACL/row changes")
            report["invariant_041"] = "PASS_BEFORE_AND_AFTER_CUTOVER"
            report["acl_negative_cases"] = []
            for name, mutation in [
                ("legacy_write_overgrant", "GRANT EXECUTE ON FUNCTION video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb) TO bridge_school_app;"),
                ("required_read_missing", "REVOKE EXECUTE ON FUNCTION video_queue.precanary_idle_snapshot() FROM bridge_school_worker;"),
            ]:
                negative = once(invariant, "-- MANAGED ACL MUTATION ANCHOR (regression fixtures only)", mutation)
                expect_result(self.psql(name, negative), state="P0001", message="SUBSET_RUNTIME_EXECUTE_MATRIX")
                require(self.read(name + "_rollback", SNAPSHOT_SQL, admin=True) == committed,
                        "ACL negative did not roll back: " + name)
                report["acl_negative_cases"].append({"name": name, "status": "PASS"})
            if full_preflight:
                shared_acl = (self.root / "database/tests/043_universal_video_terminal_size_guard.sql").read_text(encoding="utf-8").split("SELECT EXISTS", 1)[0]
                report["acl_043_negative_cases"] = []
                for name, mutation, message in [
                    ("043_legacy_enqueue", "GRANT EXECUTE ON FUNCTION video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb) TO bridge_school_app;", "fixed-subset queue capabilities mismatch"),
                    ("043_legacy_finish", "GRANT EXECUTE ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) TO bridge_school_worker;", "fixed-subset queue capabilities mismatch"),
                    ("043_direct_worker_write", "GRANT UPDATE ON video_queue.job TO bridge_school_worker;", "worker has forbidden direct job mutation"),
                ]:
                    expect_result(self.psql(name, "BEGIN;\n" + mutation + "\n" + shared_acl + "ROLLBACK;\n"),
                                  state="P0001", message=message)
                    require(self.read(name+"_rollback", SNAPSHOT_SQL, admin=True) == committed,
                            "043 ACL negative changed catalog/rows: " + name)
                    report["acl_043_negative_cases"].append({"name": name, "status": "PASS"})
                self.full_preflight(report)
            report["status"] = "PASS_FULL_ISOLATED_PREFLIGHT" if full_preflight else "PASS_OWNER_INSTALL_ONLY"
            return report
        except BaseException as error:
            primary_error = error
            report["error"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            report["events"] = self.events
            try:
                (self.evidence / "results.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError as evidence_error:
                if primary_error is None:
                    raise
                primary_error.add_note("Secondary evidence write failure: " + str(evidence_error))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--full-preflight", action="store_true")
    parser.add_argument("--evidence", type=Path, default=Path("managed-owner-evidence"))
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "NOT_RUN", "sql_execution": False,
            "server_required": "PostgreSQL 18; bridge_ci_owner NOSUPERUSER CREATEROLE",
            "cases": [case[0] for case in negative_cases(
                (ROOT / "database/migrations" / MIGRATION).read_text(encoding="utf-8"))],
            "production_preflight": "NOT_RUN_FULL_SUITE"}))
        return 0
    require(os.name == "posix", "future runner requires isolated POSIX CI")
    report = Runner(ROOT, args.evidence.resolve()).execute(full_preflight=args.full_preflight)
    print(json.dumps({key: report[key] for key in ("status", "production_preflight")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
