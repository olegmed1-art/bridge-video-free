"""New source-only catalog/activation suite; all ports are explicit test doubles."""
from contextlib import contextmanager
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import types
import sys

from test_books_authority_local import (
    Fake, F, POLICY, NOW, LATE, obj, metadata, read_fixture, authority, REC, PUB,
)
from bridge_contracts.book_material import digest
from database.books_catalog import (
    VerifierBinding, CatalogConnector, CatalogProfile, EvidenceStorage, ACTIVATE,
)


class Connector(CatalogConnector):
    def __init__(self, s):
        self.s, self.calls, self.after = s, 0, None
    def read(self, locator, **limits):
        assert not self.s.inside
        self.calls += 1
        if self.after:
            self.after()
        return json.dumps(self.s.claims, sort_keys=True).encode()


class Profile(CatalogProfile):
    def decode(self, raw):
        return json.loads(raw)


class Storage(EvidenceStorage):
    def __init__(self):
        self.data = {}
    def retain(self, key, raw):
        self.data.setdefault(key, raw)
        return key
    def read(self, locator, **kwargs):
        return self.data[locator]


class Store(Fake):
    def __init__(self):
        s, _ = read_fixture()
        self.__dict__.update(deepcopy(s.__dict__))
        self.objects.pop("proof")
        self.objects.pop("v")
        self.phases.pop(("g", ACTIVATE))
        self.phases.pop(("g", PUB))
        self.events.pop(PUB)
        self.acceptances, self.observations = {}, {}
        self.inside, self.bad_return = False, False
        self.descriptor = {
            "acceptance_ref": "TEST-ONLY", "binding_id": "TEST-ONLY",
            "implementation_sha256": "a"*64, "principal": "catalog-verifier",
            "school_id": F["school_id"], "catalog_target": "synthetic-catalog",
            "profile": "synthetic", "profile_version": "v1",
            "evidence_schema": "synthetic-proof", "semantics_version": "test",
            "connector_id": "test", "storage_id": "test",
            "freshness_policy_ref": "test", "invalidation_policy_ref": "test",
            "clock_policy_ref": "test", "state_ids": ["vp", "storage", "invalidation"],
            "max_raw_bytes": 100000, "timeout_ms": 1000,
            "max_age_seconds": 10800, "max_clock_skew_seconds": 60,
        }
        g = self.objects["g"]["body"]
        g["actions"].append(ACTIVATE)
        self.objects["principal"]["body"]["actions"].append(ACTIVATE)
        g["proof_predicates"].update(
            state_ids=deepcopy(self.descriptor["state_ids"]),
            proof_expires_at="2026-10-20T12:30:00+00:00")
        self.objects["verifier"]["body"]["binding"] = deepcopy(self.descriptor)
        for key in self.descriptor["state_ids"]:
            self.objects[key] = obj("catalog_dependency", {"binding_hash": digest(self.descriptor)})
        self.claims = {
            "target": "synthetic-catalog", "school_id": F["school_id"],
            "receipt_ref": F["published_payload"]["catalog_receipt_ref"],
            "record_id": "test-record", "revision": "test-revision",
            "durable": True, "authoritative": True, "scope": deepcopy(g["scope"]),
            "recorded": {k: self.events[REC][k] for k in
                         ("event_id", "changeset_id", "payload_hash", "event_position")},
            "provenance": "catalog_record",
        }
        self.connector, self.storage = Connector(self), Storage()

    def verifier(self):
        return VerifierBinding(deepcopy(self.descriptor), self.connector,
                               Profile(), self.storage, lambda: self.clock_value)

    @contextmanager
    def transaction(self, policy, readonly=False):
        old = deepcopy((self.acceptances, self.observations))
        self.inside = True
        try:
            with super().transaction(policy):
                yield self
        except Exception:
            self.acceptances, self.observations = old
            raise
        finally:
            self.inside = False

    def catalog_acceptance(self, proof_id):
        return deepcopy(self.acceptances.get(proof_id))
    def insert_catalog(self, proof_id, acceptance, body, actor):
        self.acceptances[proof_id] = deepcopy(acceptance)
        self.objects[proof_id] = obj("catalog", body)
    def save_observation(self, key, body):
        if self.fail_save:
            raise RuntimeError("test-save")
        if key in self.observations and self.observations[key] != body:
            raise ValueError("test-observation-conflict")
        self.observations[key] = deepcopy(body)
    def activate(self, scope, delivery, auth, *, before_effect):
        self.log.append("domain-locks")
        if self.expire_at_domain_lock:
            self.clock_value = LATE
        before_effect()
        self.events[PUB] = metadata(PUB)
        self.effects += 1
        result = deepcopy(F["publication_receipt"])
        if self.bad_return:
            result["committed"] = False
        return result


