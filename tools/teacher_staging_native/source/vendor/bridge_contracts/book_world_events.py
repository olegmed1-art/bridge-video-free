"""Canonical flat Books v2 wire protocol and trusted-operator scope checks.

Wire status is evidence, never permission. Authorization is supplied separately
by a trusted host, validated against the actual Recorded metadata before RPC.
No helper authenticates an issuer, authorizes WORLD, or proves catalog delivery.
"""
from __future__ import annotations
from copy import deepcopy
import re
from uuid import UUID
from .book_material import digest
from .book_world import WORLD_SCHEMA, world_uuid

AUTH_SCHEMA = "book-world-outbox-authorization-v1"
PURPOSE = "BOOK_WORLD_OUTBOX_DELIVERY_ONLY"
RECORDED = "BookWorldRecorded"
PUBLISHED = "BookWorldPublished"
RECORDED_CONTRACT = "book-world-recorded-v2"
PUBLISHED_CONTRACT = "book-world-published-v2"
IMPORT_RECEIPT_CONTRACT = "book-world-import-receipt-v2"
PUBLICATION_RECEIPT_CONTRACT = "book-world-publication-receipt-v2"
CORE_FIELDS = {"schema", "publication_hash", "run", "source_id", "stable_keys", "version_ids"}
DELIVERY_FIELDS = {"schema", "publication_hash", "catalog_receipt_ref"}
V2_FIELDS = {"event_contract", "publication_authorization_status"}
CAUSAL_FIELDS = {"recorded_event_id", "recorded_changeset_id", "recorded_payload_hash"}
SCOPE_FIELDS = {"school_id", "source_id", "run", "publication_hash",
                "stable_keys", "version_ids", "recorded_changeset_id"}
META_FIELDS = {"event_id", "event_type", "aggregate_version", "school_id",
               "changeset_id", "partition_key", "payload", "payload_hash",
               "changeset_status", "outbox_status", "event_position"}
STATUSES = ("allowed", "blocked", "unknown")


class BookWorldEventError(ValueError):
    pass


def _require(ok, code):
    if not ok:
        raise BookWorldEventError(code)


def _fields(value, expected, code):
    _require(type(value) is dict and set(value) == expected, code)


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 512


def _uuid(value):
    try:
        return type(value) is str and UUID(value).int != 0 and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _run(value):
    _fields(value, {"run_id", "model_version", "prompt_version"}, "BOOK_WORLD_OUTBOX_RUN")
    _require(all(_text(v) for v in value.values()), "BOOK_WORLD_OUTBOX_RUN")


def _core(value):
    _fields(value, CORE_FIELDS, "BOOK_WORLD_OUTBOX_PAYLOAD")
    _require(value["schema"] == WORLD_SCHEMA and _uuid(value["source_id"])
             and _hash(value["publication_hash"]), "BOOK_WORLD_OUTBOX_PAYLOAD")
    _run(value["run"])
    keys, versions = value["stable_keys"], value["version_ids"]
    _require(type(keys) is list and type(versions) is list
             and 0 < len(keys) == len(versions) <= 3, "BOOK_WORLD_OUTBOX_SCOPE")
    _require(all(type(k) is str and re.fullmatch(r"BOOK-[0-9a-f]{64}", k) for k in keys)
             and len(set(keys)) == len(keys), "BOOK_WORLD_OUTBOX_SCOPE")
    _require(all(_uuid(v) for v in versions) and len(set(versions)) == len(versions),
             "BOOK_WORLD_OUTBOX_SCOPE")


