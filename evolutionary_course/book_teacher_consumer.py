"""Pure teacher glue for three reviewed book cards; no database/network writer.

The host injects the existing query_knowledge and render_teacher_book functions.
Operator receipts and readbacks are trusted host inputs, never browser inputs.
This module checks consistency; it does not authenticate an operator or publish WORLD.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Callable
from uuid import UUID

MAP_SCHEMA = "teacher-book-card-map-v1"
KNOWLEDGE_CONTRACT = "CANON_KB_READ_V1"
BOOK_CONTRACT = "book-world-reviewed-v1"
LANE = "WORLD_EXTERNAL"
LABELS = ("E01", "E02", "E03")
TOPICS = {"E01": "HAND_AND_DUMMY_TERMS", "E02": "INITIAL_PLAN", "E03": "COMBINED_RESOURCES_AND_ENTRIES"}


class BookTeacherBindingError(ValueError):
    pass


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise BookTeacherBindingError(reason)


def _digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def build_card_map(packet: dict) -> dict:
    """Map the readable handoff, preserving descriptor IDs and empty new DB IDs."""
    _require(packet.get("content_contract") == BOOK_CONTRACT, "BOOK_CONTRACT_MISMATCH")
    _require(packet.get("import_state", {}).get("knowledge_import_committed") is False,
             "USE_OBSERVED_IMPORT_BINDING_FOR_COMMITTED_DATA")
    _require(packet["import_state"].get("observed_import_receipt") is None,
             "UNEXPECTED_OBSERVED_IMPORT_RECEIPT")
    cards = packet.get("cards", [])
    _require(len(cards) == 3 and {c.get("card_label") for c in cards} == set(LABELS),
             "EXACTLY_E01_E02_E03_REQUIRED")
    _require(len({c["stable_key"] for c in cards}) == 3, "DUPLICATE_STABLE_KEY")
    _require(_uuid(packet["source"].get("registry_source_id")), "EXISTING_SOURCE_BINDING_REQUIRED")
    mapped = []
    for label in LABELS:
        card = next(c for c in cards if c["card_label"] == label)
        citation, pages = card["citation"], card["source_pages"]
        _require(card["stable_key"] == card["object_id"], "OBJECT_KEY_MISMATCH")
        _require(card.get("knowledge_item_id") is None and card.get("knowledge_version_id") is None,
                 "DO_NOT_PREPOPULATE_NEW_DATABASE_IDS")
        _require(type(citation["pdf_page_index"]) is int and citation["pdf_page_index"] >= 0
                 and citation["pdf_page_index"] == pages["pdf_page_index_0based"]
                 and citation["pdf_page_index"] + 1 == pages["pdf_page_number_1based"]
                 and citation["printed_page_label"] == pages["printed_page_label"], "PAGE_BINDING_MISMATCH")
        _require(citation["source_id"] == packet["source"]["source_id"]
                 and citation["edition_id"] == packet["source"]["edition_id"]
                 and citation["rendition_sha256"] == packet["source"]["rendition_sha256"],
                 "SOURCE_DESCRIPTOR_MISMATCH")
        mapped.append({
            "card_label": label, "candidate_id": card["candidate_id"], "stable_key": card["stable_key"],
            "object_id": card["object_id"], "name": card["name"], "skill_label": card["skill"],
            "school_catalog_skill_id": None,
            "skill_mapping_status": "BOOK_SKILL_LABEL_ONLY_NOT_CANON_CATALOG_ADMISSION",
            "knowledge_item_id": None, "knowledge_version_id": None,
            "source_review_id": card["source_review_id"],
            "expected_content": {"source_object_id": card["object_id"], "statement": card["statement"],
                                 "conditions": deepcopy(card["conditions"]), "exceptions": deepcopy(card["exceptions"]),
                                 "citation": deepcopy(citation), "origin": card["origin"], "verification": card["verification"]},
            "applicability": {"topic": TOPICS[label], "dummy_exposed": True,
                              "opening_lead_made": True,
                              "initial_plan_only": label in ("E01", "E02"),
                              "rule_origin": "CONSERVATIVE_EDITORIAL_INTEGRATION_FILTER_NOT_SOURCE_QUOTE"},
        })
    return {"schema": MAP_SCHEMA, "knowledge_contract": KNOWLEDGE_CONTRACT,
            "book_contract": BOOK_CONTRACT, "authority_lane": LANE,
            "source_packet_normalized_sha256": _digest(packet),
            "source": deepcopy(packet["source"]), "run": deepcopy(packet["run"]),
            "review_evidence_summary": deepcopy(packet["review_evidence_summary"]),
            "expected_import_receipt": deepcopy(packet["expected_import_receipt"]),
            "observed_import_receipt": None, "observed_recorded_event_payload": None,
            "binding_state": "IMPORT_NOT_COMMITTED", "cards": mapped,
            "canon_activation_performed": False, "domain_writes_performed": False}


def bind_import_receipt(card_map: dict, receipt: dict, recorded_event_payload: dict) -> dict:
    """Consume the actual persist_book_world result plus its existing event payload.

    stable_keys/version_ids are paired in the event's order, not E01/E02/E03 order.
    A successful staging COMMIT still does not admit a teacher explanation.
    """
    _require(card_map.get("schema") == MAP_SCHEMA, "MAP_SCHEMA_MISMATCH")
    _require(isinstance(receipt, dict) and receipt.get("committed") is True
             and receipt.get("dry_run") is False, "IMPORT_NOT_COMMITTED")
    _require(set(receipt) == set(card_map["expected_import_receipt"]), "IMPORT_RECEIPT_FIELDS_CHANGED")
    _require(receipt["schema"] == BOOK_CONTRACT and receipt["authority_lane"] == LANE
             and receipt["canon_activation_performed"] is False
             and receipt["outbox_delivery"] == "REQUIRES_READBACK", "IMPORT_RECEIPT_BOUNDARY_CHANGED")
    _require(_uuid(receipt["changeset_id"]), "REAL_CHANGESET_ID_REQUIRED")
    _require(type(receipt["new_versions"]) is int and 0 <= receipt["new_versions"] <= 3,
             "INVALID_NEW_VERSION_COUNT")
    versions = receipt["version_ids"]
    _require(isinstance(versions, list) and len(versions) == 3 and all(_uuid(v) for v in versions)
             and len(set(versions)) == 3, "REAL_UNIQUE_VERSION_IDS_REQUIRED")
    publication_hash = receipt["publication_hash"]
    _require(isinstance(publication_hash, str) and len(publication_hash) == 64
             and all(c in "0123456789abcdef" for c in publication_hash), "PUBLICATION_HASH_REQUIRED")
    event = recorded_event_payload
    _require(isinstance(event, dict) and set(event) == {
        "schema", "publication_hash", "run", "source_id", "stable_keys", "version_ids"},
        "EXISTING_RECORDED_EVENT_PAYLOAD_REQUIRED")
    _require(event["schema"] == BOOK_CONTRACT and event["publication_hash"] == publication_hash
             and event["run"] == card_map["run"]
             and event["source_id"] == card_map["source"]["registry_source_id"]
             and event["version_ids"] == versions, "IMPORT_EVENT_BINDING_MISMATCH")
    keys = event["stable_keys"]
    _require(isinstance(keys, list) and len(keys) == 3 and len(set(keys)) == 3
             and set(keys) == {c["stable_key"] for c in card_map["cards"]}, "IMPORT_CARD_SET_MISMATCH")
    result = deepcopy(card_map)
    by_key = dict(zip(keys, versions, strict=True))
    for card in result["cards"]:
        card["knowledge_version_id"] = by_key[card["stable_key"]]
        # The persistence receipt has no knowledge_item_id. Do not derive one.
        card["knowledge_item_id"] = None
    result["observed_import_receipt"] = deepcopy(receipt)
    result["observed_recorded_event_payload"] = deepcopy(event)
    result["binding_state"] = "COMMITTED_REQUIRES_ACTIVE_WORLD_READBACK"
    return result


def _gap(label: str, reason: str, lane: str = LANE) -> dict:
    return {"status": "GAP", "reason": reason, "card_label": label, "authority_lane": lane,
            "answer": None, "citations": [], "fallback_performed": False,
            "canon_activation_performed": False, "domain_writes_performed": False,
            "message": "Проверенного применимого книжного объяснения в запрошенном контуре сейчас нет. "
                       "Staged-материал и шаблон квитанции его не заменяют."}


def consume_card(card_map: dict, card_label: str, context: dict, *,
                 query_knowledge: Callable[..., dict], render_teacher_book: Callable[[dict], dict],
                 authority_lane: str = LANE) -> dict:
    """Read active WORLD evidence, check applicability, and retain the cited response.

    No source/candidate/Canon fallback, no importer, no publication and no LLM.
    """
    if authority_lane != LANE:
        return _gap(card_label, "NO_CANON_FALLBACK_FROM_WORLD_BOOK", authority_lane)
    if card_map.get("schema") != MAP_SCHEMA:
        return _gap(card_label, "MAP_SCHEMA_MISMATCH")
    matches = [c for c in card_map.get("cards", []) if c.get("card_label") == card_label]
    if len(matches) != 1:
        return _gap(card_label, "CARD_NOT_CONFIGURED")
    card = matches[0]
    observed = card_map.get("observed_import_receipt")
    if not isinstance(observed, dict) or observed.get("committed") is not True or observed.get("dry_run") is not False:
        return _gap(card_label, "IMPORT_NOT_COMMITTED")
    if not _uuid(card.get("knowledge_version_id")):
        return _gap(card_label, "REAL_VERSION_BINDING_MISSING")
    if not isinstance(context, dict) or context.get("dummy_exposed") is not True or context.get("opening_lead_made") is not True:
        return _gap(card_label, "APPLICABILITY_FACTS_MISSING_OR_FALSE")
    if context.get("topic") != TOPICS[card_label]:
        return _gap(card_label, "SKILL_TOPIC_NOT_APPLICABLE")
    if card_label in ("E01", "E02") and context.get("stage") != "INITIAL_PLAN":
        return _gap(card_label, "INITIAL_PLAN_CONTEXT_REQUIRED")
    if context.get("asks_for") != "PRINCIPLE_EXPLANATION":
        return _gap(card_label, "SPECIFIC_CARD_OR_CONTRACT_DECISION_NOT_SUPPORTED")
    try:
        result = query_knowledge(lane=LANE, system_profile="SYSTEM_NEUTRAL", stable_key=card["stable_key"],
                                 scope_key="default", limit=2, offset=0)
    except Exception:
        return _gap(card_label, "KNOWLEDGE_READER_UNAVAILABLE")
    if not isinstance(result, dict) or result.get("contract_version") != KNOWLEDGE_CONTRACT or result.get("authority_lane") != LANE or result.get("system_profile") != "SYSTEM_NEUTRAL" or result.get("fallback_performed") is not False or result.get("candidate_activation_performed") is not False:
        return _gap(card_label, "KNOWLEDGE_ENVELOPE_INVALID")
    items = result.get("items")
    if not isinstance(items, list) or type(result.get("count")) is not int or result["count"] != len(items):
        return _gap(card_label, "KNOWLEDGE_ENVELOPE_INVALID")
    if result.get("retrieval_status") != ("WORLD_MATCH" if items else "WORLD_GAP"):
        return _gap(card_label, "KNOWLEDGE_ENVELOPE_INVALID")
    if not items:
        return _gap(card_label, "BOOK_WORLD_GAP")
    if len(items) != 1:
        return _gap(card_label, "BOOK_WORLD_VERSION_AMBIGUITY")
    item = items[0]
    if not isinstance(item, dict) or item.get("version_status") != "active" or item.get("review_status") != "reviewed" or item.get("authority_class") != "external" or item.get("system_profile") != "SYSTEM_NEUTRAL":
        return _gap(card_label, "BOOK_WORLD_NOT_ACTIVE_REVIEWED")
    if str(item.get("item_id")) != card["knowledge_version_id"] or item.get("stable_key") != card["stable_key"]:
        return _gap(card_label, "BOOK_WORLD_ID_BINDING_MISMATCH")
    if not isinstance(item.get("provenance"), dict) or item["provenance"].get("source_id") != card_map["source"]["registry_source_id"]:
        return _gap(card_label, "BOOK_WORLD_SOURCE_BINDING_MISMATCH")
    content = item.get("content")
    if not isinstance(content, dict) or content.get("schema") != BOOK_CONTRACT or any(content.get(k) != v for k, v in card["expected_content"].items()):
        return _gap(card_label, "BOOK_WORLD_CONTENT_DRIFT")
    try:
        # Existing renderer owns review/provenance/version-hash/source-asset checks.
        rendered = render_teacher_book(item)
    except Exception:
        return _gap(card_label, "BOOK_WORLD_EVIDENCE_INVALID")
    expected = card["expected_content"]
    if not isinstance(rendered, dict) or any(rendered.get(k) != v for k, v in {
        "contract_version": BOOK_CONTRACT, "authority_lane": LANE,
        "knowledge_version_id": card["knowledge_version_id"], "stable_key": card["stable_key"],
        "answer": expected["statement"], "conditions": expected["conditions"], "exceptions": expected["exceptions"],
        "verification": expected["verification"], "citations": [expected["citation"]],
        "source_review_id": card["source_review_id"],
        "pdf_page_number_1based": expected["citation"]["pdf_page_index"] + 1,
        "canon_activation_performed": False, "fallback_performed": False,
    }.items()):
        return _gap(card_label, "BOOK_RENDERER_RESPONSE_BINDING_MISMATCH")
    citation = expected["citation"]
    page = ("с. " + citation["printed_page_label"] if citation["printed_page_label"] is not None
            else "PDF-страница " + str(citation["pdf_page_index"] + 1))
    explanation_parts = [rendered["answer"]]
    for heading, field in (("Условия применения:", "conditions"),
                           ("Ограничения и исключения:", "exceptions")):
        if rendered[field]:
            explanation_parts.append(heading + "\n" + "\n".join("- " + bound for bound in rendered[field]))
    explanation_parts.append("Источник: " + citation["title"] + ", " + page + ".")
    return {"status": "EXPLANATION_READY", "card_label": card_label, "authority_lane": LANE,
            "skill": {"label": card["skill_label"], "school_catalog_skill_id": None,
                      "status": "APPLICABLE_BOOK_SKILL_LABEL_NOT_SCHOOL_CANON"},
            "book_response": deepcopy(rendered),
            "explanation_with_page": "\n\n".join(explanation_parts),
            "trace": ["ACTIVE_REVIEWED_WORLD_SOURCE_FOUND", "BOOK_SKILL_APPLICABLE", "EXPLANATION_WITH_PAGE"],
            "fallback_performed": False, "canon_activation_performed": False,
            "domain_writes_performed": False, "mastery_event_created": False}