def ingest(s):
    return authority(s).ingest_catalog("g", "opaque-record", verifier=s.verifier())


class Tests(unittest.TestCase):
    def test_unbound_before_any_io_and_cannot_supply_verified(self):
        s = Store()
        for bad in (None, {"status": "VERIFIED"}):
            with self.assertRaises(ValueError):
                authority(s).ingest_catalog("g", "opaque", verifier=bad)
        self.assertEqual((s.log, s.connector.calls, s.attempts), ([], 0, {}))
        s.descriptor.pop("clock_policy_ref")
        with self.assertRaises(ValueError):
            ingest(s)
        self.assertEqual(s.connector.calls, 0)

    def test_durable_authentic_scope_causal_claims_required(self):
        mutations = [
            lambda c: c.update(durable=False), lambda c: c.update(authoritative=False),
            lambda c: c.update(target="other"), lambda c: c.pop("provenance"),
            lambda c: c["scope"]["version_ids"].reverse(),
            lambda c: c["recorded"].update(payload_hash="0"*64),
            lambda c: c.update(provenance="expected-echo"),
        ]
        for mutate in mutations:
            s = Store()
            mutate(s.claims)
            with self.assertRaises((ValueError, KeyError)):
                ingest(s)
            self.assertEqual(s.acceptances, {})

    def test_locator_and_storage_readback_fail_closed(self):
        s = Store()
        with self.assertRaises(ValueError):
            authority(s).ingest_catalog("g", "https://other", verifier=s.verifier())
        self.assertEqual(s.connector.calls, 0)
        with patch.object(s.storage, "read", return_value=b"other"):
            with self.assertRaises(ValueError):
                ingest(s)
        self.assertEqual(s.acceptances, {})

    def test_ingest_separates_raw_acceptance_and_observation(self):
        s = Store()
        key = ingest(s)
        body = s.objects[key]["body"]
        self.assertNotEqual(body["evidence_hash"], body["evidence"]["raw_sha256"])
        self.assertEqual(len(s.acceptances), 1)
        self.assertEqual(len(s.observations), 1)
        self.assertEqual(s.effects, 0)
        self.assertEqual(ingest(s), key)
        self.assertEqual(len(s.observations), 1)
        s.clock_value = "2026-10-20T12:00:01+00:00"
        self.assertEqual(ingest(s), key)
        self.assertEqual(len(s.observations), 2)
        self.assertEqual(s.objects[key]["body"], body)
        s.clock_value = "2026-10-20T12:31:00+00:00"
        with self.assertRaises(ValueError):
            ingest(s)
        self.assertEqual(s.objects[key]["body"], body)

    def test_post_network_revoke_and_predecessor_change_rejected(self):
        for key in ("g", "principal", "policy", "verifier", "vp", "storage", "invalidation"):
            s = Store()
            s.connector.after = lambda: s.objects[key].update(status="revoked", revision=1)
            with self.assertRaises(ValueError):
                ingest(s)
            self.assertEqual(s.acceptances, {})
        s = Store()
        s.connector.after = lambda: s.phases["g", REC].update(effect_observed_at=NOW)
        with self.assertRaises(ValueError):
            ingest(s)
        self.assertEqual(s.acceptances, {})

    def test_no_success_on_ingestion_audit_or_commit_failure(self):
        for flag in ("fail_save",):
            s = Store()
            setattr(s, flag, True)
            with self.assertRaises(RuntimeError):
                ingest(s)
            self.assertEqual((s.acceptances, s.observations), ({}, {}))
            self.assertEqual(set(s.attempts.values()), {"unresolved"})
        s = Store()
        s.connector.after = lambda: setattr(s, "fail_commit", True)
        with self.assertRaises(RuntimeError):
            ingest(s)
        self.assertEqual(s.acceptances, {})

    def test_final_ingestion_clock_and_concurrent_acceptance_race(self):
        for race in ("expiry", "concurrent"):
            s = Store()
            s.objects["g"]["body"]["expires_at"] = "2026-10-20T12:00:01+00:00"
            calls = []
            def lookup(proof_id):
                calls.append(proof_id)
                if len(calls) == 2:
                    if race == "expiry":
                        s.clock_value = "2026-10-20T12:00:02+00:00"
                    else:
                        return {"concurrent": "acceptance"}
                return None
            with patch.object(s, "catalog_acceptance", side_effect=lookup):
                with self.assertRaises(ValueError):
                    ingest(s)
            self.assertEqual(len(calls), 2)
            self.assertEqual((s.acceptances, s.observations), ({}, {}))

    def test_activation_actual_return_replay_and_published_handoff(self):
        s = Store()
        key = ingest(s)
        result = authority(s).activate_world("g", key)
        self.assertEqual(result, F["publication_receipt"])
        self.assertEqual(s.phases["g", ACTIVATE]["catalog_proof_id"], key)
        self.assertEqual(authority(s).activate_world("g", key), result)
        self.assertEqual(s.effects, 1)
        self.assertEqual(authority(s).publish("g", PUB)["event_positions"], [2])

    def test_activation_revoke_expiry_and_atomic_failure(self):
        for key in ("g", "verifier", "vp", "storage", "invalidation", "proof"):
            s = Store()
            proof = ingest(s)
            s.objects[proof if key == "proof" else key]["status"] = "revoked"
            with self.assertRaises(ValueError):
                authority(s).activate_world("g", proof)
            self.assertEqual(s.effects, 0)
        for flag in ("expire_at_domain_lock", "fail_commit", "fail_save", "bad_return"):
            s = Store()
            proof = ingest(s)
            setattr(s, flag, True)
            with self.assertRaises((ValueError, RuntimeError)):
                authority(s).activate_world("g", proof)
            self.assertEqual(s.effects, 0)
            self.assertNotIn(("g", ACTIVATE), s.phases)

    def test_fresh_read_issuer_denies_invalidation_after_activation(self):
        s = Store()
        key = ingest(s)
        authority(s).activate_world("g", key)
        authority(s).publish("g", PUB)
        from database.books_authority import ReadIssuer, Binding
        original, issuer = read_fixture()
        retained = deepcopy(issuer.retained)
        retained["snapshot"]["recorded_event"] = s.events[REC]
        retained["snapshot"]["published_event"] = s.events[PUB]
        retained["phases"] = {k: s.phases["g", k] for k in (REC, PUB)}
        retained["snapshot"]["recorded_outbox_receipt"] = s.phases["g", REC]["operation_return"]
        retained["snapshot"]["published_outbox_receipt"] = s.phases["g", PUB]["operation_return"]
        s.objects["v"] = original.objects["v"]
        s.objects["v"]["body"]["retained_hash"] = digest(retained)
        issuer = ReadIssuer(store=s, policy=POLICY,
            binding=Binding("exec", s.target, F["school_id"]), validation_id="v", retained=retained)
        query = {"recorded_event": s.events[REC]}
        self.assertEqual(issuer(query)["decision"], "allowed")
        s.objects["invalidation"]["status"] = "revoked"
        with self.assertRaises(ValueError):
            issuer(query)

    def test_actual_activation_producer_checks_before_first_write(self):
        pg, rows = types.ModuleType("psycopg"), types.ModuleType("psycopg.rows")
        rows.tuple_row = object()
        with patch.dict(sys.modules, {"psycopg": pg, "psycopg.rows": rows}):
            import importlib
            p = importlib.import_module("database.book_world_persistence")
        records = p.build_world_publication(F["bundle"], F["review_receipt"])["records"]
        stored = []
        for record, version_id in zip(records, F["import_receipt"]["version_ids"]):
            provenance = {"schema": p.WORLD_SCHEMA, "source_id": F["source_id"],
                          "review_receipt": F["review_receipt"],
                          "version_hash": p.publication_version(record, F["review_receipt"])}
            stored.append((version_id, record["content"], provenance, record["stable_key"]))

        class Cursor:
            rowcount = 1
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, sql, params=()):
                self.sql, self.params = sql, params
                queries.append(sql)
            def fetchall(self):
                if "SELECT e.payload" in self.sql:
                    return [(F["recorded_payload"],)]
                if "SELECT source_locator" in self.sql:
                    return [(next(r[1]["citation"] for r in stored if r[0] == self.params[0]),)]
                if "FOR UPDATE OF kv" in self.sql:
                    return stored
                if "SELECT kv.knowledge_version_id" in self.sql:
                    return [r[:3] for r in stored]
                raise AssertionError(self.sql)
            def fetchone(self): return None

        class Conn:
            info = types.SimpleNamespace(transaction_status=2)
            autocommit = True
            def cursor(self, **kwargs): return Cursor()
            def transaction(self, **kwargs): raise AssertionError("nested transaction")
            def commit(self): raise AssertionError("internal commit")

        delivery = {"schema": "book-world-delivery-v1",
                    "publication_hash": F["import_receipt"]["publication_hash"],
                    "catalog_receipt_ref": F["published_payload"]["catalog_receipt_ref"]}
        for expired in (False, True):
            queries = []
            def guard():
                self.assertFalse(any(q.startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
                self.assertTrue(any("FOR UPDATE OF kv" in q for q in queries))
                if expired:
                    raise ValueError("test-expired")
            with (patch.object(p, "source_locator", return_value="SYNTHETIC"),
                  patch.object(p, "needs_asset_binding", return_value=False),
                  patch.object(p, "read_book_world_event", return_value=metadata(REC, True))):
                if expired:
                    with self.assertRaises(ValueError):
                        p.publish_book_world_in_transaction(Conn(), school_id=F["school_id"],
                            changeset_id=F["import_receipt"]["changeset_id"], delivery_receipt=delivery,
                            delivery_authorization=F["trusted_authorization"], before_effect=guard)
                else:
                    self.assertEqual(p.publish_book_world_in_transaction(
                        Conn(), school_id=F["school_id"],
                        changeset_id=F["import_receipt"]["changeset_id"], delivery_receipt=delivery,
                        delivery_authorization=F["trusted_authorization"], before_effect=guard),
                        F["publication_receipt"])

    def test_activation_legacy_dry_run_and_sql_observation_conflict(self):
        # Previous test loaded the real producer; isolate import for test ordering.
        pg, rows = types.ModuleType("psycopg"), types.ModuleType("psycopg.rows")
        rows.tuple_row = object()
        with patch.dict(sys.modules, {"psycopg": pg, "psycopg.rows": rows}):
            import importlib
            p = importlib.import_module("database.book_world_persistence")
        log = []
        class Conn:
            info = types.SimpleNamespace(transaction_status=0)
            @contextmanager
            def transaction(self, *, force_rollback):
                log.append(force_rollback)
                yield
                log.append("exit")
            @contextmanager
            def cursor(self):
                yield types.SimpleNamespace(execute=lambda sql: None)
        with patch.object(p, "publish_book_world_in_transaction", return_value=deepcopy(F["publication_receipt"])):
            value = p.publish_book_world(Conn(), school_id=F["school_id"], changeset_id="test",
                                         delivery_receipt={}, dry_run=True)
        self.assertEqual(log, [True, "exit"])
        self.assertEqual((value["committed"], value["dry_run"], value["publication"]),
                         (False, True, "DRY_RUN_NOT_PUBLISHED"))
        from database.books_authority_postgres import Transaction
        tx = Transaction(None)
        with (patch.object(tx, "sql", return_value=0),
              patch.object(tx, "one", return_value={"different": True})):
            with self.assertRaises(ValueError):
                tx.save_observation("test", {"proof_id": "test"})


if __name__ == "__main__":
    unittest.main()
