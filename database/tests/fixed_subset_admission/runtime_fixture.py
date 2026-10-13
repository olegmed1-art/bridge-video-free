"""Synthetic 042/043 fixtures for the disposable PG18 admission API preflight.

No DSN input, remote metadata, media, or credentials. Import and default CLI are
offline. Only the one-shot preflight provisions these four local login subjects.
Committed admission history is revoked, never deleted; destroy the container.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from universal_video.batch_plan import build_batch_plan
from universal_video.subset_contract import build_subset_contract

DATABASE = "bridge_school_preflight"
OWNER = "bridge_ci_owner"
ACTORS = {
    "approver": ("subset_ci_approver", "video_subset_approver"),
    "collector": ("subset_ci_collector", "video_subset_collector"),
    "admitter": ("subset_ci_admitter", "video_subset_admitter"),
    "worker": ("subset_ci_worker", "video_subset_worker_v2"),
}
PROFILE, REVISION = "bridge_3_1_free", "3.1-free-r25.16"
SIZE_CASES = ((536870912, 8388608, True), (1, 1, True),
              (536870913, 1024, False), (2048, 8388609, False),
              (1.5, 1024, False), (2048, 1.5, False))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def actor_setup_sql():
    return "BEGIN;\n" + "\n".join(
        f"CREATE ROLE {login} LOGIN PASSWORD '{login}' NOSUPERUSER NOCREATEDB "
        f"NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n"
        f"GRANT {capability} TO {login} WITH ADMIN FALSE, INHERIT TRUE, SET FALSE "
        "GRANTED BY CURRENT_USER;" for login, capability in ACTORS.values()) + "\nCOMMIT;"


def actor_cleanup_sql():
    return "BEGIN;\n" + "\n".join(f"DROP ROLE {login};" for login, _ in ACTORS.values()) + "\nCOMMIT;"


def raw_sources(label, count):
    rows = [{"id": f"synthetic-{label}-video-{i:03}", "name": f"Lesson {i}.mp4",
             "parents": [f"synthetic-{label}-source"], "mimeType": "video/mp4",
             "size": str(i * 1000000 + 1000000), "version": "17",
             "modifiedTime": "2026-10-10T10:00:00Z", "trashed": False,
             "md5Checksum": None} for i in range(1, count + 1)]
    rows[0]["md5Checksum"] = "1" * 32
    if label == "043":
        rows[1].update(name="Lesson 2.avi", mimeType="video/x-msvideo")
        rows[2].update(name="Lesson 3.mkv", mimeType="video/x-matroska", sha256Checksum="3" * 64)
    return rows


def contract_for(rows, canary_index=0):
    source = rows[0]["parents"][0]
    plan = build_batch_plan(rows, source_folder_id=source,
                           output_folder_id=source + "-output", work_folder_id=source + "-work")
    return build_subset_contract(plan, expected_plan_sha256=plan["plan_sha256"],
        subset_sequence=1, processing_profile=PROFILE, algorithm_revision=REVISION,
        canary_source_file_id=rows[canary_index]["id"])


def rehash(output):
    output["artifact_manifest_sha256"] = digest(output["artifact_manifest"])
    receipt = output["terminal_receipt"]
    receipt["artifact_manifest_sha256"] = output["artifact_manifest_sha256"]
    receipt.pop("evidence_sha256", None)
    evidence = digest(receipt)
    receipt["evidence_sha256"] = output["terminal_evidence_sha256"] = evidence
    return output


def terminal_output(claim, pdf_size=2048, ai_size=1024):
    flags = dict(canonical_promotion_allowed=False, database_persistence_allowed=False,
                 publication_state="NOT_PUBLISHED")
    source = dict(file_id=claim["source_file_id"], name=claim["source_name"],
                  mime_type=claim["source_mime_type"], size_bytes=claim["source_size_bytes"],
                  parent_folder_id=claim["source_folder_id"], checksum=claim["source_checksum"])
    artifacts = []
    for kind, drive_id, name, mime, size, sha, version in (
        ("master_pdf", "synthetic-result-pdf", "result.pdf", "application/pdf", pdf_size, "b"*64, "101"),
        ("ai_done", "synthetic-result-ai-done", "AI_DONE_"+claim["stable_job_key"]+".json",
         "application/json", ai_size, "c"*64, "102")):
        artifacts.append(dict(kind=kind, locator="gdrive:file:"+drive_id, drive_id=drive_id,
            name=name, mime_type=mime, size_bytes=size, parent_id=claim["output_folder_id"],
            modified_time="2026-09-03T00:00:00Z", version=version, sha256=sha))
    manifest = dict(schema_version="universal-video-artifact-manifest/v1",
        job_id=claim["stable_job_key"], source_file_id=claim["source_file_id"],
        algorithm_revision=claim["algorithm_revision"], result_mode="SHADOW_REVIEW_ONLY",
        source_identity=source, artifacts=artifacts, **flags)
    receipt = dict(schema_version="universal-video-terminal-receipt/v1", status="PASS",
        job_id=claim["stable_job_key"], source_file_id=claim["source_file_id"],
        source_identity_verified=True, drive_readback_verified=True,
        result_readback_verified=True, checksum_verified=True, artifact_count=2, **flags)
    return rehash(dict(result_mode="SHADOW_REVIEW_ONLY", source_file_id=claim["source_file_id"],
        stable_job_key=claim["stable_job_key"], algorithm_revision=claim["algorithm_revision"],
        master_pdf_drive_id=artifacts[0]["drive_id"], master_pdf_sha256="b"*64,
        ai_done_drive_id=artifacts[1]["drive_id"], ai_done_sha256="c"*64,
        artifact_locators={x["kind"]: x["drive_id"] for x in artifacts},
        artifact_manifest=manifest, terminal_receipt=receipt, **flags))


def invalid_outputs(valid):
    for key in ("publication_state", "artifact_locators", "terminal_receipt",
                "terminal_evidence_sha256", "ai_done_drive_id"):
        for missing in (True, False):
            bad = deepcopy(valid)
            if missing:
                del bad[key]
            else:
                bad[key] = None
            yield ("missing-" if missing else "null-") + key, bad, "P0001", "VIDEO_QUEUE_"
    for path, value in (
        (("artifact_locators",), []), (("artifact_manifest", "source_identity"), []),
        (("artifact_manifest", "source_identity", "size_bytes"), "12339062"),
        (("artifact_manifest", "artifacts"), {"bad": True}),
        (("artifact_manifest", "artifacts", 0, "size_bytes"), "2048"),
        (("artifact_manifest", "artifacts", 1, "size_bytes"), False),
        (("terminal_receipt", "artifact_count"), "2"),
        (("terminal_receipt", "drive_readback_verified"), "true")):
        bad = deepcopy(valid)
        target = bad
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        yield "wrong-type-" + ".".join(map(str, path)), bad, "P0001", "VIDEO_QUEUE_RESULT_CONTRACT_INVALID"
    for name in ("source-name", "duplicate-kind", "missing-revision", "forged-sha"):
        bad = deepcopy(valid)
        if name == "source-name":
            bad["artifact_manifest"]["source_identity"]["name"] = "renamed.mp4"
        elif name == "duplicate-kind":
            bad["artifact_manifest"]["artifacts"][1]["kind"] = "master_pdf"
        elif name == "missing-revision":
            del bad["artifact_manifest"]["artifacts"][0]["modified_time"]
        rehash(bad)
        if name == "forged-sha":
            bad["terminal_evidence_sha256"] = "d" * 64
        yield name, bad, "23514" if name == "missing-revision" else "P0001", (
            None if name == "missing-revision" else "VIDEO_QUEUE_RESULT_CONTRACT_INVALID")


def check_login(row, login, capability=None):
    require(row["session_user"] == row["current_user"] == login, "wrong actual login")
    require(row["database"] == DATABASE and 180000 <= row["version"] < 190000, "wrong database/server")
    require(row["rolsuper"] is row["rolcreatedb"] is row["rolreplication"] is row["rolbypassrls"] is False,
            "excess login authority")
    require(row["rolcreaterole"] is (login == OWNER), "unexpected CREATEROLE")
    if capability:
        require(row["capabilities"] == [capability], "unexpected capability membership")
        require(row["definer_member"] is False and row["definer_set"] is False,
                "runtime login can become definer")
        require(row["table_access"] == 0, "runtime direct table/column access")


LOGIN_SQL = """
SELECT session_user::text, current_user::text, current_database() AS database,
 current_setting('server_version_num')::int AS version,
 rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,
 pg_has_role(session_user,'video_subset_owner','MEMBER') AS definer_member,
 pg_has_role(session_user,'video_subset_owner','SET') AS definer_set,
 (SELECT coalesce(jsonb_agg(rolname ORDER BY rolname),'[]') FROM pg_roles
  WHERE rolname IN ('video_subset_approver','video_subset_collector','video_subset_admitter','video_subset_worker_v2')
    AND pg_has_role(session_user,oid,'MEMBER')) AS capabilities,
 (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='video_queue' AND c.relkind IN ('r','p')
    AND (has_table_privilege(session_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      OR has_any_column_privilege(session_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))) AS table_access
FROM pg_roles WHERE rolname=session_user
"""


class Fixture:
    def __init__(self, connections, report):
        self.c = connections
        self.report = report
        self.approvals = []

    def passed(self, name, **evidence):
        self.report["cases"].append(dict(name=name, status="PASS", **evidence))

    def one(self, actor, sql, params=()):
        return self.c[actor].execute(sql, params).fetchone()

    def scalar(self, actor, sql, params=()):
        return next(iter(self.one(actor, sql, params).values()))

    def reject(self, name, actor, call, state, message=None, constraint=None):
        try:
            with self.c[actor].transaction():
                call()
                raise AssertionError("unexpected acceptance: " + name)
        except psycopg.Error as error:
            require(error.sqlstate == state, f"{name}: wrong SQLSTATE {error.sqlstate}: {error}")
            require(message is None or (error.diag.message_primary.startswith(message)
                    and (message != "VIDEO_QUEUE_" or error.diag.message_primary.endswith("INVALID"))),
                    f"{name}: wrong diagnostic {error}")
            require(constraint is None or error.diag.constraint_name == constraint,
                    f"{name}: wrong constraint {error.diag.constraint_name}")
            self.passed(name, sqlstate=error.sqlstate, message=error.diag.message_primary,
                        constraint=error.diag.constraint_name)

    def denied_authority(self):
        for actor in ACTORS:
            self.reject(actor + "-direct-job-read", actor,
                lambda a=actor: self.c[a].execute("SELECT * FROM video_queue.job LIMIT 0"), "42501")
            self.reject(actor + "-legacy-finish", actor,
                lambda a=actor: self.c[a].execute("SELECT * FROM video_queue.finish_job(NULL,NULL,NULL,NULL,NULL,NULL)"), "42501")
        self.reject("worker-cannot-approve", "worker", lambda: self.c["worker"].execute(
            "SELECT video_queue.approve_subset(NULL,NULL,NULL,NULL)"), "42501")
        self.reject("admitter-cannot-claim", "admitter", lambda: self.c["admitter"].execute(
            "SELECT * FROM video_queue.claim_job('forbidden',900,%s,%s)", (PROFILE, REVISION)), "42501")

    def prepare(self, label, count, canary_index=0):
        raw = raw_sources(label, count)
        contract = contract_for(raw, canary_index)
        aid = uuid4()
        self.scalar("approver", "SELECT video_queue.approve_subset(%s,%s,%s,clock_timestamp()+interval '15 minutes')",
            (aid, canonical(contract), Jsonb(dict(approved_by=ACTORS["approver"][0],
             approval_reference="synthetic-disposable-"+label, plan_sha256=contract["plan_sha256"]))))
        self.approvals.append(aid)
        oid = uuid4()
        self.scalar("collector", "SELECT video_queue.observe_subset(%s,%s,'ADMISSION',%s,clock_timestamp(),%s)",
                    (oid, aid, Jsonb(raw), uuid4()))
        args = (aid, contract["enqueue_manifest"]["request_key"], contract["contract_sha256"],
                contract["queue_manifest_sha256"], oid)
        sql = "SELECT video_queue.admit_approved_subset(%s,%s,%s,%s,%s)"
        first = self.scalar("admitter", sql, args)
        again = self.scalar("admitter", sql, args)
        require(first["binding"] == again["binding"], "duplicate binding")
        batch = first["binding"]["batch_id"]
        shape = self.one("owner", "SELECT count(*) AS total,count(*) FILTER (WHERE status='QUEUED' AND is_canary) AS canary,count(*) FILTER (WHERE status='PENDING_CANARY') AS pending FROM video_queue.job WHERE batch_id=%s", (batch,))
        require(shape == dict(total=count, canary=1, pending=count-1), "duplicate/canary-first queue shape")
        self.passed(label+"-idempotent-admission-shape", batch_id=batch, shape=shape)
        if label == "043":
            bad = list(args)
            bad[2] = "f" * 64
            self.reject("043-conflicting-request-digest", "admitter", lambda: self.scalar("admitter", sql, bad),
                        "P0001", "SUBSET_DIGEST_MISMATCH_HOLD")
        self.scalar("approver", "SELECT video_queue.set_subset_runtime(%s,true)", (aid,))
        return dict(approval=aid, raw=raw, batch=batch, canary=raw[canary_index]["id"])

    def claim(self, fixture):
        claim = self.one("worker", "SELECT * FROM video_queue.claim_job(%s,900,%s,%s)",
                         (ACTORS["worker"][0], PROFILE, REVISION))
        require(claim and claim["is_canary"] and claim["source_file_id"] == fixture["canary"]
                and str(claim["batch_id"]) == fixture["batch"], "wrong first claim")
        return claim

    def observe(self, fixture, claim):
        oid = uuid4()
        raw = [row for row in fixture["raw"] if row["id"] == claim["source_file_id"]]
        self.scalar("collector", "SELECT video_queue.observe_subset(%s,%s,'ATTEMPT',%s,clock_timestamp(),NULL,%s,%s,%s,%s)",
            (oid, fixture["approval"], Jsonb(raw), claim["job_id"], claim["lease_token"],
             claim["attempt_count"], ACTORS["worker"][0]))
        return oid

    def prework(self, fixture, claim):
        self.scalar("worker", "SELECT video_queue.authorize_subset_attempt(%s,%s,%s,%s)",
                    (claim["job_id"], claim["lease_token"], ACTORS["worker"][0], self.observe(fixture, claim)))

    def finish(self, claim, output, observation, token=None):
        return self.one("worker", "SELECT * FROM video_queue.finish_subset_job(%s,%s,%s,'REVIEW_READY',%s,%s,NULL)",
            (claim["job_id"], token or claim["lease_token"], ACTORS["worker"][0], Jsonb(output), observation))

    def snapshot(self, fixture):
        return self.scalar("owner", """SELECT jsonb_build_object(
          'jobs',(SELECT jsonb_agg(to_jsonb(j) ORDER BY job_id) FROM video_queue.job j WHERE batch_id=%s),
          'batch',(SELECT to_jsonb(b) FROM video_queue.batch b WHERE batch_id=%s),
          'events',(SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY event_id),'[]') FROM video_queue.job_event e WHERE batch_id=%s))""",
          (fixture["batch"],)*3)

    def scenario042(self):
        require(self.scalar("owner", "SELECT video_queue.canonical_json_text(%s::jsonb)",
                ('{"z":"?","a":[true,1,null]}',)) == '{"a":[true,1,null],"z":"?"}', "canonical JSON mismatch")
        require(self.scalar("owner", "SELECT has_function_privilege('bridge_school_worker','video_queue.precanary_idle_snapshot()','EXECUTE')"), "idle capability missing")
        require(self.scalar("owner", "SELECT claimable_jobs FROM video_queue.precanary_idle_snapshot()") == 0, "empty idle mismatch")
        self.passed("042-canonical-idle")
        fixture = self.prepare("042", 2)
        claim = self.claim(fixture)
        self.passed("042-first-claim-canary")
        self.prework(fixture, claim)
        valid = terminal_output(claim)
        for name, bad, state, message in invalid_outputs(valid):
            observation = self.observe(fixture, claim)
            before = self.snapshot(fixture)
            self.reject("042-"+name, "worker", lambda: self.finish(claim, bad, observation), state, message)
            require(self.snapshot(fixture) == before, "invalid result changed rows: " + name)
        result = self.finish(claim, valid, self.observe(fixture, claim))
        require(result["batch_status"] == "CANARY_REVIEW" and result["released_jobs"] == 0, "automatic release")
        require(self.scalar("owner", "SELECT count(*) FROM video_queue.job WHERE batch_id=%s AND status='PENDING_CANARY'", (fixture["batch"],)) == 1, "pending released")
        self.passed("042-canary-review-no-release")
        self.reject("042-old-heartbeat-fence", "worker", lambda: self.scalar("worker",
            "SELECT video_queue.heartbeat_subset_job(%s,%s,%s,900)",
            (claim["job_id"], claim["lease_token"], ACTORS["worker"][0])), "P0001", "SUBSET_LEASE_LOST")
        retry = self.prepare("042-retry", 1)
        c1 = self.claim(retry)
        retry_sql = "SELECT * FROM video_queue.retry_subset_job(%s,%s,%s,'UV_TEST_FAILURE',1)"
        self.reject("042-wrong-retry-fence", "worker", lambda: self.one("worker", retry_sql,
            (c1["job_id"], uuid4(), ACTORS["worker"][0])), "P0001", "SUBSET_LEASE_LOST")
        result = self.one("worker", retry_sql, (c1["job_id"], c1["lease_token"], ACTORS["worker"][0]))
        require(result["job_status"] == "QUEUED", "retry false PASS")
        self.c["owner"].execute("UPDATE video_queue.job SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (c1["job_id"],))
        c2 = self.claim(retry)
        require(c2["lease_token"] != c1["lease_token"] and c2["attempt_count"] == 2, "retry fence unchanged")
        self.c["owner"].execute("UPDATE video_queue.job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (c2["job_id"],))
        c3 = self.claim(retry)
        require(c3["lease_token"] != c2["lease_token"] and c3["attempt_count"] == 3, "stale lease reclaim failed")
        self.c["owner"].execute("UPDATE video_queue.job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (c3["job_id"],))
        require(self.one("worker", "SELECT * FROM video_queue.claim_job(%s,900,%s,%s)",
                (ACTORS["worker"][0], PROFILE, REVISION)) is None, "exhausted job claimed")
        require(self.one("owner", "SELECT status,error_code FROM video_queue.job WHERE job_id=%s", (c3["job_id"],)) ==
                dict(status="FAILED", error_code="UV_WORKER_CRASH_RETRY_EXHAUSTED"), "interrupted worker false PASS")
        self.passed("042-retry-rotation-reclaim-exhaustion")
        bad_fixture = self.prepare("042-bad", 1)
        bad_claim = self.claim(bad_fixture)
        self.prework(bad_fixture, bad_claim)
        observation = self.observe(bad_fixture, bad_claim)
        incomplete = {k: v for k, v in terminal_output(bad_claim).items() if k in (
            "result_mode", "canonical_promotion_allowed", "database_persistence_allowed", "publication_state",
            "source_file_id", "stable_job_key", "algorithm_revision")}
        self.reject("042-incomplete-result", "worker", lambda: self.finish(bad_claim, incomplete, observation),
                    "P0001", "VIDEO_QUEUE_RESULT_CONTRACT_INVALID")
        require(self.scalar("owner", "SELECT count(*) FROM video_queue.job WHERE job_id=%s AND status='REVIEW_READY'", (bad_claim["job_id"],)) == 0, "false REVIEW_READY")

    def scenario043(self):
        fixture = self.prepare("043", 3, 1)
        claim = self.claim(fixture)
        self.passed("043-first-claim-canary")
        self.prework(fixture, claim)
        self.reject("043-wrong-finish-fence", "worker", lambda: self.finish(claim, {}, uuid4(), token=uuid4()),
                    "P0001", "SUBSET_LEASE_LOST")
        for pdf_size, ai_size, allowed in SIZE_CASES:
            output = terminal_output(claim, pdf_size, ai_size)
            observation = self.observe(fixture, claim)
            before = self.snapshot(fixture)
            name = f"043-size-{pdf_size}-{ai_size}"
            if allowed:
                with self.c["worker"].transaction():
                    result = self.finish(claim, output, observation)
                    require(result["job_status"] == "REVIEW_READY", "valid boundary not accepted")
                    raise psycopg.Rollback()
                self.passed(name, accepted_then_rolled_back=True)
            else:
                self.reject(name, "worker", lambda: self.finish(claim, output, observation),
                            "23514", constraint="video_job_terminal_size_check")
            require(self.snapshot(fixture) == before, "size attempt not atomic: " + name)

    def cleanup(self):
        errors = []
        for aid in self.approvals:
            try:
                self.scalar("approver", "SELECT video_queue.revoke_subset_approval(%s)", (aid,))
            except Exception as error:
                errors.append(str(error))
        self.report["approval_cleanup"] = dict(status="FAIL" if errors else "PASS", errors=errors,
                                              revoked_count=len(self.approvals)-len(errors))
        require(not errors, "approval cleanup failed: " + repr(errors))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("042", "043"), required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        print(canonical(dict(status="NOT_RUN", scenario=args.scenario, sql_execution=False)))
        return 0
    require(os.name == "posix" and os.environ.get("FIXED_SUBSET_DISPOSABLE_PREFLIGHT") == "1",
            "requires explicitly provisioned disposable POSIX preflight")
    evidence = Path(os.environ["FIXED_SUBSET_FIXTURE_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=True)
    report = dict(status="FAIL_OR_UNKNOWN", scenario=args.scenario, cases=[], logins={})
    primary_error = None
    try:
        with ExitStack() as stack:
            connections = {}
            for actor, (login, capability) in {"owner": (OWNER, None), **ACTORS}.items():
                conn = stack.enter_context(psycopg.connect(
                    host="127.0.0.1", port=5432, dbname=DATABASE, user=login, password=login,
                    connect_timeout=5, application_name="subset-disposable-"+args.scenario,
                    options="-c statement_timeout=60000 -c lock_timeout=5000",
                    autocommit=True, row_factory=dict_row))
                state = conn.execute(LOGIN_SQL).fetchone()
                check_login(state, login, capability)
                report["logins"][actor] = state
                connections[actor] = conn
            fixture = Fixture(connections, report)
            try:
                fixture.denied_authority()
                getattr(fixture, "scenario"+args.scenario)()
            except BaseException as error:
                primary_error = error
                raise
            finally:
                try:
                    fixture.cleanup()
                except Exception as cleanup_error:
                    if primary_error is None:
                        raise
                    primary_error.add_note("Cleanup: " + str(cleanup_error))
        report["status"] = "PASS"
    except BaseException as error:
        primary_error = error
        report["error"] = dict(type=type(error).__name__, message=str(error),
            sqlstate=getattr(error, "sqlstate", None), notes=getattr(error, "__notes__", []))
        raise
    finally:
        try:
            (evidence / (args.scenario+".json")).write_text(json.dumps(report, indent=2, default=str)+"\n", encoding="utf-8")
        except OSError as error:
            if primary_error is None:
                raise
            primary_error.add_note("Evidence: " + str(error))
    print(canonical(dict(status=report["status"], scenario=args.scenario, cases=len(report["cases"]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
