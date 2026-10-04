"""Separate trusted identity review; never derived from book text or an HTTP client."""
from uuid import UUID, NAMESPACE_URL, uuid5
from .book_material import _fields, _require, _text, digest
from .book_source_identity import book_source_matches

_FIELDS = {"schema", "review_id", "reviewer", "independence_group", "assurance",
           "source_descriptor_sha256", "school_id", "registry_source_id",
           "stored_locator", "citation_locator", "rendition_sha256",
           "source_size_bytes", "source_page_count", "asset_id", "asset_action"}


def source_from_citation(citation, review):
    return {key: citation[key] for key in
            ("source_id", "edition_id", "title", "locator", "rendition_sha256")} | {
                "page_count": review["source_page_count"]}


def validate_identity_review(source, review, identity, *, school_id=None,
                             source_id=None, stored_locator=None):
    _fields(identity, _FIELDS, "BOOK_SOURCE_IDENTITY_REVIEW_REQUIRED")
    _require(identity["schema"] == "book-source-identity-review-v1"
             and identity["assurance"] in {"I2", "I3", "I4"}
             and all(_text(identity[k]) and len(identity[k]) <= 200 for k in
                     ("review_id", "reviewer", "independence_group")),
             "BOOK_SOURCE_IDENTITY_REVIEW_REQUIRED")
    for key in ("school_id", "registry_source_id", "asset_id"):
        try:
            _require(str(UUID(identity[key])) == identity[key], "BOOK_SOURCE_IDENTITY_REVIEW_BINDING")
        except (ValueError, TypeError, AttributeError):
            raise ValueError("BOOK_SOURCE_IDENTITY_REVIEW_BINDING") from None
    _require(identity["source_descriptor_sha256"] == digest(source)
             and identity["citation_locator"] == source["locator"]
             and book_source_matches(identity["stored_locator"], identity["citation_locator"])
             and identity["rendition_sha256"] == source["rendition_sha256"]
             and identity["rendition_sha256"] == review["rendition_sha256"]
             and type(identity["source_size_bytes"]) is int
             and identity["source_size_bytes"] == review["source_size_bytes"]
             and type(identity["source_page_count"]) is int
             and identity["source_page_count"] == source["page_count"] == review["source_page_count"]
             and identity["asset_action"] in {"CREATE", "REUSE"},
             "BOOK_SOURCE_IDENTITY_REVIEW_BINDING")
    if identity["asset_action"] == "CREATE":
        expected = str(uuid5(NAMESPACE_URL, "bridge-book-world:asset:"
                            + identity["school_id"] + ":" + source["rendition_sha256"]))
        _require(identity["asset_id"] == expected, "BOOK_SOURCE_IDENTITY_REVIEW_BINDING")
    for actual, field in ((school_id, "school_id"), (source_id, "registry_source_id"),
                          (stored_locator, "stored_locator")):
        if actual is not None:
            _require(actual == identity[field], "BOOK_SOURCE_IDENTITY_REVIEW_BINDING")
