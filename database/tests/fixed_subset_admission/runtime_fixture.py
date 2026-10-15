"""Installed 0060 fail-closed canary contract on the disposable database CI host.

This fixture accepts only the fixed localhost CI identity. Every temporary role
grant and attempted queue write is rolled back, then catalog/data are rechecked.
"""

import argparse
import json
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


EXPECTED_0060 = "cfd5bf6088761dcc1d38a4d0febb6fb24f3d4f396fd67ca08568ff44994c44bc"
CLAIM = "video_queue.subset_claim_job(uuid,text,integer,text,text)"
PROFILE = "bridge_3_1_free"
REVISION = "3.1-free-r25.16"


def snapshot(db):
    # The migration owner retains ADMIN-only membership after installation,
    # not SELECT on the subset-owned reservation table. A savepoint lends
    # read access only for this snapshot and restores the exact role catalog.
    db.execute("SAVEPOINT subset_fixture_snapshot")
    try:
        db.execute("""GRANT video_subset_owner TO CURRENT_USER
          WITH ADMIN FALSE, INHERIT TRUE, SET FALSE GRANTED BY CURRENT_USER""")
        value = db.execute("""SELECT jsonb_build_object(
      'batch',(SELECT count(*) FROM video_queue.batch),
      'job',(SELECT count(*) FROM video_queue.job),
      'events',(SELECT count(*) FROM video_queue.job_event),
      'reservations',(SELECT count(*) FROM video_queue.source_reservation),
      'event_sequence',(SELECT jsonb_build_array(last_value,is_called)
                          FROM video_queue.job_event_event_id_seq),
      'worker_membership',(SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.oid),'[]'::jsonb)
                           FROM pg_auth_members a
                           WHERE a.roleid='video_subset_worker_v2'::regrole
                             AND a.member=current_user::regrole))""").fetchone()[0]
    finally:
        db.execute("ROLLBACK TO SAVEPOINT subset_fixture_snapshot")
        db.execute("RELEASE SAVEPOINT subset_fixture_snapshot")
    assert not db.execute("""SELECT pg_has_role(current_user,
      'video_subset_owner','USAGE')""").fetchone()[0]
    return value


def expected_error(db, sql, params, message):
    try:
        with db.transaction():
            db.execute(sql, params).fetchall()
    except psycopg.Error as exc:
        assert exc.sqlstate == "P0001" and str(exc).splitlines()[0] == message, str(exc)
    else:
        raise AssertionError(f"expected {message}")


