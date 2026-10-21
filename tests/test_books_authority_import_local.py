"""New guarded-import tests only. Synthetic state/SQL doubles; no real PG."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from test_books_authority_local import Fake, F, REC, POLICY, NOW, LATE, authority, metadata
from database.books_authority import Denied


class ImportFake(Fake):
    def __init__(self):
        super().__init__()
        self.phases = {}
        self.objects["g"]["body"]["actions"].append("IMPORT_STAGED")
        self.objects["principal"]["body"]["actions"].append("IMPORT_STAGED")
        self.persist_calls = 0
        self.bad_return = False

    def persist(self, bundle, review_receipt, identity_receipt, scope, auth, *, before_effect):
        self.log.append("domain-locks")
        if self.expire_at_domain_lock:
            self.clock_value = LATE
        before_effect()
        self.persist_calls += 1
        self.effects += 1
        self.events[REC] = metadata(REC)
        value = deepcopy(F["import_receipt"])
        if self.bad_return:
            value["committed"] = False
        return value


def invoke(s, **kwargs):
    return authority(s).import_staged("g", deepcopy(F["bundle"]),
                                      deepcopy(F["review_receipt"]), **kwargs)


class ImportTests(unittest.TestCase):
    def test_import_actual_return_and_outbox_handoff(self):
        s = ImportFake()
        result = invoke(s)
        self.assertEqual(result, F["import_receipt"])
        self.assertEqual(s.phases["g", "IMPORT_STAGED"]["operation_return"], result)
        self.assertEqual(s.log[-1], "commit")
        delivered = authority(s).publish("g", REC)
        self.assertEqual(delivered["event_positions"], [1])
        self.assertEqual(s.effects, 2)

    def test_exact_replay_retains_original_return_without_import(self):
        s = ImportFake()
        self.assertEqual(invoke(s), invoke(s))
        self.assertEqual(s.persist_calls, 1)
        s.phases["g", "IMPORT_STAGED"]["input_hash"] = "0" * 64
        with self.assertRaises(Denied):
            invoke(s)
        self.assertEqual(s.persist_calls, 1)

    def test_import_requires_both_actions_and_accepted_policy(self):
        for action in ("IMPORT_STAGED", REC):
            s = ImportFake()
            s.objects["g"]["body"]["actions"].remove(action)
            with self.assertRaises(Denied):
                invoke(s)
            self.assertEqual(s.persist_calls, 0)
        s = ImportFake()
        with self.assertRaises(Denied):
            authority(s, policy=None).import_staged("g", F["bundle"], F["review_receipt"])
        self.assertEqual(s.attempts, {})

    def test_revoke_before_import_and_after_success_blocks_retry(self):
        for already in (False, True):
            s = ImportFake()
            if already:
                invoke(s)
            authority(s, "approver").revoke("g")
            with self.assertRaises(Denied):
                invoke(s)
            self.assertEqual(s.persist_calls, int(already))

    def test_expiry_after_each_lock_boundary(self):
        for delay in ("expire_at_lock", "expire_at_domain_lock"):
            s = ImportFake()
            setattr(s, delay, True)
            with self.assertRaises(Denied):
                invoke(s)
            self.assertEqual(s.persist_calls, 0)

    def test_scope_run_hash_order_are_bounded(self):
        mutations = (
            lambda g: g["scope"].update(publication_hash="0"*64),
            lambda g: g["scope"]["run"].update(run_id="other-run"),
            lambda g: g["scope"]["version_ids"].reverse(),
            lambda g: g.update(executor="other"),
        )
        for mutate in mutations:
            s = ImportFake()
            mutate(s.objects["g"]["body"])
            with self.assertRaises(ValueError):
                invoke(s)
            self.assertEqual(s.persist_calls, 0)

    def test_failed_audit_commit_or_false_return_has_no_durable_effect(self):
        for name in ("fail_save", "fail_commit", "bad_return"):
            s = ImportFake()
            setattr(s, name, True)
            with self.assertRaises((ValueError, RuntimeError)):
                invoke(s)
            self.assertEqual(s.effects, 0)
            self.assertNotIn(("g", "IMPORT_STAGED"), s.phases)
            self.assertEqual(set(s.attempts.values()), {"unresolved"})

    def test_real_producer_internal_uses_caller_transaction(self):
        pg = types.ModuleType("psycopg")
        rows = types.ModuleType("psycopg.rows")
        rows.tuple_row = object()
        with patch.dict(sys.modules, {"psycopg": pg, "psycopg.rows": rows}):
            import importlib
            producer = importlib.import_module("database.book_world_persistence")
        from bridge_contracts.book_world import build_world_publication, world_uuid
        records = build_world_publication(F["bundle"], F["review_receipt"])["records"]
        citations = dict(zip(F["import_receipt"]["version_ids"], [r["content"]["citation"] for r in records]))

        class Cursor:
            rowcount = 1
            def __init__(self, conn):
                self.c = conn
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, sql, params=()):
                self.sql, self.params = sql, params
                self.c.queries.append(sql)
            def fetchone(self):
                if "SELECT knowledge_item_id" in self.sql:
                    return (world_uuid("item", F["school_id"], self.params[1]),)
                if "SELECT source_locator" in self.sql:
                    return (citations[self.params[0]],)
                return None

        class Connection:
            autocommit = True  # explicit active transaction is valid on this connection too
            info = types.SimpleNamespace(transaction_status=2)
            def __init__(self):
                self.queries = []
            def cursor(self, **kwargs):
                return Cursor(self)
            def commit(self):
                raise AssertionError("internal must not commit")
            def transaction(self, **kwargs):
                raise AssertionError("internal must not own transaction")

        conn = Connection()
        before = []
        def check_before():
            before.append(sum(q.startswith(("INSERT", "UPDATE", "DELETE")) for q in conn.queries))
        with (patch.object(producer, "source_locator", return_value="SYNTHETIC"),
              patch.object(producer, "needs_asset_binding", return_value=False)):
            result = producer.persist_book_world_in_transaction(
                conn, F["bundle"], F["review_receipt"], school_id=F["school_id"],
                source_id=F["source_id"], delivery_authorization=F["trusted_authorization"],
                before_effect=check_before)
        self.assertEqual(before, [0])
        self.assertEqual(result, F["import_receipt"])

    def test_dry_run_is_owned_only_by_legacy_transaction_wrapper(self):
        pg = types.ModuleType("psycopg")
        rows = types.ModuleType("psycopg.rows")
        rows.tuple_row = object()
        with patch.dict(sys.modules, {"psycopg": pg, "psycopg.rows": rows}):
            import importlib
            producer = importlib.import_module("database.book_world_persistence")

        class Connection:
            autocommit = True
            def __init__(self):
                self.info = types.SimpleNamespace(transaction_status=0)
                self.log = []
            @contextmanager
            def transaction(self, *, force_rollback):
                self.info.transaction_status = 2
                self.log.append(("begin", force_rollback))
                try:
                    yield
                finally:
                    self.info.transaction_status = 0
                    self.log.append("rollback" if force_rollback else "commit")
            @contextmanager
            def cursor(self):
                yield types.SimpleNamespace(execute=lambda sql: None)

        for dry_run in (False, True):
            conn = Connection()
            def operation(*args, **kwargs):
                self.assertEqual(conn.info.transaction_status, 2)
                self.assertNotIn("dry_run", kwargs)
                return deepcopy(F["import_receipt"])
            with patch.object(producer, "persist_book_world_in_transaction", side_effect=operation):
                result = producer.persist_book_world(
                    conn, F["bundle"], F["review_receipt"], school_id=F["school_id"],
                    source_id=F["source_id"], dry_run=dry_run)
            self.assertEqual(result["dry_run"], dry_run)
            self.assertEqual(result["committed"], not dry_run)
            self.assertEqual(conn.log, [("begin", dry_run), "rollback" if dry_run else "commit"])
        with self.assertRaises(TypeError):
            producer.persist_book_world_in_transaction(
                Connection(), F["bundle"], F["review_receipt"], school_id=F["school_id"],
                source_id=F["source_id"], dry_run=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
