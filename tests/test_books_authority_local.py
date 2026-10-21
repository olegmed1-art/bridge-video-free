"""NEW synthetic unit/state-machine tests only. No PG, network, app or Teacher.

The fake models rollback/lock ordering. It does not prove PostgreSQL locking.
"""
import ast
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from bridge_contracts.book_material import digest
from database.books_authority import (
    Authority, ReadIssuer, Policy, Binding, Denied, REC, PUB, READ,
    validate_catalog,
)
from database.books_authority_postgres import VALIDATION_SQL

ROOT = Path(__file__).resolve().parents[1]
F = json.loads((ROOT / "tests/fixtures/book_world_canonical_v2.json").read_text())
NOW = "2026-10-20T12:00:00+00:00"
EARLY = "2026-10-20T11:00:00+00:00"
LATE = "2026-10-20T13:00:00+00:00"
POLICY = Policy("SYNTHETIC-LOCAL-ONLY", 10800, 10800, 5000, 1000)
SCOPE = F["trusted_authorization"]["scope"]


def obj(kind, body):
    return {"kind": kind, "body": deepcopy(body), "status": "active", "revision": 0}


def metadata(kind, delivered=False):
    rec = kind == REC
    payload = F["recorded_payload" if rec else "published_payload"]
    from bridge_contracts.book_world import world_uuid
    change = F["import_receipt" if rec else "publication_receipt"]["changeset_id"]
    return {"event_id": F["published_payload"]["recorded_event_id"] if rec else
            world_uuid("publish-event", F["school_id"], F["import_receipt"]["changeset_id"]),
            "event_type": "BookWorldRecorded" if rec else "BookWorldPublished",
            "aggregate_version": 1, "school_id": F["school_id"], "changeset_id": change,
            "partition_key": "book-world", "payload": deepcopy(payload),
            "payload_hash": digest(payload), "changeset_status": "committed",
            "outbox_status": "published" if delivered else "pending",
            "event_position": (1 if rec else 2) if delivered else None}


class Fake:
    target = "synthetic-primary"

    def __init__(self):
        limits = {k: getattr(POLICY, k) for k in ("execution_ttl_seconds",
                  "validation_ttl_seconds", "statement_timeout_ms", "lock_timeout_ms")}
        g = {"grant_id": "g", "approval_revision": 1, "scope": SCOPE,
             "executor": "exec", "approver": "approver", "target": self.target,
             "principal_id": "principal", "approver_principal_id": "approver",
             "policy_id": "policy", "actions": [REC, PUB],
             "approved_at": EARLY, "not_before": EARLY, "expires_at": LATE}
        self.objects = {
            "g": obj("grant", g),
            "principal": obj("principal", {"principal": "exec", "school_id": F["school_id"],
                                         "target": self.target, "actions": [REC, PUB, *READ]}),
            "approver": obj("principal", {"principal": "approver", "school_id": F["school_id"],
                                         "target": self.target, "actions": ["REVOKE_BOOKS_GRANT"]}),
            "policy": obj("policy", {"acceptance_ref": POLICY.acceptance_ref,
                                    "school_id": F["school_id"], "target": self.target,
                                    "limits": limits})}
        self.events = {REC: metadata(REC), PUB: metadata(PUB)}
        self.phases = {("g", "IMPORT_STAGED"): {
            "scope": deepcopy(SCOPE), "approval_revision": 1,
            "operation_return": deepcopy(F["import_receipt"]), "metadata": metadata(REC)}}
        self.log, self.effects, self.attempts = [], 0, {}
        self.clock_value, self.fail_commit, self.fail_save = NOW, False, False
        self.expire_at_lock = self.expire_at_domain_lock = False
        self.reads = 0

    def begin_attempt(self, attempt, *args):
        self.attempts[attempt] = "unresolved"

    @contextmanager
    def transaction(self, policy):
        old = deepcopy((self.objects, self.events, self.phases, self.effects, self.attempts))
        self.log.append("begin")
        try:
            yield self
            if self.fail_commit:
                raise RuntimeError("synthetic-commit-error")
            self.log.append("commit")
        except Exception:
            self.objects, self.events, self.phases, self.effects, self.attempts = old
            self.log.append("rollback")
            raise

    def object(self, key):
        return deepcopy(self.objects[key])

    def lock_states(self, ids, exclusive):
        self.log.append(("exclusive" if exclusive else "share", list(ids)))
        if exclusive and self.expire_at_lock:
            self.clock_value = LATE
        return {key: self.object(key) for key in ids}

    def clock(self):
        self.log.append("clock")
        return self.clock_value

    def event(self, scope, phase):
        return deepcopy(self.events[phase])

    def phase(self, grant, phase):
        return deepcopy(self.phases.get((grant, phase)))

    def publish(self, event, authorization, *, before_effect):
        self.log.append("domain-locks")
        if self.expire_at_domain_lock:
            self.clock_value = LATE
        before_effect()
        self.log.append("effect")
        phase = REC if event["event_type"] == "BookWorldRecorded" else PUB
        self.events[phase] = metadata(phase, True)
        self.effects += 1
        return {"changeset_id": event["changeset_id"], "published_count": 1,
                "event_positions": [1 if phase == REC else 2]}

    def save_phase(self, grant, phase, value):
        if self.fail_save:
            raise RuntimeError("synthetic-audit-error")
        self.phases[grant, phase] = deepcopy(value)

    def finish_attempt(self, attempt, status):
        self.attempts[attempt] = status

    def revoke(self, grant, revision, actor):
        self.objects[grant]["status"] = "revoked"
        self.objects[grant]["revision"] += 1

    def validation_snapshot(self, validation, policy):
        self.reads += 1
        return deepcopy({"validation": self.objects[validation], "objects": self.objects,
            "phases": {k: self.phases["g", k] for k in (REC, PUB)},
            "predecessors": {REC: self.phases["g", "IMPORT_STAGED"],
                             PUB: self.phases["g", "ACTIVATE_REVIEWED_WORLD"]},
            "recorded": self.events[REC], "published": self.events[PUB], "now": self.clock_value})