def test_terminal_size_guard(db):
    row = db.execute("""SELECT pg_get_constraintdef(oid), convalidated
      FROM pg_constraint WHERE conrelid='video_queue.job'::regclass
        AND conname='video_job_terminal_size_check'""").fetchone()
    assert row is not None and row[1] is False, "0059 size guard missing or validated"
    definition = row[0].removesuffix(" NOT VALID")
    assert definition.startswith("CHECK (") and ";" not in definition

    # Copy the installed catalog predicate, rather than mirroring its Python
    # logic. A temporary relation avoids the admission and lease APIs while
    # proving that PostgreSQL enforces the actual 0059 boundary expression.
    db.execute("""CREATE TEMP TABLE subset_fixture_terminal_guard
      (status text NOT NULL, output jsonb NOT NULL) ON COMMIT DROP""")
    db.execute(sql.SQL("""ALTER TABLE pg_temp.subset_fixture_terminal_guard
      ADD CONSTRAINT video_job_terminal_size_check {}""").format(sql.SQL(definition)))

    cases = (
        (1, 1, True), (536870912, 8388608, True),
        (0, 1024, False), (536870913, 1024, False),
        (2048, 0, False), (2048, 8388609, False),
        (1.5, 1024, False), (2048, 1.5, False),
        (None, 1024, False), (2048, "1024", False),
    )
    for pdf_size, ai_size, allowed in cases:
        output = json.dumps({"artifact_manifest": {"artifacts": [
            {"size_bytes": pdf_size}, {"size_bytes": ai_size}]}})
        db.execute("SAVEPOINT subset_fixture_size_case")
        try:
            try:
                db.execute("""INSERT INTO pg_temp.subset_fixture_terminal_guard
                  (status, output) VALUES ('REVIEW_READY',%s::jsonb)""", (output,))
                assert allowed, f"invalid terminal sizes accepted: {pdf_size}, {ai_size}"
            except psycopg.errors.CheckViolation as exc:
                assert not allowed and exc.diag.constraint_name == "video_job_terminal_size_check", str(exc)
        finally:
            db.execute("ROLLBACK TO SAVEPOINT subset_fixture_size_case")
            db.execute("RELEASE SAVEPOINT subset_fixture_size_case")

    # The size predicate is scoped to REVIEW_READY; unrelated terminal states
    # retain their earlier behavior under the same installed constraint.
    db.execute("""INSERT INTO pg_temp.subset_fixture_terminal_guard
      (status, output) VALUES ('FAILED','{}'::jsonb)""")
    assert db.execute("SELECT count(*) FROM pg_temp.subset_fixture_terminal_guard").fetchone()[0] == 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=("042", "043"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("explicit --execute required for disposable CI fixture")

    raw = os.environ.get("DATABASE_URL", "")
    info = conninfo_to_dict(raw) if raw else {}
    if (info.get("host") not in ("localhost", "127.0.0.1")
        or info.get("port") != "5432"
        or info.get("dbname") != "bridge_school_ci"
        or info.get("user") != "bridge_ci_owner"
        or not info.get("password")
        or info.get("hostaddr") or info.get("service")):
        raise SystemExit("refusing non-local database identity")

    db = psycopg.connect(host="127.0.0.1", hostaddr="127.0.0.1", port=5432,
                         dbname="bridge_school_ci", user="bridge_ci_owner",
                         password=info["password"], sslmode="disable",
                         connect_timeout=5,
                         options="-c statement_timeout=5000 -c lock_timeout=2000")
    try:
        version, identity, checksum = db.execute("""SELECT
          current_setting('server_version_num')::integer, current_user,
          (SELECT checksum FROM schema_migration
           WHERE migration_key='0060_fixed_subset_admission')""").fetchone()
        assert 180000 <= version < 190000 and identity == "bridge_ci_owner"
        assert checksum == EXPECTED_0060
        before = snapshot(db)

        if args.scenario == "043":
            test_terminal_size_guard(db)
            db.rollback()
            assert snapshot(db) == before, "043 fixture changed queue data or roles"
            db.rollback()
            print(json.dumps({"scenario": "043", "status": "PASS_INSTALLED_SIZE_GUARD",
                              "catalog_constraint": "video_job_terminal_size_check",
                              "size_cases": 10, "exact_snapshot_equal": True}))
            return

        # CI owner created the NOLOGIN roles but has no inherited runtime right.
        # Mirror the connector's transient, non-SET membership within a rollback.
        db.execute("""GRANT video_subset_worker_v2 TO CURRENT_USER
          WITH ADMIN FALSE, INHERIT TRUE, SET FALSE GRANTED BY CURRENT_USER""")
        assert db.execute("SELECT has_function_privilege(current_user,%s,'EXECUTE')",
                          (CLAIM,)).fetchone()[0]
        assert db.execute("SELECT count(*) FROM video_queue.subset_claim_job(%s,%s,%s,%s,%s)",
                          (uuid.uuid4(), "ci-issue881-worker", 900, PROFILE, REVISION)).fetchone()[0] == 0
        expected_error(db,
            "SELECT * FROM video_queue.subset_claim_job(NULL,%s,%s,%s,%s)",
            ("ci-issue881-worker", 900, PROFILE, REVISION), "SUBSET_BATCH_REQUIRED")

        # A legacy enqueue cannot manufacture a canary outside an approved
        # fixed-subset admission, even when invoked by the migration owner.
        source = "ci-issue881-" + uuid.uuid4().hex
        expected_error(db, """SELECT * FROM video_queue.enqueue_drive_batch(
          %s,'source-folder-123456','output-folder-123456','work-folder-123456',
          %s,%s,%s,repeat('a',64),
          jsonb_build_array(jsonb_build_object('sequence',1,'file_id',%s::text,
            'name','canary.mp4','mime_type','video/mp4','size_bytes',12339062,
            'checksum','md5:'||repeat('1',32))))""",
            (source, PROFILE, REVISION, source, source), "SUBSET_LEGACY_INSERT_HOLD")
        db.rollback()
        assert snapshot(db) == before, "042 fixture leaked rows, sequence, or role membership"
        db.rollback()
    finally:
        db.close()
    print(json.dumps({"scenario": "042", "status": "PASS_INSTALLED_CANARY_HOLD",
                      "scoped_claim_without_admission": 0,
                      "null_batch_rejected": True, "legacy_enqueue_rejected": True,
                      "exact_snapshot_equal": True}))


if __name__ == "__main__":
    main()
