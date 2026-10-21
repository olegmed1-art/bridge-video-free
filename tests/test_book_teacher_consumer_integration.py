"""Synthetic adapter + actual renderer integration; never connects to a DB."""
from copy import deepcopy
import pytest

from bridge_contracts.book_material import digest
from bridge_contracts.book_world import WORLD_SCHEMA, build_world_publication, publication_version, render_teacher_book, world_uuid
from test_book_material_staging import bundle, record
from test_book_world import reviewed_fixture
from test_book_world_version_identity import replay_fixture, SCHOOL, SOURCE

from evolutionary_course import book_teacher_consumer as adapter


def fixture(*, multiple_bounds=False):
    value, receipt = reviewed_fixture()
    records = [record(page=i) for i in range(3)]
    if multiple_bounds:
        for obj in records:
            obj["content"]["conditions"] = ["Synthetic visible-information condition", "Synthetic opening-lead condition"]
            obj["content"]["exceptions"] = ["Synthetic no-contract-guarantee limit", "Synthetic entry-and-tempo limit"]
    value["objects"] = bundle(records)["objects"]
    template = receipt["claims"][0]
    receipt["claims"] = [{**deepcopy(template), "object_id": obj["object_id"],
                          "content_sha256": digest(obj["content"]),
                          "anchor_sha256": digest(obj["anchor"])} for obj in value["objects"]]
    publication = build_world_publication(value, receipt)
    expected = {"schema": WORLD_SCHEMA, "changeset_id": None, "version_ids": [None] * 3,
                "new_versions": None, "authority_lane": "WORLD_EXTERNAL", "publication_hash": None,
                "outbox_delivery": "REQUIRES_READBACK", "canon_activation_performed": False,
                "dry_run": False, "committed": None}
    packet = {"content_contract": WORLD_SCHEMA,
              "import_state": {"knowledge_import_committed": False, "observed_import_receipt": None},
              "source": {**value["source"], "registry_source_id": SOURCE},
              "run": value["run"], "review_evidence_summary": {"synthetic_only": True},
              "expected_import_receipt": expected, "cards": []}
    items = {}
    for label, projected in zip(adapter.LABELS, publication["records"], strict=True):
        key, content = projected["stable_key"], projected["content"]
        citation = content["citation"]
        version_hash = publication_version(projected, receipt)
        version_id = world_uuid("version", SCHOOL, key + ":" + version_hash)
        packet["cards"].append({"card_label": label, "candidate_id": "SYNTHETIC-" + label,
            "name": "Synthetic " + label, "skill": "Synthetic skill", "stable_key": key, "object_id": key,
            **{k: deepcopy(content[k]) for k in ("statement", "conditions", "exceptions", "origin", "verification")},
            "citation": deepcopy(citation), "source_pages": {"pdf_page_index_0based": citation["pdf_page_index"],
            "pdf_page_number_1based": citation["pdf_page_index"] + 1, "printed_page_label": citation["printed_page_label"]},
            "source_review_id": receipt["review_id"], "knowledge_item_id": None, "knowledge_version_id": None})
        items[label] = {**deepcopy(projected), "item_id": version_id, "version_status": "active",
            "authority_class": "external", "review_status": "reviewed", "system_profile": "SYSTEM_NEUTRAL",
            "provenance": {"schema": WORLD_SCHEMA, "review_receipt": deepcopy(receipt),
                           "version_hash": version_hash, "source_id": SOURCE},
            "sources": [{"source_id": SOURCE, "status": "active",
                         "source_locator": deepcopy(citation), "canonical_locator": value["source"]["locator"]}]}
    mapping = adapter.build_card_map(packet)
    versions = [items[label]["item_id"] for label in adapter.LABELS]
    observed = {**expected, "changeset_id": world_uuid("changeset", SCHOOL, value["run"]["run_id"]),
                "version_ids": versions, "new_versions": 3, "publication_hash": publication["publication_hash"],
                "committed": True}
    event = {"schema": WORLD_SCHEMA, "publication_hash": publication["publication_hash"],
             "run": value["run"], "source_id": SOURCE,
             "stable_keys": [items[label]["stable_key"] for label in adapter.LABELS], "version_ids": versions}
    return packet, mapping, adapter.bind_import_receipt(mapping, observed, event), items


def consume(mapping, label, rows, calls):
    def query(**kwargs):
        calls.append(("query", kwargs))
        return {"contract_version": adapter.KNOWLEDGE_CONTRACT, "authority_lane": adapter.LANE,
                "system_profile": "SYSTEM_NEUTRAL", "fallback_performed": False,
                "candidate_activation_performed": False, "count": len(rows), "items": rows,
                "retrieval_status": "WORLD_MATCH" if rows else "WORLD_GAP"}
    def render(item):
        calls.append(("renderer", None))
        return render_teacher_book(item)
    context = {"topic": adapter.TOPICS[label], "stage": "INITIAL_PLAN", "dummy_exposed": True,
               "opening_lead_made": True, "asks_for": "PRINCIPLE_EXPLANATION"}
    return adapter.consume_card(mapping, label, context, query_knowledge=query, render_teacher_book=render)


