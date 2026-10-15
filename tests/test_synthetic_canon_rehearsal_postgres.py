"""Opt-in PostgreSQL regression using only runner-created disposable databases."""

import os
import runpy
import unittest
from pathlib import Path

import psycopg


RUNNER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "database/tests/402_video_canon_positive_synthetic_rehearsal.py"),
    run_name="canon_guard_postgres_test",
)


@unittest.skipUnless(
    os.environ.get("CANON_SYNTHETIC_PG_ADMIN_DSN") and
    os.environ.get("CANON_SYNTHETIC_PG_TEMPLATE"),
    "explicit disposable PostgreSQL administrator and schema template required",
)
class PostgreSQLDisposableGuardTests(unittest.TestCase):
    def test_actual_inet_readonly_boundary_unlisted_data_and_positive_replay(self):
        admin_dsn = os.environ["CANON_SYNTHETIC_PG_ADMIN_DSN"]
        template = os.environ["CANON_SYNTHETIC_PG_TEMPLATE"]
        create = RUNNER["_create_disposable_database"]
        rehearse_created = RUNNER["_rehearse_created"]

        rejected_dsn, rejected_id, rejected_baseline = create(admin_dsn, template)
        with psycopg.connect(rejected_dsn) as conn:
            addr = conn.execute("SELECT inet_server_addr()::text, host(inet_server_addr())").fetchone()
            self.assertEqual(addr, ("127.0.0.1/32", "127.0.0.1"))
            conn.execute("CREATE TABLE public.unlisted_data (id integer PRIMARY KEY)")
            conn.execute("INSERT INTO public.unlisted_data(id) VALUES (1)")
        with self.assertRaisesRegex(ValueError, "public.unlisted_data"):
            rehearse_created(rejected_dsn, rejected_id, rejected_baseline)
        with psycopg.connect(rejected_dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'canon_rehearsal_%_login'"
            ).fetchone()[0], 0)

        positive_dsn, positive_id, positive_baseline = create(admin_dsn, template)
        result = rehearse_created(positive_dsn, positive_id, positive_baseline)
        self.assertEqual(result["readback_rows"], 1)
        self.assertTrue(result["replay_same_receipt"])
        with psycopg.connect(positive_dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM public.canon_activation WHERE status='active'"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT count(*),count(DISTINCT execution_principal) "
                "FROM bidding.video_canon_ai_verification"
            ).fetchone(), (16, 4))
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM bidding.video_canon_ai_promotion_receipt"
            ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
