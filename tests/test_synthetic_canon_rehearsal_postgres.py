"""Opt-in PostgreSQL regression using only runner-created disposable databases."""

import os
import runpy
import secrets
import unittest
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


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
        RUNNER["_validate_admin_source"](admin_dsn, template)

        template_dsn = make_conninfo(**{**conninfo_to_dict(admin_dsn), "dbname": template})
        with psycopg.connect(template_dsn) as conn:
            try:
                conn.execute("GRANT pg_read_all_data TO bridge_school_reader")
                with self.assertRaisesRegex(ValueError, "schema template DDL/ACL"):
                    RUNNER["_validate_schema_manifest"](conn)
            finally:
                conn.rollback()

        tampered_template = "canon_schema_template_tampered_" + secrets.token_hex(4)
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} WITH TEMPLATE {}").format(
                sql.Identifier(tampered_template), sql.Identifier(template)))
            admin.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(tampered_template), sql.Literal(RUNNER["TEMPLATE_COMMENT"])))
        with psycopg.connect(make_conninfo(**{
            **conninfo_to_dict(admin_dsn), "dbname": tampered_template,
        })) as conn:
            conn.execute("ALTER TABLE public.school ADD CONSTRAINT rehearsal_template_probe CHECK (true)")
        with self.assertRaisesRegex(ValueError, "schema template DDL/ACL"):
            create(admin_dsn, tampered_template)

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
                "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)",
                (list(RUNNER["_rehearsal_principals"](rejected_id).values()) +
                 [RUNNER["_reader_principal"](rejected_id)],),
            ).fetchone()[0], 0)

        ddl_dsn, ddl_id, ddl_baseline = create(admin_dsn, template)
        with psycopg.connect(ddl_dsn) as conn:
            conn.execute("ALTER TABLE public.school ADD CONSTRAINT rehearsal_ddl_probe CHECK (true)")
        with self.assertRaisesRegex(ValueError, "schema DDL/ACL"):
            rehearse_created(ddl_dsn, ddl_id, ddl_baseline)

        acl_dsn, acl_id, acl_baseline = create(admin_dsn, template)
        with psycopg.connect(acl_dsn) as conn:
            conn.execute("REVOKE SELECT ON public.school FROM bridge_school_reader")
        with self.assertRaisesRegex(ValueError, "schema DDL/ACL"):
            rehearse_created(acl_dsn, acl_id, acl_baseline)

        trigger_dsn, trigger_id, trigger_baseline = create(admin_dsn, template)
        with psycopg.connect(trigger_dsn) as conn:
            schema, table, trigger = conn.execute(
                "SELECT n.nspname,c.relname,t.tgname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE t.tgisinternal AND n.nspname NOT LIKE 'pg_%' "
                "ORDER BY n.nspname,c.relname,t.tgname LIMIT 1"
            ).fetchone()
            conn.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}").format(
                sql.Identifier(schema, table), sql.Identifier(trigger)))
        with self.assertRaisesRegex(ValueError, "schema DDL/ACL"):
            rehearse_created(trigger_dsn, trigger_id, trigger_baseline)

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
        second_dsn, second_id, second_baseline = create(admin_dsn, template)
        second = rehearse_created(second_dsn, second_id, second_baseline)
        self.assertEqual(second["readback_rows"], 1)
        self.assertNotEqual(positive_id, second_id)


if __name__ == "__main__":
    unittest.main()
