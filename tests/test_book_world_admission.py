"""Local synthetic publication admission; no connection or source bytes required.

The cursor scripts delivered/stored rows, not PostgreSQL semantics or delivery.
Review receipts are test evidence only, never a real source-review attestation.
"""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bridge_contracts.book_material import InvalidBookMaterial, digest
from bridge_contracts.book_world import (
    WORLD_SCHEMA, build_world_publication, publication_version,
    render_teacher_book, world_uuid,
)
from database import book_world_persistence as persistence
from test_book_material_staging import bundle, record
from test_book_world import reviewed_fixture


SCHOOL = "11111111-1111-1111-1111-111111111111"
SOURCE = "22222222-2222-2222-2222-222222222222"
CHANGESET = "33333333-3333-3333-3333-333333333333"


def candidate_batch():
    value, receipt = reviewed_fixture()
    other = bundle([record(page=1)])["objects"][0]
    value["objects"].append(other)
    receipt["claims"].append({**deepcopy(receipt["claims"][0]),
        "object_id": other["object_id"], "anchor_sha256": digest(other["anchor"])})
    publication = build_world_publication(value, receipt)
    items = []
    for projected in publication["records"]:
        version_hash = publication_version(projected, receipt)
        items.append({**projected,
            "item_id": world_uuid("version", SCHOOL, projected["stable_key"] + ":" + version_hash),
            "authority_class": "external", "review_status": "reviewed",
            "version_status": "staged", "system_profile": "SYSTEM_NEUTRAL",
            "provenance": {"schema": WORLD_SCHEMA, "review_receipt": deepcopy(receipt),
                           "version_hash": version_hash, "source_id": SOURCE},
            "sources": [{"source_id": SOURCE, "status": "active",
                         "canonical_locator": value["source"]["locator"],
                         "source_locator": deepcopy(projected["content"]["citation"])}]})
    delivery = {"schema": "book-world-delivery-v1",
                "publication_hash": publication["publication_hash"],
                "catalog_receipt_ref": "synthetic-delivery-not-a-real-catalog"}
    return items, delivery


class PublicationCursor:
    def __init__(self, items, delivery):
        self.items, self.delivery = items, delivery
        self.writes = []
        self.rowcount = 1

    def execute(self, statement, params=None):
        sql = " ".join(statement.split())
        self.rows = []
        if sql.startswith("SELECT e.payload"):
            self.rows = [({"publication_hash": self.delivery["publication_hash"],
                          "source_id": SOURCE,
                          "version_ids": [i["item_id"] for i in self.items]},)]
        elif sql.startswith("SELECT kv.knowledge_version_id,kv.content,kv.provenance"):
            self.rows = [(i["item_id"], deepcopy(i["content"]), deepcopy(i["provenance"]))
                         + ((i["stable_key"],) if "ki.stable_key" in sql else ()) for i in self.items]
        elif sql.startswith("SELECT source_locator"):
            self.rows = [(i["sources"][0]["source_locator"],)
                         for i in self.items if i["item_id"] == params[0]]
        elif sql.startswith(("INSERT ", "UPDATE ")):
            self.writes.append((sql, params))
        else:
            assert sql.startswith(("SET LOCAL ", "SELECT pg_catalog.pg_advisory_xact_lock",
                                   "SELECT payload_hash FROM public.domain_event")), sql

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def publish(monkeypatch, items, delivery, cursor):
    # Source binding is supplied, not tested by this local cursor double.
    monkeypatch.setattr(persistence, "source_locator", lambda _cur, _school, _source, locator, **kw: locator)
    connection = SimpleNamespace(info=SimpleNamespace(transaction_status=0),
        transaction=lambda **kw: nullcontext(), cursor=lambda **kw: nullcontext(cursor))
    return persistence.publish_book_world(connection, school_id=SCHOOL,
        changeset_id=CHANGESET, delivery_receipt=delivery)


MALFORMED_PATHS = [
    ("content",), ("content", "conditions"), ("content", "exceptions"),
    ("content", "citation"), ("provenance",), ("provenance", "review_receipt"),
    ("provenance", "review_receipt", "claims"),
    ("provenance", "review_receipt", "claims", 0),
    ("provenance", "review_receipt", "claims", 0, "checks"),
]


@pytest.mark.parametrize("path", MALFORMED_PATHS, ids=lambda p: ".".join(map(str, p)))
@pytest.mark.parametrize("bad", [None, [None], "bad"], ids=["null", "list", "string"])
def test_malformed_stored_candidate_rejected_before_any_publication_write(monkeypatch, path, bad):
    items, delivery = candidate_batch()
    item = items[1]  # A good first row must not be partially published.
    target = item
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = deepcopy(bad)
    provenance = item["provenance"]
    if isinstance(provenance, dict):
        provenance["version_hash"] = publication_version(
            {"stable_key": item["stable_key"], "content": item["content"]}, provenance["review_receipt"])
    before = deepcopy(items)
    cursor = PublicationCursor(items, delivery)
    with pytest.raises(InvalidBookMaterial):
        publish(monkeypatch, items, delivery, cursor)
    assert cursor.writes == []  # Includes changeset, activation, event and outbox.
    assert items == before
    assert all(i["version_status"] == "staged" for i in items)


def test_valid_candidate_admission_preserves_teacher_citation_and_identity(monkeypatch):
    items, delivery = candidate_batch()
    before = deepcopy(items)
    cursor = PublicationCursor(items, delivery)
    result = publish(monkeypatch, items, delivery, cursor)
    assert result["version_ids"] == [i["item_id"] for i in items]
    assert sum(sql.startswith("UPDATE public.knowledge_version SET status='active'")
               for sql, _ in cursor.writes) == 1
    assert items == before
    for item in items:
        with pytest.raises(InvalidBookMaterial, match="BOOK_WORLD_NOT_ACTIVE"):
            render_teacher_book(item)
        # Simulate successful activation explicitly; the cursor is not a DB engine.
        answer = render_teacher_book({**item, "version_status": "active"})
        assert answer["knowledge_version_id"] == item["item_id"]
        assert answer["stable_key"] == item["stable_key"]
        assert answer["citations"] == [item["content"]["citation"]]
        assert answer["answer"] == item["content"]["statement"]


@pytest.mark.parametrize("relation", ["CONTRADICTS", "COUNTEREXAMPLE_OF", "SUPERSEDES"])
@pytest.mark.parametrize("incoming", [False, True], ids=["outgoing", "incoming"])
def test_related_atom_never_reaches_persistence_connection(relation, incoming):
    value, receipt = reviewed_fixture()
    value["objects"].append(bundle([record(page=1)])["objects"][0])
    selected, unselected = [o["object_id"] for o in value["objects"]]
    left, right = (unselected, selected) if incoming else (selected, unselected)
    value["relations"] = [{"from_id": left, "to_id": right, "type": relation}]
    before = deepcopy(value)
    # object() has no connection API: any connection use would fail this test.
    with pytest.raises(InvalidBookMaterial, match="BOOK_WORLD_RELATIONS_UNSUPPORTED"):
        persistence.persist_book_world(object(), value, receipt, school_id=SCHOOL, source_id=SOURCE)
    assert value == before
    assert value["processing"] == "STAGED"
    assert value["publication"] == "NOT_PUBLISHED"
