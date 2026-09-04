from database.video_candidate_persistence import (
    _drive_identity_lookup_key,
    _versioned_stable_key,
)


def test_video_canon_staging_identity_is_content_addressed_and_idempotent():
    first_hash = "a" * 64
    second_hash = "b" * 64
    first = _versioned_stable_key("video_school_canon_candidate", "assertion-7", first_hash)
    second = _versioned_stable_key("video_school_canon_candidate", "assertion-7", second_hash)
    assert first != second
    assert first.endswith(first_hash)
    assert _versioned_stable_key("video_school_canon_candidate", first, first_hash) == first


def test_other_staging_identities_remain_unchanged():
    assert _versioned_stable_key("EXPLANATION_CANDIDATE", "why:7", "c" * 64) == "why:7"


def test_drive_source_lookup_uses_the_authoritative_provider_namespace():
    assert _drive_identity_lookup_key("file-123") == "google-drive:file-123"
    assert _drive_identity_lookup_key("google-drive:file-123") == "google-drive:file-123"
