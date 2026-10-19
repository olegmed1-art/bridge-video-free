"""Independent I2 synthetic rejection tests. No database, filesystem corpus, or network."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
import pytest
from bridge_contracts.book_world import WORLD_SCHEMA, build_world_publication, publication_version, world_uuid
from database import book_world_persistence as persistence
from test_book_world import reviewed_fixture
from test_book_world_admission import candidate_batch, PublicationCursor, publish, SCHOOL, SOURCE

class ReplayCursor:
    """Projects a stored synthetic version using requested SQL column names.

    This proves application checks only; it is not a PostgreSQL engine.
    """
    def __init__(self, row, item_id):
        self.row, self.item_id = row, item_id
        self.rows, self.writes, self.rowcount = [], [], 1

    def execute(self, statement, params=None):
        sql = " ".join(statement.split())
        self.rows = []
        if sql.startswith("SELECT knowledge_item_id FROM public.knowledge_item"):
            self.rows = [(self.item_id,)]
        elif "FROM public.knowledge_version WHERE knowledge_version_id=" in sql:
            columns = sql.split(" FROM ")[0].removeprefix("SELECT ").split(",")
            self.rows = [tuple(deepcopy(self.row[c.strip()]) for c in columns)]
        elif sql.startswith("SELECT source_locator FROM public.knowledge_version_source"):
            self.rows = [(deepcopy(self.row["content"]["citation"]),)]
        elif sql.startswith(("INSERT ", "UPDATE ")):
            self.writes.append((sql, params))
        else:
            assert sql.startswith(("SET LOCAL ", "SELECT pg_catalog.pg_advisory_xact_lock",
                                   "SELECT payload_hash FROM public.domain_event")), sql

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

def replay_fixture(monkeypatch, drift):
    value, receipt = reviewed_fixture()
    record = build_world_publication(value, receipt)["records"][0]
    item_id = world_uuid("item", SCHOOL, record["stable_key"])
    version_hash = publication_version(record, receipt)
    row = {"content": deepcopy(record["content"]),
           "provenance": {"schema": WORLD_SCHEMA, "version_hash": version_hash,
                          "review_receipt": receipt, "source_id": SOURCE},
           "status": "staged", "knowledge_item_id": item_id,
           "authority_class": "external", "review_status": "reviewed",
           "bidding_system_key": "SYSTEM_NEUTRAL", "method_version": WORLD_SCHEMA,
           "version_no": 1}
    row.update(drift)
    cursor = ReplayCursor(row, item_id)
    monkeypatch.setattr(persistence, "source_locator", lambda _cur, _school, _source, locator, **kw: locator)
    connection = SimpleNamespace(info=SimpleNamespace(transaction_status=0),
        transaction=lambda **kw: nullcontext(), cursor=lambda **kw: nullcontext(cursor))
    return connection, value, receipt, cursor

@pytest.mark.parametrize("field,bad", [
    ("knowledge_item_id", "44444444-4444-4444-4444-444444444444"),
    ("authority_class", "school_canon"), ("review_status", "unreviewed"),
    ("bidding_system_key", "OTHER_SYSTEM"), ("method_version", "other-method"), ("version_no", 2),
])
def test_replay_rejects_stored_version_binding_drift(monkeypatch, field, bad):
    connection, value, receipt, cursor = replay_fixture(monkeypatch, {field: bad})
    with pytest.raises(ValueError):
        persistence.persist_book_world(connection, value, receipt, school_id=SCHOOL, source_id=SOURCE)

def test_unchanged_replay_is_idempotent(monkeypatch):
    connection, value, receipt, cursor = replay_fixture(monkeypatch, {})
    result = persistence.persist_book_world(connection, value, receipt, school_id=SCHOOL, source_id=SOURCE)
    assert result["new_versions"] == 0

@pytest.mark.parametrize("drift", ["statement", "anchor", "verification", "stable_key", "version_id", "review_id"])
def test_publication_rejects_rehashed_version_drift(monkeypatch, drift):
    items, delivery = candidate_batch()
    item = items[1]
    if drift == "statement":
        item["content"]["statement"] = "Synthetic replacement never approved by the original review."
    elif drift == "anchor":
        item["content"]["citation"]["printed_page_label"] = "unreviewed-label"
        item["sources"][0]["source_locator"] = deepcopy(item["content"]["citation"])
    elif drift == "verification":
        item["content"]["verification"] = "M2"
    elif drift == "stable_key":
        item["stable_key"] = "SYNTHETIC-UNRELATED-STABLE-KEY"
    elif drift == "review_id":
        item["provenance"]["review_receipt"]["review_id"] = "SYNTHETIC-NEW-REVIEW"
    else:
        item["item_id"] = "44444444-4444-4444-4444-444444444444"
    item["provenance"]["version_hash"] = publication_version(
        {"stable_key": item["stable_key"], "content": item["content"]},
        item["provenance"]["review_receipt"])
    cursor = PublicationCursor(items, delivery)
    with pytest.raises(ValueError):
        publish(monkeypatch, items, delivery, cursor)
    assert cursor.writes == []