def _payload(value, kind, *, require_allowed=True):
    _require(kind in (RECORDED, PUBLISHED), "BOOK_WORLD_OUTBOX_EVENT_TYPE")
    fields = CORE_FIELDS | V2_FIELDS if kind == RECORDED else DELIVERY_FIELDS | V2_FIELDS | CAUSAL_FIELDS
    _fields(value, fields, "BOOK_WORLD_OUTBOX_SCHEMA")
    _require(value["event_contract"] == (RECORDED_CONTRACT if kind == RECORDED else PUBLISHED_CONTRACT),
             "BOOK_WORLD_OUTBOX_SCHEMA")
    status = value["publication_authorization_status"]
    _require(type(status) is str and status in STATUSES, "BOOK_WORLD_OUTBOX_AUTHORIZATION")
    if kind == RECORDED:
        _core({k: value[k] for k in CORE_FIELDS})
    else:
        _require(value["schema"] == "book-world-delivery-v1"
                 and _hash(value["publication_hash"]) and _text(value["catalog_receipt_ref"])
                 and _uuid(value["recorded_event_id"]) and _uuid(value["recorded_changeset_id"])
                 and _hash(value["recorded_payload_hash"]), "BOOK_WORLD_OUTBOX_PAYLOAD")
    _require(not require_allowed or status == "allowed", "BOOK_WORLD_OUTBOX_NOT_ALLOWED")


def validate_authorization(authorization, core, *, school_id, changeset_id,
                           require_allowed=False):
    """Validate a trusted host receipt; do not mint or authenticate permission."""
    _core(core)
    _require(_uuid(school_id) and _uuid(changeset_id), "BOOK_WORLD_OUTBOX_METADATA")
    _fields(authorization, {"schema", "authorization_ref", "decision", "purpose", "scope"},
            "BOOK_WORLD_OUTBOX_AUTHORIZATION")
    _require(authorization["schema"] == AUTH_SCHEMA and _text(authorization["authorization_ref"])
             and authorization["purpose"] == PURPOSE
             and type(authorization["decision"]) is str and authorization["decision"] in STATUSES,
             "BOOK_WORLD_OUTBOX_AUTHORIZATION")
    scope = authorization["scope"]
    _fields(scope, SCOPE_FIELDS, "BOOK_WORLD_OUTBOX_SCOPE")
    expected = {"school_id": school_id, "recorded_changeset_id": changeset_id,
                **{k: deepcopy(core[k]) for k in CORE_FIELDS - {"schema"}}}
    _require(scope == expected, "BOOK_WORLD_OUTBOX_SCOPE_MISMATCH")
    _require(changeset_id == world_uuid("changeset", school_id, core["run"]["run_id"]),
             "BOOK_WORLD_OUTBOX_CHANGESET")
    _require(not require_allowed or authorization["decision"] == "allowed",
             "BOOK_WORLD_OUTBOX_NOT_ALLOWED")


def _metadata(event, kind, *, delivered=False, require_allowed=True):
    _fields(event, META_FIELDS, "BOOK_WORLD_OUTBOX_METADATA")
    _require(_uuid(event["event_id"]) and _uuid(event["school_id"]) and _uuid(event["changeset_id"])
             and event["event_type"] == kind and type(event["aggregate_version"]) is int
             and event["aggregate_version"] == 1 and event["partition_key"] == "book-world",
             "BOOK_WORLD_OUTBOX_METADATA")
    _payload(event["payload"], kind, require_allowed=require_allowed)
    _require(event["payload_hash"] == digest(event["payload"]), "BOOK_WORLD_OUTBOX_PAYLOAD_HASH")
    _require(event["changeset_status"] == "committed", "BOOK_WORLD_OUTBOX_CHANGESET")
    position = event["event_position"]
    _require(event["outbox_status"] in ("pending", "published")
             and ((event["outbox_status"] == "pending" and position is None)
                  or (event["outbox_status"] == "published" and type(position) is int and position > 0)),
             "BOOK_WORLD_OUTBOX_DELIVERY")
    if delivered or event["outbox_status"] == "published":
        _require(event["outbox_status"] == "published" and type(position) is int and position > 0,
                 "BOOK_WORLD_OUTBOX_DELIVERY")
    payload, school = event["payload"], event["school_id"]
    if kind == RECORDED:
        key = payload["run"]["run_id"]
        expected_change, expected_event = world_uuid("changeset", school, key), world_uuid("event", school, key)
    else:
        key = payload["recorded_changeset_id"]
        expected_change, expected_event = world_uuid("publish", school, key), world_uuid("publish-event", school, key)
    _require(event["changeset_id"] == expected_change and event["event_id"] == expected_event,
             "BOOK_WORLD_OUTBOX_CHANGESET")


