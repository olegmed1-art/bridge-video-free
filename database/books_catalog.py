"""Accepted source design, deliberately no real connector/profile/storage binding.

The abstract ports below are trusted host dependencies, never request callbacks.
There is no default implementation that asserts authenticity or durable acceptance.
Restricting ledger writes and attesting these ports remains a deployment gate.
"""
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import math
import re
from uuid import uuid4

from bridge_contracts.book_material import digest
from bridge_contracts.book_world_events import validate_book_outbox_event
from .books_authority import (
    need, active, authority, receipt, window, instant, validate_catalog,
    validate_operation, validate_predecessor, REC, PUB, ACTIONS,
)

ACTIVATE = "ACTIVATE_REVIEWED_WORLD"
CONTRACT = "books-catalog-ingestion-v1"


class CatalogConnector(ABC):
    @abstractmethod
    def read(self, locator, *, timeout_ms, max_bytes):
        """Authenticated authoritative raw bytes; enforce limit while receiving.

        Implementation must pin target and peer identity, prohibit redirects to
        other targets, reject partial/queue-only results and enforce timeout.
        """


class CatalogProfile(ABC):
    @abstractmethod
    def decode(self, raw):
        """Decode authenticated bytes into observed claims, never expected scope.

        Return exact normalized fields checked below. A journal bridge requires
        its own accepted authentic implementation; missing claims cannot be filled.
        """


class EvidenceStorage(ABC):
    @abstractmethod
    def retain(self, raw_hash, raw):
        """Idempotently retain immutable private bytes and return opaque locator."""

    @abstractmethod
    def read(self, locator, *, max_bytes):
        """Read exact immutable bytes, with an enforced bounded receive."""


@dataclass(frozen=True)
class VerifierBinding:
    descriptor: dict
    connector: CatalogConnector
    profile: CatalogProfile
    storage: EvidenceStorage
    clock: object

    def check(self):
        d = self.descriptor
        fields = {"acceptance_ref", "binding_id", "implementation_sha256", "principal",
                  "school_id", "catalog_target", "profile", "profile_version",
                  "evidence_schema", "semantics_version", "connector_id", "storage_id",
                  "freshness_policy_ref", "invalidation_policy_ref", "clock_policy_ref",
                  "state_ids", "max_raw_bytes", "timeout_ms", "max_age_seconds",
                  "max_clock_skew_seconds"}
        need(type(d) is dict and set(d) == fields, "CATALOG_UNBOUND")
        for key in fields - {"state_ids", "max_raw_bytes", "timeout_ms",
                             "max_age_seconds", "max_clock_skew_seconds"}:
            need(type(d[key]) is str and bool(d[key].strip()), "CATALOG_UNBOUND")
        need(re.fullmatch("[0-9a-f]{64}", d["implementation_sha256"]) is not None)
        need(type(d["state_ids"]) is list and len(d["state_ids"]) >= 3
             and len(set(d["state_ids"])) == len(d["state_ids"])
             and all(type(v) is str and v for v in d["state_ids"]), "CATALOG_UNBOUND")
        for key in ("max_raw_bytes", "timeout_ms"):
            need(type(d[key]) is int and d[key] > 0, "CATALOG_UNBOUND")
        for key in ("max_age_seconds", "max_clock_skew_seconds"):
            need(type(d[key]) in (int, float) and math.isfinite(d[key])
                 and d[key] > 0, "CATALOG_UNBOUND")
        need(isinstance(self.connector, CatalogConnector)
             and isinstance(self.profile, CatalogProfile)
             and isinstance(self.storage, EvidenceStorage) and callable(self.clock),
             "CATALOG_UNBOUND")


def dependencies(g):
    return set(g["proof_predicates"].get("state_ids", []))


