"""Opt-in synthetic PostgreSQL 18 proof; fixed localhost, never a production DSN."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "recovery" / "fixed_subset"))
import acl_snapshot as acl


@pytest.mark.skipif(os.getenv("SUBSET_SYNTHETIC_PG18") != "1", reason="disposable localhost PG18 only")
def test_exact_acl_owner_recovery(tmp_path):
    import psycopg

    expected_data = Path(os.environ["SUBSET_SYNTHETIC_PG18_DATADIR"]).resolve()
    assert expected_data.is_relative_to(Path(tempfile.gettempdir()).resolve())
    assert expected_data.name.startswith("subset-acl-pg18-")
    with psycopg.connect(host="127.0.0.1", port=55439, dbname="postgres", user="postgres", autocommit=True) as db:
        assert 180000 <= int(db.execute("SHOW server_version_num").fetchone()[0]) < 190000
        assert db.execute("SELECT current_setting('listen_addresses')").fetchone()[0] == "127.0.0.1"
        assert Path(db.execute("SHOW data_directory").fetchone()[0]).resolve() == expected_data
        # Only this verified disposable cluster is eligible for scoped cleanup.
        db.execute("DROP SCHEMA IF EXISTS video_queue CASCADE")
        for role in ("synthetic_unlisted", "video_subset_owner", "bridge_school_worker", "bridge_school_app"):
            if db.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                db.execute(f"DROP OWNED BY {role}")
                db.execute(f"DROP ROLE {role}")
        db.execute("CREATE ROLE bridge_school_app NOLOGIN")
        db.execute("CREATE ROLE bridge_school_worker NOLOGIN")
        db.execute("GRANT bridge_school_app TO bridge_school_worker")
        db.execute("CREATE SCHEMA video_queue")
        db.execute("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")
        db.execute("CREATE TABLE video_queue.job (id integer PRIMARY KEY)")
        db.execute("CREATE SEQUENCE video_queue.job_event_event_id_seq")
        db.execute("CREATE FUNCTION video_queue.claim_job() RETURNS integer LANGUAGE sql AS 'SELECT 1'")
        db.execute("REVOKE ALL ON FUNCTION video_queue.claim_job() FROM PUBLIC")
        db.execute("GRANT EXECUTE ON FUNCTION video_queue.claim_job() TO bridge_school_worker")
        allowed = frozenset({("bridge_school_app", "bridge_school_worker")})
        before = acl.collect(db)
        assert not acl.unsafe_writer_paths(before, allowed_memberships=allowed)
        acl.write_snapshot(tmp_path / "acl-before.json", before)
        artifact = (tmp_path / "acl-before.json").read_text()
        assert all(value not in artifact.lower() for value in ("password", "127.0.0.1", "postgresql://", "select 1"))

        db.execute("CREATE ROLE video_subset_owner NOLOGIN")
        db.execute("CREATE TABLE video_queue.source_reservation (id integer)")
        db.execute("CREATE FUNCTION video_queue.subset_disabled() RETURNS integer LANGUAGE sql AS 'SELECT 0'")
        db.execute("REVOKE ALL ON FUNCTION video_queue.subset_disabled() FROM PUBLIC")
        installed = acl.collect(db)

        # A fault after a temporary GRANT and ownership transfer must roll back.
        with pytest.raises(psycopg.Error):
            with db.transaction():
                db.execute("GRANT video_subset_owner TO postgres")
                db.execute("ALTER FUNCTION video_queue.claim_job() OWNER TO video_subset_owner")
                db.execute("DO $$ BEGIN RAISE EXCEPTION 'synthetic fault'; END $$")
        assert acl.collect(db) == installed

        db.execute("GRANT video_subset_owner TO postgres")
        db.execute("ALTER FUNCTION video_queue.claim_job() OWNER TO video_subset_owner")
        db.execute("REVOKE EXECUTE ON FUNCTION video_queue.claim_job() FROM bridge_school_worker")
        db.execute("GRANT EXECUTE ON FUNCTION video_queue.claim_job() TO PUBLIC")
        changed = acl.collect(db)
        assert acl.unsafe_writer_paths(changed, allowed_memberships=allowed)
        with pytest.raises(AssertionError):
            acl.assert_restored(before, changed, allowed_memberships=allowed)

        # Committed restoration retains new objects and roles, but no new API grant.
        db.execute("REVOKE EXECUTE ON FUNCTION video_queue.claim_job() FROM PUBLIC")
        db.execute("ALTER FUNCTION video_queue.claim_job() OWNER TO postgres")
        db.execute("GRANT EXECUTE ON FUNCTION video_queue.claim_job() TO bridge_school_worker")
        db.execute("REVOKE video_subset_owner FROM postgres")
        restored = acl.collect(db)
        acl.assert_restored(before, restored, allowed_memberships=allowed)
        assert any(x["name"] == "source_reservation" for x in restored["objects"])
        assert any(x["name"] == "video_subset_owner" for x in restored["roles"])
        acl.write_snapshot(tmp_path / "acl-restored.json", restored)
        db.execute("CREATE ROLE synthetic_unlisted NOLOGIN")
        db.execute("GRANT bridge_school_worker TO synthetic_unlisted")
        assert "unreviewed writer membership: bridge_school_worker/synthetic_unlisted" in acl.unsafe_writer_paths(
            acl.collect(db), allowed_memberships=allowed
        )
        with pytest.raises(AssertionError):
            acl.assert_restored(before, acl.collect(db), allowed_memberships=allowed)
        db.execute("REVOKE bridge_school_worker FROM synthetic_unlisted")
        db.execute("DROP ROLE synthetic_unlisted")
        db.execute("GRANT video_subset_owner TO postgres")
        with pytest.raises(AssertionError, match="memberships differs"):
            acl.assert_restored(before, acl.collect(db), allowed_memberships=allowed)
        db.execute("REVOKE video_subset_owner FROM postgres")
        db.execute("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC")
        assert any("PUBLIC default function EXECUTE" in x for x in acl.unsafe_writer_paths(
            acl.collect(db), allowed_memberships=allowed
        ))
        db.execute("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")
        db.execute("GRANT EXECUTE ON FUNCTION video_queue.subset_disabled() TO PUBLIC")
        with pytest.raises(AssertionError, match="new subset API remains executable"):
            acl.assert_restored(before, acl.collect(db), allowed_memberships=allowed)
        db.execute("REVOKE EXECUTE ON FUNCTION video_queue.subset_disabled() FROM PUBLIC")
        assert acl.collect(db) == restored
        db.execute("GRANT UPDATE ON video_queue.job TO PUBLIC")
        assert any("broad table writer" in x for x in acl.unsafe_writer_paths(
            acl.collect(db), allowed_memberships=allowed
        ))
        db.execute("DROP SCHEMA video_queue CASCADE")
        for role in ("video_subset_owner", "bridge_school_worker", "bridge_school_app"):
            db.execute(f"DROP OWNED BY {role}")
            db.execute(f"DROP ROLE {role}")
        db.execute("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC")
        assert not acl.collect(db)["objects"]