def build_recorded_event(core: dict, *, school_id: str, changeset_id: str,
                         authorization: dict) -> dict:
    validate_authorization(authorization, core, school_id=school_id, changeset_id=changeset_id)
    result = {**deepcopy(core), "event_contract": RECORDED_CONTRACT,
              "publication_authorization_status": authorization["decision"]}
    _payload(result, RECORDED, require_allowed=False)
    return result


def _causality(payload, recorded_event, school_id):
    _metadata(recorded_event, RECORDED, delivered=True)
    _require(recorded_event["school_id"] == school_id
             and all(payload[field] == recorded_event[key] for field, key in (
                 ("recorded_event_id", "event_id"), ("recorded_changeset_id", "changeset_id"),
                 ("recorded_payload_hash", "payload_hash")))
             and payload["publication_hash"] == recorded_event["payload"]["publication_hash"],
             "BOOK_WORLD_OUTBOX_CAUSAL_BINDING")


def build_published_event(recorded_event: dict, delivery_receipt: dict, *,
                          school_id: str, recorded_changeset_id: str,
                          authorization: dict) -> dict:
    _metadata(recorded_event, RECORDED, delivered=True)
    _require(recorded_event["school_id"] == school_id
             and recorded_event["changeset_id"] == recorded_changeset_id, "BOOK_WORLD_OUTBOX_SCOPE_MISMATCH")
    validate_book_outbox_event(recorded_event, authorization=authorization)
    _fields(delivery_receipt, DELIVERY_FIELDS, "BOOK_WORLD_OUTBOX_PAYLOAD")
    result = {**deepcopy(delivery_receipt), "event_contract": PUBLISHED_CONTRACT,
              "publication_authorization_status": recorded_event["payload"]["publication_authorization_status"],
              "recorded_event_id": recorded_event["event_id"],
              "recorded_changeset_id": recorded_event["changeset_id"],
              "recorded_payload_hash": recorded_event["payload_hash"]}
    _payload(result, PUBLISHED)
    _causality(result, recorded_event, school_id)
    return result


def validate_book_outbox_event(event: dict, *, authorization: dict,
                               recorded_event=None) -> None:
    """Bind actual DB metadata, causal parent and explicit trusted host scope."""
    _fields(event, META_FIELDS, "BOOK_WORLD_OUTBOX_METADATA")
    kind = event["event_type"]
    _require(type(kind) is str and kind in (RECORDED, PUBLISHED), "BOOK_WORLD_OUTBOX_EVENT_TYPE")
    _metadata(event, kind)
    if kind == PUBLISHED:
        _causality(event["payload"], recorded_event, event["school_id"])
        recorded = recorded_event
    else:
        recorded = event
    core = {k: deepcopy(recorded["payload"][k]) for k in CORE_FIELDS}
    validate_authorization(authorization, core, school_id=recorded["school_id"],
                           changeset_id=recorded["changeset_id"], require_allowed=True)
    _require(authorization["decision"] == event["payload"]["publication_authorization_status"],
             "BOOK_WORLD_OUTBOX_AUTHORIZATION")


def normalize_recorded_event(raw_event: dict) -> dict:
    """Explicit legacy read view, never an upgrade or publication permission."""
    if type(raw_event) is dict and set(raw_event) == CORE_FIELDS:
        _core(raw_event)
        return deepcopy(raw_event)
    _payload(raw_event, RECORDED)
    return {k: deepcopy(raw_event[k]) for k in CORE_FIELDS}
