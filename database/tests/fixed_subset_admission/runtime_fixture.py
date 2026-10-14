"""Installed 0060 fail-closed canary contract on the disposable database CI host.

This fixture accepts only the fixed localhost CI identity. Every temporary role
grant and attempted queue write is rolled back, then catalog/data are rechecked.
"""

import argparse
import json
import os
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict


EXPECTED_0060 = "27932f2b5f22f383d9f84341c0ae6e88dc346c7940ec92490edc0a3d37358dfe"
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=("042",))
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
