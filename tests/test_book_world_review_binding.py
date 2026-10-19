"""Synthetic stored-evidence binding; no database or real book content."""
from copy import deepcopy

import pytest

from bridge_contracts.book_material import InvalidBookMaterial
from bridge_contracts.book_world import publication_version, render_teacher_book
from test_book_world import stored_fixture


DRIFT = [
    (("content", "statement"), "Synthetic unreviewed replacement."),
    (("content", "domain"), "DEFENSE"),
    (("content", "conditions"), ["Synthetic changed condition"]),
    (("content", "exceptions"), ["Synthetic changed exception"]),
    (("content", "verification"), "M2"),
    (("content", "citation", "pdf_page_index"), 1),
    (("content", "citation", "printed_page_label"), "replacement"),
    (("content", "citation", "bbox"), [0.2, 0.3, 0.7, 0.8]),
    (("content", "citation", "segment_sha256"), "c" * 64),
    (("provenance", "review_receipt", "claims", 0, "content_sha256"), "0" * 64),
    (("provenance", "review_receipt", "claims", 0, "anchor_sha256"), "0" * 64),
    (("provenance", "review_receipt", "claims", 0, "verification"), "M2"),
    (("stable_key",), "BOOK-SYNTHETIC-OTHER-KEY"),
]


def drifted_teacher_items():
    """Fresh hashes and synchronized source links cannot replace claim review."""
    for path, replacement in DRIFT:
        item = stored_fixture()
        target = item
        for key in path[:-1]:
            target = target[key]
        assert target[path[-1]] != replacement
        target[path[-1]] = deepcopy(replacement)
        item["sources"][0]["source_locator"] = deepcopy(item["content"]["citation"])
        item["provenance"]["version_hash"] = publication_version(
            {"stable_key": item["stable_key"], "content": item["content"]},
            item["provenance"]["review_receipt"])
        yield ".".join(map(str, path)), item


@pytest.mark.parametrize("label,item", list(drifted_teacher_items()),
                         ids=[label for label, _ in drifted_teacher_items()])
def test_teacher_rejects_rehashed_evidence_not_bound_to_claim(label, item):
    before = deepcopy(item)
    with pytest.raises(InvalidBookMaterial):
        render_teacher_book(item)
    assert item == before


def test_exact_reviewed_teacher_version_keeps_existing_identity_and_citation():
    item = stored_fixture()
    before = deepcopy(item)
    result = render_teacher_book(item)
    assert result["knowledge_version_id"] == item["item_id"]
    assert result["answer"] == item["content"]["statement"]
    assert result["citations"] == [item["content"]["citation"]]
    assert item == before
