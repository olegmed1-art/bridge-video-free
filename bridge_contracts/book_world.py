"""Reviewed book atoms projected into the existing WORLD knowledge contract.

Review receipts are supplied by the trusted operator after independent source
review. This validates their binding, not their authenticity: do not accept them
from an unauthenticated endpoint or from text embedded in a book.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from .book_material import InvalidBookMaterial, SHA, _fields, _require, _text, digest, validate_bundle

WORLD_SCHEMA = "book-world-reviewed-v1"
CHECKS = {"source_anchor", "semantic", "conditions", "exceptions", "positive_example",
          "counterexample", "errata", "internal_rights", "semantic_dedup"}
MAX_ATOMS = 3


def build_world_publication(bundle: dict, receipt: dict) -> dict:
    """Prepare, but never publish, a maximum of three independently reviewed atoms."""
    validate_bundle(bundle)
    _fields(receipt, {"schema", "review_id", "reviewer", "independence_group", "assurance",
                      "rendition_sha256", "source_size_bytes", "source_page_count", "claims"}, "REVIEW_FIELDS")
    _require(receipt["schema"] == "book-source-review-v1" and receipt["assurance"] in {"I2", "I3", "I4"}, "INDEPENDENT_REVIEW_REQUIRED")
    _require(all(_text(receipt[k]) for k in ("review_id", "reviewer", "independence_group")), "REVIEW_IDENTITY")
    source = bundle["source"]
    _require(receipt["rendition_sha256"] == source["rendition_sha256"]
             and receipt["source_page_count"] == source["page_count"]
             and type(receipt["source_page_count"]) is int
             and type(receipt["source_size_bytes"]) is int and receipt["source_size_bytes"] > 0, "SOURCE_PROOF_BINDING")
    claims = receipt["claims"]
    _require(isinstance(claims, list) and 0 < len(claims) <= MAX_ATOMS, "ATOM_BUDGET")
    objects = {obj["object_id"]: obj for obj in bundle["objects"]}
    records, selected = [], set()
    for claim in claims:
        _fields(claim, {"object_id", "content_sha256", "anchor_sha256", "verification",
                        "checks", "positive_example_ref", "counterexample_ref", "review_evidence_ref"}, "CLAIM_REVIEW_FIELDS")
        object_id = claim["object_id"]
        _require(isinstance(object_id, str) and object_id in objects and object_id not in selected, "REVIEW_OBJECT_ID")
        selected.add(object_id)
        obj = objects[object_id]
        _require(len(object_id) <= 200, "STABLE_KEY_BUDGET")
        _require(obj["kind"] == "ATOM" and obj["rights"] == "INTERNAL_ALLOWED"
                 and not obj["errata_ids"], "ATOM_NOT_PUBLISHABLE")
        _require(claim["content_sha256"] == digest(obj["content"])
                 and claim["anchor_sha256"] == digest(obj["anchor"]), "CLAIM_PROOF_BINDING")
        _require(claim["verification"] in {"M1", "M2"}, "CLAIM_VERIFICATION")
        _fields(claim["checks"], CHECKS, "REVIEW_CHECKS")
        _require(all(value == "PASS" for value in claim["checks"].values()), "REVIEW_CHECK_FAILED")
        _require(all(_text(claim[k]) for k in ("positive_example_ref", "counterexample_ref", "review_evidence_ref")), "REVIEW_EVIDENCE_REQUIRED")
        _require(bool(obj["content"]["conditions"]) and bool(obj["content"]["exceptions"]), "BOUNDED_CLAIM_REQUIRED")
        # Only short, original paraphrases enter runtime. Full excerpts stay in the private source pack.
        _require(len(obj["content"]["statement"]) <= 1500
                 and sum(len(v) for v in obj["content"]["conditions"] + obj["content"]["exceptions"]) <= 3000, "PARAPHRASE_BUDGET")
        citation = {"source_id": source["source_id"], "edition_id": source["edition_id"],
                    "title": source["title"], "locator": source["locator"],
                    "rendition_sha256": source["rendition_sha256"], **deepcopy(obj["anchor"])}
        content = {"schema": WORLD_SCHEMA, "source_object_id": object_id,
                   "statement": obj["content"]["statement"], "domain": obj["content"]["domain"],
                   "conditions": deepcopy(obj["content"]["conditions"]),
                   "exceptions": deepcopy(obj["content"]["exceptions"]), "citation": citation,
                   "verification": claim["verification"], "origin": obj["origin"]}
        records.append({"stable_key": object_id, "content": content})
    result = {"schema": WORLD_SCHEMA, "source": deepcopy(source), "run": deepcopy(bundle["run"]),
              "receipt": deepcopy(receipt), "records": records, "authority_class": "external",
              "system_profile": "SYSTEM_NEUTRAL"}
    result["publication_hash"] = digest({k: v for k, v in result.items() if k != "run"})
    return result


def publication_version(record: dict, receipt: dict) -> str:
    return digest({"record": record, "receipt": receipt})


def world_uuid(kind: str, school_id: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"bridge-book-world:{kind}:{school_id}:{key}"))


def render_teacher_book(item: dict) -> dict:
    """Render a stored, reviewed WORLD row. Never silently falls back to a preview."""
    content = item.get("content")
    _require(isinstance(content, dict) and content.get("schema") == WORLD_SCHEMA, "BOOK_WORLD_REQUIRED")
    _require(item.get("authority_class") == "external" and item.get("review_status") == "reviewed"
             and item.get("version_status") == "active" and item.get("system_profile") == "SYSTEM_NEUTRAL", "BOOK_WORLD_NOT_ACTIVE")
    _require(content.get("domain") in {"CARD_PLAY", "DEFENSE"}, "BOOK_WORLD_DOMAIN")
    provenance = item.get("provenance") or {}
    _require(provenance.get("schema") == WORLD_SCHEMA and isinstance(provenance.get("review_receipt"), dict), "BOOK_WORLD_PROVENANCE")
    receipt = provenance["review_receipt"]
    _require(provenance.get("version_hash") == publication_version(
        {"stable_key": item["stable_key"], "content": content}, receipt), "BOOK_WORLD_VERSION_HASH")
    citation = content["citation"]
    _require(citation["rendition_sha256"] == receipt.get("rendition_sha256"), "BOOK_WORLD_SOURCE_HASH")
    claims = [c for c in receipt.get("claims", []) if c.get("object_id") == content.get("source_object_id")]
    _require(len(claims) == 1 and claims[0].get("checks") == {k: "PASS" for k in CHECKS}
             and receipt.get("assurance") in {"I2", "I3", "I4"}, "BOOK_WORLD_REVIEW")
    matching = [src for src in item.get("sources", []) if src.get("source_locator") == citation
                and src.get("canonical_locator") == citation["locator"] and src.get("status") == "active"]
    _require(len(matching) == 1, "BOOK_WORLD_SOURCE_LINK")
    return {"contract_version": WORLD_SCHEMA, "authority_lane": "WORLD_EXTERNAL",
            "knowledge_version_id": str(item["item_id"]), "stable_key": item["stable_key"],
            "answer": content["statement"], "conditions": content["conditions"],
            "exceptions": content["exceptions"], "verification": content["verification"],
            "citations": [deepcopy(citation)], "source_review_id": receipt["review_id"],
            "pdf_page_number_1based": citation["pdf_page_index"] + 1,
            "canon_activation_performed": False, "fallback_performed": False}