def check_dependencies(g, deps):
    """Every mutable binding dependency participates in the same revoke protocol."""
    rules = g["proof_predicates"]
    ids = dependencies(g)
    need(len(ids) >= 3, "CATALOG_UNBOUND")
    for key in ids:
        row = deps[key]
        need(row["status"] == "active" and row["revision"] >= 0)
    # Roles and target scopes are immutable in the verifier's accepted descriptor.
    verifier = active(deps[rules["verifier_id"]], "verifier")
    d = verifier["binding"]
    need(set(d["state_ids"]) == ids and d["school_id"] == g["scope"]["school_id"])
    for key in ids:
        need(deps[key]["body"]["binding_hash"] == digest(d))
    need(d["principal"] == rules["verifier_principal"]
         and d["catalog_target"] == rules["catalog_target"]
         and d["profile"] == rules["profile"]
         and d["profile_version"] == rules["profile_version"]
         and d["evidence_schema"] == rules["evidence_schema"]
         and d["max_age_seconds"] == rules["max_age_seconds"])
    return d


def verify(binding, locator, g, recorded, phase):
    """Called only by ingestion, outside the effect transaction; no result input."""
    binding.check()
    d = binding.descriptor
    need(type(locator) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", locator),
         "CATALOG_LOCATOR_REJECTED")
    try:
        raw = binding.connector.read(locator, timeout_ms=d["timeout_ms"],
                                     max_bytes=d["max_raw_bytes"])
        need(type(raw) is bytes and 0 < len(raw) <= d["max_raw_bytes"],
             "CATALOG_RAW_LIMIT")
        claims = binding.profile.decode(raw)
    except Exception as exc:
        raise ValueError("CATALOG_UNAVAILABLE_OR_REJECTED") from exc
    need(type(claims) is dict and set(claims) == {
        "target", "school_id", "receipt_ref", "record_id", "revision",
        "durable", "authoritative", "scope", "recorded", "provenance"},
        "CATALOG_CLAIMS_REJECTED")
    need(claims["durable"] is True and claims["authoritative"] is True
         and claims["target"] == d["catalog_target"]
         and claims["school_id"] == d["school_id"]
         and claims["scope"] == g["scope"]
         and claims["provenance"] == "catalog_record", "CATALOG_CLAIMS_REJECTED")
    causal = {k: recorded[k] for k in ("event_id", "changeset_id", "payload_hash", "event_position")}
    need(claims["recorded"] == causal, "CATALOG_CAUSAL_REJECTED")
    for key in ("receipt_ref", "record_id", "revision"):
        need(type(claims[key]) is str and bool(claims[key].strip()))
    raw_hash = sha256(raw).hexdigest()
    raw_ref = binding.storage.retain(raw_hash, raw)
    need(type(raw_ref) is str and raw_ref
         and binding.storage.read(raw_ref, max_bytes=d["max_raw_bytes"]) == raw,
         "CATALOG_STORAGE_READBACK")
    verified_at = binding.clock()
    instant(verified_at)
    acceptance = {"schema": CONTRACT, "grant_id": g["grant_id"],
                  "approval_revision": g["approval_revision"],
                  "profile": d["profile"], "profile_version": d["profile_version"],
                  "claims": deepcopy(claims)}
    identity = {k: acceptance[k] for k in
                ("grant_id", "approval_revision", "profile", "profile_version")}
    identity.update(school_id=claims["school_id"], target=claims["target"],
                    record_id=claims["record_id"], receipt_ref=claims["receipt_ref"],
                    revision=claims["revision"])
    proof_id = "catalog:" + digest(identity)
    observation = {"proof_id": proof_id, "binding_hash": digest(d),
                   "verified_at": verified_at, "raw_sha256": raw_hash, "raw_ref": raw_ref}
    observation_id = digest(observation)
    evidence = {"schema": d["evidence_schema"], "ingestion_contract": CONTRACT,
                "acceptance": acceptance, "acceptance_hash": digest(acceptance),
                "binding_hash": digest(d), "observation_id": observation_id,
                "raw_sha256": raw_hash, "raw_ref": raw_ref}
    body = {
        "grant_id": g["grant_id"], "approval_revision": g["approval_revision"],
        "scope": deepcopy(g["scope"]), "verifier_principal": d["principal"],
        "profile": d["profile"], "profile_version": d["profile_version"],
        "catalog_target": d["catalog_target"], "catalog_receipt_ref": claims["receipt_ref"],
        "evidence": evidence, "evidence_hash": digest(evidence),
        "verified_at": verified_at, "expires_at": g["proof_predicates"]["proof_expires_at"],
        "recorded_phase_hash": digest(phase),
        **{"recorded_" + k: v for k, v in causal.items()},
    }
    return proof_id, acceptance, observation_id, observation, body


