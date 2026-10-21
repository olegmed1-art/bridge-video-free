"""Unbound-by-default Books authority adapters. No connections at import time.

Only a trusted host may supply Policy/Binding/Store. No HTTP registration,
principal provisioning, policy defaults or migration execution is provided.
PostgresStore owns SQL; injected effects must use its current transaction.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from uuid import uuid4

from bridge_contracts.book_material import digest
from bridge_contracts.book_world_events import (
    AUTH_SCHEMA, PURPOSE, CORE_FIELDS, validate_authorization,
    validate_book_outbox_event,
)

REC = "DELIVER_RECORDED_OUTBOX"
PUB = "DELIVER_PUBLISHED_OUTBOX"
READ = frozenset(("VALIDATE_RECORDED_DELIVERY", "VALIDATE_PUBLISHED_DELIVERY"))
ACTIONS = frozenset(("IMPORT_STAGED", REC, "ACTIVATE_REVIEWED_WORLD", PUB))


class Denied(ValueError):
    pass


def need(ok, code="BOOK_AUTHORITY_DENIED"):
    if not ok:
        raise Denied(code)


def instant(value):
    need(type(value) is str)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    need(result.tzinfo is not None)
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class Policy:
    acceptance_ref: str
    execution_ttl_seconds: float
    validation_ttl_seconds: float
    statement_timeout_ms: int
    lock_timeout_ms: int

    def check(self):
        need(type(self.acceptance_ref) is str and bool(self.acceptance_ref.strip()))
        for value in (self.execution_ttl_seconds, self.validation_ttl_seconds):
            need(type(value) in (int, float) and math.isfinite(value) and value > 0)
        for value in (self.statement_timeout_ms, self.lock_timeout_ms):
            need(type(value) is int and value > 0)


@dataclass(frozen=True)
class Binding:
    principal: str
    target: str
    school: str


def window(body, now, limit):
    approved, start, end = (instant(body[k]) for k in
                            ("approved_at", "not_before", "expires_at"))
    need(approved <= start < end and (end - approved).total_seconds() <= limit)
    need(start <= instant(now) < end, "BOOK_AUTHORITY_EXPIRED")


def active(obj, kind):
    need(type(obj) is dict and obj["kind"] == kind and obj["status"] == "active")
    need(type(obj["revision"]) is int and obj["revision"] >= 0)
    return obj["body"]


def authority(body, dependencies, binding, policy, actions):
    need(body["scope"]["school_id"] == binding.school and body["target"] == binding.target)
    principal = active(dependencies[body["principal_id"]], "principal")
    pol = active(dependencies[body["policy_id"]], "policy")
    need(principal["principal"] == binding.principal
         and principal["school_id"] == binding.school
         and principal["target"] == binding.target)
    need(set(actions) <= set(principal["actions"]))
    need(pol["acceptance_ref"] == policy.acceptance_ref
         and pol["school_id"] == binding.school
         and pol["target"] == binding.target)
    need(pol["limits"] == {
        "execution_ttl_seconds": policy.execution_ttl_seconds,
        "validation_ttl_seconds": policy.validation_ttl_seconds,
        "statement_timeout_ms": policy.statement_timeout_ms,
        "lock_timeout_ms": policy.lock_timeout_ms,
    }, "BOOK_AUTHORITY_POLICY_LIMITS")


def receipt(scope, ref):
    core = {"schema": "book-world-reviewed-v1",
            **{key: deepcopy(scope[key]) for key in CORE_FIELDS - {"schema"}}}
    value = {"schema": AUTH_SCHEMA, "authorization_ref": ref, "decision": "allowed",
             "purpose": PURPOSE, "scope": deepcopy(scope)}
    validate_authorization(value, core, school_id=scope["school_id"],
                           changeset_id=scope["recorded_changeset_id"],
                           require_allowed=True)
    return value


def validate_catalog(proof, grant, recorded, recorded_phase, verifier, now):
    """Validate late append-only evidence; never authenticate arbitrary input."""
    body = active(proof, "catalog")
    rules = grant["proof_predicates"]
    verify = active(verifier, "verifier")
    need(verify["principal"] == body["verifier_principal"]
         == rules["verifier_principal"]
         and verify["profile"] == rules["profile"]
         and verify["profile_version"] == rules["profile_version"]
         and verify["evidence_schema"] == rules["evidence_schema"]
         and verify["target"] == rules["catalog_target"])
    need(body["grant_id"] == grant["grant_id"]
         and body["approval_revision"] == grant["approval_revision"]
         and body["scope"] == grant["scope"])
    need(body["profile"] == rules["profile"]
         and body["profile_version"] == rules["profile_version"]
         and body["evidence"]["schema"] == rules["evidence_schema"]
         and body["catalog_target"] == rules["catalog_target"])
    need(body["evidence_hash"] == digest(body["evidence"])
         and type(body["catalog_receipt_ref"]) is str
         and bool(body["catalog_receipt_ref"].strip()))
    need(instant(body["verified_at"]) <= instant(now) < instant(body["expires_at"]))
    age = rules["max_age_seconds"]
    need(type(age) in (int, float) and math.isfinite(age) and age > 0)
    need((instant(now) - instant(body["verified_at"])).total_seconds() <= age)
    # Lower bound, not a fabricated commit timestamp. The trusted catalog
    # recorder must have read this durable committed predecessor phase.
    need(instant(recorded_phase["effect_observed_at"]) <= instant(body["verified_at"]))
    need(body["recorded_phase_hash"] == digest(recorded_phase))
    need(recorded["outbox_status"] == "published")
    for field in ("event_id", "changeset_id", "payload_hash", "event_position"):
        need(body["recorded_" + field] == recorded[field])
    validate_book_outbox_event(recorded, authorization=receipt(grant["scope"], grant["grant_id"]))


class Authority:
    def __init__(self, *, store=None, policy=None, binding=None):
        self.store, self.policy, self.binding = store, policy, binding

    def bound(self):
        need(self.store is not None and type(self.policy) is Policy
             and type(self.binding) is Binding, "BOOK_AUTHORITY_UNBOUND")
        self.policy.check()
        need(all(type(v) is str and bool(v.strip()) for v in
                 (self.binding.principal, self.binding.target, self.binding.school)))
        need(self.store.target == self.binding.target, "BOOK_AUTHORITY_TARGET_MISMATCH")

    def ingest_catalog(self, grant_id, locator, *, verifier=None):
        from .books_catalog import ingest
        return ingest(self, grant_id, locator, verifier)

    def activate_world(self, grant_id, proof_id):
        from .books_catalog import activate
        return activate(self, grant_id, proof_id)

    def import_staged(self, grant_id, bundle, review_receipt, *, identity_receipt=None):
        """Guarded source import only; no catalog call or WORLD activation."""
        self.bound()
        bundle, review_receipt, identity_receipt = deepcopy(
            (bundle, review_receipt, identity_receipt))
        from bridge_contracts.book_world import build_world_publication, publication_version, world_uuid
        # Pure validated computation. No source/DB/network effects.
        publication = build_world_publication(bundle, review_receipt, identity_receipt)
        attempt = str(uuid4())
        self.store.begin_attempt(attempt, grant_id, "IMPORT_STAGED", self.binding, self.policy)
        with self.store.transaction(self.policy) as tx:
            initial = tx.object(grant_id)
            g = initial["body"]
            deps = tx.lock_states(sorted({g["principal_id"], g["policy_id"]}), exclusive=False)
            current = tx.lock_states([grant_id], exclusive=True)[grant_id]
            need(current["body"] == initial["body"])
            g = active(current, "grant")
            need(g["grant_id"] == grant_id and g["executor"] == self.binding.principal
                 and g["approver"] != g["executor"])
            need(type(g["approval_revision"]) is int and g["approval_revision"] > 0)
            needed = {"IMPORT_STAGED", REC}
            need(needed <= set(g["actions"]) <= ACTIONS)
            authority(g, deps, self.binding, self.policy, needed)
            window(g, tx.clock(), self.policy.execution_ttl_seconds)
            planned = {
                "school_id": self.binding.school, "source_id": g["scope"]["source_id"],
                "run": publication["run"], "publication_hash": publication["publication_hash"],
                "stable_keys": [r["stable_key"] for r in publication["records"]],
                "version_ids": [world_uuid("version", self.binding.school,
                    r["stable_key"] + ":" + publication_version(r, review_receipt, identity_receipt))
                    for r in publication["records"]],
                "recorded_changeset_id": world_uuid(
                    "changeset", self.binding.school, publication["run"]["run_id"]),
            }
            need(planned == g["scope"], "BOOK_AUTHORITY_IMPORT_SCOPE")
            auth = receipt(planned, grant_id)
            input_hash = digest({"scope": planned, "bundle": bundle,
                                 "review_receipt": review_receipt,
                                 "identity_receipt": identity_receipt})
            prior = tx.phase(grant_id, "IMPORT_STAGED")
            if prior is not None:
                need(prior["input_hash"] == input_hash
                     and prior["scope"] == planned
                     and prior["approval_revision"] == g["approval_revision"],
                     "BOOK_AUTHORITY_REPLAY_CONFLICT")
                actual = tx.event(planned, REC)
                validate_book_outbox_event(actual, authorization=auth)
                validate_predecessor(prior, actual, planned, REC)
                window(g, tx.clock(), self.policy.execution_ttl_seconds)
                result = deepcopy(prior["operation_return"])
            else:
                def before_effect():
                    window(g, tx.clock(), self.policy.execution_ttl_seconds)
                before_effect()
                result = tx.persist(
                    bundle, review_receipt, identity_receipt, planned, auth,
                    before_effect=before_effect)
                actual = tx.event(planned, REC)
                validate_book_outbox_event(actual, authorization=auth)
                saved = {
                    "scope": planned, "approval_revision": g["approval_revision"],
                    "state_revision": current["revision"], "input_hash": input_hash,
                    "operation_return": deepcopy(result), "metadata": actual,
                    "effect_observed_at": tx.clock(),
                }
                validate_predecessor(saved, actual, planned, REC)
                tx.save_phase(grant_id, "IMPORT_STAGED", saved)
            tx.finish_attempt(attempt, "success")
        return result

    def publish(self, grant_id, phase):
        """Only the two outbox phases are wired in this minimal delta."""
        self.bound()
        need(phase in (REC, PUB), "BOOK_AUTHORITY_PHASE_NOT_IMPLEMENTED")
        attempt = str(uuid4())
        self.store.begin_attempt(attempt, grant_id, phase, self.binding, self.policy)
        try:
            with self.store.transaction(self.policy) as tx:
                # Immutable approval read determines a closed dependency set.
                initial = tx.object(grant_id)
                g = initial["body"]
                predecessor = tx.phase(grant_id, "IMPORT_STAGED" if phase == REC
                                       else "ACTIVATE_REVIEWED_WORLD")
                need(predecessor is not None, "BOOK_AUTHORITY_PREDECESSOR_REQUIRED")
                ids = {g["principal_id"], g["policy_id"]}
                if phase == PUB:
                    from .books_catalog import dependencies
                    ids.update((g["proof_predicates"]["verifier_id"],
                                predecessor["catalog_proof_id"]))
                    ids.update(dependencies(g))
                ids = sorted(ids)
                deps = tx.lock_states(ids, exclusive=False)
                current = tx.lock_states([grant_id], exclusive=True)[grant_id]
                need(current["body"] == initial["body"])
                g = active(current, "grant")
                need(g["grant_id"] == grant_id and g["executor"] == self.binding.principal
                     and g["approver"] != g["executor"])
                need(type(g["approval_revision"]) is int and g["approval_revision"] > 0)
                need(set(g["actions"]) <= ACTIONS and phase in g["actions"])
                authority(g, deps, self.binding, self.policy, [phase])
                window(g, tx.clock(), self.policy.execution_ttl_seconds)
                auth = receipt(g["scope"], grant_id)
                event = tx.event(g["scope"], phase)
                parent = tx.event(g["scope"], REC) if phase == PUB else None
                validate_book_outbox_event(event, authorization=auth, recorded_event=parent)
                # Persisted source/WORLD receipts are required, not minted here.
                need(predecessor["scope"] == g["scope"]
                     and predecessor["approval_revision"] == g["approval_revision"])
                validate_predecessor(predecessor, event, g["scope"], phase)
                if phase == PUB:
                    if g["proof_predicates"].get("state_ids"):
                        from .books_catalog import check_dependencies
                        check_dependencies(g, deps)
                    recorded_phase = tx.phase(grant_id, REC)
                    need(recorded_phase is not None)
                    need(deps[predecessor["catalog_proof_id"]]["body"]["catalog_receipt_ref"]
                         == event["payload"]["catalog_receipt_ref"],
                         "BOOK_AUTHORITY_CATALOG_RECEIPT_MISMATCH")
                    validate_operation(recorded_phase["operation_return"], parent)
                    validate_catalog(deps[predecessor["catalog_proof_id"]], g, parent,
                                     recorded_phase, deps[g["proof_predicates"]["verifier_id"]],
                                     tx.clock())
                # Input hash excludes mutable outbox position/status for idempotent retry.
                input_hash = digest({"phase": phase, "scope": g["scope"],
                                     "event_id": event["event_id"],
                                     "payload_hash": event["payload_hash"],
                                     "predecessor": predecessor})
                prior = tx.phase(grant_id, phase)
                if prior is not None:
                    need(prior["input_hash"] == input_hash, "BOOK_AUTHORITY_REPLAY_CONFLICT")
                    validate_operation(prior["operation_return"], event)
                    result = deepcopy(prior["operation_return"])
                    window(g, tx.clock(), self.policy.execution_ttl_seconds)
                else:
                    def before_effect():
                        now = tx.clock()
                        window(g, now, self.policy.execution_ttl_seconds)
                        if phase == PUB:
                            validate_catalog(
                                deps[predecessor["catalog_proof_id"]], g, parent,
                                recorded_phase, deps[g["proof_predicates"]["verifier_id"]], now)
                    before_effect()
                    result = tx.publish(event, auth, before_effect=before_effect)
                    fresh = tx.event(g["scope"], phase)
                    validate_book_outbox_event(fresh, authorization=auth, recorded_event=parent)
                    validate_operation(result, fresh)
                    if parent is not None:
                        need(fresh["event_position"] > parent["event_position"])
                    tx.save_phase(grant_id, phase, {
                        "scope": g["scope"], "approval_revision": g["approval_revision"],
                        "state_revision": current["revision"], "input_hash": input_hash,
                        "operation_return": deepcopy(result), "metadata": fresh,
                        "effect_observed_at": tx.clock(),
                    })
                tx.finish_attempt(attempt, "success")
            # No return from inside transaction: commit failure is not success.
            return result
        except Exception:
            # Do not turn uncertain commit into a definitive failure.
            # The durable attempt/phase are reconciled by a later authorized read.
            raise

    def revoke(self, grant_id):
        """Not bound to public routing; only verified existing approver may call."""
        self.bound()
        with self.store.transaction(self.policy) as tx:
            first = tx.object(grant_id)
            g = first["body"]
            deps = tx.lock_states(sorted({g["approver_principal_id"], g["policy_id"]}), exclusive=False)
            row = tx.lock_states([grant_id], exclusive=True)[grant_id]
            need(row["body"] == g and row["kind"] == "grant")
            authority({**g, "principal_id": g["approver_principal_id"]},
                      deps, self.binding, self.policy, ["REVOKE_BOOKS_GRANT"])
            need(g["approver"] == self.binding.principal)
            if row["status"] != "revoked":
                tx.revoke(grant_id, row["revision"], self.binding.principal)
        return None


def validate_predecessor(saved, event, scope, phase):
    value = saved["operation_return"]
    base = {"changeset_id", "version_ids", "canon_activation_performed",
            "dry_run", "committed", "receipt_contract", "publication_authorization_status"}
    expected = (base | {"schema", "new_versions", "authority_lane", "publication_hash", "outbox_delivery"}
                if phase == REC else base | {"publication"})
    need(type(value) is dict and set(value) == expected)
    need(value["changeset_id"] == event["changeset_id"]
         and value["version_ids"] == scope["version_ids"]
         and value["canon_activation_performed"] is False
         and value["dry_run"] is False and value["committed"] is True
         and value["publication_authorization_status"] == "allowed")
    if phase == REC:
        need(value["schema"] == "book-world-reviewed-v1"
             and value["receipt_contract"] == "book-world-import-receipt-v2"
             and value["authority_lane"] == "WORLD_EXTERNAL"
             and value["publication_hash"] == scope["publication_hash"]
             and value["outbox_delivery"] == "REQUIRES_READBACK"
             and type(value["new_versions"]) is int
             and 0 <= value["new_versions"] <= len(scope["version_ids"]))
    else:
        need(value["receipt_contract"] == "book-world-publication-receipt-v2"
             and value["publication"] == "WORLD_VERIFIED")
    # Retained metadata is the actual pre-delivery event; only delivery fields
    # may evolve after it was captured, never payload/identity/causal metadata.
    need({k: v for k, v in saved["metadata"].items() if k not in ("outbox_status", "event_position")}
         == {k: v for k, v in event.items() if k not in ("outbox_status", "event_position")})


def validate_operation(value, event):
    need(type(value) is dict and set(value) == {"changeset_id", "published_count", "event_positions"})
    need(value["changeset_id"] == event["changeset_id"]
         and type(value["published_count"]) is int and value["published_count"] == 1
         and event["outbox_status"] == "published"
         and value["event_positions"] == [event["event_position"]])


class ReadIssuer:
    def __init__(self, *, store=None, policy=None, binding=None, validation_id=None,
                 retained=None):
        self.store, self.policy, self.binding = store, policy, binding
        self.validation_id, self.retained = validation_id, deepcopy(retained)

    def __call__(self, query):
        Authority(store=self.store, policy=self.policy, binding=self.binding).bound()
        need(type(query) is dict and set(query) == {"recorded_event"})
        need(type(self.validation_id) is str and self.retained is not None)
        # Store contract: ONE fresh primary READ COMMITTED read-only SELECT,
        # joining ledger + domain metadata. No cached RR state or writes.
        snap = self.store.validation_snapshot(self.validation_id, self.policy)
        v = active(snap["validation"], "validation")
        need(v["principal"] == self.binding.principal)
        authority(v, snap["objects"], self.binding, self.policy, READ)
        need(set(v["permissions"]) == READ)
        window(v, snap["now"], self.policy.validation_ttl_seconds)
        need(v["retained_hash"] == digest(self.retained))
        need(set(v["parents"]) == {REC, PUB})
        auth = receipt(v["scope"], self.validation_id)
        for phase in (REC, PUB):
            parent = snap["objects"][v["parents"][phase]]
            g = active(parent, "grant")  # Expiry alone does not change active state.
            need(g["scope"] == v["scope"] and phase in g["actions"])
            need(g["target"] == self.binding.target)
            active(snap["objects"][g["principal_id"]], "principal")
            active(snap["objects"][g["policy_id"]], "policy")
            saved = snap["phases"][phase]
            need(saved["scope"] == v["scope"]
                 and saved["approval_revision"] == g["approval_revision"])
            need(saved == self.retained["phases"][phase])
            event = snap["recorded"] if phase == REC else snap["published"]
            need(saved["metadata"] == event)
            validate_operation(saved["operation_return"], event)
            predecessor = snap["predecessors"][phase]
            need(predecessor["scope"] == v["scope"]
                 and predecessor["approval_revision"] == g["approval_revision"])
            validate_predecessor(predecessor, event, v["scope"], phase)
            retained_return = (self.retained["import_receipt"] if phase == REC else
                               self.retained["snapshot"]["publication_receipt"])
            need(predecessor["operation_return"] == retained_return)
        need(snap["recorded"] == query["recorded_event"])
        need(snap["recorded"] == self.retained["snapshot"]["recorded_event"]
             and snap["published"] == self.retained["snapshot"]["published_event"])
        need(self.retained["snapshot"]["recorded_outbox_receipt"]
             == snap["phases"][REC]["operation_return"])
        need(self.retained["snapshot"]["published_outbox_receipt"]
             == snap["phases"][PUB]["operation_return"])
        # Conservative read policy: current proof/verifier must remain active
        # and proof must still meet its approved freshness bounds. A validation
        # binding cannot extend a revoked or expired catalog proof.
        published_grant = snap["objects"][v["parents"][PUB]]["body"]
        if published_grant["proof_predicates"].get("state_ids"):
            from .books_catalog import check_dependencies
            check_dependencies(published_grant, snap["objects"])
        activation = snap["predecessors"][PUB]
        need(snap["objects"][activation["catalog_proof_id"]]["body"]["catalog_receipt_ref"]
             == snap["published"]["payload"]["catalog_receipt_ref"],
             "BOOK_AUTHORITY_CATALOG_RECEIPT_MISMATCH")
        validate_catalog(
            snap["objects"][activation["catalog_proof_id"]],
            published_grant, snap["recorded"], snap["phases"][REC],
            snap["objects"][published_grant["proof_predicates"]["verifier_id"]],
            snap["now"])
        validate_book_outbox_event(snap["recorded"], authorization=auth)
        validate_book_outbox_event(snap["published"], authorization=auth,
                                  recorded_event=snap["recorded"])
        need(snap["published"]["event_position"] > snap["recorded"]["event_position"])
        return deepcopy(auth)