def authority(store, principal="exec", policy=POLICY):
    return Authority(store=store, policy=policy,
                     binding=Binding(principal, store.target, F["school_id"]))


def read_fixture():
    s = Fake()
    s.events = {k: metadata(k, True) for k in (REC, PUB)}
    for k in (REC, PUB):
        s.phases["g", k] = {"scope": deepcopy(SCOPE), "approval_revision": 1,
                           "metadata": deepcopy(s.events[k]),
                           "operation_return": {"changeset_id": s.events[k]["changeset_id"],
                                                "published_count": 1,
                                                "event_positions": [s.events[k]["event_position"]]}}
    s.phases["g", "ACTIVATE_REVIEWED_WORLD"] = {
        "scope": deepcopy(SCOPE), "approval_revision": 1,
        "operation_return": deepcopy(F["publication_receipt"]), "metadata": metadata(PUB),
        "catalog_proof_id": "proof"}
    s.phases["g", REC]["effect_observed_at"] = EARLY
    s.objects["g"]["body"]["proof_predicates"] = {
        "verifier_id": "verifier", "verifier_principal": "catalog-verifier",
        "profile": "synthetic", "profile_version": "v1",
        "catalog_target": "synthetic-catalog", "evidence_schema": "synthetic-proof",
        "max_age_seconds": 10800}
    s.objects["verifier"] = obj("verifier", {
        "principal": "catalog-verifier", "profile": "synthetic", "profile_version": "v1",
        "target": "synthetic-catalog", "evidence_schema": "synthetic-proof"})
    proof_body = {
        "grant_id": "g", "approval_revision": 1, "scope": deepcopy(SCOPE),
        "verifier_principal": "catalog-verifier", "profile": "synthetic",
        "profile_version": "v1", "catalog_target": "synthetic-catalog",
        "evidence": {"schema": "synthetic-proof"},
        "catalog_receipt_ref": F["published_payload"]["catalog_receipt_ref"],
        "verified_at": NOW, "expires_at": LATE,
        "recorded_phase_hash": digest(s.phases["g", REC])}
    proof_body["evidence_hash"] = digest(proof_body["evidence"])
    for key in ("event_id", "changeset_id", "payload_hash", "event_position"):
        proof_body["recorded_" + key] = s.events[REC][key]
    s.objects["proof"] = obj("catalog", proof_body)
    retained = {"snapshot": {
        "schema": "retained-fixture-only",
        "recorded_event": deepcopy(s.events[REC]), "published_event": deepcopy(s.events[PUB]),
        "recorded_outbox_receipt": s.phases["g", REC]["operation_return"],
        "published_outbox_receipt": s.phases["g", PUB]["operation_return"],
        "publication_receipt": deepcopy(F["publication_receipt"])},
        "import_receipt": deepcopy(F["import_receipt"]),
        "phases": {k: deepcopy(s.phases["g", k]) for k in (REC, PUB)}}
    s.objects["v"] = obj("validation", {
        "principal": "exec", "scope": deepcopy(SCOPE), "target": s.target,
        "principal_id": "principal", "policy_id": "policy", "permissions": list(READ),
        "parents": {REC: "g", PUB: "g"}, "retained_hash": digest(retained),
        "approved_at": EARLY, "not_before": EARLY, "expires_at": LATE})
    issuer = ReadIssuer(store=s, policy=POLICY,
        binding=Binding("exec", s.target, F["school_id"]), validation_id="v", retained=retained)
    return s, issuer