def gate(owner, tx, grant_id, *, proof_id=None, readonly=False):
    initial = tx.object(grant_id)
    g = initial["body"]
    ids = {g["principal_id"], g["policy_id"], g["proof_predicates"]["verifier_id"],
           *dependencies(g)}
    if proof_id is not None:
        ids.add(proof_id)
    deps = ({key: tx.object(key) for key in sorted(ids)} if readonly else
            tx.lock_states(sorted(ids), exclusive=False))
    current = tx.object(grant_id) if readonly else tx.lock_states([grant_id], exclusive=True)[grant_id]
    need(current["body"] == g)
    g = active(current, "grant")
    needed = {ACTIVATE, PUB}
    need(g["grant_id"] == grant_id and g["executor"] == owner.binding.principal
         and g["approver"] != g["executor"]
         and type(g["approval_revision"]) is int and g["approval_revision"] > 0
         and needed <= set(g["actions"]) <= ACTIONS)
    authority(g, deps, owner.binding, owner.policy, needed)
    window(g, tx.clock(), owner.policy.execution_ttl_seconds)
    descriptor = check_dependencies(g, deps)
    recorded = tx.event(g["scope"], REC)
    phase = tx.phase(grant_id, REC)
    need(phase is not None and phase["scope"] == g["scope"]
         and phase["approval_revision"] == g["approval_revision"]
         and phase["metadata"] == recorded)
    validate_book_outbox_event(recorded, authorization=receipt(g["scope"], grant_id))
    validate_operation(phase["operation_return"], recorded)
    return g, deps, descriptor, recorded, phase, current


def ingest(owner, grant_id, locator, binding):
    owner.bound()
    need(type(binding) is VerifierBinding, "CATALOG_UNBOUND")
    binding.check()  # no DB or catalog IO if unbound
    with owner.store.transaction(owner.policy, readonly=True) as tx:
        g, deps, d, recorded, phase, current = gate(owner, tx, grant_id, readonly=True)
        need(d == binding.descriptor, "CATALOG_BINDING_MISMATCH")
        before = deepcopy((g, deps, recorded, phase, current))
    proof_id, acceptance, observation_id, observation, body = verify(
        binding, locator, g, recorded, phase)
    attempt = str(uuid4())
    owner.store.begin_attempt(attempt, grant_id, "INGEST_CATALOG", owner.binding, owner.policy)
    with owner.store.transaction(owner.policy) as tx:
        existing = tx.catalog_acceptance(proof_id)
        g, deps, d, recorded, phase, current = gate(
            owner, tx, grant_id, proof_id=proof_id if existing is not None else None)
        original = deps.pop(proof_id, None)
        need((g, deps, recorded, phase, current) == before, "CATALOG_PRE_READ_CHANGED")
        now = tx.clock()
        window(g, now, owner.policy.execution_ttl_seconds)
        lag = (instant(now) - instant(body["verified_at"])).total_seconds()
        need(0 <= lag <= d["max_clock_skew_seconds"], "CATALOG_CLOCK_SKEW")
        need(instant(body["verified_at"]) < instant(body["expires_at"])
             and (instant(body["expires_at"]) - instant(body["verified_at"])).total_seconds()
             <= d["max_age_seconds"], "CATALOG_EXPIRY_BOUND")
        candidate = {"kind": "catalog", "body": body, "status": "active", "revision": 0}
        validate_catalog(candidate, g, recorded, phase,
                         deps[g["proof_predicates"]["verifier_id"]], now)
        # Under exclusive grant lock, one acceptance can create exactly one proof.
        # Another ingestion may have committed while this one waited for grant.
        latest = tx.catalog_acceptance(proof_id)
        need(existing == latest, "CATALOG_CONCURRENT_RETRY_REQUIRED")
        if existing is not None:
            need(existing == acceptance, "CATALOG_ACCEPTANCE_CONFLICT")
            need(original["body"]["evidence"]["acceptance"] == acceptance)
        # Last DB read may itself consume time. Recheck immediately before the
        # first ledger write, using the original proof on exact acceptance retry.
        now = tx.clock()
        window(g, now, owner.policy.execution_ttl_seconds)
        need(0 <= (instant(now) - instant(body["verified_at"])).total_seconds()
             <= d["max_clock_skew_seconds"], "CATALOG_CLOCK_SKEW")
        validate_catalog(original if existing is not None else candidate,
                         g, recorded, phase, deps[g["proof_predicates"]["verifier_id"]], now)
        if existing is None:
            tx.insert_catalog(proof_id, acceptance, body, owner.binding.principal)
        tx.save_observation(observation_id, observation)
        tx.finish_attempt(attempt, "success")
    return proof_id  # no fabricated WORLD/publication receipt


