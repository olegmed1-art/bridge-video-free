"""One synthetic canonical wire fixture consumed by producer and pinned Teacher."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from bridge_contracts.book_material import digest
from bridge_contracts.book_world import world_uuid
from bridge_contracts.book_world_events import build_recorded_event, build_published_event
from evolutionary_course import book_teacher_consumer as legacy
from evolutionary_course import book_teacher_delivery_consumer_v2 as teacher

FIXTURE = Path(__file__).parent / "fixtures/book_world_canonical_v2.json"

def canonical():
    return json.loads(FIXTURE.read_text())

def recorded_metadata(f):
    payload = deepcopy(f["recorded_payload"])
    return {"event_id": f["published_payload"]["recorded_event_id"],
            "event_type": "BookWorldRecorded", "aggregate_version": 1,
            "school_id": f["school_id"], "changeset_id": f["import_receipt"]["changeset_id"],
            "partition_key": "book-world", "payload": payload, "payload_hash": digest(payload),
            "changeset_status": "committed", "outbox_status": "published", "event_position": 1}

def test_producer_emits_exact_teacher_recorded_fixture_and_binds_without_rewriting():
    f = canonical()
    core = {k: deepcopy(f["recorded_payload"][k]) for k in teacher.RECORDED_BASE}
    actual = build_recorded_event(core, school_id=f["school_id"],
        changeset_id=f["import_receipt"]["changeset_id"], authorization=f["trusted_authorization"])
    assert actual == f["recorded_payload"]
    meta = recorded_metadata(f)
    meta["payload"], meta["payload_hash"] = actual, digest(actual)
    bound = teacher.bind_import_receipt_v2(legacy.build_card_map(f["packet"]),
        f["import_receipt"], meta, expected_school_id=f["school_id"], test_only=True)
    assert bound["observed_recorded_event_v2"]["payload"] == actual
    assert all(c["knowledge_item_id"] is None for c in bound["cards"])

def test_published_fixture_uses_full_actual_recorded_hash_and_causal_triple():
    f = canonical()
    receipt = {k: f["published_payload"][k] for k in teacher.PUBLISHED_BASE}
    actual = build_published_event(recorded_metadata(f), receipt, school_id=f["school_id"],
        recorded_changeset_id=f["import_receipt"]["changeset_id"],
        authorization=f["trusted_authorization"])
    assert actual == f["published_payload"]
    assert actual["recorded_payload_hash"] == digest(f["recorded_payload"])

def test_teacher_source_is_exact_pinned_bytes():
    from hashlib import sha256
    assert sha256(Path(teacher.__file__).read_bytes()).hexdigest() == "ec90bee83dc0d77a38b9023759a838098133a4004f03e65e85c02059089098ef"
    assert sha256(Path(legacy.__file__).read_bytes()).hexdigest() == "335c7b381c63530a5b27dff024c2bd80f8c0252b333c371b56e571a874dcb0a0"


def test_canonical_fixture_matches_one_checked_in_json_schema_and_teacher_fields():
    import jsonschema
    schema = json.loads((Path(__file__).parents[1]/"schemas/book-world-delivery-v2.schema.json").read_text())
    f = canonical()
    for key in ("recorded_payload","published_payload","import_receipt","publication_receipt"):
        jsonschema.Draft202012Validator(schema).validate(f[key])
    assert set(f["recorded_payload"]) == teacher.RECORDED_BASE|teacher.V2_FIELDS
    assert set(f["published_payload"]) == teacher.PUBLISHED_BASE|teacher.V2_FIELDS|teacher.PUBLISHED_RECORDED_REFS
    assert set(f["import_receipt"]) == teacher.IMPORT_BASE|teacher.RECEIPT_V2_FIELDS
    assert set(f["publication_receipt"]) == teacher.PUBLICATION_BASE|teacher.RECEIPT_V2_FIELDS