class Tests(unittest.TestCase):
    def test_catalog_proof_causality_and_predicates(self):
        s = Fake()
        authority(s).publish("g", REC)
        recorded_phase = s.phases["g", REC]
        g = s.objects["g"]["body"]
        g["proof_predicates"] = {
            "verifier_id": "verifier", "verifier_principal": "catalog-verifier",
            "profile": "synthetic", "profile_version": "v1",
            "catalog_target": "synthetic-catalog", "evidence_schema": "synthetic-proof",
            "max_age_seconds": 60}
        verifier = obj("verifier", {
            "principal": "catalog-verifier", "profile": "synthetic", "profile_version": "v1",
            "target": "synthetic-catalog", "evidence_schema": "synthetic-proof"})
        body = {
            "grant_id": "g", "approval_revision": 1, "scope": deepcopy(SCOPE),
            "verifier_principal": "catalog-verifier", "profile": "synthetic",
            "profile_version": "v1", "catalog_target": "synthetic-catalog",
            "evidence": {"schema": "synthetic-proof"},
            "catalog_receipt_ref": F["published_payload"]["catalog_receipt_ref"],
            "verified_at": NOW, "expires_at": LATE,
            "recorded_phase_hash": digest(recorded_phase)}
        body["evidence_hash"] = digest(body["evidence"])
        for key in ("event_id", "changeset_id", "payload_hash", "event_position"):
            body["recorded_" + key] = s.events[REC][key]
        proof = obj("catalog", body)
        validate_catalog(proof, g, s.events[REC], recorded_phase, verifier, NOW)
        for key, value in (("recorded_payload_hash", "0"*64),
                           ("recorded_event_position", 9),
                           ("recorded_phase_hash", "0"*64),
                           ("verifier_principal", "another"),
                           ("profile_version", "v2"),
                           ("verified_at", EARLY)):
            bad = deepcopy(proof)
            bad["body"][key] = value
            with self.subTest(field=key), self.assertRaises(Denied):
                validate_catalog(bad, g, s.events[REC], recorded_phase, verifier, NOW)
        s.objects["proof"] = proof
        s.objects["verifier"] = verifier
        s.phases["g", "ACTIVATE_REVIEWED_WORLD"] = {
            "scope": deepcopy(SCOPE), "approval_revision": 1, "catalog_proof_id": "proof",
            "operation_return": deepcopy(F["publication_receipt"]), "metadata": metadata(PUB)}
        s.objects["proof"]["body"]["catalog_receipt_ref"] = "SYNTHETIC-WRONG-RECEIPT"
        with self.assertRaises(Denied):
            authority(s).publish("g", PUB)
        s.objects["proof"]["body"]["catalog_receipt_ref"] = F["published_payload"]["catalog_receipt_ref"]
        result = authority(s).publish("g", PUB)
        self.assertEqual(result["event_positions"], [2])
        s.objects["proof"]["status"] = "revoked"
        with self.assertRaises(Denied):
            authority(s).publish("g", PUB)

    def test_real_transaction_publisher_boundary_without_database(self):
        # Dependency doubles are installed before import: no psycopg network path.
        pg = types.ModuleType("psycopg")
        preflight = types.ModuleType("database.runtime_worker_preflight")
        preflight.normalize_dsn = lambda value: value
        with patch.dict(sys.modules, {"psycopg": pg, "database.runtime_worker_preflight": preflight}):
            import importlib
            module = importlib.import_module("database.outbox_publisher")
        event = metadata(REC)

        class Cursor:
            def __init__(self, connection):
                self.c = connection
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute(self, sql, params):
                self.c.sql.append(sql)
                if "WITH locked_outbox" in sql:
                    self.rows = [("outbox", event["event_id"], "allowed", event["event_type"],
                                  event["school_id"], event["changeset_id"], event["payload"])]
                elif "SELECT public.publish_outbox_event" in sql:
                    self.c.rpc += 1
                    self.rows = [(1,)]
                else:
                    from database.book_world_event_readback import COLUMNS
                    self.rows = [tuple(event[k] for k in COLUMNS)]
            def fetchall(self):
                return self.rows
            def fetchone(self):
                return self.rows[0]

        class Connection:
            autocommit = False
            info = types.SimpleNamespace(transaction_status=2)
            def __init__(self):
                self.sql, self.rpc = [], 0
            def cursor(self):
                return Cursor(self)

        c = Connection()
        checks = []
        result = module.publish_changeset_outbox_in_transaction(
            c, event["changeset_id"], expected_book_event_id=event["event_id"],
            authorization_provider=lambda q: deepcopy(F["trusted_authorization"]),
            before_publication=lambda: checks.append(c.rpc))
        self.assertEqual((c.rpc, checks, result["event_positions"]), (1, [0], [1]))
        for guard in ("missing-provider", "expired-after-lock", "wrong-event"):
            c = Connection()
            def expire():
                raise Denied("expiry-after-lock")
            with self.subTest(guard=guard), self.assertRaises((Denied, RuntimeError)):
                module.publish_changeset_outbox_in_transaction(
                    c, event["changeset_id"],
                    expected_book_event_id="wrong" if guard == "wrong-event" else event["event_id"],
                    authorization_provider=None if guard == "missing-provider" else
                    lambda q: deepcopy(F["trusted_authorization"]),
                    before_publication=expire if guard == "expired-after-lock" else None)
            self.assertEqual(c.rpc, 0)

    def test_no_policy_unbound_before_io(self):
        s = Fake()
        with self.assertRaises(Denied):
            authority(s, policy=None).publish("g", REC)
        self.assertEqual((s.attempts, s.effects), ({}, 0))

    def test_replay_fresh_checks_without_second_effect(self):
        s = Fake()
        a = authority(s)
        result = a.publish("g", REC)
        self.assertEqual(a.publish("g", REC), result)
        self.assertEqual(s.effects, 1)
        self.assertEqual(set(result), {"changeset_id", "published_count", "event_positions"})

    def test_revoke_blocks_retry_but_preserves_history(self):
        s = Fake()
        authority(s).publish("g", REC)
        authority(s, "approver").revoke("g")
        with self.assertRaises(Denied):
            authority(s).publish("g", REC)
        self.assertEqual(s.effects, 1)
        self.assertIn(("g", REC), s.phases)

    def test_executor_cannot_revoke(self):
        with self.assertRaises(Denied):
            authority(Fake()).revoke("g")

    def test_failure_rolls_back_effect_and_success_receipt(self):
        for failure in ("fail_save", "fail_commit"):
            with self.subTest(failure=failure):
                s = Fake()
                setattr(s, failure, True)
                with self.assertRaises(RuntimeError):
                    authority(s).publish("g", REC)
                self.assertEqual(s.effects, 0)
                self.assertNotIn(("g", REC), s.phases)
                self.assertEqual(set(s.attempts.values()), {"unresolved"})

    def test_expiry_after_authority_or_domain_lock(self):
        for delay in ("expire_at_lock", "expire_at_domain_lock"):
            s = Fake()
            setattr(s, delay, True)
            with self.assertRaises(Denied):
                authority(s).publish("g", REC)
            self.assertEqual(s.effects, 0)

    def test_lock_order_clock_then_effect_then_commit(self):
        s = Fake()
        authority(s).publish("g", REC)
        self.assertLess(s.log.index(("share", ["policy", "principal"])),
                        s.log.index(("exclusive", ["g"])))
        i = s.log.index("domain-locks")
        self.assertEqual(s.log[i:i+3], ["domain-locks", "clock", "effect"])
        self.assertEqual(s.log[-1], "commit")

    def test_predecessor_tamper_and_causal_tamper_denied(self):
        for mutate in (lambda s: s.phases["g", "IMPORT_STAGED"]["operation_return"].update(committed=False),
                       lambda s: s.events[REC].update(payload_hash="0"*64),
                       lambda s: s.objects["g"]["body"]["scope"].update(source_id="bad"),
                       lambda s: s.phases["g", "IMPORT_STAGED"]["metadata"].update(event_id="bad")):
            s = Fake()
            mutate(s)
            with self.assertRaises(ValueError):
                authority(s).publish("g", REC)
            self.assertEqual(s.effects, 0)

    def test_numeric_policy_mismatch_denied(self):
        s = Fake()
        p = Policy(POLICY.acceptance_ref, 20000, 10800, 5000, 1000)
        with self.assertRaises(Denied):
            authority(s, policy=p).publish("g", REC)

    def test_read_exact_receipt_no_writes_and_fresh_each_call(self):
        s, issuer = read_fixture()
        before = deepcopy((s.objects, s.events, s.phases, s.attempts))
        r = issuer({"recorded_event": s.events[REC]})
        self.assertEqual(set(r), {"schema", "authorization_ref", "decision", "purpose", "scope"})
        self.assertEqual(r["scope"], SCOPE)
        issuer({"recorded_event": s.events[REC]})
        self.assertEqual(s.reads, 2)
        self.assertEqual((s.objects, s.events, s.phases, s.attempts), before)

    def test_read_separate_validation_outlives_execution_expiry_not_revoke(self):
        s, issuer = read_fixture()
        s.objects["g"]["body"]["expires_at"] = EARLY
        issuer({"recorded_event": s.events[REC]})
        s.objects["g"]["status"] = "revoked"
        with self.assertRaises(Denied):
            issuer({"recorded_event": s.events[REC]})

    def test_read_missing_permission_expiry_membership_or_policy_denied(self):
        mutations = [
            lambda s: s.objects["v"]["body"].update(permissions=["VALIDATE_RECORDED_DELIVERY"]),
            lambda s: s.objects["v"]["body"].update(expires_at=NOW),
            lambda s: s.objects["principal"].update(status="revoked"),
            lambda s: s.objects["policy"].update(status="revoked"),
            lambda s: s.objects["v"].update(status="revoked"),
            lambda s: s.objects["proof"].update(status="revoked"),
            lambda s: s.objects["verifier"].update(status="revoked"),
            lambda s: s.objects["proof"]["body"].update(expires_at=NOW),
            lambda s: s.objects["proof"]["body"].update(catalog_receipt_ref="SYNTHETIC-WRONG-RECEIPT"),
            lambda s: s.events[PUB]["payload"].update(recorded_payload_hash="0"*64),
        ]
        for mutate in mutations:
            s, issuer = read_fixture()
            mutate(s)
            with self.assertRaises(ValueError):
                issuer({"recorded_event": s.events[REC]})

    def test_read_wrong_target_before_snapshot_and_query_mutation_denied(self):
        s, issuer = read_fixture()
        issuer.binding = Binding("exec", "wrong-target", F["school_id"])
        with self.assertRaises(Denied):
            issuer({"recorded_event": s.events[REC]})
        self.assertEqual(s.reads, 0)
        s, issuer = read_fixture()
        q = deepcopy(s.events[REC])
        q["school_id"] = "wrong"
        with self.assertRaises(Denied):
            issuer({"recorded_event": q})

    def test_sql_source_freshness_and_transaction_ownership(self):
        self.assertIn("clock_timestamp()", VALIDATION_SQL)
        self.assertNotIn("now()", VALIDATION_SQL)
        for verb in ("INSERT ", "UPDATE ", "DELETE "):
            self.assertNotIn(verb, VALIDATION_SQL)
        tree = ast.parse((ROOT / "database/outbox_publisher.py").read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "publish_changeset_outbox_in_transaction")
        calls = [n.func.attr for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertNotIn("commit", calls)
        self.assertNotIn("connect", calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