@pytest.mark.parametrize("label", adapter.LABELS)
def test_uncommitted_map_never_calls_reader_or_renderer(label):
    packet, mapping, bound, items = fixture()
    before = deepcopy(mapping)
    calls = []
    result = consume(mapping, label, [items[label]], calls)
    assert result["status"] == "GAP" and result["reason"] == "IMPORT_NOT_COMMITTED"
    assert calls == [] and mapping == before
    assert all(c["knowledge_item_id"] is None and c["knowledge_version_id"] is None for c in mapping["cards"])


@pytest.mark.parametrize("label", adapter.LABELS)
def test_exact_active_reviewed_world_uses_real_renderer(label):
    _, _, mapping, items = fixture()
    before = deepcopy((mapping, items))
    calls = []
    result = consume(mapping, label, [items[label]], calls)
    assert result["status"] == "EXPLANATION_READY"
    assert result["book_response"] == render_teacher_book(items[label])
    assert result["book_response"]["knowledge_version_id"] == items[label]["item_id"]
    assert result["canon_activation_performed"] is False and result["domain_writes_performed"] is False
    assert [c[0] for c in calls] == ["query", "renderer"] and (mapping, items) == before


@pytest.mark.parametrize("field,value,reason", [
    ("version_status", "staged", "BOOK_WORLD_NOT_ACTIVE_REVIEWED"),
    ("version_status", "candidate", "BOOK_WORLD_NOT_ACTIVE_REVIEWED"),
    ("review_status", "unreviewed", "BOOK_WORLD_NOT_ACTIVE_REVIEWED"),
    ("authority_class", "school_canon", "BOOK_WORLD_NOT_ACTIVE_REVIEWED"),
    ("item_id", "44444444-4444-4444-4444-444444444444", "BOOK_WORLD_ID_BINDING_MISMATCH"),
])
def test_committed_staging_or_wrong_version_never_becomes_answer(field, value, reason):
    _, _, mapping, items = fixture()
    items["E01"][field] = value
    calls = []
    result = consume(mapping, "E01", [items["E01"]], calls)
    assert result["status"] == "GAP" and result["reason"] == reason
    assert [c[0] for c in calls] == ["query"]


def test_rehashed_receipt_with_unreviewed_proof_is_gap_from_real_renderer():
    _, _, mapping, items = fixture()
    item = items["E01"]
    item["provenance"]["review_receipt"]["claims"][0]["content_sha256"] = "0" * 64
    item["provenance"]["version_hash"] = publication_version(
        {"stable_key": item["stable_key"], "content": item["content"]}, item["provenance"]["review_receipt"])
    calls = []
    result = consume(mapping, "E01", [item], calls)
    assert result["status"] == "GAP" and result["reason"] == "BOOK_WORLD_EVIDENCE_INVALID"
    assert [c[0] for c in calls] == ["query", "renderer"]


@pytest.mark.parametrize("status", ["staged", "active"])
def test_exact_replay_preserves_existing_version_identity(monkeypatch, status):
    connection, value, receipt, cursor = replay_fixture(monkeypatch, {"status": status})
    from database.book_world_persistence import persist_book_world
    first = persist_book_world(connection, value, receipt, school_id=SCHOOL, source_id=SOURCE)
    second = persist_book_world(connection, value, receipt, school_id=SCHOOL, source_id=SOURCE)
    assert first == second and second["new_versions"] == 0
    assert not any(sql.startswith("INSERT INTO public.knowledge_version ") for sql, _ in cursor.writes)
    assert cursor.row["status"] == status


@pytest.mark.parametrize("label", adapter.LABELS)
def test_standalone_explanation_keeps_all_bounds_from_actual_renderer(label):
    _, _, mapping, items = fixture(multiple_bounds=True)
    before = deepcopy((mapping, items))
    rendered = render_teacher_book(items[label])
    calls = []
    result = consume(mapping, label, [items[label]], calls)
    citation = rendered["citations"][0]
    expected = "\n\n".join([
        rendered["answer"],
        "Условия применения:\n" + "\n".join("- " + v for v in rendered["conditions"]),
        "Ограничения и исключения:\n" + "\n".join("- " + v for v in rendered["exceptions"]),
        "Источник: " + citation["title"] + ", с. " + citation["printed_page_label"] + ".",
    ])
    assert result["status"] == "EXPLANATION_READY"
    assert result["explanation_with_page"] == expected
    assert result["book_response"] == rendered
    assert [c[0] for c in calls] == ["query", "renderer"]
    assert (mapping, items) == before
