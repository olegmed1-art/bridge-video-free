"""Synthetic metadata only; no book or credential values."""
from copy import deepcopy
import pytest
from bridge_contracts.book_source_identity import (
    canonical_book_locator, book_source_matches, is_public_book_locator)
from bridge_contracts.book_world import render_teacher_book
from bridge_contracts.book_material import validate_bundle, InvalidBookMaterial
from test_book_world import stored_fixture, reviewed_fixture, reviewed_identity_fixture

ID = "SyntheticDriveFileId_0123456789"
HTTPS = "https://drive.google.com/file/d/" + ID + "/view"


@pytest.mark.parametrize("value", [
    "drive://" + ID, "gdrive://" + ID, "drive:short",
    "drive:" + ID + "?token=synthetic", "drive:" + ID + "#fragment",
    " drive:" + ID, "drive:" + ID + "\n", "http://example.invalid/book",
    "https://user:pass@example.invalid/book", "https://example.invalid/book?q=x",
    "https://example.invalid/book#x", "https://example.invalid:wrong/book",
])
def test_reject_unobserved_or_unsafe_alias(value):
    with pytest.raises(ValueError):
        canonical_book_locator(value)
    assert not book_source_matches(value, HTTPS)


def test_alias_is_identity_only_not_public_citation():
    assert canonical_book_locator("drive:" + ID) == HTTPS
    assert book_source_matches("drive:" + ID, HTTPS)
    assert not is_public_book_locator("drive:" + ID)
    assert not book_source_matches("drive:" + ID + "other", HTTPS)
    assert not book_source_matches("drive:" + ID, HTTPS + "?usp=sharing")


def test_staging_still_requires_https_and_rejects_legacy_citation():
    value, _ = reviewed_fixture()
    value["source"]["locator"] = "drive:" + ID
    with pytest.raises(InvalidBookMaterial, match="CANONICAL_LOCATOR_REQUIRED"):
        validate_bundle(value)


def test_renderer_requires_verified_asset_for_legacy_and_preserves_https():
    item = stored_fixture()
    citation = item["content"]["citation"]
    # Rebind this synthetic record's signed hash after changing its synthetic URL.
    citation["locator"] = HTTPS
    from bridge_contracts.book_world import publication_version
    from bridge_contracts.book_source_review import source_from_citation
    source = source_from_citation(citation, item["provenance"]["review_receipt"])
    identity = reviewed_identity_fixture(
        {"source": source}, item["provenance"]["review_receipt"],
        "11111111-1111-1111-1111-111111111111", item["provenance"]["source_id"], "drive:" + ID)
    item["provenance"].update(source_identity_review=identity, school_id=identity["school_id"])
    item["provenance"]["version_hash"] = publication_version(
        {"stable_key": item["stable_key"], "content": item["content"]},
        item["provenance"]["review_receipt"], identity)
    item["sources"][0].update(canonical_locator="drive:" + ID)
    for flag in (None, False, 1):
        invalid = deepcopy(item)
        invalid["sources"][0]["book_asset_verified"] = flag
        with pytest.raises(InvalidBookMaterial, match="BOOK_WORLD_SOURCE_LINK"):
            render_teacher_book(invalid)
    item["sources"][0]["book_asset_verified"] = True
    assert render_teacher_book(item)["citations"][0]["locator"] == HTTPS
    item["sources"][0]["canonical_locator"] += "wrong"
    with pytest.raises(InvalidBookMaterial, match="BOOK_WORLD_SOURCE_LINK"):
        render_teacher_book(item)


def test_world_only_asset_read_does_not_change_canon_query():
    import bridge_school_api.knowledge as knowledge
    world = knowledge._version_query(knowledge.AuthorityLane.WORLD_EXTERNAL)
    assert "'book_asset_verified', EXISTS" in world
    for lane in (knowledge.AuthorityLane.ACTIVE_SCHOOL_CANON,
                 knowledge.AuthorityLane.SCHOOL_CANON_CANDIDATE):
        assert "public.asset" not in knowledge._version_query(lane)



def test_teacher_rejects_source_uuid_link_mismatch():
    item = stored_fixture()
    item["sources"][0]["source_id"] = "33333333-3333-3333-3333-333333333333"
    with pytest.raises(InvalidBookMaterial, match="BOOK_WORLD_SOURCE_LINK"):
        render_teacher_book(item)