def activate(owner, grant_id, proof_id):
    owner.bound()
    need(type(proof_id) is str and proof_id.startswith("catalog:"))
    attempt = str(uuid4())
    owner.store.begin_attempt(attempt, grant_id, ACTIVATE, owner.binding, owner.policy)
    with owner.store.transaction(owner.policy) as tx:
        g, deps, d, recorded, phase, current = gate(owner, tx, grant_id, proof_id=proof_id)
        proof = deps[proof_id]
        e = proof["body"]["evidence"]
        need(e["ingestion_contract"] == CONTRACT and e["binding_hash"] == digest(d)
             and e["acceptance_hash"] == digest(e["acceptance"])
             and tx.catalog_acceptance(proof_id) == e["acceptance"],
             "CATALOG_INGESTION_REQUIRED")
        auth = receipt(g["scope"], grant_id)
        def before_effect():
            now = tx.clock()
            window(g, now, owner.policy.execution_ttl_seconds)
            validate_catalog(proof, g, recorded, phase,
                             deps[g["proof_predicates"]["verifier_id"]], now)
        before_effect()
        input_hash = digest({"scope": g["scope"], "proof_id": proof_id,
                             "proof": proof["body"], "recorded_phase": phase})
        prior = tx.phase(grant_id, ACTIVATE)
        if prior is not None:
            need(prior["input_hash"] == input_hash and prior["catalog_proof_id"] == proof_id
                 and prior["scope"] == g["scope"]
                 and prior["approval_revision"] == g["approval_revision"])
            actual = tx.event(g["scope"], PUB)
            validate_book_outbox_event(actual, authorization=auth, recorded_event=recorded)
            validate_predecessor(prior, actual, g["scope"], PUB)
            before_effect()
            result = deepcopy(prior["operation_return"])
        else:
            delivery = {"schema": "book-world-delivery-v1",
                        "publication_hash": g["scope"]["publication_hash"],
                        "catalog_receipt_ref": proof["body"]["catalog_receipt_ref"]}
            result = tx.activate(g["scope"], delivery, auth, before_effect=before_effect)
            actual = tx.event(g["scope"], PUB)
            validate_book_outbox_event(actual, authorization=auth, recorded_event=recorded)
            need(actual["payload"]["catalog_receipt_ref"] == delivery["catalog_receipt_ref"])
            saved = {"scope": g["scope"], "approval_revision": g["approval_revision"],
                     "state_revision": current["revision"], "input_hash": input_hash,
                     "catalog_proof_id": proof_id, "operation_return": deepcopy(result),
                     "metadata": actual, "effect_observed_at": tx.clock()}
            validate_predecessor(saved, actual, g["scope"], PUB)
            tx.save_phase(grant_id, ACTIVATE, saved)
        tx.finish_attempt(attempt, "success")
    return result
